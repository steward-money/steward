"""Same-account spec, Asking and the one-time check: "They're different" makes a held feed a normal new account with
its history, or remembers a pair; either can be undone; neither is asked again. The check raises each pair already in
the book once, after the upgrade that brought feeds, or from the CLI."""
import json
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from steward.db.models import (Account, AccountFeed, AccountType, BalanceSnapshot, EventLog, HeldFeed,
                               SameAccountAnswer, SimplefinLink, Transaction, UndoEntry, UndoText, WatchState)
from steward.events import log_event
from steward.services.same_account import apart, journal, questions
from steward.services.undo import CHANGED_SINCE, UndoRefused, undo
from tests.fake_simplefin import make_fake
from tests.same_account_fixtures import (AT_NEW, BALANCE, BALANCE_DAY, LATER, NOW, bank, booked, feed_account,
                                         held_copy, in_book_pair, jr_account, jr_login, linked, sam_login, snapshot,
                                         sync)

pytestmark = pytest.mark.usefixtures("undo_entries_hold_no_text")   # P36: no note or name in an undo entry

AT = datetime(2026, 10, 28, 17, 0, tzinfo=timezone.utc)
runner = CliRunner()


def test_keeping_a_held_feed_apart_makes_it_an_account_with_its_history(db):
    keep = jr_account(db)
    h = held_copy(db, keep)
    institution_id = h.institution_id                       # the held row is gone once it is answered
    token = apart.keep_apart(db, h, 1, AT)
    db.commit()
    acct = feed_account(db, "sfin:sam-joint")
    assert (acct.name, acct.type, acct.owner, acct.external_id, acct.institution_id) == (
        "Joint Taxable", AccountType.unknown, "joint", "sfin:sam-joint", institution_id)
    assert sorted(db.scalars(select(Transaction.external_id).where(Transaction.account_id == acct.id))) == [
        f"sam-{i}" for i in range(5)]
    assert db.get(BalanceSnapshot, (acct.id, BALANCE_DAY)).balance_cents == BALANCE
    e = db.scalar(select(EventLog).where(EventLog.actionable.is_(True)))
    assert e.message == "New account 'Joint Taxable' from Betterment needs its type and owner set in Settings"
    a = db.scalar(select(SameAccountAnswer))
    assert (a.account_id, a.other_account_id, a.held_external_id, a.answer, a.answered_by, a.answered_at) == (
        keep.id, acct.id, "sfin:sam-joint", "different", 1, AT)
    assert db.scalars(select(HeldFeed)).all() == [] and token


def test_a_held_feed_kept_apart_is_never_asked_about_again(db):
    app, state = make_fake(); jr_login(state)
    with TestClient(app) as http:
        link = linked(db, http)
        sync(db, http, link)
        sam_login(state)
        sync(db, http, link, LATER)
        apart.keep_apart(db, db.scalar(select(HeldFeed)), 1, AT)
        db.commit()
        res = sync(db, http, link, AT + timedelta(hours=1))
    assert res.accounts_held == 0 and db.scalars(select(HeldFeed)).all() == []
    assert db.scalar(select(func.count()).select_from(Account).where(Account.name == "Joint Taxable")) == 2
    assert apart.check_book(db, AT) == 0                       # the pair it became is remembered too


def _lines(db) -> list[tuple]:
    return [(e.id, e.at, e.level, e.kind, e.message, e.actionable)
            for e in db.scalars(select(EventLog).order_by(EventLog.id))]


def test_undo_of_keeping_a_held_feed_apart_holds_it_again(db):
    """Undo never deletes an event_log row (audit log): the "New account" line stays, resolved, because the account it
    asked about went with the Undo. Every other row is as it was, and nothing is left open that wasn't."""
    keep = jr_account(db)
    h = held_copy(db, keep)
    log_event(db, "warn", "sync", "Big Bank needs you to sign in again at SimpleFIN", actionable=True)
    db.commit()
    before, open_before = snapshot(db), _open_lines(db)
    token = apart.keep_apart(db, h, 1, AT)
    db.commit()
    lines = _lines(db)
    assert len(lines) == len(before["event_log"]) + 1 and lines[-1][4].startswith("New account 'Joint Taxable'")
    entry = db.get(UndoEntry, token)
    assert "New account" not in entry.before + entry.after              # the line is kept by its id only
    undo(db, token, 1, AT + timedelta(minutes=5))
    db.commit()
    after = snapshot(db)
    assert {k: v for k, v in after.items() if k != "event_log"} == {k: v for k, v in before.items() if k != "event_log"}
    assert _lines(db) == lines                                           # every log line is still there
    assert db.get(EventLog, lines[-1][0]).resolved_at == AT + timedelta(minutes=5)
    assert _open_lines(db) == open_before
    assert db.scalar(select(HeldFeed.external_id)) == "sfin:sam-joint"   # asked again


def _open_lines(db) -> list[int]:
    return list(db.scalars(select(EventLog.id).where(EventLog.resolved_at.is_(None)).order_by(EventLog.id)))


def test_undo_of_an_entry_that_kept_its_line_as_made_still_keeps_the_line(db):
    """An entry written while the journal counted a log line as a row it made (before["made"] names it) keeps the
    line through Undo too, and resolves it."""
    keep = jr_account(db)
    token = apart.keep_apart(db, held_copy(db, keep), 1, AT)
    db.commit()
    line = db.scalar(select(EventLog).where(EventLog.message.startswith("New account")))
    entry = db.get(UndoEntry, token)
    before, after = json.loads(entry.before), json.loads(entry.after)
    before.pop("logged")
    before["made"].append(["event_log", f"[{line.id}]"])
    after["made"]["event_log"] = {f"[{line.id}]": {"id": line.id, "resolved_at": None}}
    entry.before, entry.after = json.dumps(before), json.dumps(after)
    db.commit()
    undo(db, token, 1, AT + timedelta(minutes=5))
    db.commit()
    assert db.get(EventLog, line.id).resolved_at == AT + timedelta(minutes=5)
    assert db.scalar(select(func.count()).select_from(Account).where(Account.name == "Joint Taxable")) == 1


ZORBLAX = "Sam's Zorblax brokerage"       # a name planted on the held feed, so on the account it becomes


def _texts(db, token) -> list[tuple]:
    return sorted((x.model, x.row_key, x.field, x.value, x.written)
                  for x in db.scalars(select(UndoText).where(UndoText.token == token)))


def _kept_apart_with_a_planted_name(db) -> tuple[int, str]:
    h = held_copy(db, jr_account(db))
    h.name = ZORBLAX
    db.commit()
    hid, token = h.id, apart.keep_apart(db, h, 1, AT)
    db.commit()
    return hid, token


def test_the_account_they_re_different_makes_is_journaled_without_its_name(db):
    """P36: no name enters undo_entry. The account it made is kept by its id (and what Undo checks besides); its name,
    and the held feed's it deletes, go to undo_text. Undo still puts the book back exactly."""
    keep = jr_account(db)
    h = held_copy(db, keep)
    h.name = ZORBLAX
    db.commit()
    hid, before = h.id, snapshot(db)
    token = apart.keep_apart(db, h, 1, AT)
    db.commit()
    acct = feed_account(db, "sfin:sam-joint")
    entry = db.get(UndoEntry, token)
    assert acct.name == ZORBLAX and "Zorblax" not in entry.before + entry.after
    for table, cols in journal.TEXT.items():     # no text column's value of any row it made or deleted
        for k in json.loads(entry.after)["made"].get(table, {}).values():
            assert not set(k) & set(cols)
        for k in json.loads(entry.before)["gone"].get(table, {}).values():
            assert not set(k) & set(cols)
    assert _texts(db, token) == [("account", f"[{acct.id}]", "name", None, ZORBLAX),
                                 ("held_feed", f"[{hid}]", "name", ZORBLAX, None)]
    undo(db, token, 1, AT + timedelta(minutes=5))
    db.commit()
    assert {k: v for k, v in snapshot(db).items() if k != "event_log"} == {
        k: v for k, v in before.items() if k != "event_log"}
    assert db.scalar(select(func.count()).select_from(Account).where(Account.name == ZORBLAX)) == 0
    assert db.get(HeldFeed, hid).name == ZORBLAX


def test_undo_refuses_once_the_account_they_re_different_made_was_renamed(db):
    _, token = _kept_apart_with_a_planted_name(db)
    feed_account(db, "sfin:sam-joint").name = "Sam's brokerage"
    db.commit()
    before = snapshot(db)
    with pytest.raises(UndoRefused) as e:
        undo(db, token, 1, AT + timedelta(minutes=5))
    assert (e.value.status, e.value.message) == (409, CHANGED_SINCE)
    db.rollback()
    assert snapshot(db) == before


def test_an_entry_written_with_the_names_inside_still_undoes(db):
    """An entry the journal wrote before names went to undo_text: the made account's name in after["made"], the held
    feed's in its deleted row, and the "New account" line as a row it made, message and all. Undo still removes the
    account, brings the held feed back as it was and keeps the line."""
    keep = jr_account(db)
    h = held_copy(db, keep)
    h.name = ZORBLAX
    db.commit()
    hid, before = h.id, snapshot(db)
    token = apart.keep_apart(db, h, 1, AT)
    db.commit()
    acct = feed_account(db, "sfin:sam-joint")
    line = db.scalar(select(EventLog).where(EventLog.message.startswith("New account")))
    entry = db.get(UndoEntry, token)
    old_b, old_a = json.loads(entry.before), json.loads(entry.after)
    old_b.pop("made_texts")
    old_b.pop("logged")
    old_b["made"].append(["event_log", f"[{line.id}]"])
    old_b["gone"]["held_feed"][f"[{hid}]"]["name"] = ZORBLAX
    old_a["made"]["account"][f"[{acct.id}]"]["name"] = ZORBLAX
    old_a["made"]["event_log"] = {f"[{line.id}]": {"id": line.id, "message": line.message, "resolved_at": None}}
    entry.before, entry.after = json.dumps(old_b), json.dumps(old_a)
    db.execute(delete(UndoText).where(UndoText.token == token))
    db.commit()
    undo(db, token, 1, AT + timedelta(minutes=5))
    db.commit()
    assert {k: v for k, v in snapshot(db).items() if k != "event_log"} == {
        k: v for k, v in before.items() if k != "event_log"}
    assert db.get(EventLog, line.id).resolved_at == AT + timedelta(minutes=5)


def test_an_old_entry_with_the_name_inside_still_refuses_once_the_account_was_renamed(db):
    _, token = _kept_apart_with_a_planted_name(db)
    acct = feed_account(db, "sfin:sam-joint")
    entry = db.get(UndoEntry, token)
    old_b, old_a = json.loads(entry.before), json.loads(entry.after)
    old_b.pop("made_texts")
    old_a["made"]["account"][f"[{acct.id}]"]["name"] = ZORBLAX
    entry.before, entry.after = json.dumps(old_b), json.dumps(old_a)
    db.execute(delete(UndoText).where(UndoText.token == token, UndoText.model == "account"))
    acct.name = "Sam's brokerage"
    db.commit()
    with pytest.raises(UndoRefused) as e:
        undo(db, token, 1, AT + timedelta(minutes=5))
    assert (e.value.status, e.value.message) == (409, CHANGED_SINCE)


def test_keeping_a_pair_apart_is_remembered_and_undo_asks_again(db):
    keep, other = in_book_pair(db)
    assert apart.check_book(db, AT) == 1
    q = db.scalar(select(SameAccountAnswer))
    assert (q.account_id, q.other_account_id, q.answer, q.match_count, q.created_at) == (keep.id, other.id, "open", 5, AT)
    token = apart.keep_apart(db, q, 1, AT)
    db.commit()
    db.refresh(q)
    assert (q.answer, q.answered_by) == ("different", 1) and questions.open_count(db) == 0
    assert apart.check_book(db, AT) == 0
    undo(db, token, 1, AT + timedelta(minutes=5))
    db.commit()
    db.refresh(q)
    assert (q.answer, q.answered_at) == ("open", None) and questions.open_count(db) == 1


def test_the_check_raises_each_pair_once(db):
    in_book_pair(db)
    assert apart.check_book(db, AT) == 1
    assert apart.check_book(db, AT) == 0
    assert db.scalar(select(func.count()).select_from(SameAccountAnswer)) == 1


def test_the_check_skips_pairs_on_one_login(db):
    """Review Focus 4: two 529s with one name and the same deposits, on one login, are two accounts."""
    inst = bank(db, "jr")
    booked(db, "kid1", inst, NOW, name="529 Plan")
    booked(db, "kid2", inst, AT_NEW, name="529 Plan")
    assert apart.check_book(db, AT) == 0


def test_the_check_skips_other_names_inactive_accounts_and_different_money(db):
    keep, other = in_book_pair(db)
    other.is_active = False
    db.commit()
    assert apart.check_book(db, AT) == 0
    other.is_active = True
    other.name = db.scalar(select(AccountFeed).where(AccountFeed.account_id == other.id)).name = "Sam's brokerage"
    db.commit()
    assert apart.check_book(db, AT) == 0
    a, b = in_book_pair(db, keys=("kim", "lee"), name="529 Plan",
                        other_days=[(date(2026, 10, 22), 400_00, "CONTRIBUTION"), (date(2026, 10, 25), 7_90, "DIVIDEND")],
                        other_balance=(BALANCE_DAY, 30_500_00))
    assert apart.check_book(db, AT) == 0


def test_the_check_runs_once_after_the_upgrade_that_brought_feeds(db, engine):
    in_book_pair(db)
    assert apart.check_once(engine, None, AT) == 0          # a new book: nothing to look at, only marked
    if (mark := db.get(WatchState, apart.CHECK_KEY)) is not None:
        db.delete(mark)                                        # a book from before feeds has no mark
    db.commit()
    assert apart.check_once(engine, "0026", AT) == 1
    assert apart.check_once(engine, "0027", AT) == 0        # already ran
    db.rollback()
    assert questions.open_count(db) == 1


def test_start_up_hands_the_check_each_household_books_revision_from_before_the_upgrade(books, monkeypatch):
    seen = []
    monkeypatch.setattr("steward.services.same_account.apart.check_once",
                        lambda engine, was, now: seen.append(was) or 0)
    books.upgrade_all()
    assert seen == ["0038"]                                 # ours, already at the head


def test_questions_are_found_by_their_ids_only_while_open(db):
    keep = jr_account(db)
    h = held_copy(db, keep)
    assert questions.get(db, f"held-{h.id}") is h
    for bad in ("held-999", f"pair-{h.id}", "nope", "held-", "held-²", f"held-{h.id}-1", "held-" + "9" * 40):
        assert questions.get(db, bad) is None, bad
    h.candidate_account_id = None
    db.commit()
    assert questions.get(db, f"held-{h.id}") is None and questions.open_count(db) == 0


def test_the_cli_counts_the_open_questions_and_checks_again(settings):
    from steward.books.registry import open_books
    from steward.cli import app
    assert runner.invoke(app, ["init-key"]).exit_code == 0
    assert runner.invoke(app, ["migrate"]).exit_code == 0
    books = open_books(settings)
    with Session(books.engine_for(books.book("ours"))) as s:
        in_book_pair(s)
    books.dispose()
    for args, out in ((["same-accounts"], "ours: no questions open.\n"),
                      (["same-accounts", "--check"], "ours: 1 pair looks like the same account. It's on Home.\n"),
                      (["same-accounts"], "ours: 1 question open.\n"),
                      (["same-accounts", "--check"], "ours: nothing looks like the same account.\n")):
        r = runner.invoke(app, args)
        assert r.exit_code == 0 and out in r.output, (args, r.output)


def _synced_at(db, at=NOW) -> None:
    """The book's latest sync: a feed seen then is live."""
    db.add(SimplefinLink(access_url="https://demo:demo@testserver/simplefin", claimed_at=at, last_sync_at=at))
    db.commit()


def _feed(db, account: Account) -> AccountFeed:
    return db.scalar(select(AccountFeed).where(AccountFeed.account_id == account.id))


def test_the_check_counts_a_name_given_in_the_book(db):
    """Rule 2 between two accounts in the book: one's SimpleFIN name against the other's feed name or its own name.
    JR renamed his old 529 "Ava's 529" (migration 0027 filled its feed name from that), then named the new one the
    same."""
    keep, other = in_book_pair(db, name="529 Plan")
    keep.name = _feed(db, keep).name = "Ava's 529"
    db.commit()
    assert apart.check_book(db, AT) == 0
    other.name = "Ava's 529"
    db.commit()
    assert apart.check_book(db, AT) == 1


def test_the_check_keeps_two_live_accounts_with_different_balances_apart(db):
    """Rule 3: both feeds were seen in the latest sync and their balances differ, so the rows that coincide (two kids'
    529s with the same contributions) don't make them one."""
    _synced_at(db)
    in_book_pair(db, name="my529 Account", other_balance=(BALANCE_DAY, BALANCE - 40_000_00))
    assert apart.check_book(db, AT) == 0


def test_the_check_asks_about_a_reconnect_whose_old_feed_went_quiet(db):
    _synced_at(db)
    keep, _ = in_book_pair(db, other_balance=(BALANCE_DAY, BALANCE + 5_000_00))
    _feed(db, keep).last_seen_at = NOW - timedelta(days=20)
    db.commit()
    assert apart.check_book(db, AT) == 1


def test_the_check_asks_about_a_joint_account_two_live_logins_send(db):
    _synced_at(db)
    in_book_pair(db, other_balance=(BALANCE_DAY, BALANCE + 50))                  # within $1
    assert apart.check_book(db, AT) == 1


def test_a_sync_asks_about_a_renamed_account_once_its_feed_shows_the_banks_name(db):
    """Migration 0027 filled a feed's name from the account's, which is JR's rename. The next sync puts the bank's
    name back on the feed, and then the pair is checked, without waiting for steward same-accounts --check."""
    keep, other = in_book_pair(db)
    keep.name = _feed(db, keep).name = "Our Joint"
    db.commit()
    assert apart.check_book(db, AT) == 0
    app, state = make_fake(); jr_login(state); sam_login(state)
    with TestClient(app) as http:
        link = linked(db, http)
        res = sync(db, http, link, LATER)
        assert res.pairs_raised == 1
        q = db.scalar(select(SameAccountAnswer))
        assert (q.account_id, q.other_account_id, q.answer, q.created_at) == (keep.id, other.id, "open", LATER)
        apart.keep_apart(db, q, 1, AT)
        db.commit()
        _feed(db, keep).name = "Our Joint"                    # the name changes again: still "they're different"
        db.commit()
        res = sync(db, http, link, LATER + timedelta(days=1))
    assert res.pairs_raised == 0 and questions.open_count(db) == 0


def test_a_sync_checks_only_the_accounts_whose_name_changed_or_that_are_new(db):
    """The check after a sync is cheap: a pair it didn't touch waits for steward same-accounts --check."""
    in_book_pair(db)
    app, state = make_fake(); jr_login(state); sam_login(state)
    with TestClient(app) as http:
        res = sync(db, http, linked(db, http), LATER)
    assert res.pairs_raised == 0 and questions.open_count(db) == 0


def test_a_failing_check_after_a_sync_never_loses_the_sync(db, monkeypatch):
    def broken(*a, **k):
        db.add(SameAccountAnswer(account_id=1, other_account_id=2, answer="open", match_count=0, created_at=AT))
        db.flush()
        raise RuntimeError("boom")
    monkeypatch.setattr(apart, "check_book", broken)
    app, state = make_fake(); jr_login(state)
    with TestClient(app) as http:
        res = sync(db, http, linked(db, http), LATER)
    assert (res.accounts_new, res.pairs_raised) == (2, 0)
    assert db.scalar(select(func.count()).select_from(Account)) == 2
    assert db.scalars(select(SameAccountAnswer)).all() == []          # what it wrote is rolled back
