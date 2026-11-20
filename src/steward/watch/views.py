"""What the flag screens read (README screens 1-3 and 7): rows for Home and Look into, the flag page and its History,
and when the checks last ran. Names come from the directory at read time; a book stores only ids (spec 2.2).
No account number is ever shown: account names go through clean_account (spec 4.3)."""
from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime, timedelta

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from steward.books.history import names_for
from steward.books.people import mid_sentence
from steward.db.models import Account, ChangeRecord, Flag, FlagEvent, InventoryItem, Transaction, WatchKnown
from steward import clock
from steward.inventory.present import (
    at_label, clean_account, joined, mask, mask_numbers, ordinal, parent_name, safe_line, usd, when_label,
)
from steward.inventory.views import item_row, load
from steward.watch import vocab as V
from steward.watch.checks import day
from steward.watch.due import expectations, payments
from steward.watch.flags import expected_sheet, flag_payee, range_text
from steward.watch.payees import payee, scam_kind
from steward.watch.store import LAST_RUN, also_of, facts_of, get_state, large_cents

PAGE = 50
AFTER_WORDS = {"sync": "after the bank feed sync", "import": "after an import", "daily": "in the morning check",
               "floor": "when the floor was set"}
VIEWER_LINE = "Only managers can mark flags. Emails for this book are set in Settings → Watching."
EXPECTED_NOTE = {
    "premium": 'Expected here means "not due this month". If the premium changed (paid yearly, a new amount, a new '
               'account), edit the item so the check looks for the right payment.',
    "deposit": 'Expected here means "not due this month". If the deposit changed (a new amount, a new day, a new '
               'account), edit the item so the check looks for the right deposit.',
}


def local_day(t: datetime) -> date:
    return t.astimezone(clock.household_tz()).date()


def weekday(d: date) -> str:
    return f"{d:%a}, {d:%b} {d.day}"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def scrub(text: str, facts: dict, account: str | None = None, clean=mask_numbers) -> str:
    """The text with each name the flag kept from the bank replaced by its safe form: the account by its number-free
    name (or `account`), the payee, item, label and bank text by `clean`. The rest of a summary is the app's own
    wording and structured amounts and dates, so nothing else is rewritten."""
    for key in ("account", "item", "label", "payee", "raw"):
        raw = facts.get(key)
        if raw and isinstance(raw, str):
            safe = (account if account is not None and key == "account" else
                    clean_account(raw) if key != "payee" and key != "raw" else clean(raw))
            if safe != raw:
                text = text.replace(raw, safe)
    return safe_line(text)


def _m(text):
    return mask_numbers(text) if isinstance(text, str) else text


def ran(db: Session, today: date) -> dict | None:
    raw = get_state(db, LAST_RUN)
    if not raw:
        return None
    r = json.loads(raw)
    at = datetime.fromisoformat(r["at"])
    after = AFTER_WORDS.get(r["after"], "")
    return {"short": f"Checked {when_label(at, today)}", "line": f"The checks ran {at_label(at, today)}, {after}.",
            "home_empty": f"checked {when_label(at, today)}, {after}",
            "empty": f"The checks ran {at_label(at, today)} over {_plural(r['transactions'], 'new transaction')} in "
                     f"{_plural(r['accounts'], 'account')}."}


def flag_row(f: Flag, name: str, names: dict[int, str], today: date) -> dict:
    facts = facts_of(f)
    done = f.status in V.DONE
    who = names.get(f.status_by) if f.status_by is not None else None
    if f.check in ("deposit", "premium"):
        when, place = (f"Due {weekday(f.due_on)}" if f.due_on else ""), facts.get("item") or facts.get("account", "")
    elif f.check == "low":
        when, place = f"Since {weekday(date.fromisoformat(facts['as_of']))}", facts.get("account", "")
    elif facts.get("posted_at"):
        when, place = weekday(date.fromisoformat(facts["posted_at"])), facts.get("account", "")
    else:
        when, place = weekday(local_day(f.created_at)), facts.get("account", "")
    by = None
    if f.status == "checking" or done:
        on = day(local_day(f.status_at or f.resolved_at or f.created_at))
        by = f"{who}, {on}" if who else f"the checks, {on}" if f.status == "arrived" else None
    return {
        "id": f.id, "check": f.check, "check_label": V.check_label(f.check, name), "icon": V.CHECK_ICONS[f.check],
        "severity": f.severity, "urgent": f.severity == "urgent" and not done, "status": f.status,
        "status_label": V.STATUS_LABELS[f.status], "by": by, "note": f.note, "note_by": who,
        "summary": scrub(f.summary, facts), "title": scrub(f.title, facts), "place": clean_account(place),
        "when": when, "flagged": f"Flagged {day(local_day(f.created_at))}",
        "amount_cents": facts.get("amount_cents"), "done": done, "account_id": f.account_id,
    }


def list_flags(db: Session, directory: Engine, *, show: str, check: str | None, account_id: int | None, offset: int,
               today: date, owners: dict[str, str]) -> dict:
    statuses = V.NOT_DONE if show == "open" else tuple(sorted(V.DONE))
    in_show = list(db.scalars(select(Flag).where(Flag.status.in_(statuses))))
    count = {"open": db.scalar(select(func.count()).select_from(Flag).where(Flag.status.in_(V.NOT_DONE))),
             "done": db.scalar(select(func.count()).select_from(Flag).where(Flag.status.in_(tuple(V.DONE))))}
    account_names = dict(db.execute(select(Account.id, Account.name)).all())
    accounts = sorted({f.account_id for f in in_show if f.account_id is not None}, key=lambda i: account_names.get(i, ""))
    scoped = [f for f in in_show if account_id is None or f.account_id == account_id]
    by_check = Counter(f.check for f in scoped)
    name = parent_name(owners.values())
    rows = [f for f in scoped if check is None or f.check == check]
    if show == "open":
        rows.sort(key=lambda f: (f.severity != "urgent", -f.created_at.timestamp(), -f.id))
        page, next_offset = rows, None
    else:
        rows.sort(key=lambda f: ((f.resolved_at or f.created_at), f.id), reverse=True)
        page = rows[offset:offset + PAGE]
        next_offset = offset + PAGE if len(rows) > offset + PAGE else None
    names = names_for(directory, [f.status_by for f in page])
    return {
        "show": show, "flags": [flag_row(f, name, names, today) for f in page], "counts": count,
        "checks": [{"check": c, "label": V.check_label(c, name), "count": by_check[c]} for c in V.RANK if by_check[c]],
        "accounts": [{"id": i, "name": clean_account(account_names.get(i, ""))} for i in accounts],
        "next_offset": next_offset,
    }


def counts(db: Session) -> dict:
    """Decision 5: open plus Still checking; urgent while any of them is."""
    rows = db.execute(select(Flag.severity).where(Flag.status.in_(V.NOT_DONE))).scalars().all()
    return {"count": len(rows), "urgent": "urgent" in rows}


def _fact(f: Flag, facts: dict) -> str:
    account = clean_account(facts.get("account", ""))
    if f.check in ("deposit", "premium"):
        nom, due = date.fromisoformat(facts["nominal"]), date.fromisoformat(facts["due"])
        amount = f", {usd(facts['amount_cents'])}," if facts.get("amount_cents") is not None else ""
        verb, prep, missing = (("arrives in", "deposit", "hasn't arrived") if f.check == "deposit"
                               else ("leaves", "premium", "hasn't left"))
        who = facts["item"] if f.check == "deposit" else facts["label"]
        text = (f"the {clean_account(who)} {prep}{amount} usually {verb} {account} around the "
                f"{ordinal(int(facts['usual_day']))}.")
        if facts.get("weekend"):
            text += f" {day(nom)} was a {facts['weekend']}, so it was due {weekday(due)}."
        return text + f" It {missing} as of {day(date.fromisoformat(facts['as_of']))}."
    if f.check == "low":
        return f"{account} is {usd(facts['balance_cents'])}, under its {usd(facts['floor_cents'])} floor."
    if f.check == "account":
        return f"{scrub(f.summary, facts)}."
    return f"{scrub(f.summary, facts)}, from {account}."


def _txn_row(t: Transaction, name: str, accounts: dict[int, str], with_day: bool = False) -> dict:
    when = weekday(t.posted_at) if with_day else day(t.posted_at)
    return {"name": name, "amount_cents": abs(t.amount_cents),
            "meta": f"{when} · {clean_account(accounts.get(t.account_id, ''))}"}


def _transaction(db: Session, f: Flag, facts: dict, owners: dict[str, str]) -> dict | None:
    if f.transaction_id is None:
        return None
    t = db.get(Transaction, f.transaction_id)
    if t is None:      # removed after it was flagged (Review Focus 2): the facts kept on the flag
        posted = date.fromisoformat(facts["posted_at"]) if facts.get("posted_at") else None
        return {"removed": True, "id": None, "payee": _m(facts.get("payee")), "amount_cents": facts.get("amount_cents"),
                "date_label": f"{weekday(posted)}, {posted.year}" if posted else None,
                "account": clean_account(facts.get("account") or ""),
                "joint_names": None, "raw": mask_numbers(facts.get("raw") or "") or None, "category_label": None}
    acct = db.get(Account, t.account_id)
    joint = joined([lbl for k, lbl in owners.items() if k != "joint"]) if acct is not None and acct.owner == "joint" else None
    return {"removed": False, "id": t.id, "payee": _m(facts.get("payee")), "amount_cents": abs(t.amount_cents),
            "date_label": f"{weekday(t.posted_at)}, {t.posted_at.year}",
            "account": clean_account(acct.name) if acct else "",
            "joint_names": joint, "raw": mask_numbers(t.raw_description), "category_label": t.category.replace("_", " ").capitalize()}


def _others(db: Session, f: Flag, facts: dict, accounts: dict[int, str]) -> list[dict]:
    """The other half of a duplicate, or last month's charge for a new monthly charge, with the bank's text."""
    ids = [i for i in (facts.get("other_id"), facts.get("prev_id")) if i is not None]
    out = []
    for i in ids:
        t = db.get(Transaction, i)
        if t is not None:
            out.append({**_txn_row(t, _m(facts.get("payee", "")), accounts, with_day=True), "raw": mask_numbers(t.raw_description)})
    return out


def _earlier(db: Session, f: Flag, facts: dict, accounts: dict[int, str]) -> dict | None:
    key = facts.get("payee_key")
    if not key or f.transaction_id is None or f.check == "duplicate" or not facts.get("posted_at"):
        return None
    posted = date.fromisoformat(facts["posted_at"])
    rows = [t for t in db.scalars(select(Transaction).where(
        Transaction.posted_at >= posted - timedelta(days=365), Transaction.posted_at <= posted,
        Transaction.amount_cents < 0, Transaction.id != f.transaction_id)
        .order_by(Transaction.posted_at.desc(), Transaction.id.desc()))
        if payee(t.raw_description, t.merchant).key == key]
    name, card = _m(facts.get("payee", "")), facts.get("method", "card") == "card"
    heading = f"Earlier at {name}" if card else f"Earlier to {name}"
    if not rows:
        return {"heading": heading, "line": f"No earlier payments to {name} in the last 12 months.", "rows": []}
    amounts = [abs(t.amount_cents) for t in rows]
    noun = "purchase" if card else "payment"
    line = (f"1 {noun} in the last 12 months, {usd(amounts[0])}." if len(rows) == 1 else
            f"{len(rows)} {noun}s in the last 12 months, from {usd(min(amounts))} to {usd(max(amounts))}.")
    if facts.get("kind") == "gift_card" and not any(scam_kind(t.raw_description) for t in rows):
        line += " None were gift cards."
    return {"heading": heading, "line": line, "rows": [_txn_row(t, name, accounts) for t in rows[:3]]}


def _payments(db: Session, f: Flag, accounts: dict[int, str]) -> list[dict]:
    if f.check not in ("deposit", "premium"):
        return []
    e = next((x for x in expectations(db) if x.item_id == f.item_id), None)
    return [] if e is None else [_txn_row(t, mask_numbers(t.raw_description), accounts, with_day=True) for t in payments(db, e)]


def _related(db: Session, f: Flag, owners: dict[str, str], today: date) -> tuple[dict | None, str | None]:
    if f.item_id is None:
        return None, None
    L = load(db, owners)
    item = L.by_id.get(f.item_id)
    if item is None:
        return None, None
    row = item_row(L, item, today)
    related = {"id": item.id, "name": clean_account(row["name"]), "sub": _m(row["sub"]),
               "amount_cents": row["amount_cents"], "status": item.status}
    line = None
    if f.check in ("deposit", "premium"):
        parts = [f"Policy {mask(item.last4)}" if item.kind == "insurance" and item.last4 else None]
        for c in L.items:
            d = L.details[c.id]
            if c.kind == "contact" and item.id in d.get("looks_after_ids", []) and d.get("phone"):
                parts.append(f"{mask_numbers(c.institution or c.name)}: {d['phone']}")
                break
        line = " · ".join(p for p in parts if p) or None
    return related, line


def _known(db: Session, f: Flag) -> dict | None:
    k = db.scalar(select(WatchKnown).where(WatchKnown.flag_id == f.id, WatchKnown.removed_at.is_(None)))
    return {"id": k.id, "label": f"{mask_numbers(k.payee_name)}, {range_text(k.low_cents, k.high_cents)}"} if k else None


def _event_line(e: FlagEvent, names: dict[int, str]) -> tuple[str, str]:
    detail = json.loads(e.detail or "{}")
    who = joined([mid_sentence(names.get(u, "someone")) for u in json.loads(e.user_ids or "[]")])
    if e.kind == "flagged":
        return "flag", f"Flagged by the checks {AFTER_WORDS.get(detail.get('after'), '')}".strip()
    if e.kind == "emailed":
        return "mail", f"Urgent email sent to {who}"
    if e.kind == "summary":
        return "mail", f"In the daily summary to {who}"
    if e.kind == "email_failed":
        what = "Daily summary" if detail.get("kind") == "summary" else "Urgent email"
        return "mail-warning", f"{what} to {who} couldn't be sent: {detail.get('error', '')}".rstrip(": ")
    posted = date.fromisoformat(detail["posted_at"])
    late = int(detail.get("late_days", 0))
    if e.kind == "paid":
        return "banknote", (f"Payment left {day(posted)}, {usd(detail['amount_cents'])}, "
                            f"{_plural(late, 'day')} after it was due")
    timing = "on time" if late == 0 else f"{_plural(late, 'day')} late"
    return "calendar-check", f"Arrived {day(posted)}, {usd(detail['amount_cents'])}, {timing}. Closed by the checks."


def history(db: Session, directory: Engine, f: Flag, today: date, owners: dict[str, str]) -> list[dict]:
    """The flag's History, newest first: what the checks did (flag_event) and what people did (change record)."""
    events = list(db.scalars(select(FlagEvent).where(FlagEvent.flag_id == f.id)))
    changes = list(db.scalars(select(ChangeRecord).where(ChangeRecord.target_type == "flag",
                                                         ChangeRecord.target_id == f.id)))
    names = names_for(directory, [r.user_id for r in changes] + [u for e in events for u in json.loads(e.user_ids or "[]")])
    out = []
    for e in events:
        icon, text = _event_line(e, names)
        out.append({"id": f"e{e.id}", "icon": icon, "text": text, "note": None, "at": e.at.isoformat(), "_n": e.id})
    for r in changes:
        who = names.get(r.user_id, "Someone")
        after = json.loads(r.after or "{}")
        if r.action == "flag.note":
            icon, text = "hourglass", f"{who} changed the note"
        elif after.get("status") == "open":
            icon, text = "flag", f"{who} reopened it"
        else:
            status = after.get("status", "")
            icon, text = {"checking": "hourglass", "expected": "circle-check", "handled": "check"}.get(status, "flag"), \
                f"{who} marked {V.STATUS_LABELS.get(status, status)}"
            if (after.get("known") or {}).get("add"):
                text += f" and added {after['known']['add']} to Known payees"
        out.append({"id": f"c{r.id}", "icon": icon, "text": text, "note": r.note, "at": r.at.isoformat(), "_n": r.id})
    out.sort(key=lambda x: (x["at"], x["_n"]), reverse=True)
    for x in out:
        del x["_n"]
    return out


def flag_detail(db: Session, directory: Engine, f: Flag, today: date, owners: dict[str, str], *,
                can_edit: bool) -> dict:
    name = parent_name(owners.values())
    facts = facts_of(f)
    names = names_for(directory, [f.status_by])
    accounts = dict(db.execute(select(Account.id, Account.name)).all())
    related, related_line = _related(db, f, owners, today)
    done = f.status in V.DONE
    large = large_cents(db)
    # The payee as the transaction gives it now, for the parts that name who was paid or look up their other payments;
    # the summary and title keep the payee they were written with, and scrub() needs that one (flag_payee).
    p = flag_payee(db, f, facts)
    now = {**facts, "payee_key": p.key, "payee": p.name, "method": p.method} if facts.get("payee_key") else facts
    return {
        **flag_row(f, name, names, today),
        "why": V.why(f.check, facts.get("large_cents") or large), "fact": _fact(f, facts),
        "also": [{"check": a["check"], "label": V.check_label(a["check"], name), "icon": V.CHECK_ICONS[a["check"]],
                  "detail": a["detail"]} for a in also_of(f)],
        "flagged_at": f.created_at.isoformat(), "status_at": f.status_at.isoformat() if f.status_at else None,
        "transaction": _transaction(db, f, now, owners), "others": _others(db, f, now, accounts),
        "earlier": _earlier(db, f, now, accounts), "payments": _payments(db, f, accounts),
        "related": related, "related_line": related_line,
        "expected": None if done else expected_sheet(f, large, p), "expected_note": EXPECTED_NOTE.get(f.check),
        "known": _known(db, f), "history": history(db, directory, f, today, owners),
        "can_edit": can_edit, "viewer_line": None if can_edit else VIEWER_LINE,
    }


def account_label(db: Session, f: Flag) -> str:
    """The account in an email: the inventory item's name with its digits removed, then the stored last 4 ("••4417")."""
    facts = facts_of(f)
    item = db.scalar(select(InventoryItem).where(InventoryItem.kind == "account", InventoryItem.account_id == f.account_id)
                     ) if f.account_id is not None else None
    name = (clean_account(item.name, item.last4) if item is not None else clean_account(facts.get("account", "")))
    last4 = mask(item.last4) if item is not None and item.last4 else None
    return f"{name} {last4}" if last4 else name


def email_facts(db: Session, f: Flag) -> list[tuple[str, str]]:
    """The fact rows an urgent email shows (README screen 6). Built only from structured fields: the amount from
    cents, dates from date fields, the payee with its digits removed, the account from the inventory item. Never the
    bank's own text (spec 4.3)."""
    facts = facts_of(f)
    account = account_label(db, f)
    if f.check in ("deposit", "premium"):
        rows = [("Amount", usd(facts["amount_cents"])) if facts.get("amount_cents") is not None else None,
                ("Usually", f"around the {ordinal(int(facts['usual_day']))}"),
                ("Due", weekday(date.fromisoformat(facts["due"]))),
                ("Into" if f.check == "deposit" else "From", account)]
        return [r for r in rows if r]
    if f.check == "low":
        return [("Balance", usd(facts["balance_cents"])), ("Floor", usd(facts["floor_cents"])), ("Account", account)]
    if f.check == "account":
        return [("Account", account), ("Bank", clean_account(facts.get("institution", "")))]
    rows = [("Amount", usd(facts["amount_cents"])), ("Paid to", clean_account(facts.get("payee", "")))]
    if facts.get("posted_at"):
        rows.append(("Date", weekday(date.fromisoformat(facts["posted_at"]))))
    item = db.get(InventoryItem, f.item_id) if f.item_id is not None else None
    if item is not None and item.kind == "account" and item.institution and item.institution.lower() not in account.lower():
        account = f"{account} ({clean_account(item.institution)})"            # "Joint checking (Wells Fargo)"
    rows.append(("From", account))
    return rows
