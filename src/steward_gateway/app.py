"""The gateway's HTTP and WebSocket surface (spec 3.2). It listens on the gateway's Tailscale address only; every route
but pairing needs the bearer token from pairing. The NAS opens a WebSocket per turn; the gateway never connects to the
NAS. /v1/health carries no household data. Two routes open statement PDFs for the NAS when this machine's sandbox can
(steward_gateway.pdf)."""
from __future__ import annotations

import asyncio
import base64
import hmac
import json
import logging
import time
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
from datetime import datetime, timezone
from types import SimpleNamespace

import anthropic
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from steward_boundary.aliases import is_issuable
from steward_boundary.canonical import canonical, sha256_hex
from steward_boundary.protocol import LINES
from steward_boundary.statements_wire import MAX_PNG, MAX_PNG_SIDE, hint_errors, page_request_errors, pages_errors, png_size
from steward_gateway.accelerator import of_detectors
from steward_gateway.bodycap import BodyCap
from steward_gateway.pdf import PDF_OFF, PdfOff, PdfReader, page_of, read_pdf_body
from steward_gateway.statements import STATEMENTS_AWAY, StatementModel, StatementsAway
from steward_gateway.logs import api_error_reason, log_event, setup_logging
from steward_gateway.categorize import (ALLOWED, CATEGORIZER_AWAY, CATEGORIZER_FEATURES, MAX_BATCH,
                                        MAX_DESCRIPTION, MAX_EXAMPLES, Categorizer, CategorizerAway, Example, Row)
from steward_gateway.config import ANSWER_MODELS, PREWARM_EVERY_S, GatewayConfig
from steward_gateway.ledger import CapReached, Ledger
from steward_gateway.mcp.nas import NasClient
from steward_gateway.mcp.server import mount_mcp
from steward_gateway.loop import NasGone, TurnDeps, run_turn
from steward_gateway.mac import load_or_create_key
from steward_gateway.ollama import Ollama
from steward_gateway.pairing import PairRefused, claim
from steward_gateway.prices import cost_nd, estimate_nd, reserve_price, settle_price
from steward_gateway.prompt import PREFIX_ID, prefix, prefix_sha256, prewarm_body, prewarm_payload
from steward_gateway.redact import Guarded, load_real_detectors, redact
from steward_gateway.secrets import api_key, read_private
from steward_gateway import sending

log = logging.getLogger("steward_gateway.app")
MAX_TEXT = 4000


def _now() -> datetime:
    return datetime.now(timezone.utc)


def make_client(cfg: GatewayConfig, key: str | None):
    """The Anthropic client, or None when no key is set. The caller read the key through api_key(), whose permission
    check stops start-up on a key file others can read (spec 9.5)."""
    if key is None:
        return None
    import anthropic
    # No retries: a sent attempt that timed out, then a retry that failed to connect, would read as "never
    # connected" and release a reservation Anthropic may have billed (final review, finding 3). open_telemetry=False:
    # the SDK makes no spans and sends no trace context, whatever tracer provider the process has. The HTTP client is
    # the SDK's default with a hook that says when a request starts going out (steward_gateway.sending); it adds no
    # header and changes nothing sent.
    return anthropic.AsyncAnthropic(api_key=key, base_url=cfg.anthropic_base_url, max_retries=0, open_telemetry=False,
                                    http_client=sending.http_client())


class WsIO:
    """One turn's socket. pump() is the only reader: it queues each frame and returns when the NAS goes away, so the
    handler can cancel the turn at once instead of the turn noticing at its next send or receive (Task 8.5). A cancel
    frame sets `cancelled` as it arrives, so a turn in the middle of a request stops then too (final review, finding
    1)."""

    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.inbox: asyncio.Queue[str | None] = asyncio.Queue()
        self.cancelled = asyncio.Event()

    async def pump(self) -> None:
        try:
            while True:
                raw = await self.ws.receive_text()
                with suppress(ValueError):
                    frame = json.loads(raw)
                    if isinstance(frame, dict) and frame.get("type") == "cancel":
                        self.cancelled.set()
                await self.inbox.put(raw)
        except (WebSocketDisconnect, RuntimeError):
            await self.inbox.put(None)      # the NAS has gone

    async def send(self, frame: dict) -> None:
        try:
            await self.ws.send_text(json.dumps(frame, ensure_ascii=False))
        except (WebSocketDisconnect, RuntimeError):
            raise NasGone() from None

    async def receive(self, kinds: tuple[str, ...], timeout: float) -> dict:
        while True:
            raw = await asyncio.wait_for(self.inbox.get(), timeout)
            if raw is None:
                self.inbox.put_nowait(None)
                raise NasGone()
            frame = json.loads(raw)
            if isinstance(frame, dict) and frame.get("type") in kinds:
                return frame


def _body_int(body: dict, key: str) -> int:
    v = body.get(key)
    if isinstance(v, bool) or not isinstance(v, int):
        raise HTTPException(422, detail={"error": "That request wasn't understood."})
    return v


NOT_UNDERSTOOD = {"error": "That request wasn't understood."}


async def _json(request: Request):
    try:
        return await request.json()
    except ValueError:
        raise HTTPException(422, detail=NOT_UNDERSTOOD) from None


def _rows(items) -> list[Row]:
    """Rows as the NAS sends them: a description of at most 200 characters (digits already masked) and an amount."""
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_BATCH:
        raise HTTPException(422, detail=NOT_UNDERSTOOD)
    out = []
    for item in items:
        d = item.get("description") if isinstance(item, dict) else None
        a = item.get("amount_cents") if isinstance(item, dict) else None
        if not isinstance(d, str) or len(d) > MAX_DESCRIPTION or isinstance(a, bool) or not isinstance(a, int):
            raise HTTPException(422, detail=NOT_UNDERSTOOD)
        out.append(Row(d, a))
    return out


def _examples(items) -> list[Example]:
    """The household's rules the NAS sends along (phase 12 decision 3): none, or at most 40, each a masked description
    of at most 200 characters and a category the model may answer."""
    if items is None:
        return []
    if not isinstance(items, list) or len(items) > MAX_EXAMPLES:
        raise HTTPException(422, detail=NOT_UNDERSTOOD)
    out = []
    for item in items:
        d = item.get("description") if isinstance(item, dict) else None
        c = item.get("category") if isinstance(item, dict) else None
        if not isinstance(d, str) or len(d) > MAX_DESCRIPTION or c not in ALLOWED:
            raise HTTPException(422, detail=NOT_UNDERSTOOD)
        out.append(Example(d, c))
    return out


def create_gateway_app(cfg: GatewayConfig, *, detectors=None, client=None, ledger: Ledger | None = None,
                       now=None, ollama=None, categorizer=None, nas=None, statements=None,
                       pdf=None) -> FastAPI:
    now = now or _now
    st = SimpleNamespace(ledger=None, mac_key=b"", detectors=[], problem=None, client=None, last_prewarm=None, ollama=None,
                         categorizer=None, nas=None, statements=None, pdf=None, accelerator=None)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Start-up (spec 9.5, 3.1, review focus 5), in this order, all before the first turn is accepted:
        # 1. every secret file is read through its permission check, so a file others can read stops start-up;
        key = api_key(cfg)
        read_private(cfg.token_path)
        st.mac_key = load_or_create_key(cfg.history_key_path)
        read_private(cfg.history_key_path)
        # 2. logging goes through the scrubbing filter (uvicorn's, httpx's and anthropic's loggers included);
        setup_logging()
        # 3. reservations a crash left open count in full.
        st.ledger = ledger or Ledger(cfg.ledger_path, cfg.timezone, cfg.default_cap_cents, cfg.max_cap_cents,
                                     cfg.default_model)
        settled = st.ledger.settle_stale()
        if settled:
            log_event(log, "ledger.stale_settled", level=logging.WARNING, count=settled)
        loaded, st.problem = (list(detectors), None) if detectors is not None else load_real_detectors()
        st.accelerator = of_detectors(loaded)
        st.detectors = [Guarded(loaded)] if loaded else []
        st.client = client if client is not None else make_client(cfg, key)
        st.ollama = ollama if ollama is not None else Ollama(cfg.ollama_url, cfg.title_model)
        st.categorizer = categorizer if categorizer is not None else Categorizer(
            cfg.ollama_url, cfg.categorizer_model, parallel=cfg.categorizer_parallel)
        st.statements = statements if statements is not None else StatementModel(
            cfg.ollama_url, cfg.statement_layout_model, layout_timeout=cfg.statement_layout_timeout_s,
            vision_model=cfg.statement_vision_model, page_timeout=cfg.statement_page_timeout_s,
            wait_idle=getattr(st.categorizer, "wait_idle", None))
        st.pdf = pdf if pdf is not None else PdfReader()
        st.nas = nas if nas is not None else (NasClient(cfg.nas_url, lambda: read_private(cfg.token_path))
                                              if cfg.nas_url else None)
        try:
            yield
        finally:
            if client is None and st.client is not None:
                await st.client.close()
            if ollama is None and st.ollama is not None:
                await st.ollama.close()
            if categorizer is None and st.categorizer is not None:
                await st.categorizer.close()
            if statements is None and st.statements is not None:
                await st.statements.close()
            if pdf is None and st.pdf is not None:
                await st.pdf.close()
            if nas is None and st.nas is not None:
                await st.nas.close()

    app = FastAPI(title="Steward gateway", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(BodyCap)
    app.state.shared = st

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        body = exc.detail if isinstance(exc.detail, dict) else {"error": str(exc.detail)}
        return JSONResponse(body, status_code=exc.status_code)

    def token_ok(header: str | None) -> bool:
        token = read_private(cfg.token_path)
        if token is None:
            # the NAS shows this one: it fills {gateway} with its name
            raise HTTPException(503, detail={"error": "{gateway} isn't paired with Steward yet."})
        # As bytes: compare_digest raises TypeError on a str that isn't ASCII, which would be a 500, not a 401.
        return hmac.compare_digest((header or "").encode("utf-8"), f"Bearer {token}".encode("utf-8"))

    def auth(request: Request) -> None:
        if not token_ok(request.headers.get("authorization")):
            raise HTTPException(401, detail={"error": "Not allowed."})

    def stopped() -> bool:
        return cfg.stop_flag_path.exists()

    @app.get("/v1/health", dependencies=[Depends(auth)])
    async def health():
        statement = await st.statements.features()
        opening = await st.pdf.features()                   # remote-reading P58: only while the wall holds
        ollama_up = await st.ollama.answering()
        return {"key_set": st.client is not None, "redaction_ready": bool(st.detectors),
                "redaction_problem": st.problem, "ollama": ollama_up,
                "accelerator": st.accelerator,
                "ollama_accelerated": await st.ollama.accelerated() if ollama_up else None,
                "categorizer": await st.categorizer.ready(), "chat_stopped": stopped(), "mcp": cfg.mcp,
                "budget": st.ledger.state(now(), cfg),
                "categorizer_features": list(CATEGORIZER_FEATURES),
                "statement_features": [*statement, *opening]}

    @app.post("/v1/redact", dependencies=[Depends(auth)])
    async def redact_route(request: Request):
        body = await request.json()
        text = body.get("text") if isinstance(body, dict) else None
        # The aliases and bracket tokens the NAS issued in this question, as in the turn's START frame, so the preview
        # is what will be sent (spec 4.3, "The preview").
        issued = body.get("issued", []) if isinstance(body, dict) else None
        if (not isinstance(text, str) or len(text) > MAX_TEXT or not isinstance(issued, list) or len(issued) > 2000
                or not all(is_issuable(t) for t in issued)):
            raise HTTPException(422, detail={"error": "That request wasn't understood."})
        if not st.detectors:
            raise HTTPException(503, detail={"error": LINES["privacy_not_ready"]})
        r = await asyncio.to_thread(redact, text, st.detectors, frozenset(issued))
        return {"text": r.text, "edits": [asdict(e) for e in r.edits]}

    @app.post("/v1/title", dependencies=[Depends(auth)])
    async def title_route(request: Request):
        body = await request.json()
        text = body.get("text") if isinstance(body, dict) else None
        if not isinstance(text, str) or len(text) > MAX_TEXT:
            raise HTTPException(422, detail={"error": "That request wasn't understood."})
        if not await st.ollama.answering():
            return {"title": None}
        return {"title": await st.ollama.title(text)}

    async def _categorize(rows: list[Row], examples: list[Example]) -> list[dict]:
        t0 = time.monotonic()
        try:
            answers = await st.categorizer.categorize(rows, examples)
        except CategorizerAway:
            log_event(log, "categorize.away", level=logging.WARNING, rows=len(rows))
            raise HTTPException(503, detail={"error": CATEGORIZER_AWAY}) from None
        log_event(log, "categorize.done", rows=len(rows),
                  categorized=sum(a.category != "uncategorized" for a in answers),
                  guessed=sum(a.guess is not None for a in answers), examples=len(examples),
                  ms=int((time.monotonic() - t0) * 1000))
        return [{"category": a.category, "merchant": a.merchant,
                 **({"confidence": a.confidence} if a.confidence is not None else {}),
                 **({"guess": a.guess} if a.guess is not None else {}),
                 **({"business": a.business} if a.business is not None else {})} for a in answers]

    @app.post("/v1/categorize", dependencies=[Depends(auth)])
    async def categorize_one(request: Request):
        body = await _json(request)
        (answer,) = await _categorize(_rows([body] if isinstance(body, dict) else None), [])
        return answer

    @app.post("/v1/categorize/batch", dependencies=[Depends(auth)])
    async def categorize_batch(request: Request):
        body = await _json(request)
        rows = _rows(body.get("rows") if isinstance(body, dict) else None)
        return {"rows": await _categorize(rows, _examples(body.get("examples") if isinstance(body, dict) else None))}

    @app.post("/v1/statements/layout", dependencies=[Depends(auth)])
    async def statements_layout(request: Request):
        """P27: a new layout's lines (P13; account-number runs masked on the NAS) and P21's hint; the layout back, held
        to the wire's schema. The body was capped at 512 KB before this ran (BodyCap)."""
        body = await _json(request)
        if not isinstance(body, dict) or set(body) != {"pages", "hint"} or pages_errors(body["pages"]) \
                or hint_errors(body["hint"]):
            raise HTTPException(422, detail=NOT_UNDERSTOOD)
        pages = body["pages"]
        t0 = time.monotonic()
        try:
            layout = await st.statements.layout(pages, body["hint"])
        except StatementsAway:
            log_event(log, "statements.away", level=logging.WARNING, pages=len(pages))
            raise HTTPException(503, detail={"error": STATEMENTS_AWAY}) from None
        log_event(log, "statements.layout.done", pages=len(pages), lines=sum(len(p["lines"]) for p in pages),
                  ms=int((time.monotonic() - t0) * 1000), ok=True)
        return {"layout": layout, "model": st.statements.layout_model}    # statement_layout.model on the NAS

    @app.post("/v1/statements/page", dependencies=[Depends(auth)])
    async def statements_page(request: Request):
        """P27: one scanned page (P26: at most 3 MB and 2,400 px a side, a PNG by its header); the page's rows or
        holdings as printed, held to P25's schema. The body was capped at 5 MB before this ran."""
        body = await _json(request)
        if page_request_errors(body):
            raise HTTPException(422, detail=NOT_UNDERSTOOD)
        try:
            raw = base64.b64decode(body["image"], validate=True)
        except ValueError:
            raise HTTPException(422, detail=NOT_UNDERSTOOD) from None
        size = png_size(raw)
        if len(raw) > MAX_PNG or size is None or size != (body["width_px"], body["height_px"]) \
                or max(size) > MAX_PNG_SIDE:
            raise HTTPException(422, detail=NOT_UNDERSTOOD)
        del raw
        t0 = time.monotonic()
        try:
            page = await st.statements.page(body)
        except StatementsAway:
            log_event(log, "statements.away", level=logging.WARNING, pages=1)
            raise HTTPException(503, detail={"error": STATEMENTS_AWAY}) from None
        log_event(log, "statements.page.done", pages=1, rows=len(page["rows"]), ms=int((time.monotonic() - t0) * 1000),
                  ok=True)
        return {"page": page, "model": st.statements.vision_model}

    async def pdf_on() -> None:
        """P58: refuse before the body is read, so a walled-off gateway never buffers a PDF only to answer 503."""
        if not await st.pdf.ok():
            log_event(log, "statements.pdf_off", level=logging.WARNING)
            raise HTTPException(503, detail={"error": PDF_OFF})

    @app.post("/v1/statements/extract", dependencies=[Depends(auth)])
    async def statements_extract(request: Request):
        """Remote-reading P57: a whole statement PDF, raw (capped at 15 MB + 64 KB before routing), opened by this
        machine's sandbox; the child's answer as the parent's checks left it, which the NAS checks again (P62)."""
        await pdf_on()
        data = await read_pdf_body(request)
        t0 = time.monotonic()
        try:
            out = await st.pdf.extract(data)
        except PdfOff:
            log_event(log, "statements.pdf_off", level=logging.WARNING)
            raise HTTPException(503, detail={"error": PDF_OFF}) from None
        finally:
            del data
        log_event(log, "statements.extract.done", pages=len(out.get("pages") or ()),
                  ms=int((time.monotonic() - t0) * 1000), ok="error" not in out, status=out.get("error", "read"))
        return out

    @app.post("/v1/statements/render", dependencies=[Depends(auth)])
    async def statements_render(request: Request):
        """Remote-reading P57: one page of a whole PDF as render_page's picture, for the NAS to check and send to the
        vision model (P67)."""
        n = page_of(request)
        await pdf_on()
        data = await read_pdf_body(request)
        t0 = time.monotonic()
        try:
            out = await st.pdf.render(data, n)
        except PdfOff:
            log_event(log, "statements.pdf_off", level=logging.WARNING)
            raise HTTPException(503, detail={"error": PDF_OFF}) from None
        finally:
            del data
        log_event(log, "statements.render.done", pages=1, ms=int((time.monotonic() - t0) * 1000),
                  ok="error" not in out, status=out.get("error", "read"))
        return out

    @app.websocket("/v1/turn")
    async def turn(ws: WebSocket):
        try:
            ok = token_ok(ws.headers.get("authorization"))
        except HTTPException:
            ok = False
        if not ok:
            await ws.close(code=1008)
            return
        await ws.accept()
        io = WsIO(ws)

        async def converse() -> None:
            start = await io.receive(("start",), 30)
            await run_turn(start, io, TurnDeps(config=cfg, ledger=st.ledger, client=st.client, detectors=st.detectors,
                                               mac_key=st.mac_key, stopped=stopped, now=now))

        reader, talker = asyncio.create_task(io.pump()), asyncio.create_task(converse())
        cancel = asyncio.create_task(io.cancelled.wait())
        try:
            await asyncio.wait({reader, talker, cancel}, return_when=asyncio.FIRST_COMPLETED)
            if not talker.done():
                # The NAS cancelled or went away mid-turn: cancel the turn now, so an Anthropic request in flight stops
                # and its reservation is released or settled in run_turn's finally (Task 11's cannot-verify, Task 8.5).
                # On a cancel, run_turn then sends done with the totals, settled reservation included.
                talker.cancel()
            (result,) = await asyncio.gather(talker, return_exceptions=True)
            if cancel.done() and isinstance(result, asyncio.CancelledError):
                log_event(log, "turn.cancelled")
            elif isinstance(result, (NasGone, asyncio.TimeoutError, asyncio.CancelledError)):
                log_event(log, "turn.nas_gone")
            elif isinstance(result, BaseException):
                raise result
        finally:
            for task in (talker, reader, cancel):
                if not task.done():
                    task.cancel()
            await asyncio.gather(talker, reader, cancel, return_exceptions=True)
            with suppress(Exception):
                await ws.close()

    def due() -> bool:
        return st.last_prewarm is None or (now() - st.last_prewarm).total_seconds() >= PREWARM_EVERY_S

    @app.get("/v1/instructions", dependencies=[Depends(auth)])
    async def instructions():
        """The fixed instructions and tool definitions every request starts with, whole, with the hash a Getting ready
        row records (public issue #3). The same for every install of this version: no book, person or date in it."""
        return {"prefix": PREFIX_ID, "prefix_sha256": prefix_sha256(), "content": prefix()}

    @app.post("/v1/prewarm/plan", dependencies=[Depends(auth)])
    async def prewarm_plan(request: Request):
        payload = prewarm_payload()
        return {"due": due() and st.client is not None and not stopped(), "payload": payload,
                "sha256": sha256_hex(payload), "model": st.ledger.model()}

    @app.post("/v1/prewarm", dependencies=[Depends(auth)])
    async def prewarm(request: Request):
        body = await request.json()
        book_id, user_id = _body_int(body, "book_id"), _body_int(body, "user_id")
        payload = prewarm_payload()
        if body.get("sha256") != sha256_hex(payload):
            raise HTTPException(409, detail={"error": "That isn't the current prefix."})
        if not due() or st.client is None or stopped():
            return {"sent": False}
        model = st.ledger.model()
        req = prewarm_body(model)
        reserved = estimate_nd(len(json.dumps(req)), 0, reserve_price(cfg, model))
        try:
            rid = st.ledger.reserve(now=now(), book_id=book_id, user_id=user_id, turn_id=None, channel="prewarm",
                                    model=model, estimate_nd=reserved)
        except CapReached:
            return {"sent": False}
        st.last_prewarm = now()
        st.ledger.record_sent(now(), [(sha256_hex(payload), len(canonical(payload)))], book_id, "prewarm")
        attempt = sending.Attempt()

        def unwind(e: BaseException) -> None:
            """Released when Anthropic refused it with an error status or it certainly never went out; otherwise it
            may have been billed and counts at its reservation: the same rule as a turn's requests."""
            if isinstance(e, anthropic.APIStatusError) or sending.nothing_sent(attempt, e):
                st.ledger.release(rid)
            else:
                st.ledger.settle(rid, reserved)
        try:
            with sending.watching(attempt):
                msg = await st.client.messages.create(**req)
        except Exception as e:  # noqa: BLE001 - a failed prewarm changes nothing
            unwind(e)
            kind, message = api_error_reason(e) if isinstance(e, anthropic.APIStatusError) else ("", "")
            log_event(log, "prewarm.failed", level=logging.WARNING, error_type=type(e).__name__,
                      error_kind=kind, error_message=message)
            return {"sent": False}
        except BaseException as e:      # cancelled (the gateway stopping): no reservation is left open (Argon 2.3)
            unwind(e)
            raise
        usage = msg.usage.to_dict(mode="json")
        st.ledger.settle(rid, cost_nd(usage, settle_price(cfg, msg.model, usage)))
        log_event(log, "prewarm.done", cache_write_tokens=usage.get("cache_creation_input_tokens") or 0,
                  cache_read_tokens=usage.get("cache_read_input_tokens") or 0, cost_nd=cost_nd(usage, settle_price(cfg, msg.model, usage)))
        return {"sent": True}

    @app.get("/v1/usage", dependencies=[Depends(auth)])
    def usage():
        return st.ledger.usage(now())

    @app.get("/v1/settings", dependencies=[Depends(auth)])
    def settings_get():
        return {"cap_cents": st.ledger.cap_cents(), "max_cap_cents": cfg.max_cap_cents, "model": st.ledger.model(),
                "models": list(ANSWER_MODELS)}

    @app.post("/v1/settings", dependencies=[Depends(auth)])
    async def settings_set(request: Request):
        body = await request.json()
        if not isinstance(body, dict) or ("model" in body and not isinstance(body["model"], str)):
            raise HTTPException(422, detail={"error": "That request wasn't understood."})
        cap = _body_int(body, "cap_cents") if "cap_cents" in body else None
        try:        # both are checked before either is saved
            st.ledger.update_settings(cap_cents=cap, model=body["model"] if "model" in body else None)
        except ValueError as e:
            raise HTTPException(422, detail={"error": str(e)}) from None
        return settings_get()

    @app.get("/v1/sent", dependencies=[Depends(auth)])
    def sent(since: str):
        try:
            when = datetime.fromisoformat(since)
        except ValueError:
            raise HTTPException(422, detail={"error": "That request wasn't understood."}) from None
        return {"sent": st.ledger.sent_since(when)}

    @app.post("/v1/pair")
    async def pair(request: Request):
        body = await request.json()
        code = body.get("code") if isinstance(body, dict) else None
        if not isinstance(code, str):
            raise HTTPException(422, detail={"error": "That request wasn't understood."})
        try:
            return {"token": claim(cfg, code, now())}
        except PairRefused as e:
            detail = {"error": e.message} | ({"tries_left": e.tries_left} if e.tries_left is not None else {})
            raise HTTPException(e.status, detail=detail) from None

    if cfg.mcp:                             # release spec 2026-11-05, section 6: Claude Code is off unless asked for
        mount_mcp(app, cfg, st, stopped=stopped, now=now)
    return app
