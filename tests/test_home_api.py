"""GET /api/home (Home spec, API): Spending's figure, the signs, For you's rows in order with viewers filtered,
Since with the server's mark, a refetch's ?from=, household books only, no note text, and at most 30 queries."""
import re
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from steward import clock
from steward.db.directory_models import User
from steward.db.models import (BalanceSnapshot, BusinessState, CategorySource, HomeVisit, MerchantRule, Plan, Reimburser,
                               SameAccountAnswer, SetAside, SetAsideMatch, Transaction, TransactionNote)
from steward.engine.version import ENGINE_VERSION
from steward.events import log_event
from steward.services.plan_cache import InputsCache, PlanCache
from tests.factories import add_fact, login, make_account, make_person, make_txn
from tests.spending_fixtures import household

OCT_25 = datetime(2026, 10, 25, 19, 0, tzinfo=timezone.utc)          # noon in California
LEVERS = '{"assumed": {}, "try": {}, "pinned": []}'


@pytest.fixture
def oct25(monkeypatch):
    monkeypatch.setattr("steward.clock.now", lambda: OCT_25)


def _jr(ddb) -> int:
    return ddb.scalar(select(User.id).where(User.username == "jr"))


def home(client, **params) -> dict:
    r = client.get("/api/b/ours/home", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_the_figure_and_the_flexible_sign_are_spendings_head(oct25, signed_in, db):
    household(db)
    h = home(signed_in)
    head = signed_in.get("/api/b/ours/spending").json()["head"]
    assert (h["flow"]["in_cents"], h["flow"]["out_cents"]) == (head["money_in_cents"], head["money_out_cents"])
    assert h["flow"]["net_cents"] == head["money_in_cents"] - head["money_out_cents"]
    assert (h["signs"]["flexible"]["spent_cents"], h["signs"]["flexible"]["target_cents"]) == (
        head["everyday_spent_cents"], head["everyday_target_cents"])
    assert [m["month"] for m in h["flow"]["months"]][-2:] == ["2026-09", "2026-10"] and len(h["flow"]["months"]) == 12
    assert (h["as_of"], h["month"], h["day"], h["days"], h["mark"]) == ("2026-10-25", "2026-10", 25, 31, OCT_25.isoformat())


def test_a_matched_set_aside_payment_still_counts_and_giving_outside_doesnt(oct25, signed_in, db):
    household(db)
    before = home(signed_in)["flow"]
    t = db.scalar(select(Transaction).where(Transaction.raw_description == "COUNTY TAX"))
    sa = SetAside(name="Property tax", kind="yearly", amount_cents=421_000, due_month=10, due_day=20,
                  first_month=date(2026, 1, 1), payee_key="COUNTY TAX", category="taxes")
    db.add(sa)
    db.flush()
    db.add(SetAsideMatch(transaction_id=t.id, set_aside_id=sa.id, due_on=date(2026, 10, 20), how="auto"))
    db.commit()
    add_fact(db, "giving.outside_cents", "50000")
    assert home(signed_in)["flow"] == before


def test_the_household_on_the_25th_has_a_late_bill_its_best_month_and_shopping_over(oct25, signed_in, db):
    household(db)
    h = home(signed_in)
    assert h["verdict"] == {"opener": "problem",
                            "problems": [{"kind": "late_bill", "category": "utilities", "usual_day": 12}],
                            "clauses": [], "stale": None, "best_month": True}
    assert h["signs"]["bills"] == {"paid": 2, "of": 3, "late": [{"category": "utilities", "usual_day": 12}]}
    assert h["signs"]["flexible"]["rows"] == [{"category": "shopping", "spent_cents": 51_200, "target_cents": 40_000,
                                               "over_cents": 11_200, "over_level": "clear"}]
    assert h["flow"]["payday"] is None                                          # the 1st's pay came on the 1st
    assert (h["signs"]["plan"], h["signs"]["has_business"], h["since"]) == (None, False, None)


def test_overdrawn_checking_and_a_stale_bank_account_say_so(oct25, signed_in, db):
    chk = make_account(db, "Checking", source="simplefin", institution_name="Big Bank")
    make_txn(db, chk, date(2026, 10, 2), 100_000, "PAYROLL", merchant="Northwind payroll", category="income")
    db.add(BalanceSnapshot(account_id=chk.id, as_of=date(2026, 10, 22), balance_cents=-12_000))
    db.commit()
    v = home(signed_in)["verdict"]
    assert v["problems"] == [{"kind": "overdrawn", "account": "Checking", "cents": -12_000}]
    assert v["stale"] == {"n": 1, "oldest_days": 3, "newest_days": 3, "account": "Checking"}
    db.add(BalanceSnapshot(account_id=chk.id, as_of=date(2026, 10, 23), balance_cents=-12_000))
    db.commit()
    assert home(signed_in)["verdict"]["stale"] is None                           # 2 days old is fresh
    assert home(signed_in)["signs"]["net_worth"] == {"total_cents": -12_000, "change_cents": None}


def test_two_stale_bank_accounts_are_counted_with_their_ages(oct25, signed_in, db):
    """#15: the server sends how many and how old, so the line no longer says "one" and "a few days" for any count."""
    chk = make_account(db, "Checking", source="simplefin", institution_name="Big Bank")
    card = make_account(db, "Card", type="credit", source="simplefin", institution_name="Big Bank")
    make_txn(db, chk, date(2026, 10, 2), 100_000, "PAYROLL", merchant="Northwind payroll", category="income")
    db.add_all([BalanceSnapshot(account_id=chk.id, as_of=date(2026, 9, 26), balance_cents=50_000),
                BalanceSnapshot(account_id=card.id, as_of=date(2026, 10, 20), balance_cents=-3_000)])
    db.commit()
    assert home(signed_in)["verdict"]["stale"] == {"n": 2, "oldest_days": 29, "newest_days": 5, "account": None}


def test_a_twice_yearly_bill_counts_only_when_paid_this_month_and_commitments_never(oct25, signed_in, db):
    accts = household(db)
    for m in (4, 7):
        make_txn(db, accts["checking"], date(2026, m, 20), -60_000, "BIG MUTUAL", merchant="Big Mutual", category="insurance")
    assert home(signed_in)["signs"]["bills"]["of"] == 3                         # paid in 2 of 6 months: not expected
    make_txn(db, accts["checking"], date(2026, 10, 20), -60_000, "BIG MUTUAL", merchant="Big Mutual", category="insurance")
    make_txn(db, accts["checking"], date(2026, 10, 5), -10_000, "GRACE CHURCH", merchant="Grace Church", category="giving")
    bills = home(signed_in)["signs"]["bills"]
    assert (bills["paid"], bills["of"]) == (3, 4)                               # insurance paid; Giving is a Commitment


def test_a_book_with_a_business_says_so(oct25, signed_in, db):
    db.add(BusinessState(key="type", value="sole_prop"))
    db.commit()
    assert home(signed_in)["signs"]["has_business"] is True


def test_an_empty_book_has_nothing_to_go_on(oct25, signed_in, db):
    h = home(signed_in)
    assert h["verdict"]["opener"] == "empty" and h["for_you"] == [] and h["since"] is None


def plan_sign(client) -> dict | None:
    """The plan sign without the instant it carries for one release (see the test below)."""
    sign = home(client)["signs"]["plan"]
    return None if sign is None else {k: v for k, v in sign.items() if k != "saved_at"}


def test_the_plan_sign_reads_the_saved_plan_with_its_date_and_when_it_still_says_so(oct25, signed_in, db, ddb):
    """#17: Home shows the saved plan, not today's solve (Home's budget), so the sign carries when it was saved and the
    web says so; Plan shows today's solve. #52: it sends the household's day, and the web names the save by that day,
    not by the instant read in the device's zone."""
    def save(when: datetime, age: int | None) -> None:
        db.add(Plan(saved_at=when, saved_by=1, levers=LEVERS, engine_version=ENGINE_VERSION, comfortable_age=age,
                    likely_age=None))
        db.commit()

    assert plan_sign(signed_in) is None
    save(OCT_25 - timedelta(days=10), 54)
    assert plan_sign(signed_in) == {"age": 54, "still": False, "saved_day": "2026-10-15"}  # no last look, none before
    db.add(HomeVisit(user_id=_jr(ddb), seen_at=OCT_25 - timedelta(days=3)))
    db.commit()
    assert plan_sign(signed_in) == {"age": 54, "still": True, "saved_day": "2026-10-15"}   # saved before the last look
    save(OCT_25 - timedelta(hours=13), 55)                         # 23:00 on Oct 24 in California, Oct 25 in UTC
    assert plan_sign(signed_in) == {"age": 55, "still": False, "saved_day": "2026-10-24"}
    save(OCT_25 - timedelta(hours=1), 55)
    assert plan_sign(signed_in) == {"age": 55, "still": True, "saved_day": "2026-10-25"}   # the same age as before
    save(OCT_25 - timedelta(minutes=1), None)
    assert plan_sign(signed_in) == {"age": None, "still": False, "saved_day": "2026-10-25"}  # Not by 70


def test_the_plan_signs_saved_at_is_sent_for_one_release_only(oct25, signed_in, db):
    """#52 review: 0.1.2's Home sends saved_day, and a tab still running 0.1.1's bundle reads saved_at until it reloads.
    The sign and the headline's plan clause keep saved_at for 0.1.2 only; this fails from 0.1.3 on until it goes."""
    from steward import __version__
    household(db)
    saved = OCT_25 - timedelta(days=3)
    db.add(Plan(saved_at=saved, saved_by=1, levers=LEVERS, engine_version=ENGINE_VERSION, comfortable_age=61,
                likely_age=59))
    db.commit()
    h = home(signed_in)
    clauses = [c for c in h["verdict"]["clauses"] if c["kind"] == "plan"]
    assert len(clauses) == 1, h["verdict"]
    version = tuple(int(n) for n in re.match(r"(\d+)\.(\d+)\.(\d+)", __version__).groups())
    if version >= (0, 1, 3):
        assert "saved_at" not in h["signs"]["plan"] and "saved_at" not in clauses[0], \
            "remove the plan's saved_at from services/home.py (OLD_SAVED_AT)"
    else:
        assert h["signs"]["plan"]["saved_at"] == clauses[0]["saved_at"] == saved.isoformat()


def test_home_never_works_out_the_plan(oct25, signed_in, db, monkeypatch):
    """#17's ruling: today's solve would cost Home a build of the plan's inputs and, when cold, a solve (measured with
    the perf fixture: 67 ms and 200 ms on the dev box, about 2.5 times that on the NAS, against Home's 600 ms ready
    budget), so Home reads the saved plan and says when it was saved."""
    household(db)
    db.add(Plan(saved_at=OCT_25 - timedelta(days=3), saved_by=1, levers=LEVERS, engine_version=ENGINE_VERSION,
                comfortable_age=61, likely_age=59))
    db.commit()

    def refuse(*_a, **_k):
        raise AssertionError("Home worked out the plan")
    monkeypatch.setattr(PlanCache, "once", refuse)
    monkeypatch.setattr(InputsCache, "get_or_build", refuse)
    monkeypatch.setattr("steward.services.plan_inputs.build_inputs", refuse)
    assert home(signed_in)["signs"]["plan"]["age"] == 61


def test_for_you_holds_each_kind_in_order_and_folds_the_new_account_event(oct25, signed_in, db):
    accts = household(db)
    sync = log_event(db, "warn", "sync", "Big Bank needs you to sign in again at SimpleFIN", actionable=True)
    log_event(db, "info", "system", "New account 'Acct 9' from Big Bank needs its type and owner set in Settings",
              actionable=True)
    chat = log_event(db, "error", "chat", "A sent answer is missing from the record.", actionable=True)
    log_event(db, "info", "import", "Imported 12 rows into Checking from bank.csv")       # not actionable: never here
    make_account(db, "Acct 9", type="unknown")
    other = make_account(db, "Old checking")
    db.add(SameAccountAnswer(account_id=accts["checking"].id, other_account_id=other.id, answer="open"))
    payer = Reimburser(name="Northwind")
    db.add(payer)
    db.flush()
    t = make_txn(db, accts["checking"], date(2026, 10, 5), -118_400, "HOTEL", merchant="Hotel", category="dining")
    t.reimburser_id = payer.id
    db.commit()
    got = home(signed_in)["for_you"]
    assert [x["kind"] for x in got] == ["event", "event", "same_account", "untyped", "owed"]
    assert [x["id"] for x in got[:2]] == [chat.id, sync.id]                      # newest first
    assert got[0] == {"kind": "event", "id": chat.id, "event_kind": "chat", "level": "error",
                      "message": "A sent answer is missing from the record."}
    assert got[2:] == [{"kind": "same_account", "n": 1, "name": "Old checking"}, {"kind": "untyped", "n": 1},
                       {"kind": "owed", "payers": [{"name": "Northwind", "cents": 118_400}], "others": {"n": 0, "cents": 0}}]


def test_a_viewer_gets_no_manager_only_rows(oct25, client, db, ddb, ours):
    chk = make_account(db, "Checking")
    make_account(db, "Acct 9", type="unknown")
    db.add(SameAccountAnswer(account_id=chk.id, other_account_id=make_account(db, "Old checking").id, answer="open"))
    log_event(db, "warn", "sync", "Big Bank needs you to sign in again at SimpleFIN", actionable=True)
    db.commit()
    make_person(ddb, "val", [(ours.id, "viewer")])
    login(client, "val")
    assert [x["kind"] for x in home(client)["for_you"]] == ["event"]


def test_an_actionable_import_event_is_for_managers_only(oct25, client, db, ddb, ours):
    """Engineering choice 14: a statement's import event names a file, so a viewer's For you never carries it."""
    make_account(db, "Checking")
    log_event(db, "warn", "import", "A statement didn't add up", actionable=True)
    db.commit()
    make_person(ddb, "val", [(ours.id, "viewer")])
    login(client, "val")
    assert home(client)["for_you"] == []
    make_person(ddb, "mgr", [(ours.id, "manager")])
    login(client, "mgr")
    assert [x["event_kind"] for x in home(client)["for_you"]] == ["import"]


def test_owed_shows_the_two_largest_sums_the_rest_and_leaves_out_payers_at_zero(oct25, signed_in, db):
    chk = make_account(db, "Checking")
    for name, owed, repaid in (("Ada", 30_000, 0), ("Bo", 20_000, 0), ("Cy", 10_000, 0), ("Di", 5_000, 5_000)):
        p = Reimburser(name=name)
        db.add(p)
        db.flush()
        make_txn(db, chk, date(2026, 10, 5), -owed, f"{name} TRIP", category="dining").reimburser_id = p.id
        if repaid:
            make_txn(db, chk, date(2026, 10, 6), repaid, f"{name} REPAID", category="reimbursement").reimburser_id = p.id
    db.commit()
    owed = next(x for x in home(signed_in)["for_you"] if x["kind"] == "owed")
    assert owed == {"kind": "owed", "payers": [{"name": "Ada", "cents": 30_000}, {"name": "Bo", "cents": 20_000}],
                    "others": {"n": 1, "cents": 10_000}}


def test_since_lists_whats_new_and_the_mark_is_the_servers_time(oct25, signed_in, db, ddb):
    chk = make_account(db, "Checking")
    db.add(HomeVisit(user_id=_jr(ddb), seen_at=OCT_25 - timedelta(days=3)))
    db.commit()
    make_txn(db, chk, date(2026, 10, 24), 421_200, "PAYROLL", merchant="Northwind payroll", category="income")
    h = home(signed_in)
    assert h["mark"] == OCT_25.isoformat()
    assert (h["since"]["from"], h["since"]["from_day"]) == ((OCT_25 - timedelta(days=3)).isoformat(), "2026-10-22")
    assert h["since"]["lines"] == [{"kind": "pay", "category": "income", "name": "Northwind payroll", "n": 1,
                                    "places": 1, "cents": 421_200}]


def test_a_glance_moves_from_only_on_the_next_household_day(signed_in, db, fake_clock):
    first = home(signed_in)
    assert first["since"] is None
    r = signed_in.post("/api/b/ours/home/seen", json={"mark": first["mark"], "glance": True})
    assert r.status_code == 204
    assert home(signed_in)["since"] is None                                       # a quick refresh today
    fake_clock.advance(timedelta(days=1))
    assert home(signed_in)["since"]["from"] == first["mark"]


def test_a_refetch_from_holds_the_list_and_an_old_or_odd_one_is_ignored(oct25, signed_in, db, ddb):
    db.add(HomeVisit(user_id=_jr(ddb), seen_at=OCT_25 - timedelta(days=3)))
    db.commit()
    own, five = (OCT_25 - timedelta(days=3)).isoformat(), (OCT_25 - timedelta(days=5)).isoformat()
    assert home(signed_in, **{"from": five})["since"]["from"] == five
    assert home(signed_in, **{"from": (OCT_25 - timedelta(days=40)).isoformat()})["since"]["from"] == own
    assert home(signed_in, **{"from": "last tuesday"})["since"]["from"] == own


def test_no_note_text_reaches_home(oct25, signed_in, db, ddb):
    chk = make_account(db, "Checking")
    db.add(HomeVisit(user_id=_jr(ddb), seen_at=OCT_25 - timedelta(days=3)))
    t = make_txn(db, chk, date(2026, 10, 24), -30_000, "STREAMBOX", merchant="StreamBox", category="shopping")
    db.add(TransactionNote(transaction_id=t.id, author_id=1, author_name="Jr", body="zorblax", created_at=OCT_25))
    db.add(MerchantRule(pattern="STREAMBOX", merchant="StreamBox", category="shopping", source=CategorySource.correction,
                        note="for Dr. Alvarez", note_by=1, note_by_name="Jr"))
    db.commit()
    text = signed_in.get("/api/b/ours/home").text
    assert "StreamBox" in text and "zorblax" not in text and "Alvarez" not in text


def test_a_parent_book_has_no_home(client, margaret):
    login(client, "jr")
    assert client.get(f"/api/b/{margaret.slug}/home").status_code == 404
    r = client.post(f"/api/b/{margaret.slug}/home/seen", json={"mark": "2026-10-30T18:00:00+00:00", "glance": True})
    assert r.status_code == 404


QUERY = re.compile(r"^\s*(SELECT|INSERT|UPDATE|DELETE|WITH)\b", re.IGNORECASE)


def test_home_asks_at_most_30_queries_on_a_demo_book(client, books, ours, engine):
    """P39: a ceiling in place of a time budget. A warm-up GET first, so connection PRAGMAs and BEGIN aren't counted;
    then every statement on the book's engine (engineering choice 12). If this fails, list the queries it prints and
    take one out (the settlements table is read once per category_months call: three of them); never raise 30."""
    from steward.demo import seed, seed_people
    with Session(engine) as db:
        seed(db, years=1, seed=7)
        db.commit()
    with Session(books.directory) as ddb:
        seed_people(ddb, ours.id)
    alex = next(p.id for p in books.people(ours.id) if p.username == "alex")
    with Session(engine) as db:
        db.add(HomeVisit(user_id=alex, seen_at=clock.now() - timedelta(days=3)))
        db.commit()
    login(client, "alex", "pw-demo-alex-2026")
    assert client.get("/api/b/ours/home").status_code == 200
    seen: list[str] = []

    def count(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(engine, "before_cursor_execute", count)
    try:
        r = client.get("/api/b/ours/home")
    finally:
        event.remove(engine, "before_cursor_execute", count)
    queries = [s for s in seen if QUERY.match(s)]
    assert r.status_code == 200 and r.json()["since"] is not None
    assert len(queries) <= 30, f"{len(queries)} queries:\n" + "\n".join(q.split("\n")[0][:160] for q in queries)


def test_payday_came_names_the_usual_payday_that_landed_since_the_last_look(oct25, signed_in, db, ddb):
    household(db)
    assert home(signed_in)["flow"]["payday_came"] is None                         # a first visit: no last look
    db.add(HomeVisit(user_id=_jr(ddb), seen_at=datetime(2026, 10, 2, 19, 0, tzinfo=timezone.utc)))
    db.commit()
    assert home(signed_in)["flow"]["payday_came"] == {"day": 1}                    # the 1st's pay arrived since Oct 2
    v = db.get(HomeVisit, _jr(ddb))
    v.seen_at = OCT_25
    db.commit()
    assert home(signed_in)["flow"]["payday_came"] is None                         # nothing new since this morning's look
