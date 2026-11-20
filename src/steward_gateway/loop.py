"""One turn on the gateway (spec 4.5, 8.2). Redact the question; then for each request (at most 8): run the outgoing
checks on the new parts, reserve the worst case, have the NAS record the parts, send them to the NAS as "sent", stream
from Anthropic with server-side fallback, settle the cost, and pass any tool calls to the NAS. In approved store rows
that come back, a person's name the model finds in an unticked row is replaced first (checks.mask_store_names). Every
exit is a done frame with a stop code; nothing is ever retried by sending more (spec 10). The reservation is sized on
the whole request body; every exit that isn't a settled request releases it, or, when the request may have been sent and was
then cancelled or dropped with no usage back, settles it at the full reservation (preflight ruling 4, chunk 2 ruling;
steward_gateway.sending says when nothing can have been sent). Logs carry
turn ids, timings, tokens and costs only (spec 3.1)."""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import aclosing, suppress
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Protocol

import anthropic

from steward_boundary import PROTOCOL
from steward_boundary import protocol as P
from steward_boundary import tools as T
from steward_boundary.about import about_text
from steward_boundary.aliases import BRACKETS
from steward_boundary.canonical import canonical
from steward_boundary.store_rows import ticks_of
from steward_gateway.checks import Part, Stopped, check_parts, mask_store_names, verify_history
from steward_gateway.config import FALLBACK_BETA, MAX_STEPS, MAX_TOKENS, GatewayConfig
from steward_gateway.ledger import CapReached, Ledger
from steward_gateway.logs import api_error_reason, log_event
from steward_gateway.mac import mac
from steward_gateway.prices import cost_nd, estimate_nd, reserve_price, settle_price
from steward_gateway.prompt import context_text, request_body, system_message, user_text
from steward_gateway.redact import Detector, redact
from steward_gateway.sending import Attempt, nothing_sent, watching

log = logging.getLogger("steward_gateway.loop")


class NasGone(Exception):
    """The NAS closed the socket."""


class TurnIO(Protocol):
    async def send(self, frame: dict) -> None: ...

    async def receive(self, kinds: tuple[str, ...], timeout: float) -> dict: ...


@dataclass
class TurnDeps:
    config: GatewayConfig
    ledger: Ledger
    client: Any                          # anthropic.AsyncAnthropic, or None when no key is set
    detectors: Sequence[Detector]        # empty when the models didn't load
    mac_key: bytes
    stopped: Callable[[], bool]
    now: Callable[[], datetime]
    reply_timeout: float = 180.0
    approval_timeout: float = 660.0      # a store-names card waits 10 minutes on the NAS (plan B ruling 7)


def _unwind(ledger: Ledger, rid: int, reserved: int, billable: bool) -> int:
    """A request that never reached Anthropic, or that Anthropic refused with an error status, cost nothing and is
    released. One that may have been sent and then was cancelled or dropped with no usage back may have been billed,
    so it counts at its full reservation (chunk 2 ruling: billing on cancel is unknown, and this keeps the cap safe).
    Returns what it was charged, so the turn's done frame carries the real cost."""
    if billable:
        ledger.settle(rid, reserved)
        return reserved
    ledger.release(rid)
    return 0


class StreamCut(Exception):
    """The stream broke once Anthropic had answered 200, or ended without message_start or message_stop: an answer cut
    short, with no final usage (Argon 3.1)."""


async def _events(stream) -> AsyncIterator[Any]:
    """The stream's events. Whatever reading it raises comes out as StreamCut from that error: httpx2 failing to read
    the body, an event that isn't UTF-8 or JSON, the SDK's own parsing (a bare APIError, APIResponseValidationError, a
    ValueError on a tool's input, an IndexError on a block that never started). An error event keeps its status error,
    so its kind can still read as busy."""
    events = stream.__aiter__()
    while True:
        try:
            event = await events.__anext__()
        except StopAsyncIteration:
            return
        except anthropic.APIStatusError:
            raise
        except Exception as e:  # noqa: BLE001 - anything the stream raises is a broken stream
            raise StreamCut(type(e).__name__) from e
        yield event


def _broken(streamed: bool, otherwise: str = "api_error") -> str:
    """A request that failed: "dropped" (the answer is incomplete) when some of this turn's answer already reached the
    NAS, in any step; `otherwise` ("api_error", or "busy" for a try-again-shortly error) when none did."""
    return "dropped" if streamed else otherwise

# Error kinds that mean "try again shortly", from a status (429, 503, 529) or an error event mid-stream.
BUSY = (anthropic.RateLimitError, anthropic.ServiceUnavailableError, anthropic.OverloadedError)
BUSY_KINDS = ("rate_limit_error", "overloaded_error")


async def run_turn(start: dict, io: TurnIO, deps: TurnDeps) -> dict:
    turn_id = start.get("turn_id", "?") if isinstance(start, dict) else "?"
    t0 = time.monotonic()
    totals = {"requests": 0, "cost_nd": 0, "last_input_tokens": 0}
    ended: list[dict] = []

    async def done(stop: str, check: str | None = None, path: str | None = None) -> dict:
        frame = {"type": "done", "stop": stop, "line": P.LINES.get(stop), "check": check, "path": path, **totals}
        log_event(log, "turn.done", turn_id=turn_id, stop=stop, requests=totals["requests"],
                  cost_nd=totals["cost_nd"], ms=int((time.monotonic() - t0) * 1000))
        await io.send(frame)
        ended.append(frame)
        return frame

    try:
        return await _turn(start, io, deps, totals, done, turn_id)
    except asyncio.CancelledError:
        # The NAS cancelled mid-request (Stop, a card's timeout, the browser gone) or went away. The request in flight
        # was settled or released on the way out, so the totals are the turn's real cost: the NAS records them
        # (final review, finding 1). A NAS that has gone gets nothing.
        if not ended:
            with suppress(Exception):
                await done("cancelled")
        raise


async def _turn(start: dict, io: TurnIO, deps: TurnDeps, totals: dict, done, turn_id: str) -> dict:
    if P.check_frame(P.START, start):
        return await done("bad_request")
    if start["protocol"] != PROTOCOL:       # Ask about this P11: a NAS on another protocol, before anything else
        return await done("bad_request")
    if deps.stopped():
        return await done("stopped_by_gateway")
    if deps.client is None:
        return await done("not_set_up")
    if not deps.detectors:
        return await done("privacy_not_ready")
    try:
        messages = verify_history(start["history"], deps.mac_key)
    except Stopped as e:
        return await done("stopped_check", e.check, e.path)

    issued = P.issued(start)
    redacted = await asyncio.to_thread(redact, start["question"], deps.detectors, issued)
    await io.send({"type": "redacted", "text": redacted.text, "edits": [asdict(e) for e in redacted.edits]})
    fp = start["fingerprints"]
    salt, hashes, approvable = fp["salt"], frozenset(fp["hashes"]), frozenset(fp.get("approvable", ()))
    # The tokens the NAS issued, plus the brackets this gateway's own redaction just put into the question.
    issued = issued | {e.label for e in redacted.edits if e.label in BRACKETS}
    model = deps.ledger.model()
    ctx = context_text(start["context"])
    # The about sentence, built here from the checked typed object: the fingerprints don't read it (Argon follow-up 1).
    about = about_text(start["context"]["about"]) if start["context"].get("about") else None
    new_msgs = [user_text(redacted.text), system_message(ctx)]
    parts = [Part("question", {"text": redacted.text}), Part("system", {"text": ctx})]

    streamed = False        # answer text has reached the NAS in this turn, in any step: the NAS shows all of it
    for step in range(1, MAX_STEPS + 1):
        try:
            await asyncio.to_thread(check_parts, parts, detectors=deps.detectors, salt=salt, hashes=hashes,
                                    approvable=approvable, issued=issued, about=about)
        except Stopped as e:
            return await done("stopped_check", e.check, e.path)

        # Reserve first, on the estimate over the full request body (worst case). On CapReached nothing is recorded,
        # so the book's log never lists a request that wasn't sent (spec 4.6).
        body = request_body(model, messages + new_msgs)
        reserved = estimate_nd(len(canonical(body)), MAX_TOKENS, reserve_price(deps.config, model))
        try:
            rid = deps.ledger.reserve(now=deps.now(), book_id=start["book_id"], user_id=start["user_id"],
                                      turn_id=start["turn_id"], channel="chat", model=model, estimate_nd=reserved)
        except CapReached:
            return await done("cap" if step == 1 else "cap_mid")

        open_ = True        # the reservation is open until it is settled below; every other exit unwinds it
        sent = False        # the request was handed to the SDK, so it may be billed even with no usage back
        attempt = Attempt()     # unless the HTTP client says it never began writing it (Argon 2.1)
        answered = False    # Anthropic accepted the request and began a stream: billable, whatever `attempt` says

        def unwind() -> None:
            """Before any done frame (and on every other exit): what this request was charged joins the totals."""
            nonlocal open_
            if open_:
                open_ = False
                billable = sent and (answered or not nothing_sent(attempt))
                totals["cost_nd"] += _unwind(deps.ledger, rid, reserved, billable)

        try:
            await io.send({"type": "record", "seq": step, "model": model, "parts": [
                {"part": p.part, "payload": p.payload, "sha256": p.sha256(), "bytes": p.size()} for p in parts]})
            try:
                reply = await io.receive(("recorded", "record_failed"), deps.reply_timeout)
            except asyncio.TimeoutError:
                unwind()
                return await done("dropped")
            if reply["type"] != "recorded" or reply.get("seq") != step:
                unwind()
                return await done("record_failed")

            await io.send({"type": "sent",
                           "messages": [{"message": m, "mac": mac(deps.mac_key, m)} for m in new_msgs]})
            messages = messages + new_msgs
            deps.ledger.record_sent(deps.now(), [(p.sha256(), p.size()) for p in parts], start["book_id"], "chat")
            totals["requests"] = step

            started = stopped = False       # this stream's message_start and message_stop
            try:
                with watching(attempt):
                    manager = deps.client.beta.messages.stream(**body, betas=[FALLBACK_BETA], fallbacks="default")
                    sent = True
                    async with manager as stream:
                        answered = True
                        try:
                            async with aclosing(_events(stream)) as events:
                                async for event in events:
                                    if event.type == "message_start":
                                        started = True
                                    elif event.type == "message_stop":
                                        stopped = True
                                    elif event.type == "content_block_start" and event.content_block.type == "tool_use":
                                        await io.send({"type": "status", "tool": event.content_block.name})
                                    elif event.type == "content_block_delta" and event.delta.type == "text_delta":
                                        await io.send({"type": "text", "delta": event.delta.text})
                                        streamed = True
                        except StreamCut:
                            if not stopped:
                                raise
                            # It broke after message_stop: the answer and its usage are whole, so it ends as usual.
                        if not (started and stopped):
                            raise StreamCut("no message_start or message_stop")
                        final = stream.current_message_snapshot
                        if final.stop_reason is None:   # message_stop with no message_delta: no final usage
                            raise StreamCut("no stop_reason")
            except anthropic.APIStatusError as e:
                if not answered:    # Anthropic refused it with an error status: nothing to bill
                    sent = False
                kind, message = api_error_reason(e)
                log_event(log, "turn.request_failed", level=logging.WARNING, turn_id=turn_id, step=step,
                          status=e.status_code, request_id=e.request_id, error_type=type(e).__name__,
                          error_kind=kind, error_message=message)
                unwind()
                if isinstance(e, BUSY) or e.status_code == 529 or kind in BUSY_KINDS:
                    return await done(_broken(streamed, "busy"))
                if isinstance(e, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
                    return await done("not_set_up")
                return await done(_broken(streamed))
            except anthropic.APIConnectionError as e:
                if not answered and nothing_sent(attempt, e):
                    sent = False
                log_event(log, "turn.request_failed", level=logging.WARNING, turn_id=turn_id, step=step,
                          error_type=type(e).__name__)
                unwind()
                return await done(_broken(streamed))
            except StreamCut as e:
                # Anthropic answered and the stream then broke: it was sent and no final usage came back, so it counts
                # in full, and the turn still ends with a done the NAS can show (Argon 3.1).
                log_event(log, "turn.request_failed", level=logging.WARNING, turn_id=turn_id, step=step,
                          error_type=type(e.__cause__ or e).__name__)
                unwind()
                return await done(_broken(streamed))

            usage = final.usage.to_dict(mode="json")
            cost = cost_nd(usage, settle_price(deps.config, final.model, usage))
            deps.ledger.settle(rid, cost)
            open_ = False
        finally:
            unwind()    # cancellation or any exception

        totals["cost_nd"] += cost
        totals["last_input_tokens"] = sum(int(usage.get(k) or 0) for k in (
            "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        log_event(log, "turn.request", turn_id=turn_id, step=step, model=final.model,
                  input_tokens=usage.get("input_tokens") or 0,
                  cache_write_tokens=usage.get("cache_creation_input_tokens") or 0,
                  cache_read_tokens=usage.get("cache_read_input_tokens") or 0,
                  output_tokens=usage.get("output_tokens") or 0, cost_nd=cost)
        assistant = {"role": "assistant", "content": [b.to_dict(mode="json", exclude_none=True) for b in final.content]}
        await io.send({"type": "assistant", "message": assistant, "mac": mac(deps.mac_key, assistant),
                       "model": final.model, "usage": usage})
        messages = messages + [assistant]

        if final.stop_reason == "tool_use":
            if step == MAX_STEPS:
                break
            calls = [{"id": b["id"], "name": b["name"], "input": b.get("input") or {}}
                     for b in assistant["content"] if b.get("type") == "tool_use"]
            await io.send({"type": "tool_calls", "seq": step, "calls": calls})
            wait = deps.approval_timeout if any(c["name"] == T.MERCHANT for c in calls) else deps.reply_timeout
            try:
                res = await io.receive(("tool_results", "cancel"), wait)
            except asyncio.TimeoutError:
                return await done("dropped")
            if res["type"] == "cancel":
                return await done("cancelled")
            if (P.check_frame(P.TOOL_RESULTS, res) or res["seq"] != step
                    # each result carries its own call's name too, so none is relabelled into the store-rows checks
                    or [(r["id"], r["name"]) for r in res["results"]] != [(c["id"], c["name"]) for c in calls]):
                return await done("bad_request")
            blocks, parts = [], []
            for r in res["results"]:
                is_error = r["error"] is not None
                content = {"error": r["error"]} if is_error else r["content"]
                # Approved store rows (plan B ruling 3). An empty result had no card, so it is an ordinary result
                # (preflight ruling P1).
                store = (r["name"] == T.MERCHANT and not is_error and isinstance(content, dict)
                         and bool(content.get("rows") or content.get("payees")))
                if store:       # a person's name the model finds in an unticked row goes as [name] (public issue #53)
                    content = await asyncio.to_thread(mask_store_names, content, ticks_of(r), deps.detectors, issued)
                blocks.append({"type": "tool_result", "tool_use_id": r["id"], "content": canonical(content).decode(),
                               "is_error": is_error})
                kind = "merchant_rows" if store else "tool_result"
                parts.append(Part(kind, {"tool_use_id": r["id"], "name": r["name"], "is_error": is_error,
                                         "content": content}))
            new_msgs = [{"role": "user", "content": blocks}]
            continue
        if final.stop_reason == "max_tokens":
            return await done("max_tokens")
        if final.stop_reason == "refusal":
            return await done("refusal")
        return await done("end_turn")
    return await done("steps")
