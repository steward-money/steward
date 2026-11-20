"""One turn on the NAS: against the real gateway and the fake Anthropic server where the whole path matters, and
against a scripted gateway for drops, cancels and stops."""
import json
import threading
from dataclasses import replace
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from websockets.exceptions import ConnectionClosedError

from steward.advisor.control import Control
from steward.advisor.gateway import GatewayClient
from steward.advisor.history import conversation_detail
from steward.advisor.state import set_chat_on
from steward.advisor.turn import (
    ENDED_LINE, NOT_MEMBER, AskEnv, ChatRefused, run_turn, start_turn,
)
from steward.books.service import remove_member
from steward.db.models import AdvisorOutgoing, ChatConversation, ChatMessage, ChatTurn, EventLog
from steward.demo import seed, seed_people
from steward.demo_parent import seed_parent
from tests.advisor.rig import gateway_rig
from tests.advisor.stubs import StubGateway, part
from tests.gateway.fake_anthropic import Reply, text, tool_use

NOW = datetime(2026, 10, 20, 17, tzinfo=timezone.utc)
TODAY = date(2026, 10, 20)


def env_for(books, book, user_id, gateway):
    return AskEnv(bind=books.engine_for(book).execution_options(immediate=True), directory=books.directory, book=book,
                  user_id=user_id, people=lambda: books.people(book.id), plan_cache=books.plan_cache(book.id),
                  gateway=gateway, now=lambda: NOW, today=lambda: TODAY)


def turn_on(books, book):
    with Session(books.engine_for(book)) as db:
        set_chat_on(db, True, None, NOW)
        db.commit()


@pytest.fixture
def household(books, ours, engine):
    with Session(engine) as db:
        seed(db, years=2, seed=7)
    with Session(books.directory) as ddb:
        seed_people(ddb, ours.id)
    turn_on(books, ours)
    return next(p.id for p in books.people(ours.id) if p.username == "alex")


def ask(env, question, conversation_id=None):
    return list(run_turn(env, start_turn(env, question, conversation_id)))


def test_a_question_is_answered_and_every_sent_part_is_logged(tmp_path, books, ours, household):
    def script(body, n):
        if n == 1:
            return Reply([tool_use("toolu_01", "book_summary", {})], stop_reason="tool_use")
        return Reply([text("Person 1's net worth is about $2.4 million.")])
    with gateway_rig(tmp_path, script) as rig:
        events = ask(env_for(books, ours, household, rig.client), "What's our net worth?")
    kinds = [e["type"] for e in events]
    assert kinds[0] == "conversation" and kinds[1] == "labels" and kinds[2] == "redacted" and kinds[-1] == "done"
    assert {"type": "status", "tool": "book_summary", "text": "Looking at the book", "done": "Looked at the book"} in events
    done = events[-1]
    assert done["stop"] == "end_turn" and done["requests"] == 2 and done["parts"] == 3
    assert done["answer"] == "Alex's net worth is about $2.4 million."
    with Session(books.engine_for(ours)) as db:
        rows = list(db.scalars(select(AdvisorOutgoing).order_by(AdvisorOutgoing.id)))
        assert [(r.seq, r.part) for r in rows] == [(1, "question"), (1, "system"), (2, "tool_result")]
        assert json.loads(rows[2].payload)["content"]["asker"] == "Person 1"
        roles = [m.role for m in db.scalars(select(ChatMessage).order_by(ChatMessage.id))]
        assert roles == ["user", "system", "assistant", "user", "assistant"]


def test_the_question_is_redacted_before_it_leaves(tmp_path, books, margaret, mctx):
    with Session(books.engine_for(margaret)) as db:
        seed_parent(db, date(2026, 10, 18))
    turn_on(books, margaret)
    q = "Is Margaret's pension landing in Way2Save savings? Email lpark@example.com about Tom Becker."
    with gateway_rig(tmp_path, lambda b, n: Reply([text("I can't see that.")])) as rig:
        events = ask(env_for(books, margaret, mctx.user_id, rig.client), q)
        sent = rig.fake.requests[0]["messages"][-2]["content"][0]["text"]
    assert sent.startswith("Is Parent's pension landing in Account ")
    assert sent.endswith("? Email [email] about [name].")
    for word in ("Margaret", "Way2Save", "lpark", "Tom"):
        assert word not in sent
    chips = next(e for e in events if e["type"] == "redacted")["chips"]
    assert [c["label"] for c in chips][:1] == ["Parent"] and {c["layer"] for c in chips} == {"steward", "gliner"}


def test_a_conversation_continues_with_its_exact_history(tmp_path, books, ours, household):
    with gateway_rig(tmp_path, lambda b, n: Reply([text(f"Answer {n}.")])) as rig:
        env = env_for(books, ours, household, rig.client)
        first = ask(env, "How much is in savings?")
        cid = first[0]["id"]
        ask(env, "And last month?", cid)
        one, two = rig.fake.requests
    assert two["messages"][:len(one["messages"]) + 1] == one["messages"] + [
        {"role": "assistant", "content": [{"type": "text", "text": "Answer 1."}]}]
    with Session(books.engine_for(ours)) as db:
        parts = [r.part for r in db.scalars(select(AdvisorOutgoing).where(AdvisorOutgoing.seq == 1)
                                            .order_by(AdvisorOutgoing.id))]
    assert parts == ["question", "system", "history_ref", "question", "system"]


def test_a_dropped_gateway_ends_the_turn_plainly(books, ours, household):
    gw = StubGateway([{"type": "redacted", "text": "How much is in savings?", "edits": []},
                      {"type": "record", "seq": 1, "parts": [part("question", {"text": "How much is in savings?"})]},
                      ConnectionClosedError(None, None)])
    events = ask(env_for(books, ours, household, gw), "How much is in savings?")
    done = events[-1]
    assert (done["stop"], done["line"]) == ("dropped", "The gateway stopped answering, so this answer is incomplete.")
    assert gw.connects == 1 and gw.ws.closed
    with Session(books.engine_for(ours)) as db:
        msgs = [e.message for e in db.scalars(select(EventLog).where(EventLog.kind == "chat"))]
    assert msgs == ["The gateway stopped answering, so this answer is incomplete."]


def test_the_start_frame_carries_the_aliases_issued_this_turn(books, ours, household):
    gw = StubGateway([{"type": "done", "stop": "end_turn", "line": None, "check": None, "path": None, "requests": 1,
                       "cost_nd": 0, "last_input_tokens": 0}])
    ask(env_for(books, ours, household, gw), "Is Sam's account open?")
    start = gw.ws.sent[0]
    assert start["type"] == "start" and start["question"].startswith("Is Person 2's account open?")
    assert start["issued"] == ["Person 2"]
    assert start["context"]["asker"] == "Person 1"       # the asker is protected by the context, not `issued`


def test_the_start_frame_splits_payee_hashes_and_keeps_plan_as_rules(books, ours, household):
    """Plan B Task 1: the START frame carries payee and institution hashes apart from the rest, disjoint, with the
    same salt and canonical forms; the issued list and the stored history are as plan A made them."""
    from steward_boundary import protocol as P
    gw = StubGateway([{"type": "done", "stop": "end_turn", "line": None, "check": None, "path": None, "requests": 1,
                       "cost_nd": 0, "last_input_tokens": 0}])
    ask(env_for(books, ours, household, gw), "Is Sam's account open?")
    start = gw.ws.sent[0]
    assert P.check_frame(P.START, start) == []
    fp = start["fingerprints"]
    assert fp["hashes"] and fp["approvable"] and not set(fp["hashes"]) & set(fp["approvable"])
    assert fp["hashes"] == sorted(fp["hashes"]) and fp["approvable"] == sorted(fp["approvable"])
    assert start["issued"] == ["Person 2"] and start["history"] == []


def test_a_pass_that_cannot_prove_the_text_clean_refuses_the_question(books, ours, household, monkeypatch):
    from steward.advisor import turn as turn_mod
    from steward.advisor.redact import Refused

    def refuse(*a, **k):
        raise Refused
    monkeypatch.setattr(turn_mod, "exact_redact", refuse)
    with pytest.raises(ChatRefused) as e:
        start_turn(env_for(books, ours, household, StubGateway()), "Hi")
    assert e.value.message.startswith("Steward stopped this answer because something that looked like personal")


def test_a_removed_member_is_stopped_at_the_next_tool_call(books, ours, household, ddb):
    from tests.factories import make_person
    kai = make_person(ddb, "kai", [(ours.id, "viewer")])
    gw = StubGateway([{"type": "tool_calls", "seq": 1, "calls": [{"id": "toolu_01", "name": "accounts",
                                                                  "input": {"include_closed": False}}]}])
    env = env_for(books, ours, kai.id, gw)
    prep = start_turn(env, "How much is in checking?")
    remove_member(ddb, ours.id, kai.id, by=household)
    ddb.commit()
    events = list(run_turn(env, prep))
    assert (events[-1]["stop"], events[-1]["line"]) == ("cancelled", NOT_MEMBER)
    assert gw.ws.sent[-1] == {"type": "cancel", "reason": "not_a_member"}
    assert not any(f["type"] == "tool_results" for f in gw.ws.sent)


def test_chat_turned_off_mid_turn_stops_at_the_next_tool_call(books, ours, household):
    gw = StubGateway([{"type": "tool_calls", "seq": 1, "calls": [{"id": "toolu_01", "name": "debts", "input": {}}]}])
    env = env_for(books, ours, household, gw)
    prep = start_turn(env, "What do we owe?")
    with Session(books.engine_for(ours)) as db:
        set_chat_on(db, False, household, NOW)
        db.commit()
    events = list(run_turn(env, prep))
    assert events[-1]["line"] == "Chat is off for this book. A manager can turn it on in Settings → Chat."
    assert gw.ws.sent[-1] == {"type": "cancel", "reason": "chat_off"}


def test_membership_and_the_switch_are_checked_before_every_call_in_a_batch(books, ours, household, ddb, monkeypatch):
    from steward.advisor import turn as turn_mod
    from tests.factories import make_person
    kai = make_person(ddb, "kai", [(ours.id, "viewer")])
    calls = [{"id": "toolu_01", "name": "debts", "input": {}}, {"id": "toolu_02", "name": "debts", "input": {}}]
    gw = StubGateway([{"type": "tool_calls", "seq": 1, "calls": calls}])
    ran = []

    def run_calls(batch, **kw):
        ran.extend(c["id"] for c in batch)
        remove_member(ddb, ours.id, kai.id, by=household)      # the person is removed while the first call runs
        ddb.commit()
        return [{"id": c["id"], "name": c["name"], "content": {}, "error": None} for c in batch]
    monkeypatch.setattr(turn_mod, "run_calls", run_calls)
    env = env_for(books, ours, kai.id, gw)
    events = list(run_turn(env, start_turn(env, "What do we owe?")))
    assert ran == ["toolu_01"]
    assert (events[-1]["stop"], events[-1]["line"]) == ("cancelled", NOT_MEMBER)
    assert not any(f["type"] == "tool_results" for f in gw.ws.sent)


def test_closing_the_turn_closes_the_gateway_socket(books, ours, household):
    gw = StubGateway([{"type": "text", "delta": "Hello"}, {"type": "text", "delta": " there"}])
    env = env_for(books, ours, household, gw)
    turn = run_turn(env, start_turn(env, "Hi"))
    assert next(turn)["type"] == "conversation"
    assert next(turn)["type"] == "labels"
    assert next(turn) == {"type": "text", "delta": "Hello"}
    assert not gw.ws.closed
    turn.close()                                   # the browser went away mid-stream
    assert gw.ws.closed


def test_a_closed_turn_ends_the_conversation_so_the_next_question_starts_a_new_one(books, ours, household):
    gw = StubGateway([{"type": "text", "delta": "Hello"}, {"type": "text", "delta": " there"}])
    env = env_for(books, ours, household, gw)
    turn = run_turn(env, start_turn(env, "Hi"))
    cid = next(turn)["id"]
    next(turn)                                     # labels
    next(turn)
    turn.close()
    with Session(books.engine_for(ours)) as db:
        conv = db.get(ChatConversation, cid)
        assert conv.ended_at is not None and conv.updated_at is not None
        assert any(e.kind == "chat" for e in db.scalars(select(EventLog)))
    again = start_turn(env_for(books, ours, household, StubGateway()), "Still there?", cid)
    assert again.note == ENDED_LINE and again.conversation_id != cid


def _record_frame():
    return {"type": "record", "seq": 1, "parts": [part("question", {"text": "Hi"})]}


def test_parts_are_recorded_before_the_gateway_may_send(books, ours, household):
    gw = StubGateway([_record_frame(), {"type": "done", "stop": "end_turn", "line": None, "check": None,
                                        "path": None, "requests": 1, "cost_nd": 0, "last_input_tokens": 0}])
    ask(env_for(books, ours, household, gw), "Hi")
    assert [f["type"] for f in gw.ws.sent] == ["start", "recorded"]
    with Session(books.engine_for(ours)) as db:
        assert [r.part for r in db.scalars(select(AdvisorOutgoing))] == ["question"]


def test_a_part_that_cant_be_recorded_stops_the_turn_with_nothing_sent(books, ours, household):
    bad = part("question", {"text": "Hi"}) | {"sha256": "0" * 64}
    gw = StubGateway([{"type": "record", "seq": 1, "parts": [bad]},
                      {"type": "text", "delta": "must never be read"}])
    done = ask(env_for(books, ours, household, gw), "Hi")[-1]
    assert (done["stop"], done["line"]) == ("record_failed", "Steward couldn't record what would be sent, so it sent nothing.")
    assert gw.ws.sent[-1] == {"type": "record_failed", "seq": 1} and gw.ws.closed
    with Session(books.engine_for(ours)) as db:
        assert list(db.scalars(select(AdvisorOutgoing))) == []


def test_a_database_error_while_recording_stops_the_turn_the_same_way(books, ours, household, monkeypatch):
    from sqlalchemy.exc import OperationalError

    from steward.advisor import turn as turn_mod

    def boom(*a, **k):
        raise OperationalError("insert", {}, Exception("database is locked"))
    monkeypatch.setattr(turn_mod, "record_parts", boom)
    gw = StubGateway([_record_frame(), {"type": "text", "delta": "must never be read"}])
    done = ask(env_for(books, ours, household, gw), "Hi")[-1]
    assert (done["stop"], done["line"]) == ("record_failed", "Steward couldn't record what would be sent, so it sent nothing.")
    assert gw.ws.sent[-1] == {"type": "record_failed", "seq": 1}


def test_only_the_aliases_used_in_this_question_are_issued(books, ours, household):
    done = {"type": "done", "stop": "end_turn", "line": None, "check": None, "path": None, "requests": 1,
            "cost_nd": 0, "last_input_tokens": 0}
    ask(env_for(books, ours, household, StubGateway([done])), "Is Sam's account open?")     # Person 2 now exists
    gw = StubGateway([done])
    ask(env_for(books, ours, household, gw), "How much is in savings?")
    assert gw.ws.sent[0]["issued"] == []                 # not the whole alias table


def test_a_refused_question_carries_the_stop_word(books, ours, household, monkeypatch):
    from steward.advisor import turn as turn_mod
    from steward.advisor.redact import Refused

    def refuse(*a, **k):
        raise Refused
    monkeypatch.setattr(turn_mod, "exact_redact", refuse)
    with pytest.raises(ChatRefused) as e:
        start_turn(env_for(books, ours, household, StubGateway()), "Hi")
    assert (e.value.status, e.value.stop) == (422, "stopped_check")


def test_away_when_gateway_cant_be_reached(books, ours, household):
    env = env_for(books, ours, household, GatewayClient("http://127.0.0.1:9", "f" * 64, timeout=0.5))
    done = ask(env, "How much is in savings?")[-1]
    assert done["line"] == "Chat needs the gateway, which isn't answering. It may be asleep or turned off."


def test_a_stopped_check_logs_the_check_and_path_never_the_value(books, ours, household):
    gw = StubGateway([{"type": "done", "stop": "stopped_check", "line": None, "check": "fingerprint",
                       "path": "parts[0].text", "requests": 0, "cost_nd": 0, "last_input_tokens": 0}])
    done = ask(env_for(books, ours, household, gw), "Is Sam's account open?")[-1]
    assert done["line"].startswith("Steward stopped this answer because something that looked like personal")
    with Session(books.engine_for(ours)) as db:
        msg = db.scalar(select(EventLog.message).where(EventLog.kind == "chat"))
    assert msg == "Steward stopped an answer before anything was sent: the known-identifier check fired at parts[0].text."
    assert "Sam" not in msg


def test_a_turn_that_stops_after_a_tool_request_ends_the_conversation(books, ours, household):
    assistant = {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_01", "name": "debts", "input": {}}]}
    gw = StubGateway([{"type": "assistant", "message": assistant, "mac": "a" * 64, "model": "claude-opus-5-5",
                       "usage": {}},
                      {"type": "done", "stop": "steps", "line": None, "check": None, "path": None, "requests": 8,
                       "cost_nd": 0, "last_input_tokens": 0}])
    env = env_for(books, ours, household, gw)
    first = ask(env, "What do we owe?")
    cid = first[0]["id"]
    gw2 = StubGateway([{"type": "done", "stop": "end_turn", "line": None, "check": None, "path": None, "requests": 1,
                        "cost_nd": 0, "last_input_tokens": 0}])
    second = ask(env_for(books, ours, household, gw2), "And the mortgage?", cid)
    assert second[0]["id"] != cid and second[0]["note"] == ENDED_LINE


def test_refusals_before_anything_starts(books, ours, household, ddb):
    env = env_for(books, ours, household, StubGateway())
    with pytest.raises(ChatRefused) as e:
        start_turn(env, "x" * 2001)
    assert (e.value.status, e.value.message) == (422, "Questions can be up to 2,000 characters.")
    with pytest.raises(ChatRefused) as e:
        start_turn(env, "   ")
    assert e.value.status == 422
    with pytest.raises(ChatRefused) as e:
        start_turn(env, "Hi", 999)
    assert (e.value.status, e.value.message) == (404, "No such conversation.")
    with Session(books.engine_for(ours)) as db:
        db.add(ChatConversation(user_id=household, title="Long", created_at=NOW, updated_at=NOW,
                                last_input_tokens=200_000))
        db.commit()
        long_id = db.scalar(select(ChatConversation.id).where(ChatConversation.title == "Long"))
    with pytest.raises(ChatRefused) as e:
        start_turn(env, "Hi", long_id)
    assert e.value.message == "This conversation is long enough that it needs a new one. Start a new conversation."
    with Session(books.engine_for(ours)) as db:
        set_chat_on(db, False, household, NOW)
        db.commit()
    with pytest.raises(ChatRefused) as e:
        start_turn(env, "Hi")
    assert (e.value.status, e.value.message) == (409, "Chat is off for this book. A manager can turn it on in Settings → Chat.")


# --- A mid-turn exception ends the turn as dropped, never a cut stream (final review, finding 4) ----------------------
def _boom(*a, **k):
    from sqlalchemy.exc import OperationalError
    raise OperationalError("insert", {}, Exception("database is locked"))


@pytest.mark.parametrize("case", ["alias_step", "store", "frame_missing_a_field", "frame_not_an_object",
                                  "done_with_bad_numbers", "done_with_an_unknown_stop"])
def test_a_mid_turn_exception_ends_the_turn_as_dropped(books, ours, household, monkeypatch, case):
    from steward.advisor import turn as turn_mod
    frames = {
        "alias_step": [{"type": "tool_calls", "seq": 1, "calls": [{"id": "toolu_01", "name": "book_summary",
                                                                   "input": {}}]}],
        "store": [{"type": "assistant", "message": {"role": "assistant", "content": []}, "mac": "0" * 64}],
        "frame_missing_a_field": [{"type": "sent"}],
        "frame_not_an_object": [[1, 2, 3]],
        "done_with_bad_numbers": [{"type": "done", "stop": "end_turn", "cost_nd": "lots", "requests": 1}],
        "done_with_an_unknown_stop": [{"type": "done", "stop": {"x": 1}}],
    }[case]
    if case == "alias_step":
        monkeypatch.setattr(turn_mod, "run_calls", _boom)
    if case == "store":
        monkeypatch.setattr(turn_mod, "_store", _boom)
    gw = StubGateway(frames + [{"type": "text", "delta": "must never be read"}])
    events = ask(env_for(books, ours, household, gw), "How much is in savings?")
    done = events[-1]
    assert done["type"] == "done"
    assert (done["stop"], done["line"]) == ("dropped", "The gateway stopped answering, so this answer is incomplete.")
    assert not any(e.get("delta") == "must never be read" for e in events)
    assert gw.ws.closed
    with Session(books.engine_for(ours)) as db:
        msgs = [e.message for e in db.scalars(select(EventLog).where(EventLog.kind == "chat"))]
    assert msgs == ["The gateway stopped answering, so this answer is incomplete."]


# --- A turn the NAS ends records what it really cost (final review, finding 1) -----------------------------------------
def _held(hold):
    """A model whose stream stands still after its first text delta until `hold` is set. Tests set it in a finally,
    before leaving gateway_rig."""
    return lambda body, n: Reply([text("Dining is up about twelve percent on last year. " * 30)], hold=hold)


def _until_text(env, prep):
    turn = run_turn(env, prep)
    for e in turn:
        if e["type"] == "text":
            return turn
    raise AssertionError("no text streamed")


def _row(books, book, turn_id):
    with Session(books.engine_for(book)) as db:
        t = db.scalar(select(ChatTurn).where(ChatTurn.turn_id == turn_id))
        db.expunge(t)
        return t


def test_stop_mid_stream_records_the_cost_of_the_request_in_flight(tmp_path, books, ours, household):
    control = Control()
    hold = threading.Event()
    with gateway_rig(tmp_path, _held(hold)) as rig:
        try:
            env = replace(env_for(books, ours, household, rig.client), control=control)
            prep = start_turn(env, "Are we spending more on dining?")
            turn = _until_text(env, prep)        # the stream has begun, and stands still until hold is set
            control.stop(prep.turn_id, household, ours.id)
            done = list(turn)[-1]
        finally:
            hold.set()
        spent = rig.client.health()["budget"]["spent_cents"]
    assert (done["stop"], done["requests"]) == ("stopped", 1)
    assert done["cost_cents"] > 0 and done["cost_cents"] == spent      # the full reservation, as the gateway charged
    assert done["meta"].endswith(f"${done['cost_cents'] / 100:.2f}")
    row = _row(books, ours, prep.turn_id)
    assert (row.stop, row.cost_cents) == ("stopped", spent)
    with Session(books.engine_for(ours)) as db:          # the replayed turn shows the same cost
        conv = db.get(ChatConversation, prep.conversation_id)
        replayed = conversation_detail(db, conv, {}, {}, "household", TODAY)["turns"][-1]
    assert replayed["meta"].endswith(f"${spent / 100:.2f}")


def test_a_browser_that_leaves_after_a_send_records_the_reserved_amount(tmp_path, books, ours, household):
    hold = threading.Event()
    with gateway_rig(tmp_path, _held(hold)) as rig:
        try:
            env = replace(env_for(books, ours, household, rig.client), control=Control())
            prep = start_turn(env, "Are we spending more on dining?")
            _until_text(env, prep).close()           # the stream's reader went away between events
        finally:
            hold.set()
        spent = rig.client.health()["budget"]["spent_cents"]
    row = _row(books, ours, prep.turn_id)
    assert row.stop == "dropped" and spent > 0 and row.cost_cents == spent


def test_a_released_turn_after_a_send_records_the_reserved_amount(tmp_path, books, ours, household):
    control = Control()
    hold = threading.Event()
    with gateway_rig(tmp_path, _held(hold)) as rig:
        try:
            env = replace(env_for(books, ours, household, rig.client), control=control)
            prep = start_turn(env, "Are we spending more on dining?")
            turn = _until_text(env, prep)
            control.release(prep.turn_id)
            done = list(turn)[-1]
        finally:
            hold.set()
        spent = rig.client.health()["budget"]["spent_cents"]
    assert done["stop"] == "dropped" and spent > 0 and done["cost_cents"] == spent
    assert _row(books, ours, prep.turn_id).cost_cents == spent


@pytest.mark.parametrize("events, stop", [(2, "api_error"), (4, "dropped")], ids=["before-text", "mid-answer"])
def test_a_stream_anthropic_breaks_ends_the_turn_like_a_drop_once_text_has_shown(tmp_path, books, ours, household,
                                                                                events, stop):
    """Review finding 5: when Anthropic's stream breaks, the gateway's done says "dropped" if some of the answer had
    already shown, "api_error" if none had. Either way the NAS records the full charge the gateway made, ends the
    conversation (its question has no answer) and logs a dropped turn as a gateway drop is logged."""
    from tests.gateway.fake_anthropic import sse
    half = sse(Reply([text("Dining is up about twelve percent on last year.")]))
    with gateway_rig(tmp_path, lambda body, n: Reply([], sse=half[:events], cut=True)) as rig:
        env = env_for(books, ours, household, rig.client)
        prep = start_turn(env, "Are we spending more on dining?")
        events_ = list(run_turn(env, prep))
        spent = rig.client.health()["budget"]["spent_cents"]
    done = events_[-1]
    assert done["stop"] == stop and spent > 0 and done["cost_cents"] == spent
    assert ("text" in [e["type"] for e in events_]) == (stop == "dropped")
    with Session(books.engine_for(ours)) as db:
        assert db.get(ChatConversation, prep.conversation_id).ended_at is not None
        msgs = [e.message for e in db.scalars(select(EventLog).where(EventLog.kind == "chat"))]
    assert msgs == (["The gateway stopped answering, so this answer is incomplete."] if stop == "dropped" else [])


@pytest.mark.parametrize("line, shown", [
    ("Chat is turned off on Otter-PC.", "Chat is turned off on Otter-PC."),     # a gateway from before PR 4: as sent
    ("Chat is turned off on {gateway}.", "Chat is turned off on Zebra-Box."),   # one from after: this NAS's name
])
def test_a_gateway_line_shows_with_this_nas_s_name(books, ours, household, line, shown):
    from steward import gateway_name
    gateway_name.configure("Zebra-Box")
    gw = StubGateway([{"type": "done", "stop": "stopped_by_gateway", "line": line, "check": None, "path": None,
                       "requests": 0, "cost_nd": 0, "last_input_tokens": 0}])
    assert ask(env_for(books, ours, household, gw), "Hi")[-1]["line"] == shown
