"""Marking flags (spec 4.2; decisions 1, 7 and 9; rulings 9 and 10): Expected, Handled, Still checking, a changed
note and Reopen, plus removing a known payee. Every action writes the change record (spec 5). Callers commit."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from steward.db.models import Flag, Transaction, WatchKnown
from steward.inventory.changes import record
from steward.inventory.present import mask_numbers, short_usd
from steward.watch import vocab as V
from steward.watch.payees import Payee, known_label, payee
from steward.watch.store import facts_of

NO_SUCH_FLAG = "No such flag."
NOTE_RULE = f"Keep the note under {V.NOTE_MAX} characters."
HANDLED_NOTE = "Add a note saying what was done."
SCOPE_RULE = "Choose whether to flag this payee again."
DONE_RULE = "This flag is done. Reopen it to change it."
NOT_DONE_RULE = "This flag isn't done."
NOT_CHECKING = "Only a flag someone is checking has a note to change."
KNOWN_GONE = "That known payee was already removed."

FIXED = {
    "scam": "This marks only this flag. Gift cards, wires and money transfers are always flagged, whoever they're "
            "paid to.",
    "duplicate": "This marks only this flag. Duplicates are always flagged.",
    "deposit": "This marks only this flag. A missing deposit or a missed premium is always flagged.",
    "premium": "This marks only this flag. A missing deposit or a missed premium is always flagged.",
    "cash": "This marks only this flag. Cash withdrawals over the usual are always flagged.",
    "account": "This marks only this flag.",
    "low": "This marks only this flag. The balance is checked again after each sync.",
}


class FlagRefused(ValueError):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def band(cents: int) -> tuple[int, int]:
    """Decision 1: ±25% of the amount."""
    return cents * (100 - V.BAND_PCT) // 100, cents * (100 + V.BAND_PCT) // 100


def range_text(low: int | None, high: int | None) -> str:
    return "any amount" if low is None or high is None else f"{short_usd(low)} to {short_usd(high)}"


def silenceable(f: Flag) -> bool:
    return f.check in V.SILENCED and bool(facts_of(f).get("payee_key"))


def flag_payee(db: Session, f: Flag, facts: dict | None = None) -> Payee:
    """Who the flag is about, read from its transaction as it is now: the payee a later payment to them gives, which
    is what a Known payee must hold to cover it. The facts keep the payee as payee() read it when the flag was raised.
    That goes stale when the parser changes (public issue #49 turned "AUTHORIZED ON" into each store's own key) or
    when the row is named in Review since, so the facts serve only when the transaction is gone or has no payee."""
    facts = facts_of(f) if facts is None else facts
    kept = Payee(facts.get("payee_key") or "", facts.get("payee") or "", facts.get("method", "card"))
    if not kept.key or f.transaction_id is None:
        return kept      # no payee to compare (a numbered check, ruling 14), or not a payment's flag
    t = db.get(Transaction, f.transaction_id)
    now = payee(t.raw_description, t.merchant) if t is not None else None
    return now if now is not None and now.key else kept


def expected_sheet(f: Flag, large_cents: int, p: Payee | None = None) -> dict:
    """What the Mark Expected sheet offers (README screen 4; decision 1; ruling 8). `p` is flag_payee(), the payee
    a Known entry would hold; without it, the one in the flag's facts."""
    facts = facts_of(f)
    name = mask_numbers(p.name if p is not None else facts.get("payee", ""))
    if not silenceable(f):
        help_ = ("Nothing wrong. The flag moves to Done. Payments like this are still flagged." if f.check == "scam"
                 else "Nothing wrong. The flag moves to Done.")
        # a silenced check with no payee name (a numbered check, ruling 14) can only mark this flag
        fixed = FIXED["account"] if f.check in V.SILENCED else FIXED[f.check]
        return {"scopes": [], "fixed": fixed, "help": help_}
    low, high = band(facts["amount_cents"])
    this_line = (f"The next payment to {name} of {short_usd(large_cents)} or more is flagged again." if f.check == "large"
                 else f"Only this flag is marked. Later payments to {name} are checked as usual.")
    return {
        "scopes": [
            {"key": "about", "title": "Not for about this amount",
             "line": f"{name}, {range_text(low, high)}. A bigger payment is still flagged."},
            {"key": "any", "title": "Not for any amount",
             "line": f"{name} isn't flagged again as a new payee, a large amount, unusual or a new monthly charge."},
            {"key": "this", "title": "Yes, this one only", "line": this_line},
        ],
        "fixed": None,
        "help": f"Nothing wrong. The flag moves to Done. You choose whether {name} is flagged again.",
    }


def _note(body: dict, required: bool = False) -> str | None:
    raw = body.get("note")
    if raw is not None and not isinstance(raw, str):
        raise FlagRefused(NOTE_RULE)
    text = (raw or "").strip()
    if len(text) > V.NOTE_MAX:
        raise FlagRefused(NOTE_RULE)
    if required and not text:
        raise FlagRefused(HANDLED_NOTE)
    return text or None


def _open(f: Flag) -> None:
    if f.status in V.DONE:
        raise FlagRefused(DONE_RULE, 409)


def _mark(db: Session, ctx, f: Flag, status: str, note: str | None, now: datetime, known: str | None = None) -> None:
    old = f.status
    f.status, f.note, f.status_by, f.status_at = status, note, ctx.user_id, now
    if status in V.DONE:
        f.resolved_at, f.resolved_by = now, ctx.user_id
    after: dict = {"status": status}
    if known:
        after["known"] = {"add": known}
    record(db, ctx, "flag.status", target_id=f.id, before={"status": old}, after=after, note=note)
    db.flush()


def mark_expected(db: Session, ctx, f: Flag, body: dict, now: datetime) -> dict:
    _open(f)
    note = _note(body)
    scope = body.get("scope", "about") if silenceable(f) else "this"      # ledger R5: the default is ±25%
    if scope not in ("about", "any", "this"):
        raise FlagRefused(SCOPE_RULE)
    known = None
    if scope != "this":
        facts = facts_of(f)
        p = flag_payee(db, f, facts)
        low, high = band(facts["amount_cents"]) if scope == "about" else (None, None)
        k = WatchKnown(payee_key=p.key, payee_name=known_label(p), low_cents=low, high_cents=high, check=f.check,
                       flag_id=f.id, added_at=now, added_by=ctx.user_id, note=note)
        db.add(k)
        known = f"{k.payee_name}, {range_text(low, high)}"
    _mark(db, ctx, f, "expected", note, now, known)
    return {"done_line": f"Marked Expected. {known}, added to Known payees." if known else "Marked Expected."}


def mark_handled(db: Session, ctx, f: Flag, body: dict, now: datetime) -> None:
    _open(f)
    _mark(db, ctx, f, "handled", _note(body, required=True), now)


def mark_checking(db: Session, ctx, f: Flag, body: dict, now: datetime) -> None:
    _open(f)
    if f.status == "checking":
        change_note(db, ctx, f, body, now)
        return
    _mark(db, ctx, f, "checking", _note(body), now)


def change_note(db: Session, ctx, f: Flag, body: dict, now: datetime) -> None:
    if f.status != "checking":
        raise FlagRefused(NOT_CHECKING, 409)
    note = _note(body)
    old = f.note
    f.note, f.status_by, f.status_at = note, ctx.user_id, now
    record(db, ctx, "flag.note", target_id=f.id, before={"note": old}, after={"note": note}, note=note)
    db.flush()


def reopen(db: Session, ctx, f: Flag, now: datetime) -> dict:
    """A Done flag back to Open (ruling 9); Undo after Expected is the same. The known payee it added is removed."""
    if f.status not in V.DONE:
        raise FlagRefused(NOT_DONE_RULE, 409)
    old = f.status
    after: dict = {"status": "open"}
    k = db.scalar(select(WatchKnown).where(WatchKnown.flag_id == f.id, WatchKnown.removed_at.is_(None)))
    if k is not None:
        k.removed_at, k.removed_by = now, ctx.user_id
        after["known"] = {"remove": f"{k.payee_name}, {range_text(k.low_cents, k.high_cents)}"}
    f.status, f.note, f.status_by, f.status_at = "open", None, ctx.user_id, now
    f.resolved_at, f.resolved_by = None, None
    record(db, ctx, "flag.status", target_id=f.id, before={"status": old}, after=after)
    db.flush()
    return {"done_line": "Reopened."}


def remove_known(db: Session, ctx, k: WatchKnown, body: dict, now: datetime) -> None:
    if k.removed_at is not None:
        raise FlagRefused(KNOWN_GONE, 409)
    note = _note(body)
    k.removed_at, k.removed_by = now, ctx.user_id
    record(db, ctx, "watch.known_remove", target_id=k.id,
           before={"known": f"{k.payee_name}, {range_text(k.low_cents, k.high_cents)}"}, note=note)
    db.flush()
