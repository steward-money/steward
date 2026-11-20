"""Store names for one question (spec 4.4): a manager approves or declines the exact rows; a viewer and the command line
are answered from totals; nobody answering for 10 minutes ends the turn; Stop ends it at once."""
import json
import threading
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from steward.advisor.control import Control
from steward.advisor.state import set_chat_on
from steward.advisor.turn import (
    DECLINED_LINE, ENDED_LINE, STOPPED_LINE, TIMEOUT_LINE, AskEnv, run_turn, start_turn,
)
from steward.db.directory_models import Membership
from steward.db.models import Account, AccountType, AdvisorOutgoing, CategorySource, ChatConversation, ChatTurn, Transaction
from steward.demo import seed, seed_people
from steward_boundary import tools as T
from tests.advisor.stubs import AutoControl, StubGateway, part
from tests.factories import make_person
from tests.waiting import CEILING, wait_for_card, when_waiting

NOW = datetime(2026, 10, 20, 17, tzinfo=timezone.utc)
TODAY = date(2026, 10, 20)
Q = "Which subscriptions did we add this year?"
CTX = "Today is Tuesday, October 20, 2026. This is a household book. The person asking is Person 1."
CALL = {"id": "toolu_m1", "name": T.MERCHANT, "input": {"reason": "Totals can't name the services.",
                                                       "start": "2026-10-01", "end": "2026-10-20",
                                                       "category": "subscriptions", "payees": None}}
ROWS = {"rows": [{"date": "2026-10-18", "amount_cents": -1599, "category": "subscriptions", "merchant": "StreamBox",
                  "account": "Account 1"}], "payees": [], "more": False}


@pytest.fixture
def household(books, ours, engine, ddb):
    with Session(engine) as db:
        seed(db, years=1, seed=7)
        acct = db.scalar(select(Account.id).where(Account.type == AccountType.checking).order_by(Account.id))
        db.add(Transaction(account_id=acct, posted_at=date(2026, 10, 18), amount_cents=-1599, raw_description="STREAMBOX",
                           merchant="StreamBox", category="subscriptions", category_source=CategorySource.none))
        set_chat_on(db, True, None, NOW)
        db.commit()
    seed_people(ddb, ours.id)
    kai = make_person(ddb, "kai", [(ours.id, "viewer")])
    jr = next(p.id for p in books.people(ours.id) if p.username == "alex")
    return jr, kai.id


def env_for(books, book, uid, gw, control=None, wait=600.0):
    return AskEnv(bind=books.engine_for(book).execution_options(immediate=True), directory=books.directory, book=book,
                  user_id=uid, people=lambda: books.people(book.id), plan_cache=books.plan_cache(book.id), gateway=gw,
                  now=lambda: NOW, today=lambda: TODAY, control=control, approval_wait=wait)


def frames(after_results, call=CALL):
    user = {"role": "user", "content": [{"type": "text", "text": Q}]}
    system = {"role": "system", "content": CTX}
    ask_for = {"role": "assistant", "content": [{"type": "tool_use", **call}]}
    return [{"type": "redacted", "text": Q, "edits": []},
            {"type": "record", "seq": 1, "parts": [part("question", {"text": Q}), part("system", {"text": CTX})]},
            {"type": "sent", "messages": [{"message": user, "mac": "a" * 64}, {"message": system, "mac": "b" * 64}]},
            {"type": "status", "tool": T.MERCHANT},
            {"type": "assistant", "message": ask_for, "mac": "c" * 64, "model": "claude-opus-5-5", "usage": {}},
            {"type": "tool_calls", "seq": 1, "calls": [call]}, *after_results]


def answered(part_name, content, call=CALL):
    payload = {"tool_use_id": call["id"], "name": T.MERCHANT, "is_error": "error" in content, "content": content}
    answer = {"role": "assistant", "content": [{"type": "text", "text": "StreamBox, $15.99 a month."}]}
    return [{"type": "record", "seq": 2, "parts": [part(part_name, payload)]},
            {"type": "text", "delta": "StreamBox, $15.99 a month."},
            {"type": "assistant", "message": answer, "mac": "d" * 64, "model": "claude-opus-5-5", "usage": {}},
            {"type": "done", "stop": "end_turn", "line": None, "check": None, "path": None, "requests": 2,
             "cost_nd": 110_000_000, "last_input_tokens": 7000}]


def turn_row(books, ours, turn_id):
    with Session(books.engine_for(ours)) as db:
        return db.scalar(select(ChatTurn).where(ChatTurn.turn_id == turn_id))


def test_a_manager_approves_the_exact_rows_and_the_log_names_them(books, ours, household):
    jr, _ = household
    gw = StubGateway(frames(answered_echo()))       # the gateway records exactly the rows the NAS sent (Task 6.5)
    env = env_for(books, ours, jr, gw, AutoControl("send"))
    prep = start_turn(env, Q)
    events = list(run_turn(env, prep))
    card = next(e for e in events if e["type"] == "approval" and e["state"] == "ask")["card"]
    assert card["call_id"] == "toolu_m1" and card["reason"] == "Totals can't name the services."
    assert card["count"] == len(card["rows"]) >= 1 and {"name": "StreamBox", "count": 1} in card["names"]
    assert card["rows"][0]["store"] == "StreamBox" and card["rows"][0]["date"] == "Oct 18"
    assert card["accounts"][0]["label"] and card["until"].endswith(("AM", "PM"))
    approved = next(e for e in events if e["type"] == "approval" and e["state"] == "approved")
    assert approved["line"] == f"Alex approved {card['count']} store row{'' if card['count'] == 1 else 's'} for this conversation."
    sent = next(f for f in gw.ws.sent if f["type"] == "tool_results")["results"][0]
    assert sent["error"] is None and sent["content"]["rows"][0]["merchant"] == "StreamBox"
    assert events[-1]["stop"] == "end_turn"
    with Session(books.engine_for(ours)) as db:
        row = db.scalar(select(AdvisorOutgoing).where(AdvisorOutgoing.part == "merchant_rows"))
        assert row.approved_by == jr
    t = turn_row(books, ours, prep.turn_id)
    assert (t.approval, t.store_rows, t.approved_by) == ("approved", card["count"], jr)


def test_dont_send_returns_the_decline_and_claude_answers_from_totals(books, ours, household):
    jr, _ = household
    gw = StubGateway(frames(answered("tool_result", {"error": T.DECLINED})))
    env = env_for(books, ours, jr, gw, AutoControl("decline"))
    prep = start_turn(env, Q)
    events = list(run_turn(env, prep))
    assert {"type": "approval", "state": "declined", "line": DECLINED_LINE} in events
    sent = next(f for f in gw.ws.sent if f["type"] == "tool_results")["results"][0]
    assert (sent["content"], sent["error"]) == (None, T.DECLINED)
    assert turn_row(books, ours, prep.turn_id).approval == "declined"


def test_a_viewer_sees_no_buttons_and_is_answered_from_totals(books, ours, household):
    _, kai = household
    control = AutoControl("send")
    gw = StubGateway(frames(answered("tool_result", {"error": T.NEEDS_MANAGER})))
    env = env_for(books, ours, kai, gw, control)
    prep = start_turn(env, Q)
    events = list(run_turn(env, prep))
    viewer = next(e for e in events if e["type"] == "approval")
    assert viewer["state"] == "viewer" and "Alex" in viewer["managers"] and viewer["where"] == "Transactions"
    assert control.asked == []
    sent = next(f for f in gw.ws.sent if f["type"] == "tool_results")["results"][0]
    assert sent["error"] == T.NEEDS_MANAGER
    assert turn_row(books, ours, prep.turn_id).approval == "viewer"


def test_the_command_line_cannot_approve(books, ours, household):
    jr, _ = household
    gw = StubGateway(frames(answered("tool_result", {"error": T.NEEDS_MANAGER})))
    list(run_turn(env_for(books, ours, jr, gw, None), start_turn(env_for(books, ours, jr, gw, None), Q)))
    assert next(f for f in gw.ws.sent if f["type"] == "tool_results")["results"][0]["error"] == T.NEEDS_MANAGER


def test_an_unanswered_card_ends_the_turn_and_the_next_question_starts_fresh(books, ours, household):
    jr, _ = household
    gw = StubGateway(frames([]))
    env = env_for(books, ours, jr, gw, Control(), wait=0.05)
    prep = start_turn(env, Q)
    done = list(run_turn(env, prep))[-1]
    assert (done["stop"], done["line"]) == ("approval_timeout", TIMEOUT_LINE)
    assert gw.ws.sent[-1] == {"type": "cancel", "reason": "approval_timeout"}
    assert turn_row(books, ours, prep.turn_id).approval == "timeout"
    with Session(books.engine_for(ours)) as db:
        assert db.get(ChatConversation, prep.conversation_id).ended_at is not None
    again = start_turn(env_for(books, ours, jr, StubGateway(), Control()), "And now?", prep.conversation_id)
    assert again.new and again.note == ENDED_LINE


def test_stop_while_a_card_waits_ends_the_turn_at_once(books, ours, household):
    jr, _ = household
    control = Control()
    gw = StubGateway(frames([]))
    env = env_for(books, ours, jr, gw, control, wait=30)
    prep = start_turn(env, Q)
    helper = when_waiting(control, prep.turn_id, lambda: control.stop(prep.turn_id, jr, ours.id), call_id=CALL["id"])
    done = list(run_turn(env, prep))[-1]
    helper.done()
    assert (done["stop"], done["line"]) == ("stopped", STOPPED_LINE)
    assert gw.ws.sent[-1] == {"type": "cancel", "reason": "stopped"}


def test_stop_ends_the_turn_and_the_next_question_starts_plainly(books, ours, household):
    jr, _ = household
    control = Control()
    user = {"role": "user", "content": [{"type": "text", "text": Q}]}
    system = {"role": "system", "content": CTX}
    gw = StubGateway([{"type": "redacted", "text": Q, "edits": []},
                      {"type": "record", "seq": 1, "parts": [part("question", {"text": Q}), part("system", {"text": CTX})]},
                      {"type": "sent", "messages": [{"message": user, "mac": "a" * 64}, {"message": system, "mac": "b" * 64}]},
                      {"type": "text", "delta": "So far"}])
    env = env_for(books, ours, jr, gw, control)
    prep = start_turn(env, Q)
    turn = run_turn(env, prep)
    for e in turn:
        if e["type"] == "text":
            control.stop(prep.turn_id, jr, ours.id)
            break
    rest = list(turn)
    assert [e["type"] for e in rest] == ["done"] and rest[0]["stop"] == "stopped" and rest[0]["line"] == STOPPED_LINE
    assert turn_row(books, ours, prep.turn_id).stop == "stopped"
    assert gw.ws.sent[-1] == {"type": "cancel", "reason": "stopped"}
    # Claude's reply never arrived, so the stored history can't be continued (plan A ruling 10): a new conversation.
    follow = start_turn(env_for(books, ours, jr, StubGateway(), Control()), "And last month?", prep.conversation_id)
    assert follow.new and follow.note == ENDED_LINE


def test_chat_switched_off_during_the_wait_sends_nothing(books, ours, household):
    jr, _ = household

    class SwitchOffThenSend(AutoControl):
        def wait_for_ok(self, turn_id, call_id, timeout, digest=None):
            with Session(books.engine_for(ours)) as db:
                set_chat_on(db, False, jr, NOW)
                db.commit()
            return super().wait_for_ok(turn_id, call_id, timeout, digest)

    gw = StubGateway(frames(answered("merchant_rows", ROWS)))
    env = env_for(books, ours, jr, gw, SwitchOffThenSend("send"))
    prep = start_turn(env, Q)
    done = list(run_turn(env, prep))[-1]
    assert done["stop"] == "cancelled"
    assert gw.ws.sent[-1] == {"type": "cancel", "reason": "chat_off"}
    assert not any(m.get("type") == "tool_results" for m in gw.ws.sent)
    assert turn_row(books, ours, prep.turn_id).approval != "approved"
    with Session(books.engine_for(ours)) as db:
        assert db.scalar(select(AdvisorOutgoing).where(AdvisorOutgoing.part == "merchant_rows")) is None


def test_store_rows_nobody_approved_are_never_recorded(books, ours, household):
    jr, _ = household
    gw = StubGateway(frames(answered("merchant_rows", ROWS)))         # the gateway claims rows after a decline
    env = env_for(books, ours, jr, gw, AutoControl("decline"))
    done = list(run_turn(env, start_turn(env, Q)))[-1]
    assert done["stop"] == "record_failed"
    with Session(books.engine_for(ours)) as db:
        assert db.scalar(select(AdvisorOutgoing).where(AdvisorOutgoing.part == "merchant_rows")) is None


def test_store_rows_under_another_label_still_need_an_ok(books, ours, household):
    """Preflight ruling P1: the check is keyed on the content, so rows labelled as a plain result are refused too."""
    jr, _ = household
    gw = StubGateway(frames(answered("tool_result", ROWS)))
    env = env_for(books, ours, jr, gw, AutoControl("decline"))
    prep = start_turn(env, Q)
    done = list(run_turn(env, prep))[-1]
    assert done["stop"] == "record_failed"
    with Session(books.engine_for(ours)) as db:
        assert db.scalar(select(AdvisorOutgoing).where(AdvisorOutgoing.turn_id == prep.turn_id,
                                                       AdvisorOutgoing.seq == 2)) is None


def test_an_empty_result_needs_no_card_and_is_recorded(books, ours, household):
    """Plan B ruling 5 and preflight ruling P1: nothing found means nothing to approve; it goes as a plain result."""
    jr, _ = household
    call = {**CALL, "input": {**CALL["input"], "category": None, "payees": ["Payee 999"]}}
    empty = {"rows": [], "payees": [], "more": False}
    control = AutoControl("decline")
    gw = StubGateway(frames(answered("tool_result", empty, call), call))
    env = env_for(books, ours, jr, gw, control)
    prep = start_turn(env, Q)
    events = list(run_turn(env, prep))
    assert events[-1]["stop"] == "end_turn" and control.asked == []
    assert not [e for e in events if e["type"] == "approval"]
    sent = next(f for f in gw.ws.sent if f["type"] == "tool_results")["results"][0]
    assert (sent["content"], sent["error"]) == (empty, None)
    with Session(books.engine_for(ours)) as db:
        row = db.scalar(select(AdvisorOutgoing).where(AdvisorOutgoing.turn_id == prep.turn_id,
                                                      AdvisorOutgoing.seq == 2))
        assert row.part == "tool_result" and row.approved_by is None and json.loads(row.payload)["content"] == empty
    assert turn_row(books, ours, prep.turn_id).approval is None


# --- Task 6.5: the approval is bound to one book, one turn, a manager now, and the exact rows on the card -------------


def echoed(mutate=lambda content: content):
    """The gateway's record frame for the tool results the NAS actually sent (optionally altered)."""
    def build(sent):
        r = next(f for f in sent if f["type"] == "tool_results")["results"][0]
        error = r["error"] is not None
        content = {"error": r["error"]} if error else mutate(json.loads(json.dumps(r["content"])))
        name = "tool_result" if error else "merchant_rows"
        return {"type": "record", "seq": 2, "parts": [part(name, {"tool_use_id": r["id"], "name": r["name"],
                                                                  "is_error": error, "content": content})]}
    return build


def answered_echo(mutate=lambda content: content):
    return [echoed(mutate), *answered("merchant_rows", ROWS)[1:]]


def store_rows(books, ours):
    with Session(books.engine_for(ours)) as db:
        return list(db.scalars(select(AdvisorOutgoing).where(AdvisorOutgoing.part == "merchant_rows")))


def test_only_the_asker_in_this_book_with_the_cards_hash_can_approve_and_only_once(books, ours, household):
    jr, kai = household
    sam = next(p.id for p in books.people(ours.id) if p.id not in (jr, kai))       # the other manager
    control = Control()
    gw = StubGateway(frames(answered_echo()))
    env = env_for(books, ours, jr, gw, control, wait=30)
    prep = start_turn(env, Q)
    turn = run_turn(env, prep)
    card = next(e for e in turn if e["type"] == "approval")["card"]
    assert isinstance(card["hash"], str) and len(card["hash"]) == 64
    tries = {}

    def answer():
        tries["no hash"] = control.answer(prep.turn_id, CALL["id"], jr, ours.id, True)
        tries["altered list"] = control.answer(prep.turn_id, CALL["id"], jr, ours.id, True, "0" * 64)
        tries["another manager"] = control.answer(prep.turn_id, CALL["id"], sam, ours.id, True, card["hash"])
        tries["a viewer"] = control.answer(prep.turn_id, CALL["id"], kai, ours.id, True, card["hash"])
        tries["another book"] = control.answer(prep.turn_id, CALL["id"], jr, ours.id + 1, True, card["hash"])
        tries["another call"] = control.answer(prep.turn_id, "toolu_other", jr, ours.id, True, card["hash"])
        tries["the asker"] = control.answer(prep.turn_id, CALL["id"], jr, ours.id, True, card["hash"])
        tries["a replay"] = control.answer(prep.turn_id, CALL["id"], jr, ours.id, True, card["hash"])
    helper = when_waiting(control, prep.turn_id, answer, call_id=CALL["id"])
    rest = list(turn)
    helper.done()
    assert tries == {"no hash": False, "altered list": False, "another manager": False, "a viewer": False,
                     "another book": False, "another call": False, "the asker": True, "a replay": False}
    assert rest[-1]["stop"] == "end_turn"
    assert [r.approved_by for r in store_rows(books, ours)] == [jr]
    assert control.answer(prep.turn_id, CALL["id"], jr, ours.id, True, card["hash"]) is False   # a stale turn


def test_rows_that_differ_from_the_approved_ones_are_never_recorded(books, ours, household):
    """The approval names the exact rows: rows the gateway changed after the OK (here a store name) are refused."""
    jr, _ = household

    def swap(content):
        content["rows"][0]["merchant"] = "Someone Else"
        return content
    gw = StubGateway(frames(answered_echo(swap)))
    env = env_for(books, ours, jr, gw, AutoControl("send"))
    done = list(run_turn(env, start_turn(env, Q)))[-1]
    assert done["stop"] == "record_failed"
    assert store_rows(books, ours) == []


def test_the_approved_rows_are_recorded_when_they_match_the_card(books, ours, household):
    jr, _ = household
    control = AutoControl("send")
    gw = StubGateway(frames(answered_echo()))
    env = env_for(books, ours, jr, gw, control)
    events = list(run_turn(env, start_turn(env, Q)))
    card = next(e for e in events if e["type"] == "approval" and e["state"] == "ask")["card"]
    assert events[-1]["stop"] == "end_turn" and control.digests == [card["hash"]]
    assert [r.approved_by for r in store_rows(books, ours)] == [jr]


def test_an_asker_who_is_no_longer_a_manager_when_approving_sends_nothing(books, ours, household, ddb):
    """Managerness is checked on the NAS at the moment of the OK, never trusted from the browser."""
    jr, _ = household

    class DemotedThenSend(AutoControl):
        def wait_for_ok(self, turn_id, call_id, timeout, digest=None):
            ddb.execute(update(Membership).where(Membership.user_id == jr, Membership.book_id == ours.id)
                        .values(role="viewer"))
            ddb.commit()
            return super().wait_for_ok(turn_id, call_id, timeout, digest)

    gw = StubGateway(frames(answered("tool_result", {"error": T.NEEDS_MANAGER})))
    env = env_for(books, ours, jr, gw, DemotedThenSend("send"))
    prep = start_turn(env, Q)
    events = list(run_turn(env, prep))
    assert not [e for e in events if e["type"] == "approval" and e["state"] == "approved"]
    sent = next(f for f in gw.ws.sent if f["type"] == "tool_results")["results"][0]
    assert (sent["content"], sent["error"]) == (None, T.NEEDS_MANAGER)
    assert "StreamBox" not in json.dumps(gw.ws.sent)
    assert store_rows(books, ours) == []
    assert turn_row(books, ours, prep.turn_id).approval != "approved"


def test_declining_sends_no_store_name_and_the_turn_goes_on_from_totals(books, ours, household):
    jr, _ = household
    gw = StubGateway(frames(answered("tool_result", {"error": T.DECLINED})))
    env = env_for(books, ours, jr, gw, AutoControl("decline"))
    prep = start_turn(env, Q)
    events = list(run_turn(env, prep))
    assert events[-1]["stop"] == "end_turn"
    results = [f for f in gw.ws.sent if f["type"] == "tool_results"]
    assert results and "StreamBox" not in json.dumps(results)
    with Session(books.engine_for(ours)) as db:
        payloads = list(db.scalars(select(AdvisorOutgoing.payload).where(AdvisorOutgoing.turn_id == prep.turn_id)))
    assert payloads and not any("StreamBox" in p for p in payloads)


def test_a_browser_that_leaves_while_a_card_waits_frees_the_worker_and_the_socket_at_once(books, ours, household):
    """Task 6.5: release (the stream's browser went away) ends the wait at once, cancels at the gateway, closes the
    socket and ends the conversation, instead of holding a worker and the socket for up to 10 minutes."""
    jr, _ = household
    control = Control()
    gw = StubGateway(frames([]))
    env = env_for(books, ours, jr, gw, control, wait=30)
    prep = start_turn(env, Q)
    turn = run_turn(env, prep)
    assert next(e for e in turn if e["type"] == "approval")["state"] == "ask"
    rest = []
    worker = threading.Thread(target=lambda: rest.extend(turn))
    worker.start()
    wait_for_card(control, prep.turn_id)                # the worker is waiting on the card now
    control.release(prep.turn_id)
    worker.join(CEILING)
    assert not worker.is_alive()                        # wait=30: a worker the release didn't free would still be here
    assert gw.ws.sent[-1] == {"type": "cancel", "reason": "stopped"} and gw.ws.closed
    assert rest[-1]["stop"] == "dropped"
    assert not any(f["type"] == "tool_results" for f in gw.ws.sent)
    with Session(books.engine_for(ours)) as db:
        assert db.get(ChatConversation, prep.conversation_id).ended_at is not None
    assert turn_row(books, ours, prep.turn_id).stop == "dropped"


def test_a_browser_that_leaves_between_events_cancels_at_the_gateway_before_the_socket_closes(books, ours, household):
    """Task 6.5: when the stream closes the turn while it sits between events (the card just shown), the gateway gets
    a cancel, not only a closed socket, and nothing is sent for the card."""
    jr, _ = household
    gw = StubGateway(frames([]))
    env = env_for(books, ours, jr, gw, Control(), wait=30)
    prep = start_turn(env, Q)
    turn = run_turn(env, prep)
    assert next(e for e in turn if e["type"] == "approval")["state"] == "ask"
    turn.close()
    assert gw.ws.sent[-1] == {"type": "cancel", "reason": "stopped"} and gw.ws.closed
    assert not any(f["type"] == "tool_results" for f in gw.ws.sent)
    assert turn_row(books, ours, prep.turn_id).stop == "dropped"


def test_a_second_store_names_call_in_the_same_turn_asks_again(books, ours, household):
    """Final review, Task 6: an OK covers one call. A second merchant_detail call in the same turn shows its own card
    and waits for its own answer; nothing from the first approval carries over."""
    jr, _ = household
    call2 = {**CALL, "id": "toolu_m2"}

    def echo_last(seq):
        def build(sent):
            r = [f for f in sent if f["type"] == "tool_results"][-1]["results"][0]
            return {"type": "record", "seq": seq, "parts": [part("merchant_rows", {
                "tool_use_id": r["id"], "name": r["name"], "is_error": False, "content": r["content"]})]}
        return build
    again = {"role": "assistant", "content": [{"type": "tool_use", **call2}]}
    gw = StubGateway(frames([echo_last(2),
                             {"type": "assistant", "message": again, "mac": "e" * 64, "model": "claude-opus-5-5",
                              "usage": {}},
                             {"type": "tool_calls", "seq": 2, "calls": [call2]},
                             echo_last(3), *answered("merchant_rows", ROWS)[1:]]))
    control = AutoControl("send")
    env = env_for(books, ours, jr, gw, control)
    prep = start_turn(env, Q)
    events = list(run_turn(env, prep))
    asks = [e["card"]["call_id"] for e in events if e["type"] == "approval" and e["state"] == "ask"]
    assert asks == ["toolu_m1", "toolu_m2"] and control.asked == ["toolu_m1", "toolu_m2"]
    assert len(set(control.digests)) == 2                   # each card has its own hash
    assert events[-1]["stop"] == "end_turn"
    assert [r.seq for r in store_rows(books, ours)] == [2, 3]


# --- Public issue #1: a payment-app or transfer recipient goes as [name] unless the manager ticks that row ------------

GIFTS = {**CALL, "input": {**CALL["input"], "category": "gifts", "start": "2026-10-19", "end": "2026-10-19"}}


def zelle_turn(books, ours, jr, control):
    with Session(books.engine_for(ours)) as db:
        acct = db.scalar(select(Account.id).where(Account.type == AccountType.checking).order_by(Account.id))
        db.add(Transaction(account_id=acct, posted_at=date(2026, 10, 19), amount_cents=-2500,
                           raw_description="ZELLE PAYMENT TO PRIYA NAIR 0412", category="gifts",
                           category_source=CategorySource.none))
        db.commit()
    gw = StubGateway(frames(answered_echo(), call=GIFTS))
    env = env_for(books, ours, jr, gw, control)
    prep = start_turn(env, Q)
    events = list(run_turn(env, prep))
    card = next(e for e in events if e["type"] == "approval" and e["state"] == "ask")["card"]
    sent = next(f for f in gw.ws.sent if f["type"] == "tool_results")["results"][0]
    return card, sent, events, gw


def test_a_transfer_row_to_a_person_the_book_doesnt_know_goes_as_name_unless_ticked(books, ours, household):
    jr, _ = household
    card, sent, events, gw = zelle_turn(books, ours, jr, AutoControl("send"))
    zelle = next(i for i, r in enumerate(card["rows"]) if r["store"].startswith("Zelle"))
    assert card["rows"][zelle]["store"] == "Zelle to [name]" and card["rows"][zelle]["full"] == "Zelle to Priya Nair"
    assert sent["content"]["rows"][zelle]["merchant"] == "Zelle to [name]" and "home" not in sent
    assert "Priya" not in json.dumps(gw.ws.sent)
    assert events[-1]["stop"] == "end_turn"
    recorded = json.loads(store_rows(books, ours)[0].payload)["content"]
    assert recorded == sent["content"]


def test_a_ticked_transfer_row_sends_the_name_the_card_showed_and_the_log_records_it(books, ours, household):
    jr, _ = household
    control = AutoControl("send", ticked={"r0", "r99", "p0"})            # keys not on the card are ignored
    card, sent, events, _ = zelle_turn(books, ours, jr, control)
    assert card["rows"][0]["full"] == "Zelle to Priya Nair"
    assert sent["content"]["rows"][0]["merchant"] == "Zelle to Priya Nair"
    assert all(r["merchant"] == c["store"] for r, c in zip(sent["content"]["rows"][1:], card["rows"][1:]))
    assert events[-1]["stop"] == "end_turn"
    assert json.loads(store_rows(books, ours)[0].payload)["content"] == sent["content"]


# --- Public issue #53: the ticks go to the gateway beside the rows, which may mask a name in an unticked one ----------


def test_the_rows_a_manager_ticked_go_beside_the_content_and_only_ticks_the_card_offered(books, ours, household):
    jr, _ = household
    _, sent, _, _ = zelle_turn(books, ours, jr, AutoControl("send", ticked={"r0", "r99", "p0"}))
    assert sent["ticked"] == ["r0"] and "ticked" not in json.dumps(sent["content"])


def test_an_approval_with_nothing_ticked_says_so(books, ours, household):
    jr, _ = household
    _, sent, _, _ = zelle_turn(books, ours, jr, AutoControl("send"))
    assert sent["ticked"] == []


def test_a_result_with_no_card_carries_no_ticks(books, ours, household):
    jr, _ = household
    gw = StubGateway(frames(answered("tool_result", {"error": T.DECLINED})))
    list(run_turn(env_for(books, ours, jr, gw, AutoControl("decline")), start_turn(env_for(books, ours, jr, gw), Q)))
    sent = next(f for f in gw.ws.sent if f["type"] == "tool_results")["results"][0]
    assert "ticked" not in sent


def masking(at: int, to: str):
    def mutate(content):
        content["rows"][at]["merchant"] = to
        return content
    return mutate


@pytest.mark.parametrize("to", ["[name]", "[name]Box", "Stream[name]"])
def test_rows_the_gateway_masked_in_an_unticked_name_are_recorded_as_sent(books, ours, household, to):
    """The gateway may replace a person's name in an unticked row with [name] after the OK: text taken out, so the rows
    are still the approved ones and the log records what was sent."""
    jr, _ = household
    gw = StubGateway(frames(answered_echo(masking(0, to))))
    env = env_for(books, ours, jr, gw, AutoControl("send"))
    assert list(run_turn(env, start_turn(env, Q)))[-1]["stop"] == "end_turn"
    [row] = store_rows(books, ours)
    assert row.approved_by == jr and json.loads(row.payload)["content"]["rows"][0]["merchant"] == to


@pytest.mark.parametrize("to", ["StreamBox [name]", "[name] StreamBox", "Stream[name]Box!", "[NAME]"])
def test_rows_the_gateway_added_text_to_are_never_recorded(books, ours, household, to):
    jr, _ = household
    gw = StubGateway(frames(answered_echo(masking(0, to))))
    env = env_for(books, ours, jr, gw, AutoControl("send"))
    assert list(run_turn(env, start_turn(env, Q)))[-1]["stop"] == "record_failed"
    assert store_rows(books, ours) == []


def test_a_ticked_row_must_go_exactly_as_the_manager_approved_it(books, ours, household):
    """A tick is the manager's choice to send the name: the gateway can't mask it, and the NAS refuses one that did."""
    jr, _ = household
    with Session(books.engine_for(ours)) as db:
        acct = db.scalar(select(Account.id).where(Account.type == AccountType.checking).order_by(Account.id))
        db.add(Transaction(account_id=acct, posted_at=date(2026, 10, 19), amount_cents=-2500,
                           raw_description="ZELLE PAYMENT TO PRIYA NAIR 0412", category="gifts",
                           category_source=CategorySource.none))
        db.commit()
    gw = StubGateway(frames([echoed(masking(0, "Zelle to [name]")), *answered("merchant_rows", ROWS)[1:]],
                            call=GIFTS))
    env = env_for(books, ours, jr, gw, AutoControl("send", ticked={"r0"}))
    assert list(run_turn(env, start_turn(env, Q)))[-1]["stop"] == "record_failed"
    assert store_rows(books, ours) == []


def test_the_cards_hash_covers_the_names_a_tick_could_send():
    from steward.advisor.turn import card_digest
    content = {"rows": [], "payees": [], "more": False}
    assert card_digest("t", "c", content, {"r0": "Zelle to Priya Nair"}) != card_digest("t", "c", content, {})
    assert card_digest("t", "c", content, {"r0": "Zelle to A"}) != card_digest("t", "c", content, {"r0": "Zelle to B"})


def test_a_tick_is_taken_only_with_the_ok_and_forgotten_by_the_next_card(books, ours, household):
    control = Control()
    control.begin("t_1", 1, ours.id)
    got = []
    helper = when_waiting(control, "t_1", lambda: got.append(
        control.answer("t_1", "c1", 1, ours.id, True, "h" * 64, ticked=["r0", "p2"])), call_id="c1")
    assert control.wait_for_ok("t_1", "c1", CEILING, digest="h" * 64) == "send"
    helper.done()
    assert got == [True] and control.ticked("t_1") == frozenset({"r0", "p2"})
    helper = when_waiting(control, "t_1", lambda: control.answer("t_1", "c2", 1, ours.id, False, "g" * 64,
                                                                  ticked=["r0"]), call_id="c2")
    assert control.wait_for_ok("t_1", "c2", CEILING, digest="g" * 64) == "decline"
    helper.done()
    assert control.ticked("t_1") == frozenset()                         # a decline sends nothing, ticked or not
    control.end("t_1")
