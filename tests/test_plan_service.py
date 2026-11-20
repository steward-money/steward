from dataclasses import replace
from datetime import date, datetime, timezone

from sqlalchemy import select

from steward.books.people import Person, attach_people
from steward.db.models import EventLog
from steward.engine import ChecklistInputs, InputError, Levers, SolveResult, savings_checklist
from steward.engine.synthetic import synthetic_household
from steward.services import plan as svc
from steward.services.plan_cache import LRU, PlanCache
from steward.services.plan_inputs import build_inputs
from steward.services.plan_levers import LeverState
from tests.factories import make_ready_household, make_user

T = datetime(2026, 10, 13, 19, 0, tzinfo=timezone.utc)


def test_lru_evicts_the_least_recently_used():
    c = LRU(2)
    c.put("a", 1)
    c.put("b", 2)
    assert c.get("a") == 1
    c.put("c", 3)
    assert c.get("b") is None and c.get("a") == 1 and c.get("c") == 3 and len(c) == 2
    assert (c.hits, c.misses) == (3, 1)


def test_solves_are_cached_by_input_digest():
    cache = PlanCache()
    inputs = synthetic_household()
    first = svc.solve_cached(cache, inputs)
    assert svc.solve_cached(cache, inputs) is first and cache.results.hits == 1
    moved = replace(inputs, levers=Levers(save_more_monthly_cents=50_000))
    assert svc.solve_cached(cache, moved).input_digest != first.input_digest and cache.results.misses == 2
    hh = inputs.household
    richer = replace(inputs, household=replace(hh, cash=replace(hh.cash, balance_cents=9_000_000)))
    svc.solve_cached(cache, richer)
    assert cache.results.misses == 3
    assert len(cache.prepared) == 1                       # one household shape: its random draws are reused


def test_impacts_are_cached_too():
    cache = PlanCache()
    inputs = synthetic_household()
    a = svc.impacts_cached(cache, inputs, Levers(spending_pct=90))
    assert svc.impacts_cached(cache, inputs, Levers(spending_pct=90)) is a
    assert {"spending", "save_more"} <= {i.key for i in a}
    assert svc.impacts_cached(cache, inputs, Levers(spending_pct=80)) is not a

NAMES = {"jr": "JR", "spouse": "Sam", "joint": "Joint"}


def test_result_out_shape():
    out = svc.result_out(svc.solve_cached(PlanCache(), synthetic_household()), NAMES)
    assert set(out) == {"comfortable_age", "likely_age", "comfortable_year", "likely_year", "band_age", "band",
                        "success_by_age", "assumptions", "assumption_groups", "input_digest", "engine_version", "histories"}
    assert set(out["band"][0]) == {"year", "jr_age", "p10_cents", "p50_cents", "p90_cents"}
    assert out["engine_version"] == "2026.10.5" and out["histories"] == 2000


def test_saves_keep_every_row_and_the_newest_wins(db):
    u = make_user(db)
    res = svc.solve_cached(PlanCache(), synthetic_household())
    a = svc.save_plan(db, u.id, LeverState(Levers(), Levers()), res, T)
    b = svc.save_plan(db, u.id, LeverState(Levers(spending_pct=90), Levers()), res, T)     # the same instant
    db.commit()
    assert svc.latest_plan(db).id == b.id
    assert [p.id for p in svc.plan_history(db, 10)] == [b.id, a.id]
    out = svc.plan_out(db, b, current=True)
    assert out["saved_by"] == {"id": u.id, "name": "JR"} and out["current"] is True
    assert out["levers"]["assumed"]["spending_pct"] == 90 and out["saved_at"] == T.isoformat()
    assert out["comfortable_age"] == res.comfortable_age and out["engine_version"] == "2026.10.5"
    assert out["saved_day"] == "2026-10-13"
    assert svc.saved_state(b).assumed.spending_pct == 90 and svc.saved_state(None) is None


def test_a_save_is_named_by_the_households_day():
    """#52: 23:30 in California is the next day in UTC; Home, Plan and Taxes all say the household's day."""
    assert svc.saved_on(datetime(2026, 10, 12, 6, 30, tzinfo=timezone.utc)).isoformat() == "2026-10-11"
    assert svc.saved_on(datetime(2026, 10, 12, 7, 0, tzinfo=timezone.utc)).isoformat() == "2026-10-12"


def test_failures_log_once_and_return_the_last_saved_ages(db):
    assert svc.record_failure(db, RuntimeError("boom")) == {"error": "The plan couldn't be worked out right now.", "last_saved": None}
    u = make_user(db)
    res = svc.solve_cached(PlanCache(), synthetic_household())
    svc.save_plan(db, u.id, LeverState(Levers(), Levers()), res, T)
    db.commit()
    body = svc.record_failure(db, InputError("The younger spouse is already 95 or older; there is nothing left to plan to."))
    assert body["last_saved"] == {"comfortable_age": res.comfortable_age, "likely_age": res.likely_age, "saved_at": T.isoformat(),
                                  "saved_day": "2026-10-13"}
    assert db.scalars(select(EventLog.message).where(EventLog.kind == "system")).all() == \
        ["The retirement plan couldn't be worked out: RuntimeError."]


def test_an_engine_refusal_reads_with_the_households_labels(db):
    """TRI-1: the people attached to the session name the slots; with none, the slots read Person 1 and Person 2."""
    refusal = InputError("{person1} must be born before {person2}.")
    assert svc.engine_text(db, refusal) == "Person 1 must be born before Person 2."
    attach_people(db, lambda: (Person(5, "alex", "Alex"), Person(6, "sam", "Sam")))
    assert svc.engine_text(db, refusal) == "Alex must be born before Sam."


def test_checklist_out_adds_the_backdoor_sheet():
    ci = ChecklistInputs(spending_basis_cents=0, cash_cents=0, k401_pct_bp=None, match_cap_bp=None, k401_contribution_cents=None,
                         has_credit_accounts=False, interest_charges_3mo=None, has_hsa=False, hsa_contributions_12mo_cents=None,
                         owners=("JR", "Sam"), roth_contributed_owners=frozenset(), taxable_contributions_12mo_cents=None)
    items = svc.checklist_out(savings_checklist(ci), NAMES)
    roth = next(i for i in items if i["key"] == "roth_ira")
    assert roth["sheet"] == svc.BACKDOOR_ROTH_SHEET and "JR, Sam" in roth["why"]
    assert len(items) == 7 and all("sheet" not in i for i in items if i["key"] != "roth_ira")


def test_inputs_summary_names_what_the_change_line_compares(db):
    make_user(db)
    make_user(db, "sam", "sam@x.com", "Sam")
    make_ready_household(db)
    pi = build_inputs(db, date(2026, 10, 13))
    pi = replace(pi, set_asides=replace(pi.set_asides, one_offs=(
        {"id": 4, "name": "Roof", "year": 2029, "due_year": 2029, "due_month": 6, "overdue": False, "cents": 1_800_000,
         "monthly_cents": 50_000, "is_estimate": False},)))
    # 60 at 85 %, 61 at 95 %: the 90 % line is crossed half way, at 60.5, so 726 months.
    res = SolveResult(61, 60, 2043, 2042, ((60, 0.85), (61, 0.95), (62, 0.97)), 61, (), (), "d", "v", 2000)
    assert svc.inputs_summary(pi, res) == {
        "version": 1, "spending_basis_cents": 7_200_000,
        "balances_cents": {"taxed_later": 45_000_000, "tax_free": 9_000_000, "taxable": 15_000_000, "cash": 4_000_000},
        "saving_cents": 200_000, "social_security": {"jr": "record", "spouse": "estimate"},
        "big_costs": [{"name": "Roof", "year": 2029}], "comfortable_months": 726,
    }
    never = replace(res, comfortable_age=None, success_by_age=((60, 0.5), (70, 0.8)))
    assert svc.inputs_summary(pi, never)["comfortable_months"] is None


def test_the_summary_calls_an_estimated_record_an_estimate():
    from steward.engine.synthetic import synthetic_household as synth
    from steward.services.plan_inputs import PlanInputs
    hh = synth().household
    est = replace(hh, jr=replace(hh.jr, estimated=True))
    for household, word in ((hh, "record"), (est, "estimate")):
        pi = PlanInputs(household, None, None, {}, {})
        res = type("R", (), {"success_by_age": ((50, 0.95),)})()
        assert svc.inputs_summary(pi, res)["social_security"]["jr"] == word


def test_a_save_keeps_its_summary_and_older_rows_have_none(db):
    u = make_user(db)
    res = svc.solve_cached(PlanCache(), synthetic_household())
    a = svc.save_plan(db, u.id, LeverState(Levers(), Levers()), res, T)
    b = svc.save_plan(db, u.id, LeverState(Levers(), Levers()), res, T, summary={"version": 1, "saving_cents": 5})
    db.commit()
    assert svc.plan_out(db, a, current=False)["inputs_summary"] is None
    assert svc.plan_out(db, b, current=True)["inputs_summary"] == {"version": 1, "saving_cents": 5}
