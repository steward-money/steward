"""Marking flags (spec 4.2): Expected with decision 1's scope, Handled with a required note and Still checking with an
optional one (decision 7), the note changed, Reopen (ruling 9), and known payees removed. Spec 11: Expected silences
repeats."""
import json

import pytest
from sqlalchemy import select

from steward.db.models import ChangeRecord, WatchKnown
from steward.watch.flags import (
    DONE_RULE, HANDLED_NOTE, KNOWN_GONE, NOT_CHECKING, NOTE_RULE, SCOPE_RULE, FlagRefused, change_note,
    expected_sheet, flag_payee, mark_checking, mark_expected, mark_handled, remove_known, reopen,
)
from steward.watch.payees import Payee
from steward.watch.run import run_watch
from steward.watch.store import add_flag
from tests.watch_helpers import NOW, TODAY, account, flags, history, txn

DENTAL = {"payee": "Brea Family Dental", "payee_key": "BREA FAMILY DENTAL", "method": "card", "amount_cents": 64_000,
          "account": "Joint checking", "posted_at": "2026-10-08"}


def _flag(db, check="large", severity="normal", facts=DENTAL, summary="$640.00 to Brea Family Dental"):
    return add_flag(db, check=check, severity=severity, summary=summary, title=summary, facts=facts, now=NOW,
                    after="sync")


def _row(db, action="flag.status") -> ChangeRecord:
    return db.scalars(select(ChangeRecord).where(ChangeRecord.action == action).order_by(ChangeRecord.id.desc())).first()


def test_expected_for_about_this_amount_adds_a_known_payee(margaret, mdb, mctx):
    f = _flag(mdb)
    out = mark_expected(mdb, mctx, f, {"scope": "about", "note": "Margaret's crown."}, NOW)
    assert out == {"done_line": "Marked Expected. Brea Family Dental, $480 to $800, added to Known payees."}
    assert (f.status, f.note, f.status_by, f.resolved_by, f.resolved_at) == ("expected", "Margaret's crown.",
                                                                            mctx.user_id, mctx.user_id, NOW)
    [k] = mdb.scalars(select(WatchKnown)).all()
    assert (k.payee_key, k.payee_name, k.low_cents, k.high_cents, k.check, k.flag_id, k.note) == (
        "BREA FAMILY DENTAL", "Brea Family Dental", 48_000, 80_000, "large", f.id, "Margaret's crown.")
    row = _row(mdb)
    assert (row.target_type, row.target_id, row.note) == ("flag", f.id, "Margaret's crown.")
    assert json.loads(row.before) == {"status": "open"}
    assert json.loads(row.after) == {"status": "expected", "known": {"add": "Brea Family Dental, $480 to $800"}}


def test_expected_for_any_amount_or_this_one_only(margaret, mdb, mctx):
    a, b = _flag(mdb), _flag(mdb, facts={**DENTAL, "payee": "J. Smith", "payee_key": "J SMITH", "method": "zelle"})
    assert mark_expected(mdb, mctx, a, {"scope": "this"}, NOW) == {"done_line": "Marked Expected."}
    assert mdb.scalars(select(WatchKnown)).all() == []
    assert mark_expected(mdb, mctx, b, {"scope": "any"}, NOW) == {
        "done_line": "Marked Expected. Zelle to J. Smith, any amount, added to Known payees."}
    [k] = mdb.scalars(select(WatchKnown)).all()
    assert (k.low_cents, k.high_cents) == (None, None)


def test_expected_on_a_scam_marks_only_that_flag(margaret, mdb, mctx):
    f = _flag(mdb, check="scam", severity="urgent", summary="$500.00 gift card at CVS Pharmacy",
              facts={**DENTAL, "payee": "CVS Pharmacy", "payee_key": "CVS PHARMACY", "kind": "gift_card"})
    sheet = expected_sheet(f, 50_000)
    assert sheet["scopes"] == [] and sheet["fixed"].startswith("This marks only this flag. Gift cards, wires")
    mark_expected(mdb, mctx, f, {"scope": "any"}, NOW)
    assert f.status == "expected" and mdb.scalars(select(WatchKnown)).all() == []


def test_the_expected_sheet_for_a_large_amount():
    from steward.db.models import Flag
    f = Flag(check="large", severity="normal", status="open", summary="", title="", facts=json.dumps(DENTAL))
    sheet = expected_sheet(f, 50_000)
    assert [(s["key"], s["title"], s["line"]) for s in sheet["scopes"]] == [
        ("about", "Not for about this amount", "Brea Family Dental, $480 to $800. A bigger payment is still flagged."),
        ("any", "Not for any amount", "Brea Family Dental isn't flagged again as a new payee, a large amount, unusual "
                                      "or a new monthly charge."),
        ("this", "Yes, this one only", "The next payment to Brea Family Dental of $500 or more is flagged again."),
    ]
    assert sheet["fixed"] is None


def test_the_flags_payee_is_its_rows_payee_now_and_the_facts_only_without_one(margaret, mdb, mctx):
    """flag_payee: the payee a later payment gives (public issue #49 moved Wells Fargo's keys); the facts serve when
    the row is gone, and a flag with no payee key (a numbered check) stays without one."""
    chk = account(mdb)
    t = txn(mdb, chk, 2, -64_000, "PURCHASE AUTHORIZED ON 10/17 BREA FAMILY DENTAL BREA CA S382074123456789 CARD 1234")
    old = {**DENTAL, "payee": "Authorized On", "payee_key": "AUTHORIZED ON"}
    f = add_flag(mdb, check="large", severity="normal", summary="", title="", facts=old, now=NOW, after="sync",
                 transaction_id=t.id)
    assert flag_payee(mdb, f) == Payee("BREA FAMILY DENTAL", "Brea Family Dental", "card")
    paper = txn(mdb, chk, 1, -64_000, "CHECK 1234", merchant="Brea Family Dental")
    numbered = add_flag(mdb, check="large", severity="normal", summary="", title="", now=NOW, after="sync",
                        facts={**DENTAL, "payee": "Check 1234", "payee_key": ""}, transaction_id=paper.id)
    assert flag_payee(mdb, numbered).key == ""
    mdb.delete(t)
    mdb.flush()
    assert flag_payee(mdb, f) == Payee("AUTHORIZED ON", "Authorized On", "card")


def test_refusals(margaret, mdb, mctx):
    f = _flag(mdb)
    with pytest.raises(FlagRefused, match=SCOPE_RULE):
        mark_expected(mdb, mctx, f, {"scope": "never"}, NOW)
    with pytest.raises(FlagRefused, match=HANDLED_NOTE):
        mark_handled(mdb, mctx, f, {"note": "  "}, NOW)
    with pytest.raises(FlagRefused, match=NOTE_RULE):
        mark_checking(mdb, mctx, f, {"note": "x" * 301}, NOW)
    with pytest.raises(FlagRefused, match=NOT_CHECKING) as e:
        change_note(mdb, mctx, f, {"note": "later"}, NOW)
    assert e.value.status == 409
    mark_handled(mdb, mctx, f, {"note": "Called the dentist; it's right."}, NOW)
    with pytest.raises(FlagRefused, match=DONE_RULE):
        mark_checking(mdb, mctx, f, {}, NOW)


def test_still_checking_and_changing_the_note(margaret, mdb, mctx):
    f = _flag(mdb)
    mark_checking(mdb, mctx, f, {"note": "Calling Margaret this morning."}, NOW)
    assert (f.status, f.note, f.resolved_at) == ("checking", "Calling Margaret this morning.", None)
    change_note(mdb, mctx, f, {"note": "Left a message."}, NOW)
    row = _row(mdb, "flag.note")
    assert json.loads(row.before) == {"note": "Calling Margaret this morning."}
    assert json.loads(row.after) == {"note": "Left a message."} and row.note == "Left a message."
    mark_checking(mdb, mctx, f, {"note": "Waiting to hear back."}, NOW)      # again: just a new note
    assert _row(mdb, "flag.note").note == "Waiting to hear back."


def test_reopen_removes_the_known_payee_it_added(margaret, mdb, mctx):
    f = _flag(mdb)
    mark_expected(mdb, mctx, f, {"scope": "about"}, NOW)
    assert reopen(mdb, mctx, f, NOW) == {"done_line": "Reopened."}
    assert (f.status, f.resolved_at, f.resolved_by, f.note) == ("open", None, None, None)
    k = mdb.scalar(select(WatchKnown))
    assert k.removed_at == NOW and k.removed_by == mctx.user_id
    assert json.loads(_row(mdb).after) == {"status": "open", "known": {"remove": "Brea Family Dental, $480 to $800"}}
    assert json.loads(_row(mdb).before) == {"status": "expected"}


def test_removing_a_known_payee(margaret, mdb, mctx):
    f = _flag(mdb, facts={**DENTAL, "payee": "K. Nguyen", "payee_key": "K NGUYEN", "method": "zelle"})
    mark_expected(mdb, mctx, f, {"scope": "any"}, NOW)
    k = mdb.scalar(select(WatchKnown))
    remove_known(mdb, mctx, k, {"note": "Paid in full for the fence."}, NOW)
    assert k.removed_at == NOW
    row = _row(mdb, "watch.known_remove")
    assert json.loads(row.before) == {"known": "Zelle to K. Nguyen, any amount"} and row.target_id == k.id
    assert f.status == "expected"                                            # the flag stays Done
    with pytest.raises(FlagRefused, match=KNOWN_GONE):
        remove_known(mdb, mctx, k, {}, NOW)


def test_expected_silences_repeats_and_reopen_brings_them_back(margaret, mdb, mctx):
    a = account(mdb)
    history(mdb, a)
    txn(mdb, a, 100, -1000, "BREA FAMILY DENTAL", category="health", watched=True)
    txn(mdb, a, 11, -64_000, "BREA FAMILY DENTAL", category="health")
    run_watch(mdb, TODAY, NOW, after="sync")
    [f] = flags(mdb)
    mark_expected(mdb, mctx, f, {"scope": "about"}, NOW)
    txn(mdb, a, 3, -70_000, "BREA FAMILY DENTAL", category="health")
    run_watch(mdb, TODAY, NOW, after="sync")
    assert len(flags(mdb)) == 1
    reopen(mdb, mctx, f, NOW)
    txn(mdb, a, 1, -71_000, "BREA FAMILY DENTAL", category="health")
    run_watch(mdb, TODAY, NOW, after="sync")
    assert [x.check for x in flags(mdb)] == ["large", "large"]
