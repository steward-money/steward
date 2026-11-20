"""Home: How are we doing? (phase 11 spec). GET /api/home's answer, built from Spending's own pieces: the month's
money in and out, the verdict's signals (the server picks; the web words them, P1), the vital signs, For you's rows
and Since you last looked (services/home_since.py). No copy lives here. Integer cents; `today` is the household's
day."""
from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from statistics import median_low

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from steward.business.settings import business_type
from steward.db.models import (CATEGORIES, Account, AccountType, BalanceSnapshot, EventLog, HeldFeed, Reimburser,
                               SameAccountAnswer, Source, Transaction)
from steward.events import event_visible
from steward.services import home_since as hs
from steward.services import networth
from steward.services import spending as sp
from steward.services.feeds import NEW_ACCOUNT_MSG
from steward.services.plan import plan_history, saved_on
from steward.services.reimburse import owed_by
from steward.services.same_account.questions import open_count
from steward.services.spending import ym
from steward.services.vault_flows import month_add, personal_amount

PAYDAY_SHARE = 5               # P8: an income row of at least a fifth (20%) of its month's income
PAYDAY_NEAR = 4                # days within 4 of each other are one payday; pay up to 4 days early has come
PAYDAY_MONTHS = 4              # a payday is seen in at least 4 of the 6 window months
MAX_PAYDAYS = 2                # at most two (the 1st and the 15th)
MAX_PROBLEMS = 2               # P2
MAX_SOFT = 2
MAX_GOOD_WITH_PROBLEM = 2
MAX_GOOD = 3
OLD_SAVED_AT = "saved_at"      # the plan sign's and clause's instant, sent for 0.1.1's open tabs; see _plan
NEW_MONTH_DAYS = 3             # days 1-3 open with "A new month."
MORE_OUT_DAY = 8               # "more out than in" from day 8
JOY_DAY = 21                   # P40: the best-month line from day 21
JOY_MONTHS = 6                 # with at least 6 earlier months that had money in or out
UNPAID = ("waiting", "late", "none")   # a Bill state that isn't paid (Vital signs: paid is any other state)


def usual_paydays(window: list[str], rows: list[tuple[date, int]]) -> list[int]:
    """P8: in each window month, the days of income rows (personal share) worth at least a fifth of that month's
    income. Days chained within 4 of each other are one cluster (engineering choice 15); a cluster seen in at least 4
    of the months is a payday on its median day. At most two, those seen in the most months (ties: the earlier day),
    in day order."""
    by_month: dict[str, list[tuple[int, int]]] = {}
    for d, cents in rows:
        by_month.setdefault(ym(d), []).append((d.day, cents))
    hits: list[tuple[int, str]] = []
    for m in window:
        got = by_month.get(m, [])
        total = sum(c for _, c in got if c > 0)
        hits += [(day, m) for day, c in got if total > 0 and c * PAYDAY_SHARE >= total]
    hits.sort()
    clusters: list[list[tuple[int, str]]] = []
    for h in hits:
        if clusters and h[0] - clusters[-1][-1][0] <= PAYDAY_NEAR:
            clusters[-1].append(h)
        else:
            clusters.append([h])
    found = [(len({m for _, m in c}), median_low([d for d, _ in c])) for c in clusters]
    best = sorted((f for f in found if f[0] >= PAYDAY_MONTHS), key=lambda f: (-f[0], f[1]))[:MAX_PAYDAYS]
    return sorted(day for _, day in best)


def pending_payday(paydays: list[int], this_month: list[tuple[date, int]], typical_income: int | None,
                   today: date) -> int | None:
    """P9: the first usual payday this month that hasn't come yet: today is no later than 4 days after it, and no
    qualifying deposit (an income row of at least a fifth of the typical month's income, engineering choice 14) came
    from 4 days before it to today. A day past the month's end reads as its last day. None when there isn't one: no
    "late pay" alarm."""
    last = calendar.monthrange(today.year, today.month)[1]
    floor = (typical_income or 0) // PAYDAY_SHARE
    for usual in paydays:
        day = min(usual, last)
        if today.day > day + PAYDAY_NEAR:
            continue
        came = any(d <= today and d.day >= day - PAYDAY_NEAR and cents > 0 and cents >= floor for d, cents in this_month)
        if not came:
            return day
    return None


def landed_payday(paydays: list[int], this_month: list[tuple[date, int, datetime]], typical_income: int | None,
                  today: date, look: hs.Look | None) -> int | None:
    """Phase 17 spec 3.7: the first usual payday whose pay came this month (the deposit pending_payday counts as come:
    at least a fifth of the typical month's income, from 4 days before the usual day to today) and reached Steward
    after the last look, posted no more than 10 days before it, as Since counts (P30), so a history import never says
    payday. None on a first visit, or when none did."""
    if look is None:
        return None
    last = calendar.monthrange(today.year, today.month)[1]
    floor = (typical_income or 0) // PAYDAY_SHARE
    posted_from = hs.household_day(look.at) - timedelta(days=hs.POSTED_DAYS)
    for usual in paydays:
        day = min(usual, last)
        if any(posted_from <= d <= today and d.day >= day - PAYDAY_NEAR and cents > 0 and cents >= floor and at > look.at
               for d, cents, at in this_month):
            return day
    return None


def best_month(months: list[dict], day: int) -> bool:
    """P40: from day 21, this month's net so far is above $0 and above each of the 11 months before it, with at least
    6 of them holding money in or out. `months` is flow_view's, oldest first, this month last."""
    *before, now = months
    with_money = [m for m in before if m["in_cents"] or m["out_cents"]]
    return (day >= JOY_DAY and len(with_money) >= JOY_MONTHS and now["net_cents"] > 0
            and all(now["net_cents"] > m["net_cents"] for m in before))


def bills_sign(bills: list[dict]) -> dict:
    """P12: the Bills expected this month plus any Bill paid this month (watched_rows holds exactly those), how many
    are paid, and the late ones with their usual day. Commitments never count."""
    return {"paid": sum(1 for b in bills if b["state"] not in UNPAID), "of": len(bills),
            "late": [{"category": b["category"], "usual_day": b["typical_day"]} for b in bills if b["state"] == "late"]}


def verdict(*, empty: bool, day: int, net: int, payday_day: int | None, typical_income: int | None,
            flexible: list[dict], has_flex_target: bool, bills: list[dict], overdrawn: list[tuple[str, int]],
            plan: dict | None, stale: dict | None, best_month: bool) -> dict:
    """The headline's signals, in the spec's priority order (The headline, Building it). `flexible` is flexible_rows,
    `bills` the Bills of watched_rows, `overdrawn` (name, cents) per checking account below $0, `plan` {age, still,
    saved_day} (and, for one release, saved_at)."""
    if empty:
        return {"opener": "empty", "problems": [], "clauses": [], "stale": None, "best_month": False}
    problems: list[dict] = []
    late = [b for b in bills if b["state"] == "late"]
    if len(late) == 1:
        problems.append({"kind": "late_bill", "category": late[0]["category"], "usual_day": late[0]["typical_day"]})
    elif late:
        problems.append({"kind": "late_bills", "n": len(late)})
    for name, cents in sorted(overdrawn, key=lambda a: a[1]):
        problems.append({"kind": "overdrawn", "account": name, "cents": cents})
    problems = problems[:MAX_PROBLEMS]

    soft: list[dict] = []
    if net < 0 and payday_day is not None:
        soft.append({"kind": "behind_until_payday", "little": typical_income is not None and -net * 2 <= typical_income})
    elif net < 0 and day >= MORE_OUT_DAY:
        soft.append({"kind": "more_out_than_in"})
    clear = [r for r in flexible if r["over_level"] == "clear"]
    if len(clear) == 1:
        soft.append({"kind": "over_one", "category": clear[0]["category"]})
    elif clear:
        soft.append({"kind": "over_many", "n": len(clear)})
    more = [b for b in bills if b["state"] == "over"]
    if more:
        top = max(more, key=lambda b: (b["spent_cents"] - (b["usual_cents"] or 0), -CATEGORIES.index(b["category"])))
        soft.append({"kind": "bill_more", "category": top["category"]})

    good: list[dict] = []
    if has_flex_target and not clear:
        good.append({"kind": "spending_close"} if any(r["over_level"] == "little" for r in flexible)
                    else {"kind": "spending_on_track"})
    expected = [b for b in bills if b["expected"]]
    if expected and all(b["state"] not in UNPAID for b in expected):
        good.append({"kind": "bills_paid"})
    if plan is not None and plan["age"] is not None:
        clause = {"kind": "plan", "age": plan["age"], "still": plan["still"], "saved_day": plan["saved_day"]}
        if OLD_SAVED_AT in plan:                                   # one release only, as in _plan
            clause[OLD_SAVED_AT] = plan[OLD_SAVED_AT]
        good.append(clause)

    new_month = day <= NEW_MONTH_DAYS
    if problems:
        opener, clauses = "problem", good[:MAX_GOOD_WITH_PROBLEM]
    elif soft:
        opener, clauses = ("new_month" if new_month else "mostly"), soft[:MAX_SOFT]
    else:
        opener, clauses = ("new_month" if new_month else "steady"), good[:MAX_GOOD]
    return {"opener": opener, "problems": problems, "clauses": clauses, "stale": stale, "best_month": best_month}


FLOW_MONTHS = 12               # the strip's months, and the joy line's year
STALE_DAYS = 3                 # P4: Home's freshness rule (web/src/lib/freshness.ts STALE_DAYS)
DAY_TO_DAY = (AccountType.checking, AccountType.savings, AccountType.business_checking, AccountType.credit,
              AccountType.business_credit)
OWED_ROWS = 2                  # For you: at most 2 payers by name, then "{n} others owe you"
_NEW_HEAD, _NEW_TAIL = "New account '", NEW_ACCOUNT_MSG.split("{bank}")[1]


def is_new_account(message: str) -> bool:
    """P19: the "New account '…' needs its type and owner" event folds into the untyped row, which comes from the
    accounts, so it leaves when they're typed. The event stays in Settings › Log."""
    return message.startswith(_NEW_HEAD) and message.endswith(_NEW_TAIL)


def _accounts(db: Session, today: date) -> list[tuple[int, str, AccountType, Source, date | None, int | None]]:
    """Every active account with its newest balance on or before today: (id, name, type, source, as_of, cents). Two
    queries; net worth is the sum of the balances, as networth.net_worth_on adds them."""
    accts = db.execute(select(Account.id, Account.name, Account.type, Account.source)
                       .where(Account.is_active.is_(True)).order_by(Account.id)).all()
    newest = (select(BalanceSnapshot.account_id, func.max(BalanceSnapshot.as_of).label("as_of"))
              .where(BalanceSnapshot.as_of <= today).group_by(BalanceSnapshot.account_id).subquery())
    latest = {aid: (d, c) for aid, d, c in db.execute(
        select(BalanceSnapshot.account_id, BalanceSnapshot.as_of, BalanceSnapshot.balance_cents)
        .join(newest, (newest.c.account_id == BalanceSnapshot.account_id) & (newest.c.as_of == BalanceSnapshot.as_of)))}
    return [(aid, name, t, src, *latest.get(aid, (None, None))) for aid, name, t, src in accts]


def stale_accounts(accounts: list[tuple], today: date) -> dict | None:
    """P4, #15: the bank-fed day-to-day accounts whose newest balance is 3 days old or more: how many, the oldest and
    newest of their ages in days, and the account's name when there is one alone. These are the accounts Home lists
    (web isDayToDay), so the name is already on the page. None when every one is fresh."""
    old = sorted(((today - d).days, name) for _, name, t, src, d, _ in accounts
                 if src != Source.manual and t in DAY_TO_DAY and d is not None and (today - d).days >= STALE_DAYS)
    if not old:
        return None
    return {"n": len(old), "oldest_days": old[-1][0], "newest_days": old[0][0],
            "account": old[0][1] if len(old) == 1 else None}


def _income_rows(db: Session, start: date, end: date) -> list[tuple[date, int, datetime]]:
    """Payday's rows (engineering choice 9): income money in, personal share, over [start, end), with when Steward got
    each (phase 17: the payday moment); business income and interest don't count."""
    pa = personal_amount()
    q = (select(Transaction.posted_at, pa, Transaction.imported_at).join(Account, Account.id == Transaction.account_id)
         .where(Transaction.posted_at >= start, Transaction.posted_at < end, Transaction.category == "income",
                Transaction.amount_cents > 0, *sp.spending_clauses()))
    return [(d, int(c), at) for d, c, at in db.execute(q)]


def _plan(db: Session, look_at: datetime | None) -> dict | None:
    """P13: the newest saved plan's comfortable age and when it was saved, so Home never solves. #17: Plan shows today's
    solve, which would cost Home a build of the plan's inputs and, cold, a solve, so the web says the saved date
    instead, as the household's day (#52: not the instant, which a browser would read in its own zone). "Still" when
    the newest save is older than the last look, or has the same age as the save before it."""
    saved = plan_history(db, 2)
    if not saved:
        return None
    newest = saved[0]
    still = ((look_at is not None and newest.saved_at < look_at)
             or (len(saved) > 1 and saved[1].comfortable_age == newest.comfortable_age))
    return {"age": newest.comfortable_age, "still": still, "saved_day": saved_on(newest.saved_at).isoformat(),
            # One release only: a tab still running 0.1.1's bundle names the plan by saved_at until it reloads. Remove
            # after 0.1.2, like 0.1.0's session cookie name (tests/test_home_api.py fails from 0.1.3 on). The web
            # reads saved_day.
            OLD_SAVED_AT: newest.saved_at.isoformat()}


def _one_question_name(db: Session) -> str | None:
    """The name a single same-account question shows (engineering choice 24): the held feed's, or the pair's other
    account's."""
    name = db.scalar(select(HeldFeed.name).where(HeldFeed.candidate_account_id.is_not(None)).limit(1))
    if name is None:
        name = db.scalar(select(Account.name).join(SameAccountAnswer, SameAccountAnswer.other_account_id == Account.id)
                         .where(SameAccountAnswer.answer == "open").limit(1))
    return name


def _owed(db: Session) -> dict | None:
    """Owed to you: payers owing above $0, largest first; two by name, then the rest summed."""
    names = dict(db.execute(select(Reimburser.id, Reimburser.name)).all())
    owing = sorted(((names[pid], o.owed_cents) for pid, o in owed_by(db).items() if o.owed_cents > 0),
                   key=lambda p: (-p[1], p[0]))
    if not owing:
        return None
    rest = owing[OWED_ROWS:]
    return {"kind": "owed", "payers": [{"name": n, "cents": c} for n, c in owing[:OWED_ROWS]],
            "others": {"n": len(rest), "cents": sum(c for _, c in rest)}}


def _for_you(db: Session, accounts: list[tuple], manager: bool) -> list[dict]:
    """For you's server rows (P18, P22): actionable unresolved events newest first (not the new-account one), then,
    for managers only, same-account questions and untyped accounts, then owed to you. The web adds over target from
    the signs, Review from /api/review, tax and Profile after ready, and puts them in order."""
    out: list[dict] = []
    for e in db.scalars(select(EventLog).where(EventLog.actionable.is_(True), EventLog.resolved_at.is_(None))
                        .order_by(EventLog.at.desc(), EventLog.id.desc())):
        if not is_new_account(e.message) and event_visible(e, manager):
            out.append({"kind": "event", "id": e.id, "event_kind": e.kind, "level": e.level, "message": e.message})
    if manager:
        n = open_count(db)
        if n:
            out.append({"kind": "same_account", "n": n, "name": _one_question_name(db) if n == 1 else None})
        untyped = sum(1 for a in accounts if a[2] == AccountType.unknown)
        if untyped:
            out.append({"kind": "untyped", "n": untyped})
    owed = _owed(db)
    if owed:
        out.append(owed)
    return out


def home_view(db: Session, today: date, now: datetime, user_id: int, manager: bool,
              asked: datetime | None = None) -> dict:
    """GET /api/home (Home spec, API). `now` is the mark (P28): when the list was worked out. `asked` is a refetch's
    ?from= (P29). Reads only; never writes."""
    this = today.replace(day=1)
    days = calendar.monthrange(today.year, today.month)[1]
    st = sp.settings(db)
    window, wsums = sp.window_sums(db, today)
    typ = sp.typicals(window, wsums)
    flows = sp.category_months(db, this, month_add(this, 1)).get(ym(this), {})
    months = sp.flow_view(db, today, FLOW_MONTHS)["months"]
    now_month = months[-1]
    flexible = sp.flexible_rows(st, flows, typ, today.day, days)
    bills = [r for r in sp.watched_rows(db, st, this, today, window, wsums, flows) if r["kind"] == "fixed"]
    unsorted = flows.get(sp.UNSORTED)
    spent = sum(r["spent_cents"] for r in flexible) + (unsorted.out_cents if unsorted else 0)
    target = sum(r["target_cents"] or 0 for r in flexible)

    accounts = _accounts(db, today)
    total = sum(c for *_, c in accounts if c is not None)
    before = networth._latest_per_account(db, this - timedelta(days=1))
    change = total - sum(before.values()) if before else None
    look = hs.last_look(db, user_id, now, asked)
    plan = _plan(db, look.at if look is not None else None)

    typical_income = sp.typical_income(window, wsums)
    income = _income_rows(db, sp.first_of(window[0]) if window else this, month_add(this, 1))
    paydays = usual_paydays(window, [(d, c) for d, c, _ in income if d < this])
    this_month = [r for r in income if r[0] >= this]
    payday = pending_payday(paydays, [(d, c) for d, c, _ in this_month], typical_income, today)
    came = landed_payday(paydays, this_month, typical_income, today, look)
    empty = not accounts or (not window and not any(m["in_cents"] or m["out_cents"] for m in months))
    overdrawn = [(name, c) for _, name, t, _, _, c in accounts if t == AccountType.checking and c is not None and c < 0]
    stale = stale_accounts(accounts, today)
    net = now_month["net_cents"]
    v = verdict(empty=empty, day=today.day, net=net, payday_day=payday, typical_income=typical_income,
                flexible=flexible, has_flex_target=target > 0, bills=bills, overdrawn=overdrawn, plan=plan,
                stale=stale, best_month=best_month(months, today.day))
    typical_out = sp.typical_cents([sp.money_out_cents(wsums[m]) for m in window])
    over = [{k: r[k] for k in ("category", "spent_cents", "target_cents", "over_cents", "over_level")}
            for r in flexible if r["over_level"] in ("little", "clear")]
    return {
        "as_of": today.isoformat(), "month": ym(this), "day": today.day, "days": days, "mark": now.isoformat(),
        "verdict": v,
        "flow": {"in_cents": now_month["in_cents"], "out_cents": now_month["out_cents"], "net_cents": net,
                 "payday": {"day": payday} if payday is not None else None,
                 "payday_came": {"day": came} if came is not None else None, "months": months},
        "signs": {"flexible": {"spent_cents": spent, "target_cents": target or None, "rows": over},
                  "bills": bills_sign(bills), "plan": plan,
                  "net_worth": {"total_cents": total, "change_cents": change},
                  "has_business": business_type(db) != "none"},
        "for_you": _for_you(db, accounts, manager),
        "since": hs.since_view(db, look, today, {c: s.kind for c, s in st.items()}, typical_out, total),
    }
