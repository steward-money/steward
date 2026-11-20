"""One turn on the NAS (spec 3.2, 4.3-4.6, 5.1, 8.2). start_turn checks the question, the member and the switch, picks
or starts the conversation, and runs the exact pass. run_turn opens the WebSocket to the gateway and answers its
frames: it records each request's parts in the book before the gateway may send them, stores messages as they're sent
and received, runs tool calls (re-checking membership and the switch each time), and ends with a done event. Nothing
is retried by sending more (spec 10)."""
from __future__ import annotations

import json
import logging
import secrets
import time
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from steward import gateway_name
from steward.advisor.about import AboutGone, AboutIn, AboutRefused, resolve
from steward.advisor.aliases import Aliases, display
from steward.advisor.gateway import GatewayUnavailable
from steward.advisor.identifiers import load_identifiers
from steward.advisor.outgoing import Approval, record_parts, record_payload
from steward.advisor.redact import Refused, exact_redact, issued_now, turn_hash_sets
from steward.advisor.state import OFF_LINE, budget_lines, chat_on, money
from steward.advisor.tools import run_call, run_calls
from steward.books.people import Person, attach_people
from steward.books.registry import BookInfo
from steward.books.sample_guard import SAMPLE_REFUSED, SampleRefused
from steward.db.directory_models import Book, Membership
from steward.db.models import AdvisorOutgoing, ChatConversation, ChatMessage, ChatTurn
from steward import clock as steward_clock
from steward.events import log_event
from steward.inventory.present import clock, day_label
from steward.services.plan_cache import PlanCache
from steward_boundary import PROTOCOL
from steward_boundary import tools as T
from steward_boundary.aliases import ALIAS_TOKEN_RE
from steward_boundary.canonical import sha256_hex
from steward_boundary.protocol import CHECK_WORDS, LINES, STOPS
from steward_boundary.spans import Edit, apply, shift
from steward_boundary.text import is_question_safe

log = logging.getLogger("steward.advisor.turn")
MAX_QUESTION = 2000
LONG_TOKENS, TOO_LONG_TOKENS = 100_000, 200_000
REPLY_TIMEOUT = 180.0
CANCEL_WAIT = 2.0           # how long a turn the NAS ends waits for the gateway's done, which carries the real cost
LONG_LINE = "This conversation is long. A new one will answer faster and cost less."
TOO_LONG = "This conversation is long enough that it needs a new one. Start a new conversation."
ENDED_LINE = "That conversation stopped partway, so this starts a new one."
NOT_MEMBER = "You're no longer a member of this book, so this answer stopped."
NO_CONVERSATION = "No such conversation."
TOO_LONG_QUESTION = "Questions can be up to 2,000 characters."
EMPTY = "Type a question first."
STATUS = {t.name: t.status for t in T.TOOLS}
POLL_S = 0.5
APPROVED_LINE = "{name} approved {rows} for this conversation."     # the rows go with its later questions (PRIV-9)
DECLINED_LINE = "Store names weren't sent. Claude answered from totals."
VIEWER_LINE = "Store and payee names need a manager's OK. Claude will answer from totals."
TIMEOUT_LINE = "Stopped while waiting for your OK."      # spec 4.4 and 10, word for word (preflight ruling P2)
STOPPED_LINE = "You stopped this answer."
NOT_WAITING = "That question isn't waiting for an OK."
NOT_RUNNING = "That answer has already finished."


def rows_word(n: int) -> str:
    return "1 store row" if n == 1 else f"{n} store rows"


def member_role(directory: Engine, book_id: int, user_id: int) -> str | None:
    with Session(directory) as d:
        return d.scalar(select(Membership.role).join(Book, Book.id == Membership.book_id).where(
            Membership.book_id == book_id, Membership.user_id == user_id, Book.archived_at.is_(None)))


def done_words(status: str) -> str:
    """"Looking at accounts" → "Looked at accounts" (plan B ruling 15)."""
    for now, then in (("Looking at", "Looked at"), ("Trying", "Tried"), ("Asking for", "Asked for")):
        if status.startswith(now):
            return then + status[len(now):]
    return status


DONE = {t.name: done_words(t.status) for t in T.TOOLS}


def meta_line(ms: int, cost_cents: int, requests: int) -> str:
    """"11 seconds · $0.09" (plan B ruling 14): seconds alone when nothing went to Anthropic."""
    s = max(1, round(ms / 1000))
    secs = "1 second" if s == 1 else f"{s} seconds"
    if requests == 0:
        return secs
    return f"{secs} · {'less than $0.01' if cost_cents == 0 else money(cost_cents)}"


class ChatRefused(Exception):
    def __init__(self, message: str, status: int = 409, stop: str | None = None):
        super().__init__(message)
        self.message, self.status, self.stop = message, status, stop


@dataclass(frozen=True)
class AskEnv:
    """guided setup spec 8.3: the turn's entry. api/chat.py _env and steward ask both build one before any gateway
    call, and a Sample book refuses here."""
    bind: Engine                                # the book's engine, IMMEDIATE (a POST's book_db bind, or the CLI's)
    directory: Engine
    book: BookInfo
    user_id: int
    people: Callable[[], tuple[Person, ...]]
    plan_cache: PlanCache
    gateway: object | None                      # GatewayClient, or None when chat isn't set up
    now: Callable[[], datetime]
    today: Callable[[], date]
    household_name: str = "Ours"
    control: object | None = None               # steward.advisor.control.Control; None from the command line
    approval_wait: float = 600.0                # spec 4.4: 10 minutes
    reply_timeout: float = REPLY_TIMEOUT
    cancel_wait: float = CANCEL_WAIT

    def __post_init__(self):
        if self.book.sample:
            raise SampleRefused(SAMPLE_REFUSED)


@dataclass
class Prepared:
    conversation_id: int
    new: bool
    note: str | None
    text: str
    chips: list[dict]
    history: list[dict]
    context: dict
    salt: str
    hashes: list[str]
    approvable: list[str]
    turn_id: str
    issued: list[str]
    asked: str = ""
    sent: str | None = None                       # the question as the gateway redacted it
    steps: list[str] = field(default_factory=list)
    started: float = 0.0
    # call id → the manager who approved its store rows, exactly the content sent with the OK, and their ticks
    approved: dict[str, Approval] = field(default_factory=dict)
    gateway_done: dict | None = None              # the gateway's done after a cancel, when the browser went away


def is_member(directory: Engine, book_id: int, user_id: int) -> bool:
    with Session(directory) as d:
        return d.scalar(select(Membership.user_id).join(Book, Book.id == Membership.book_id).where(
            Membership.book_id == book_id, Membership.user_id == user_id, Book.archived_at.is_(None))) is not None


def _session(env: AskEnv) -> Session:
    s = Session(env.bind)
    attach_people(s, env.people)
    return s


def _title(text: str) -> str:
    return " ".join(text.split()[:6])[:80] or "Question"


def start_turn(env: AskEnv, question: str, conversation_id: int | None = None, about: AboutIn | None = None) -> Prepared:
    """What a question was asked about is resolved here from typed columns (Ask about this), never trusted from an earlier /about: its wire object goes in the context and on the turn, and its alias is issued."""
    q = (question or "").strip()
    if not q:
        raise ChatRefused(EMPTY, 422)
    if len(q) > MAX_QUESTION:
        raise ChatRefused(TOO_LONG_QUESTION, 422)
    if not is_member(env.directory, env.book.id, env.user_id):
        raise ChatRefused("No such book.", 404)
    people = env.people()
    with _session(env) as db:
        if not chat_on(db):
            raise ChatRefused(OFF_LINE, 409)
        conv, note = None, None
        if conversation_id is not None:
            conv = db.get(ChatConversation, conversation_id)
            if conv is None or conv.user_id != env.user_id or conv.deleted_at is not None:
                raise ChatRefused(NO_CONVERSATION, 404)
            if conv.last_input_tokens >= TOO_LONG_TOKENS:
                raise ChatRefused(TOO_LONG, 409)
            if conv.ended_at is not None:
                conv, note = None, ENDED_LINE
        aliases = Aliases(db, env.book, people)
        wire = None
        if about is not None:
            try:
                wire = resolve(db, env.book, aliases, about, env.today())
            except AboutGone as e:
                raise ChatRefused(e.message, 404) from None
            except AboutRefused as e:
                raise ChatRefused(e.message, 422) from None
        idents = load_identifiers(db, env.book, people, env.directory, aliases, env.today())
        try:
            text, chips = exact_redact(q, idents)
        except Refused:
            raise ChatRefused(LINES["stopped_check"], 422, "stopped_check") from None
        asker = aliases.user(env.user_id)
        now = env.now()
        new = conv is None
        if new:
            conv = ChatConversation(user_id=env.user_id, title=_title(text), created_at=now, updated_at=now,
                                    last_input_tokens=0)
            db.add(conv)
            db.flush()
        history = [{"message": json.loads(m.content), "mac": m.mac} for m in db.scalars(
            select(ChatMessage).where(ChatMessage.conversation_id == conv.id).order_by(ChatMessage.id))]
        cid = conv.id
        turn_id = "t_" + secrets.token_hex(8)
        db.add(ChatTurn(conversation_id=cid, turn_id=turn_id, user_id=env.user_id, asked=q, started_at=now,
                        about=json.dumps(wire) if wire is not None else None))
        db.commit()
    salt = secrets.token_hex(16)
    rest, approvable = turn_hash_sets(idents, salt)
    context = {"today": env.today().isoformat(), "book_kind": env.book.kind, "asker": asker}
    issued = issued_now(chips)
    if wire is not None:
        context["about"] = wire
        if "account" in wire:
            issued = sorted(set(issued) | {wire["account"]})
    return Prepared(cid, new, note, text, chips, history, context, salt, rest, approvable, turn_id, issued, asked=q)


def _record(env: AskEnv, prep: Prepared, frame: dict) -> bool:
    try:
        with _session(env) as db:
            now = env.now()
            model = frame.get("model")          # the model the gateway sends this request to (PRIV-15)
            if frame.get("seq") == 1 and prep.history:
                record_payload(db, user_id=env.user_id, conversation_id=prep.conversation_id, turn_id=prep.turn_id,
                               seq=1, channel="chat", part="history_ref",
                               payload={"conversation_id": prep.conversation_id, "messages": len(prep.history)}, now=now,
                               model=model)
            record_parts(db, user_id=env.user_id, conversation_id=prep.conversation_id, turn_id=prep.turn_id,
                         seq=frame["seq"], channel="chat", model=model, parts=frame["parts"], now=now,
                         approved=prep.approved)
            db.commit()
        return True
    except Exception as e:  # noqa: BLE001 - the gateway sends nothing it couldn't record (spec 4.5, check 5)
        log.error("The log of what was sent couldn't be written: %s", type(e).__name__)
        with suppress(Exception), _session(env) as db:
            log_event(db, "warn", "chat", LINES["record_failed"])
            db.commit()
        return False


def _store(env: AskEnv, prep: Prepared, items: list[tuple]) -> None:
    with _session(env) as db:
        now = env.now()
        for message, mac, model, usage in items:
            db.add(ChatMessage(conversation_id=prep.conversation_id, turn_id=prep.turn_id, role=message["role"],
                               content=json.dumps(message, ensure_ascii=False), mac=mac, model=model,
                               usage=json.dumps(usage) if usage else None, created_at=now))
        db.commit()


def _set_title(env: AskEnv, conversation_id: int, text: str) -> None:
    with _session(env) as db:
        conv = db.get(ChatConversation, conversation_id)
        if conv is not None:
            conv.title = _title(text)
            db.commit()


def update_turn(env: AskEnv, turn_id: str, **fields) -> None:
    with _session(env) as db:
        t = db.scalar(select(ChatTurn).where(ChatTurn.turn_id == turn_id))
        for k, v in fields.items():
            setattr(t, k, v)
        db.commit()


def labels_for(env: AskEnv) -> dict[str, str]:
    """Every alias in the book with its label, for the page to put names back as text streams (plan B ruling 13)."""
    with _session(env) as db:
        out = Aliases(db, env.book, env.people(), save=False).labels()
        db.rollback()
    return out


def _still_allowed(env: AskEnv) -> str | None:
    if not is_member(env.directory, env.book.id, env.user_id):
        return "not_a_member"
    with _session(env) as db:
        return None if chat_on(db) else "chat_off"


def _dangling(db: Session, conversation_id: int) -> bool:
    last = db.scalar(select(ChatMessage).where(ChatMessage.conversation_id == conversation_id)
                     .order_by(ChatMessage.id.desc()).limit(1))
    if last is None or last.role != "assistant":
        return last is not None             # a question with no reply stored after it cannot be continued
    return any(isinstance(b, dict) and b.get("type") == "tool_use" for b in json.loads(last.content).get("content", []))


def _cap_line(env: AskEnv) -> str:
    try:
        budget = (env.gateway.health() or {}).get("budget") if env.gateway else None
    except Exception:  # noqa: BLE001 - the line is still shown without the amounts
        budget = None
    if budget:
        return budget_lines({**budget, "can_ask": False}, env.household_name, env.today())["cap_line"]
    return "This month's chat budget is used up."


def _send(ws, frame: dict) -> bool:
    try:
        ws.send(json.dumps(frame, ensure_ascii=False))
        return True
    except Exception:  # noqa: BLE001 - a send that fails means the gateway went away
        return False


def _done_ok(frame: dict) -> bool:
    """A done frame _finish can use: a known stop word, numbers that are numbers, lines that are text."""
    def num(v):
        return v is None or (isinstance(v, int) and not isinstance(v, bool) and v >= 0)
    return (frame.get("stop", "dropped") in STOPS
            and all(num(frame.get(k)) for k in ("requests", "cost_nd", "last_input_tokens"))
            and all(frame.get(k) is None or isinstance(frame.get(k), str) for k in ("line", "check", "path")))


def _retitle(env: AskEnv, prep: Prepared) -> None:
    """Spec 6.3: Ollama's title for a new conversation, after the first answer. Plan A's first six words stay when it
    has none or it isn't safe to show."""
    try:
        title = (env.gateway.title(prep.sent) or "").strip()
    except Exception:  # noqa: BLE001 - the gateway busy or away: the first six words stay
        return
    if not title or len(title) > 80 or len(title.split()) > 6:
        return
    if not is_question_safe(title, issued=frozenset(ALIAS_TOKEN_RE.findall(title))):
        return
    with _session(env) as db:
        conv = db.get(ChatConversation, prep.conversation_id)
        if conv is None:                    # deleted while the title was being made (final review, finding 4)
            return
        conv.title = title
        db.commit()


def _finish(env: AskEnv, prep: Prepared, final: dict, answer: str) -> Iterator[dict]:
    stop = final.get("stop", "dropped")
    line = final.get("line") or LINES.get(stop)
    if line:
        line = gateway_name.fill(line)
    if stop == "cap":
        line = _cap_line(env)
    with _session(env) as db:
        conv = db.get(ChatConversation, prep.conversation_id)
        now = env.now()
        conv.updated_at = now
        if final.get("last_input_tokens"):
            conv.last_input_tokens = final["last_input_tokens"]
        if _dangling(db, conv.id):
            conv.ended_at = now
        if stop == "stopped_check":
            log_event(db, "warn", "chat", f"Steward stopped an answer before anything was sent: the "
                                          f"{CHECK_WORDS.get(final.get('check'), 'privacy')} check fired at "
                                          f"{final.get('path')}.")
        elif stop == "dropped":
            log_event(db, "warn", "chat", gateway_name.fill(LINES["dropped"]))
        parts, size = db.execute(select(func.count(), func.coalesce(func.sum(AdvisorOutgoing.bytes), 0))
                                 .where(AdvisorOutgoing.turn_id == prep.turn_id)).one()
        labels = Aliases(db, env.book, env.people(), save=False).labels()
        ms = int((time.monotonic() - prep.started) * 1000) if prep.started else 0
        cost = (final.get("cost_nd") or 0) // 10_000_000
        t = db.scalar(select(ChatTurn).where(ChatTurn.turn_id == prep.turn_id))
        t.stop, t.line, t.cost_cents, t.ms, t.ended_at = stop, line, cost, ms, now
        t.steps = json.dumps(prep.steps)
        db.commit()
    yield {"type": "done", "stop": stop, "line": line, "answer": display(answer, labels),
           "long_line": LONG_LINE if (final.get("last_input_tokens") or 0) >= LONG_TOKENS else None,
           "conversation_id": prep.conversation_id, "turn_id": prep.turn_id, "requests": final.get("requests", 0),
           "cost_cents": cost, "parts": int(parts), "bytes": int(size),
           "meta": meta_line(ms, cost, final.get("requests", 0))}
    if prep.new and stop == "end_turn" and prep.sent and env.gateway is not None:
        _retitle(env, prep)


def run_turn(env: AskEnv, prep: Prepared) -> Iterator[dict]:
    prep.started = time.monotonic()
    yield {"type": "conversation", "id": prep.conversation_id, "note": prep.note, "turn_id": prep.turn_id}
    yield {"type": "labels", "labels": labels_for(env)}
    if env.gateway is None:
        yield from _finish(env, prep, {"stop": "not_set_up"}, "")
        return
    try:
        ws = env.gateway.connect()
    except GatewayUnavailable:
        yield from _finish(env, prep, {"stop": "away", "line": LINES["away"]}, "")
        return
    start = {"type": "start", "protocol": PROTOCOL, "turn_id": prep.turn_id, "book_id": env.book.id,
             "user_id": env.user_id, "question": prep.text, "context": prep.context, "history": prep.history,
             "fingerprints": {"salt": prep.salt, "hashes": prep.hashes, "approvable": prep.approvable},
             "issued": prep.issued}
    answer: list[str] = []
    if env.control is not None:
        env.control.begin(prep.turn_id, env.user_id, env.book.id)
    try:
        final = yield from _converse(env, prep, ws, start, answer)
    except GeneratorExit:                       # the browser went away: close the turn so the next question starts fresh
        _abandon(env, prep)
        raise
    finally:
        if env.control is not None:
            env.control.end(prep.turn_id)
    yield from _finish(env, prep, final, "".join(answer))


def _abandon(env: AskEnv, prep: Prepared) -> None:
    try:
        with _session(env) as db:
            conv = db.get(ChatConversation, prep.conversation_id)
            conv.updated_at = env.now()
            conv.ended_at = conv.updated_at     # a turn that never finished leaves history that can't be continued
            t = db.scalar(select(ChatTurn).where(ChatTurn.turn_id == prep.turn_id))
            if t is not None:
                t.stop, t.line, t.ended_at = "dropped", gateway_name.fill(LINES["dropped"]), conv.updated_at
                t.steps = json.dumps(prep.steps)
                t.cost_cents = ((prep.gateway_done or {}).get("cost_nd") or 0) // 10_000_000
                if prep.started:
                    t.ms = int((time.monotonic() - prep.started) * 1000)
            log_event(db, "warn", "chat", gateway_name.fill(LINES["dropped"]))
            db.commit()
    except Exception:  # noqa: BLE001 - nothing more can be shown to a browser that left
        pass


def _await_done(ws, env: AskEnv) -> dict | None:
    """The gateway's done frame after a cancel, waited for up to env.cancel_wait; None if it doesn't come."""
    waited = 0.0
    while waited < env.cancel_wait:
        try:
            frame = json.loads(ws.recv(timeout=POLL_S))
        except TimeoutError:
            waited += POLL_S
            continue
        except Exception:  # noqa: BLE001 - closed or garbled: no totals
            return None
        if isinstance(frame, dict) and frame.get("type") == "done":
            return frame if _done_ok(frame) else None
    return None


def _cancel(ws, env: AskEnv, reason: str) -> dict | None:
    """Tells the gateway to cancel, then waits briefly for its done: the gateway settles a request in flight at its
    full reservation, and those totals are the turn's real cost (final review, finding 1)."""
    return _await_done(ws, env) if _send(ws, {"type": "cancel", "reason": reason}) else None


def _with_totals(final: dict, done: dict | None) -> dict:
    """The NAS's own ending, with the requests, cost and size the gateway reported for the turn."""
    for k in ("requests", "cost_nd", "last_input_tokens"):
        if done and done.get(k) is not None:
            final[k] = done[k]
    return final


@contextmanager
def _cancelling(ws, env: AskEnv, prep: Prepared):
    """The socket for one turn. A browser that goes away mid-turn closes the turn's generator (GeneratorExit); the
    gateway is told to cancel before the socket closes (Task 6.5), and its totals are kept for the turn's row."""
    with ws:
        try:
            yield ws
        except GeneratorExit:
            prep.gateway_done = _cancel(ws, env, "stopped")
            raise


def _converse(env: AskEnv, prep: Prepared, ws, start: dict, answer: list[str]) -> Iterator[dict]:
    """Runs the socket conversation and returns the final frame; the caller finishes the turn."""
    final: dict | None = None
    with _cancelling(ws, env, prep):            # websockets 17: a sync connection is used as a context manager
        if not _send(ws, start):
            final = {"stop": "dropped"}
        while final is None:
            frame = _recv(ws, env, prep)
            if frame in ("stopped", "gone"):    # Stop, or the browser went away: cancel at the gateway now
                final = {"stop": "stopped", "line": STOPPED_LINE} if frame == "stopped" else {"stop": "dropped"}
                _with_totals(final, _cancel(ws, env, "stopped"))
                break
            if frame == "dropped":              # quiet for too long, or the socket closed: cancel if it's still there
                final = _with_totals({"stop": "dropped"}, _cancel(ws, env, "stopped"))
                break
            try:
                kind = frame.get("type")
                if kind == "redacted":
                    try:
                        edits = [Edit(**e) for e in frame.get("edits", [])]
                        chips = sorted(shift(prep.chips, edits) + apply(prep.text, edits)[1], key=lambda c: c["start"])
                    except (ValueError, TypeError):     # an edit that reaches into an alias chip is never guessed at
                        final = {"stop": "stopped_check", "check": "text", "path": "question"}
                        break
                    prep.sent = frame["text"]
                    update_turn(env, prep.turn_id, sent=frame["text"], chips=json.dumps(chips, ensure_ascii=False))
                    if prep.new:
                        _set_title(env, prep.conversation_id, frame["text"])
                    yield {"type": "redacted", "text": frame["text"], "chips": chips}
                elif kind == "record":
                    ok = _record(env, prep, frame)
                    sent_ok = _send(ws, {"type": "recorded" if ok else "record_failed", "seq": frame.get("seq")})
                    if not ok:                      # nothing that couldn't be recorded is sent, so the turn ends here
                        final = {"stop": "record_failed", "line": LINES["record_failed"]}
                        break
                    if not sent_ok:
                        final = {"stop": "dropped"}
                elif kind == "sent":
                    _store(env, prep, [(m["message"], m["mac"], None, None) for m in frame["messages"]])
                elif kind == "assistant":
                    _store(env, prep, [(frame["message"], frame["mac"], frame.get("model"), frame.get("usage"))])
                elif kind == "status":
                    tool = frame.get("tool")
                    if tool in STATUS:
                        prep.steps.append(tool)
                    yield {"type": "status", "tool": tool, "text": STATUS.get(tool, "Looking something up"),
                           "done": DONE.get(tool, "Looked something up")}
                elif kind == "text":
                    answer.append(frame["delta"])
                    yield {"type": "text", "delta": frame["delta"]}
                elif kind == "tool_calls":
                    results, stop = [], None
                    for call in frame["calls"]:     # membership and the switch are re-checked before every call
                        reason = _still_allowed(env)
                        if reason:
                            stop = {"cancel": reason, "stop": "cancelled",
                                    "line": NOT_MEMBER if reason == "not_a_member" else OFF_LINE}
                            break
                        if call["name"] == T.MERCHANT:
                            kind_, value = yield from _merchant(env, prep, call)
                            if kind_ == "stop":
                                stop = value
                                break
                            results.append(value)
                        else:
                            results += run_calls([call], **_tool_kw(env))
                    if stop:
                        final = _with_totals(stop, _cancel(ws, env, stop.pop("cancel")))
                        break
                    if not _send(ws, {"type": "tool_results", "seq": frame["seq"], "results": results}):
                        final = {"stop": "dropped"}
                    else:
                        yield {"type": "labels", "labels": labels_for(env)}
                elif kind == "done":
                    final = frame if _done_ok(frame) else {"stop": "dropped"}
            except Exception as e:  # noqa: BLE001 - a frame that can't be handled (the alias step, storing a message,
                # a malformed frame) ends the turn as dropped, with its done and book events, never a cut stream
                log.error("A turn frame couldn't be handled: %s", type(e).__name__)
                final = {"stop": "dropped"}
                break
    return final


def _recv(ws, env: AskEnv, prep: Prepared):
    """The next frame; "stopped" once the person pressed Stop; "dropped" when the gateway went quiet or away."""
    waited = 0.0
    while waited < env.reply_timeout:
        why = env.control.why_stopped(prep.turn_id) if env.control is not None else None
        if why:
            return why
        try:
            return json.loads(ws.recv(timeout=POLL_S))
        except TimeoutError:
            waited += POLL_S
        except Exception:  # noqa: BLE001 - closed or garbled: the gateway stopped answering
            return "dropped"
    return "dropped"


def _tool_kw(env: AskEnv) -> dict:
    return {"bind": env.bind, "book": env.book, "user_id": env.user_id, "people": env.people(), "today": env.today(),
            "plan_cache": env.plan_cache, "directory": env.directory}


def _declined(call: dict, message: str) -> dict:
    return {"id": call["id"], "name": call["name"], "content": None, "error": message}


def _managers(env: AskEnv) -> list[str]:
    with Session(env.directory) as d:
        ids = set(d.scalars(select(Membership.user_id).where(Membership.book_id == env.book.id,
                                                             Membership.role == "manager")))
    return [p.display_name for p in env.people() if p.id in ids]


def _card(env: AskEnv, call: dict, content: dict, home: dict[str, str]) -> dict:
    labels = labels_for(env)
    today = env.today()
    rows = content["rows"]
    days = sorted(r["date"] for r in rows)
    names = Counter(r["merchant"] for r in rows) + Counter(p["name"] for p in content["payees"])
    accounts = sorted({r["account"] for r in rows}, key=lambda a: (len(a), a))
    deadline = (env.now() + timedelta(seconds=env.approval_wait)).astimezone(steward_clock.household_tz())
    label = lambda d: day_label(date.fromisoformat(d), today)  # noqa: E731
    return {"call_id": call["id"], "reason": display(str(call["input"].get("reason") or "").strip()[:300], labels),
            "count": len(rows) + len(content["payees"]),
            "first": label(days[0]) if days else None, "last": label(days[-1]) if days else None,
            "names": [{"name": n, "count": c} for n, c in names.most_common()],
            "rows": [{"date": label(r["date"]), "store": r["merchant"], "amount_cents": r["amount_cents"],
                      "category": r["category"], "account": r["account"], "full": home.get(f"r{i}")}
                     for i, r in enumerate(rows)],
            "payees": [{**p, "full": home.get(f"p{i}")} for i, p in enumerate(content["payees"])],
            "accounts": [{"alias": a, "label": labels.get(a, a)} for a in accounts],
            "until": clock(deadline), "more": content["more"]}


def card_digest(turn_id: str, call_id: str, content: dict, home: dict[str, str]) -> str:
    """The hash an approval must carry (Task 6.5): this turn, this call, and exactly the rows and names on the card,
    which are what the NAS would send, and the names a tick could send instead (public issue #1). The gateway may still
    replace a person's name its model finds in an unticked row with [name] (public issue #53)."""
    return sha256_hex({"turn_id": turn_id, "call_id": call_id, "content": content, "home": home})


def with_ticked(content: dict, home: dict[str, str], ticked: frozenset[str]) -> dict:
    """The rows as sent: each ticked row or payee that has a name at home carries it; every other stays as the card
    showed it. A tick for a key the card didn't offer changes nothing."""
    out = json.loads(json.dumps(content))
    for key in sorted(ticked & home.keys()):
        if key[0] == "r":
            out["rows"][int(key[1:])]["merchant"] = home[key]
        else:
            out["payees"][int(key[1:])]["name"] = home[key]
    return out


def _viewer(env: AskEnv, prep: Prepared, call: dict) -> Iterator[dict]:
    update_turn(env, prep.turn_id, approval="viewer")
    yield {"type": "approval", "state": "viewer", "managers": _managers(env),
           "where": "Look into" if env.book.kind == "parent" else "Transactions"}
    return "result", _declined(call, T.NEEDS_MANAGER)


def _merchant(env: AskEnv, prep: Prepared, call: dict) -> Iterator[dict]:
    """Runs merchant_detail and, when it found rows, pauses for the asker's OK (spec 4.4). Returns ("result", r) or
    ("stop", final) through `yield from`. An OK counts only from the asker, in this book, for this turn and call, with
    the hash of this card (Control), and only if the asker is still a manager of the book when it arrives."""
    result = run_call(call, **_tool_kw(env))
    home = result.pop("home", None) or {}               # the names a tick could send stay here; never sent as such
    if result["error"] or not (result["content"]["rows"] or result["content"]["payees"]):
        return "result", result                                   # nothing to approve (plan B ruling 5)
    if env.control is None or member_role(env.directory, env.book.id, env.user_id) != "manager":
        return (yield from _viewer(env, prep, call))
    card = _card(env, call, result["content"], home)
    digest = card_digest(prep.turn_id, call["id"], result["content"], home)
    card["hash"] = digest
    yield {"type": "approval", "state": "ask", "card": card}
    answer = env.control.wait_for_ok(prep.turn_id, call["id"], env.approval_wait, digest=digest)
    if answer == "send":
        reason = _still_allowed(env)        # the wait can be long: the switch or membership may have changed since
        if reason:
            update_turn(env, prep.turn_id, approval="declined")
            return "stop", {"cancel": reason, "stop": "cancelled",
                            "line": NOT_MEMBER if reason == "not_a_member" else OFF_LINE}
        if member_role(env.directory, env.book.id, env.user_id) != "manager":     # a manager at the moment of the OK
            return (yield from _viewer(env, prep, call))
        # Only ticks the card offered count. They go beside the content, never in it, so the gateway leaves those rows
        # as they are and may mask a person's name its model finds in the rest (public issue #53).
        ticked = sorted(env.control.ticked(prep.turn_id) & home.keys())
        result = {**result, "content": with_ticked(result["content"], home, frozenset(ticked)), "ticked": ticked}
        prep.approved[call["id"]] = Approval(env.user_id, result["content"], frozenset(ticked))
        update_turn(env, prep.turn_id, approval="approved", store_rows=card["count"], approved_by=env.user_id)
        name = next((p.display_name for p in env.people() if p.id == env.user_id), "You")
        yield {"type": "approval", "state": "approved", "log_key": None,
               "line": APPROVED_LINE.format(name=name, rows=rows_word(card["count"]))}
        return "result", result
    if answer == "decline":
        update_turn(env, prep.turn_id, approval="declined")
        yield {"type": "approval", "state": "declined", "line": DECLINED_LINE}
        return "result", _declined(call, T.DECLINED)
    if answer == "timeout":
        update_turn(env, prep.turn_id, approval="timeout")
        return "stop", {"cancel": "approval_timeout", "stop": "approval_timeout", "line": TIMEOUT_LINE}
    if answer == "gone":                    # the browser went away while the card waited: nothing is sent
        return "stop", {"cancel": "stopped", "stop": "dropped"}
    return "stop", {"cancel": "stopped", "stop": "stopped", "line": STOPPED_LINE}
