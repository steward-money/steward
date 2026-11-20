"""Spec 11.3's end-to-end leak test: the NAS, the real gateway (with the stand-in detectors) and the fake Anthropic
server. A scripted model calls every tool with a spread of parameters in both synthetic books, as a manager and as a
viewer, for questions from the PII set. It fails if any recorded request carries any identifier of either book (dates
of birth in six formats, case-folded, digits joined), or if any block Steward added to a request differs byte for byte
from the book's log. Merchant detail is approved for one question per book, answered with forged approvals (that must
not count) and then declined for a second, and declined for the rest."""
import functools
import json
import re
import socket
import sqlite3
import threading
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from steward.advisor.about import AboutIn
from steward.advisor.control import Control
from steward.advisor.monitor import crosscheck
from steward.advisor.state import set_chat_on
from steward.advisor.turn import DECLINED_LINE, AskEnv, run_turn, start_turn
from steward.auth.users import find_user
from steward.books.service import add_membership, create_book
from steward.db.models import (Account, AccountType, AdvisorOutgoing, CareScenario, CategorySource, Flag, Transaction,
                               TransactionNote)
from steward.demo import seed, seed_people
from steward.demo_parent import seed_parent
from steward.demo_care import seed_care
from steward.demo_watching import seed_watching
from steward_boundary import PROTOCOL
from steward_boundary.canonical import canonical, sha256_hex
from steward_boundary.text import fold, plain
from steward_gateway.prompt import context_text, prefix
from tests.advisor.identities import (PATTERNS, all_identifiers, needles, seed_household_identities, seed_parent_identities,
                                      spelled_digit_runs)
from tests.advisor.pii_set.make_sets import HERE, read_jsonl
from tests.advisor.rig import gateway_rig
from tests.factories import make_person
from tests.gateway.fake_anthropic import Reply, text, tool_use
from tests.gateway.fakes import THIRD_PARTY_NAMES
from tests.waiting import when_waiting

TODAY = date(2026, 10, 19)
NOW = datetime(2026, 10, 19, 17, tzinfo=timezone.utc)
FIRST = [("book_summary", {}), ("accounts", {"include_closed": True}),
         ("balance_history", {"months": 6, "by_account_type": True}), ("cash_flow", {"months": 3, "by": "category"}),
         ("spending_by_category", {"start": "2026-07-01", "end": "2026-09-30", "compare_previous": True}),
         ("recurring", {}), ("holdings", {}), ("debts", {}), ("facts", {}), ("plan_result", {}),
         ("savings_checklist", {}), ("inventory", {"kind": None}), ("money_in_monthly", {"months": 6}),
         ("premiums", {}), ("flags", {"include_done": True}), ("care_projection", {})]
SECOND = [("cash_flow", {"months": 2, "by": "owner"}), ("cash_flow", {"months": 2, "by": "business"}),
          ("plan_what_if", {"levers": {"save_more_monthly_cents": 50_000, "person_1_claim_age": 70}}),
          ("plan_lever_impacts", {}), ("inventory", {"kind": "insurance"}), ("accounts", {"include_closed": False}),
          ("balance_history", {"months": 3, "by_account_type": False, "account": "Account 1"}),
          ("cash_flow", {"months": 2, "by": "category", "account": "Account 1"})]
THIRD = [("merchant_detail", {"reason": "Totals can't name the stores.", "start": "2026-09-20", "end": "2026-10-19",
                              "category": None, "payees": None})]


class ScriptedModel:
    """Three rounds of tool calls (the third asks for store names), then an answer that uses aliases. Remembers every
    block it sent back."""

    def __init__(self):
        self.sent: list[dict] = []

    def __call__(self, body, n):
        msgs = body["messages"]
        def has(m, kind):
            return isinstance(m["content"], list) and any(b.get("type") == kind for b in m["content"])
        last_question = max(i for i, m in enumerate(msgs) if m["role"] == "user" and has(m, "text"))
        rounds = sum(1 for m in msgs[last_question:] if m["role"] == "user" and has(m, "tool_result"))
        if rounds < 3:
            calls = (FIRST, SECOND, THIRD)[rounds]
            reply = Reply([tool_use(f"toolu_{n:03d}_{i:02d}", name, inp) for i, (name, inp) in enumerate(calls)],
                          stop_reason="tool_use")
        else:
            reply = Reply([text("Person 1 and Account 1 appear in the results, and Payee 1 is the largest.")])
        self.sent += reply.blocks
        return reply


class Answering(Control):
    """A real Control whose person answers each store-names card from another thread, as the browser does, with the
    card's hash. With `forge`, answers that must not count come first: no hash, an altered list, another person,
    another book, another call. Then the person answers `send`."""

    def __init__(self, user_id: int, book_id: int, send: bool, forge: bool = False):
        super().__init__()
        self.who, self.send, self.forge, self.asked, self.forged = (user_id, book_id), send, forge, [], []

    def wait_for_ok(self, turn_id, call_id, timeout, digest=None):
        self.asked.append(call_id)
        uid, bid = self.who

        def answer():
            if self.forge:
                self.forged += [self.answer(turn_id, call_id, uid, bid, True),
                                self.answer(turn_id, call_id, uid, bid, True, "0" * 64),
                                self.answer(turn_id, call_id, uid + 1000, bid, True, digest),
                                self.answer(turn_id, call_id, uid, bid + 1000, True, digest),
                                self.answer(turn_id, "toolu_forged", uid, bid, True, digest)]
            self.answer(turn_id, call_id, uid, bid, self.send, digest)
        when_waiting(self, turn_id, answer, call_id=call_id)       # answers once this card is up, from another thread
        return super().wait_for_ok(turn_id, call_id, timeout, digest)


@pytest.fixture
def world(books, ours, ddb):
    with Session(books.engine_for(ours)) as db:
        seed(db, years=2, seed=7)
    seed_people(ddb, ours.id)
    mom = create_book(books, ddb, name="Margaret Ellis", kind="parent", owner_labels=["Margaret", "Robert (husband)", "Joint"])
    jr = find_user(ddb, "alex")
    add_membership(ddb, jr.id, mom.id, "manager", by=None)
    kai = make_person(ddb, "kai", [(ours.id, "viewer")])
    vee = make_person(ddb, "vee", [(mom.id, "viewer")])
    ddb.commit()
    with Session(books.engine_for(ours)) as db:
        seed_household_identities(db, ddb)
        set_chat_on(db, True, jr.id, NOW)
        db.commit()
    with Session(books.engine_for(mom)) as db:
        seed_parent(db, TODAY, by=jr.id)
        seed_care(db, TODAY)
        seed_watching(db, TODAY, by=jr.id)
        seed_parent_identities(db)
        set_chat_on(db, True, jr.id, NOW)
        db.commit()
    return {"household": (ours, [jr.id, kai.id]), "parent": (mom, [jr.id, vee.id])}


# Ask about this: invented words seeded as a nickname, a note, an account name, a flag title and a flag note. None may
# reach Anthropic, by the context line or any other way.
ABOUT_NEEDLES = [("name", "zorblax"), ("name", "Quillfeather"), ("name", "Brindlewood"), ("name", "Thistledown")]


@pytest.fixture
def abouts(books, world):
    """One about of each kind per synthetic book, on rows that carry the needles."""
    ours, (jr, _) = world["household"]
    mom, _ = world["parent"]
    with Session(books.engine_for(ours)) as db:
        acct = db.scalar(select(Account).where(Account.type == AccountType.checking).order_by(Account.id))
        acct.name = "Quillfeather Checking"
        t = Transaction(account_id=acct.id, posted_at=date(2026, 8, 3), amount_cents=-4_210, raw_description="CORNER CAFE",
                        category="dining", category_source=CategorySource.none, nickname="zorblax")
        db.add(t)
        db.flush()
        db.add(TransactionNote(transaction_id=t.id, author_id=jr, author_name="Alex", body="zorblax for Brindlewood",
                               created_at=NOW))
        db.commit()
        household = [AboutIn("account", acct.id), AboutIn("transaction", t.id),
                     AboutIn("category", category="dining", month="2026-10"), AboutIn("plan")]
    with Session(books.engine_for(mom)) as db:
        acct = db.scalar(select(Account).order_by(Account.id))
        t = Transaction(account_id=acct.id, posted_at=date(2026, 8, 4), amount_cents=-12_345,
                        raw_description="CORNER PHARMACY", category="health", category_source=CategorySource.none)
        db.add(t)
        db.flush()
        f = Flag(check="large", severity="normal", status="checking", transaction_id=t.id,
                 summary="Two trips to Brindlewood", title="Two trips to Brindlewood",
                 note="Calling Thistledown this morning", created_at=NOW)
        db.add(f)
        db.commit()
        scenario = db.scalar(select(CareScenario.id).order_by(CareScenario.position, CareScenario.id))
        assert scenario is not None                 # seed_care gives Margaret her scenarios
        parent = [AboutIn("flag", f.id), AboutIn("care"), AboutIn("care", scenario), AboutIn("transaction", t.id)]
    return {"household": household, "parent": parent}


def env_for(books, book, user_id, gateway, control=None):
    return AskEnv(bind=books.engine_for(book).execution_options(immediate=True), directory=books.directory, book=book,
                  user_id=user_id, people=lambda: books.people(book.id), plan_cache=books.plan_cache(book.id),
                  gateway=gateway, now=lambda: NOW, today=lambda: TODAY, control=control)


def questions(book: str, per_book: int = 8) -> list[str]:
    """Questions whose identifiers the book knows, or that the stand-in detectors catch: the first of each category
    (preflight ruling 8), so names, emails, dates of birth and numbers all go through the whole path."""
    out, seen = [], set()
    for r in read_jsonl(HERE / "questions.jsonl"):
        ok = all(s["known"] or s["kind"] in PATTERNS or (s["kind"] == "name" and s["value"] in THIRD_PARTY_NAMES)
                 for s in r["spans"])
        if r["book"] == book and ok and r["category"] not in seen:
            seen.add(r["category"])
            out.append(r["text"])
    return out[:per_book]


def strings(value, out):
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        if value.get("type") == "tool_result" and isinstance(value.get("content"), str):
            # A result is canonical JSON. Only its string values are scanned: amounts are typed integer fields (spec
            # 4.1), and a short digit needle matches inside a cents value by chance (preflight ruling 8).
            strings(json.loads(value["content"]), out)
            strings({k: v for k, v in value.items() if k != "content"}, out)
            return out
        for v in value.values():
            strings(v, out)
    elif isinstance(value, list):
        for v in value:
            strings(v, out)
    return out


_RUNS = re.compile(r"\d(?:[\s\-.()/]*\d)*")


@functools.cache
def _prepared(text: str) -> tuple[str, list[tuple[str, set[int], set[int]]]]:
    """A text's folded form and its digit runs, worked out once: every text is checked against every identifier."""
    t = fold(plain(text)).casefold()
    runs = []
    for m in _RUNS.finditer(plain(text)):
        groups = list(re.finditer(r"\d+", m.group()))
        joined, starts, ends, pos = "", set(), set(), 0
        for g in groups:
            starts.add(pos)
            joined += g.group()
            pos += len(g.group())
            ends.add(pos)
        runs.append((joined, starts, ends))
    runs += [(r, {0}, {len(r)}) for r in spelled_digit_runs(text)]
    return t, runs


@functools.cache
def _word_patterns(kind: str, value: str) -> tuple[tuple[re.Pattern, ...], tuple[str, ...]]:
    words, digits = needles(kind, value)
    return tuple(re.compile(rf"(?<![\w]){re.escape(w)}(?![\w])") for w in words), tuple(filter(None, digits))


def leaks_whole(text: str, kind: str, value: str) -> bool:
    """Like identities.leaks, but a digit needle counts only when it starts and ends on digit-group boundaries of one
    run (so "4417 0029 18" is found, and a needle can't hide in the middle of an unrelated number)."""
    words, digits = _word_patterns(kind, value)
    t, runs = _prepared(text)
    if any(w.search(t) for w in words):
        return True
    for d in digits:
        for joined, starts, ends in runs:
            i = joined.find(d)
            while i != -1:
                if i in starts and i + len(d) in ends:
                    return True
                i = joined.find(d, i + 1)
    return False


def test_nothing_identifying_reaches_anthropic_and_every_part_matches_the_log(tmp_path, books, world, abouts):
    model = ScriptedModel()
    approved_turns, other_turns, forged = set(), set(), []
    lines: dict[str, tuple] = {}
    with gateway_rig(tmp_path, model) as rig:
        for book_kind, (book, members) in world.items():
            qs = questions(book_kind)
            assert len(qs) >= len(abouts[book_kind])
            for qi, q in enumerate(qs):
                about = abouts[book_kind][qi] if qi < len(abouts[book_kind]) else None
                for uid in members:
                    manager = uid == members[0]
                    control = Answering(uid, book.id, send=manager and qi == 0, forge=manager and qi == 1)
                    env = env_for(books, book, uid, rig.client, control)
                    prep = start_turn(env, q, None, about)
                    events = list(run_turn(env, prep))
                    assert events[-1]["stop"] == "end_turn", (book_kind, uid, q, about, events[-1])
                    (approved_turns if control.send and control.asked else other_turns).add(prep.turn_id)
                    lines[prep.turn_id] = (book, context_text(prep.context), about is not None)
                    if control.forge and control.asked:
                        forged.append(control.forged)
                        assert {"type": "approval", "state": "declined", "line": DECLINED_LINE} in events
        requests = list(rig.fake.requests)
        sent = rig.client.sent_since(datetime(2026, 1, 1, tzinfo=timezone.utc))["sent"]
        assert crosscheck(books, rig.client, NOW) == 0
    assert len(requests) == 4 * 2 * sum(len(questions(k)) for k in world)

    identifiers = all_identifiers() + ABOUT_NEEDLES
    APPROVABLE = {"payee", "institution", "organization"}
    for i, body in enumerate(requests):
        approved_ids = set()
        for m in body["messages"]:
            if m["role"] == "assistant":
                approved_ids |= {b["id"] for b in m["content"] if b.get("type") == "tool_use"
                                 and b["name"] == "merchant_detail"}
        for m in body["messages"]:
            if m["role"] != "user":
                continue
            if not isinstance(m["content"], list):
                for s in strings(m["content"], []):
                    hits = [(k, v) for k, v in identifiers if leaks_whole(s, k, v)]
                    assert hits == [], (i, hits, s[:200])
                continue
            for b in m["content"]:
                store = b.get("type") == "tool_result" and b.get("tool_use_id") in approved_ids and not b["is_error"]
                for s in strings(b, []):
                    kinds = [(k, v) for k, v in identifiers if not (store and k in APPROVABLE)]
                    hits = [(k, v) for k, v in kinds if leaks_whole(s, k, v)]
                    assert hits == [], (i, hits, s[:200])
        for s in strings({k: v for k, v in body.items() if k != "messages"}, []) + \
                strings([m for m in body["messages"] if m["role"] != "user"], []):
            hits = [(k, v) for k, v in identifiers if leaks_whole(s, k, v)]
            assert hits == [], (i, hits, s[:200])

    assert forged and all(f == [False] * 5 for f in forged)        # the forged path really ran, and nothing counted
    for book, members in world.values():
        with Session(books.engine_for(book)) as db:
            rows = list(db.scalars(select(AdvisorOutgoing).where(AdvisorOutgoing.part == "merchant_rows")))
            assert all(r.approved_by == members[0] for r in rows)
            assert {r.turn_id for r in rows} <= approved_turns and not {r.turn_id for r in rows} & other_turns
            if book.kind == "household":        # the approved path really ran: store rows went in the household book
                assert rows

    logged = set()
    for book, _ in world.values():
        with Session(books.engine_for(book)) as db:
            logged |= set(db.scalars(select(AdvisorOutgoing.sha256)))
    assert {r["sha256"] for r in sent} <= logged
    produced = [json.dumps(b, sort_keys=True) for b in model.sent]
    for body in requests:
        assert body["tools"] == prefix()["tools"] and body["system"] == prefix()["system"]
        names = {}
        for m in body["messages"]:
            if m["role"] == "assistant":
                for b in m["content"]:
                    assert json.dumps(b, sort_keys=True) in produced
                    if b["type"] == "tool_use":
                        names[b["id"]] = b["name"]
            elif m["role"] == "system":
                assert sha256_hex({"text": m["content"]}) in logged
            else:
                for b in m["content"]:
                    if b["type"] == "text":
                        assert sha256_hex({"text": b["text"]}) in logged
                    else:
                        content = json.loads(b["content"])
                        assert canonical(content).decode() == b["content"]
                        payload = {"tool_use_id": b["tool_use_id"], "name": names[b["tool_use_id"]],
                                   "is_error": b["is_error"], "content": content}
                        assert sha256_hex(payload) in logged

    # Every kind rode on one question in its book, as manager and viewer; every request's context part is byte for byte
    # the line the gateway built from the wire object (P4), and the needles are in no line.
    assert sum(1 for _, _, has in lines.values() if has) == 2 * sum(len(v) for v in abouts.values())
    for turn_id, (book, line, has) in lines.items():
        with Session(books.engine_for(book)) as db:
            texts = {json.loads(p)["text"] for p in db.scalars(select(AdvisorOutgoing.payload).where(
                AdvisorOutgoing.turn_id == turn_id, AdvisorOutgoing.part == "system"))}
        assert texts == {line}, turn_id
        assert (" It's about " in line) == has
        assert not [w for _, w in ABOUT_NEEDLES if w.casefold() in line.casefold()]


class Ticking(Answering):
    """Answers send, with these rows ticked when the card offers them."""

    def __init__(self, user_id: int, book_id: int, ticked=()):
        super().__init__(user_id, book_id, send=True)
        self.ticks = list(ticked)

    def answer(self, turn_id, call_id, user_id, book_id, send, digest=None, ticked=()):
        return super().answer(turn_id, call_id, user_id, book_id, send, digest, ticked=self.ticks)


def test_a_name_the_model_finds_in_an_unticked_approved_row_never_reaches_anthropic(tmp_path, books, world):
    """Public issue #53, end to end: the NAS sends a card-merchant row "Tom Becker Plumbing" (no transfer words, so no
    tick on the card) and a Zelle row; the real gateway masks the name its model finds, the NAS records the masked
    rows as approved, and Anthropic gets "[name] Plumbing". The Zelle row, ticked, keeps the name the card showed."""
    book, (jr, _) = world["household"]
    with Session(books.engine_for(book)) as db:
        acct = db.scalar(select(Account.id).where(Account.type == AccountType.checking).order_by(Account.id))
        db.add_all([Transaction(account_id=acct, posted_at=date(2026, 10, 15), amount_cents=-18_500,
                                raw_description="TOM BECKER PLUMBING", merchant="Tom Becker Plumbing",
                                category="home", category_source=CategorySource.none),
                    Transaction(account_id=acct, posted_at=date(2026, 10, 14), amount_cents=-2_500,
                                raw_description="ZELLE PAYMENT TO PRIYA NAIR 0412", category="gifts",
                                category_source=CategorySource.none)])
        db.commit()
    for ticked in ([], ["zelle"]):
        with gateway_rig(tmp_path / str(len(ticked)), ScriptedModel()) as rig:
            control = Ticking(jr, book.id)
            env = env_for(books, book, jr, rig.client, control)
            prep = start_turn(env, "Who did we pay in October?")
            turn = run_turn(env, prep)
            events = []
            for e in turn:
                events.append(e)
                if e.get("type") == "approval" and e.get("state") == "ask":
                    rows = e["card"]["rows"]
                    control.ticks = [f"r{i}" for i, r in enumerate(rows) if ticked and r["full"]]
            assert events[-1]["stop"] == "end_turn", events[-1]
            card = next(e for e in events if e.get("type") == "approval" and e.get("state") == "ask")["card"]
            plumber = next(r for r in card["rows"] if r["store"] == "Tom Becker Plumbing")
            assert plumber["full"] is None                      # the card offers no tick for it
            sent = canonical(list(rig.fake.requests)).decode()
            assert "Tom Becker" not in sent and "[name] Plumbing" in sent
            assert ("Priya Nair" in sent) == bool(ticked)
        with Session(books.engine_for(book)) as db:
            [row] = db.scalars(select(AdvisorOutgoing).where(AdvisorOutgoing.part == "merchant_rows",
                                                             AdvisorOutgoing.turn_id == prep.turn_id))
            merchants = [r["merchant"] for r in json.loads(row.payload)["content"]["rows"]]
            assert row.approved_by == jr and "[name] Plumbing" in merchants and "Tom Becker Plumbing" not in merchants
            assert ("Zelle to Priya Nair" in merchants) == bool(ticked)


def test_the_nas_connects_to_nothing_but_the_gateway(tmp_path, books, world, monkeypatch):
    book, (jr, _) = world["household"]
    seen = []
    real = socket.socket.connect
    main = threading.main_thread()

    def spy(self, address):
        if threading.current_thread() is main:
            seen.append(address)
        return real(self, address)
    with gateway_rig(tmp_path, ScriptedModel()) as rig:
        env = env_for(books, book, jr, rig.client)
        prep = start_turn(env, "How much is in savings?")
        monkeypatch.setattr(socket.socket, "connect", spy)
        events = list(run_turn(env, prep))
        monkeypatch.undo()
        port = int(rig.url.rsplit(":", 1)[1])
    assert events[-1]["stop"] == "end_turn"
    assert seen and {a[1] for a in seen} == {port}


DROP_START = {"type": "start", "protocol": PROTOCOL, "turn_id": "t_00000000000000a1", "book_id": 1, "user_id": 2,
              "question": "Are we spending more on dining than last year?",
              "context": {"today": "2026-10-19", "book_kind": "household", "asker": "Person 1"}, "history": [],
              "fingerprints": {"salt": "2" * 32, "hashes": []}, "issued": []}


def ledger_statuses(rig) -> list[str]:
    c = sqlite3.connect(rig.cfg.ledger_path)
    try:
        return [r[0] for r in c.execute("SELECT status FROM spend ORDER BY id")]
    finally:
        c.close()


# Both drop tests read the ledger only once the rig has closed. Its server stops only after every connection's handler
# has returned (uvicorn waits on them, and serve_in_thread joins the server thread and fails if it hasn't stopped), so
# the turn has been cancelled and its reservation, if it made one, unwound: nothing can still change the ledger or reach
# the fake. That is the event these tests wait for, rather than polling the ledger for a state with a deadline.
def test_a_nas_that_drops_right_after_the_redacted_frame_sends_nothing_and_releases_the_reservation(tmp_path):
    """Review focus: the NAS dies with no cancel frame. The gateway sends nothing to Anthropic and leaves nothing
    reserved. The gateway reserves after its checks, which run after the redacted frame, so the drop can land before
    the reservation (the turn is cancelled with none made) or after it (it is released): both leave nothing reserved."""
    with gateway_rig(tmp_path, ScriptedModel()) as rig:
        with rig.client.connect() as ws:
            ws.send(json.dumps(DROP_START))
            frame = json.loads(ws.recv(timeout=10))
            assert frame["type"] == "redacted"
            ws.socket.close()      # abruptly: no close handshake, no cancel frame
    assert ledger_statuses(rig) in ([], ["released"])   # nothing left open, nothing charged
    assert rig.fake.requests == []


def test_a_nas_that_drops_with_the_reservation_open_sends_nothing_and_releases_it(tmp_path):
    """The record frame comes only after the reservation, so a NAS that dies on it leaves one open for certain: the
    gateway releases it and sends nothing to Anthropic."""
    with gateway_rig(tmp_path, ScriptedModel()) as rig:
        with rig.client.connect() as ws:
            ws.send(json.dumps(DROP_START))
            assert json.loads(ws.recv(timeout=10))["type"] == "redacted"
            assert json.loads(ws.recv(timeout=10))["type"] == "record"
            assert ledger_statuses(rig) == ["reserved"]
            ws.socket.close()      # abruptly, before "recorded": no close handshake, no cancel frame
    assert ledger_statuses(rig) == ["released"]   # one reservation, released: not left open, not charged, not missing
    assert rig.fake.requests == []
