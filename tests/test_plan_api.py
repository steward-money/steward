import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone

from sqlalchemy import select

from steward.db.models import BalanceSnapshot, EventLog, Plan
from tests.factories import add_fact, make_ready_household, make_user

FINISH = "Finish setting up the plan first."
PATHS = ("/api/b/ours/plan/preview", "/api/b/ours/plan/impacts", "/api/b/ours/plan")


def ready(db):
    make_user(db, "sam", "sam@x.com", "Sam")
    return make_ready_household(db)


def test_get_plan_lists_setup_until_ready(frozen, signed_in, db):
    r = signed_in.get("/api/b/ours/plan")
    assert r.status_code == 200 and list(r.json()) == ["setup"]
    assert [i["key"] for i in r.json()["setup"]] == ["household", "pay", "earnings", "children", "health", "history"]
    setup = signed_in.get("/api/b/ours/plan/setup").json()
    assert setup["ready"] is False and "earnings_spouse" in [i["key"] for i in setup["setup"]]
    for path in PATHS:
        r = signed_in.post(path, json={})
        assert r.status_code == 409 and r.json()["error"] == FINISH and r.json()["setup"][0]["key"] == "household"
    assert db.scalar(select(Plan.id)) is None


def test_get_plan_returns_a_fresh_result(frozen, signed_in, db):
    ready(db)
    body = signed_in.get("/api/b/ours/plan").json()
    assert body["plan"] is None and body["result"]["comfortable_age"] is not None
    assert body["levers"]["try"]["consulting_cents"] == 600_000 and body["levers"]["try"]["gift_cents"] == 1_000_000
    assert body["levers"]["try_notes"] == {"spouse_earnings": "No history yet"} and body["levers"]["pinned"] == []
    specs = {s["key"]: s for s in body["lever_specs"]}
    assert specs["spouse_earnings"]["label"] == "Sam's earnings, a year"
    assert body["inputs"]["spending_months"] == 12 and body["inputs"]["spending_basis_cents"] == 7_200_000
    assert body["inputs"]["surplus_cents"] == 2_400_000 and body["inputs"]["facts_as_of"]["health.premium_cents"] == "2026-10-01"
    assert signed_in.get("/api/b/ours/plan/setup").json()["ready"] is True


def test_out_of_scale_values_are_clamped(frozen, signed_in, db):
    ready(db)
    base = signed_in.get("/api/b/ours/plan").json()["result"]["comfortable_age"]
    r = signed_in.post("/api/b/ours/plan/preview", json={"assumed": {"spending_pct": 300, "jr_claim_age": 75, "gift_cents": -5}})
    assert r.status_code == 200
    assumed = r.json()["levers"]["assumed"]
    assert (assumed["spending_pct"], assumed["jr_claim_age"], assumed["gift_cents"]) == (130, 70, 0)
    assert r.json()["result"]["comfortable_age"] is None or r.json()["result"]["comfortable_age"] >= base
    assert r.json()["result"]["band"]


def test_lever_type_errors_are_plain_words(frozen, signed_in, db):
    ready(db)
    cases = [({"assumed": {"spending_pct": "90"}}, "Spending in retirement must be a whole number."),
             ({"assumed": {"full_ss_benefits": 1}}, "Full Social Security benefits must be on or off."),
             ({"try": {"consulting_cents": 1.5}}, "Consulting income must be a whole number."),
             ({"assumed": {"jr_claim_age": True}}, "JR's claiming age must be a whole number."),
             ({"assumed": {"retire_at": 55}}, "There's no lever called retire_at."),
             ({"pinned": "consulting"}, "The pinned levers weren't understood.")]
    for body, message in cases:
        for path in PATHS:
            r = signed_in.post(path, json=body)
            assert r.status_code == 422 and r.json() == {"error": message}, (path, body)
    r = signed_in.post("/api/b/ours/plan/preview", content=b'"spending"', headers={"content-type": "application/json"})
    assert r.status_code == 422 and r.json() == {"error": "That request wasn't understood."}
    assert db.scalar(select(Plan.id)) is None


def test_impacts_use_the_vault_try_values(frozen, signed_in, db):
    ready(db)
    body = signed_in.post("/api/b/ours/plan/impacts", json={}).json()
    rows = {(i["key"], i["child_label"]): i for i in body["impacts"]}
    assert rows[("consulting", None)]["try_value"] == 600_000 and rows[("consulting", None)]["label"] == "Consulting income, a year"
    assert rows[("spouse_earnings", None)]["label"] == "Sam's earnings, a year"
    assert rows[("college", "Oldest")]["try_value"] == "in_state_public"
    assert all(isinstance(i["text"], str) and i["text"] for i in body["impacts"])


def test_save_and_history(frozen, signed_in, db):
    ready(db)
    r = signed_in.post("/api/b/ours/plan", json={"assumed": {"spending_pct": 90}, "try": {"consulting_cents": 2_000_000}})
    assert r.status_code == 201
    first = r.json()["plan"]
    assert first["current"] is True and first["saved_by"]["name"] == "JR"
    assert first["levers"]["assumed"]["spending_pct"] == 90 and first["levers"]["pinned"] == ["consulting"]
    assert first["comfortable_age"] == r.json()["result"]["comfortable_age"]
    second = signed_in.post("/api/b/ours/plan", json={"assumed": {"spending_pct": 95}}).json()["plan"]
    history = signed_in.get("/api/b/ours/plan/history").json()["history"]
    assert [h["id"] for h in history] == [second["id"], first["id"]] and [h["current"] for h in history] == [True, False]
    assert history[0]["levers"]["try"]["consulting_cents"] == 2_000_000          # pinned in the first save, kept
    plan = signed_in.get("/api/b/ours/plan").json()
    assert plan["plan"]["id"] == second["id"] and plan["levers"]["assumed"]["spending_pct"] == 95
    assert plan["levers"]["try"]["consulting_cents"] == 2_000_000
    for bad_limit in ("0", "201", "x", "²"):
        r = signed_in.get(f"/api/b/ours/plan/history?limit={bad_limit}")
        assert r.status_code == 422 and r.json() == {"error": "Choose between 1 and 200 saves."}


def test_simultaneous_saves_are_both_kept_newest_wins(frozen, signed_in, db):
    ready(db)
    with ThreadPoolExecutor(2) as ex:
        codes = list(ex.map(lambda pct: signed_in.post("/api/b/ours/plan", json={"assumed": {"spending_pct": pct}}).status_code, (90, 110)))
    assert codes == [201, 201]
    rows = db.scalars(select(Plan).order_by(Plan.id)).all()
    assert len(rows) == 2
    assert signed_in.get("/api/b/ours/plan").json()["plan"]["id"] == rows[-1].id       # same saved_at (frozen): the later row wins


def test_engine_failure_keeps_last_saved_ages_and_logs_once(frozen, signed_in, db, monkeypatch):
    ready(db)
    saved = signed_in.post("/api/b/ours/plan", json={}).json()["plan"]

    def boom(*args, **kwargs):
        raise RuntimeError("the engine fell over")
    monkeypatch.setattr("steward.services.plan.solve", boom)
    for cents in (100_000, 150_000):                        # new values miss the cache, so the engine runs
        r = signed_in.post("/api/b/ours/plan/preview", json={"assumed": {"save_more_monthly_cents": cents}})
        assert r.status_code == 503
        assert r.json() == {"error": "The plan couldn't be worked out right now.",
                            "last_saved": {"comfortable_age": saved["comfortable_age"], "likely_age": saved["likely_age"],
                                           "saved_at": saved["saved_at"], "saved_day": "2026-10-13"}}
    assert db.scalars(select(EventLog.message).where(EventLog.kind == "system")).all() == \
        ["The retirement plan couldn't be worked out: RuntimeError."]
    assert signed_in.get("/api/b/ours/plan").status_code == 200        # the saved levers' result is still in the cache


def test_home_plan_and_taxes_name_the_same_saved_day_near_midnight(frozen, signed_in, db):
    """#52: a plan saved at 23:30 household time is already the next day in UTC, and on a device east of the household.
    Home, Plan (its answer and its history) and Taxes all send the household's day, so no screen turns the saved
    instant into a day itself."""
    ready(db)
    late = datetime(2026, 10, 12, 6, 30, tzinfo=timezone.utc)             # 23:30 on Oct 11 in California
    db.add(Plan(saved_at=late, saved_by=1, engine_version="2026.10.2", levers='{"assumed": {}, "try": {}, "pinned": []}',
                comfortable_age=64, likely_age=61))
    db.commit()
    plan = signed_in.get("/api/b/ours/plan").json()
    assert (plan["plan"]["saved_day"], plan["previous"]["saved_day"]) == ("2026-10-11", "2026-10-11")
    assert signed_in.get("/api/b/ours/plan/history").json()["history"][0]["saved_day"] == "2026-10-11"
    home = signed_in.get("/api/b/ours/home").json()
    assert home["signs"]["plan"]["saved_day"] == "2026-10-11"
    # The book has a late bill, so the headline opens with it and the saved plan is its one good clause.
    assert [c["saved_day"] for c in home["verdict"]["clauses"] if c["kind"] == "plan"] == ["2026-10-11"], home["verdict"]
    assert "saved Oct 11)" in signed_in.get("/api/b/ours/taxes/roth").json()["later_line"]


def test_an_engine_refusal_names_person_1_by_label_in_the_log(frozen, client, db):
    """TRI-1: the engine's refusal goes to the event log, which the household reads. It names Person 1 by the
    household's label, never by a fixed name that every install would show."""
    make_user(db, "alex", "alex@x.com", "Alex")
    make_user(db, "sam", "sam@x.com", "Sam")
    make_ready_household(db)
    add_fact(db, "jr.dob", "2026-05-01", as_of=date(2026, 10, 2))      # born in the plan year
    client.post("/api/auth/login", json={"username": "alex", "password": "pw-one-two-3"})
    assert client.get("/api/b/ours/plan").status_code == 503
    assert db.scalars(select(EventLog.message).where(EventLog.kind == "system")).all() == \
        ["The retirement plan couldn't be worked out: Alex must be born before the plan year."]


def test_results_are_cached_until_an_input_changes(frozen, signed_in, db, app):
    ready(db)
    cache = app.state.books.plan_cache(app.state.books.book("ours").id)
    signed_in.get("/api/b/ours/plan")
    hits, misses = cache.results.hits, cache.results.misses
    signed_in.get("/api/b/ours/plan")
    assert (cache.results.hits, cache.results.misses) == (hits + 1, misses)
    add_fact(db, "jr.salary_cents", "17000000", as_of=date(2026, 10, 2))
    signed_in.get("/api/b/ours/plan")
    assert cache.results.misses == misses + 1
    signed_in.post("/api/b/ours/children", json={"label": "Youngest", "birth_year": 2021, "cost_level": "private"})
    signed_in.get("/api/b/ours/plan")
    assert cache.results.misses == misses + 2


def test_checklist_endpoint(frozen, signed_in, db):
    ready(db)
    items = signed_in.get("/api/b/ours/checklist").json()["items"]
    assert [i["key"] for i in items] == ["emergency_fund", "full_match", "no_card_balance", "hsa", "roth_ira", "k401_limit", "taxable"]
    roth = items[4]
    assert roth["status"] == "not_yet" and roth["why"] == "No Roth IRA contribution in the last 12 months for: JR, Sam."
    assert "backdoor Roth" in roth["sheet"]
    assert items[0]["status"] == "done" and items[1]["status"] == "done"


def test_plan_routes_need_sign_in(client):
    for path in ("/api/b/ours/plan", "/api/b/ours/plan/history", "/api/b/ours/plan/setup", "/api/b/ours/checklist"):
        assert client.get(path).status_code == 401, path
    for path in PATHS:
        assert client.post(path, json={}).status_code == 401, path


def test_bodiless_post_without_json_type_is_refused(frozen, signed_in, db):
    """Ruling 0: a same-site simple request (no body, no JSON content type) can't save a plan."""
    ready(db)
    for headers in ({"content-type": "text/plain"}, {"content-type": "application/x-www-form-urlencoded"}, {}):
        r = signed_in.post("/api/b/ours/plan", content=b"", headers=headers)
        assert r.status_code == 415 and r.json() == {"error": "That request wasn't understood."}, headers
    assert db.scalar(select(Plan.id)) is None
    r = signed_in.post("/api/b/ours/plan", content=b"", headers={"content-type": "application/json; charset=utf-8"})
    assert r.status_code == 201


def test_plan_and_setup_name_the_household(frozen, signed_in, db):
    ready(db)
    names = {"jr": "JR", "spouse": "Sam", "joint": "Joint"}
    assert signed_in.get("/api/b/ours/plan").json()["names"] == names
    assert signed_in.get("/api/b/ours/plan/setup").json()["names"] == names


def test_the_business_range_reaches_the_plan(frozen, signed_in, db):
    from steward.business.settings import update_settings
    ready(db)
    r = signed_in.get("/api/b/ours/plan").json()
    assert r["business"] is None and r["levers"]["try"]["consulting_cents"] == 600_000
    update_settings(db, {"type": "sole_prop", "expect": {"low_cents": 400_000, "high_cents": 1_000_000}},
                    year=2026, this_year=2026, periods=2, user_id=1, at=frozen)
    db.commit()
    r = signed_in.get("/api/b/ours/plan").json()
    assert r["business"] == {"year": 2026, "ytd_cents": 600_000, "low_cents": 1_000_000, "high_cents": 1_600_000,
                             "mid_cents": 1_300_000, "entered": True, "cash_ytd_cents": 600_000,
                             "cash_low_cents": 1_000_000, "cash_high_cents": 1_600_000, "cash_mid_cents": 1_300_000,
                             "ask_expect": False}
    assert r["levers"]["try"]["consulting_cents"] == 1_300_000
    assert r["levers"]["assumed"]["consulting_cents"] == 0           # the saved plan isn't changed (ruling 11)
    # Final review ruling I3: the home office only lowers tax, so the plan keeps trying cash profit.
    update_settings(db, {"home_office": {"method": "simplified", "office_sqft": 300}},
                    year=2026, this_year=2026, periods=2, user_id=1, at=frozen)
    db.commit()
    r = signed_in.get("/api/b/ours/plan").json()
    assert r["business"]["mid_cents"] == 1_300_000 - 150_000 and r["business"]["cash_mid_cents"] == 1_300_000
    assert r["levers"]["try"]["consulting_cents"] == 1_300_000


def test_get_plan_returns_todays_summary_and_a_save_keeps_it(frozen, signed_in, db):
    ready(db)
    body = signed_in.get("/api/b/ours/plan").json()
    now = body["inputs_summary"]
    assert {k: v for k, v in now.items() if k != "comfortable_months"} == {
        "version": 1, "spending_basis_cents": 7_200_000,
        "balances_cents": {"taxed_later": 45_000_000, "tax_free": 9_000_000, "taxable": 15_000_000, "cash": 4_000_000},
        "saving_cents": 200_000, "social_security": {"jr": "record", "spouse": "estimate"}, "big_costs": []}
    age = body["result"]["comfortable_age"]
    assert (age - 1) * 12 <= now["comfortable_months"] <= age * 12
    saved = signed_in.post("/api/b/ours/plan", json={}).json()["plan"]
    assert saved["inputs_summary"] == now
    assert json.loads(db.scalar(select(Plan.inputs_summary).where(Plan.id == saved["id"]))) == now


def test_the_saved_summary_stays_while_todays_follows_the_vault(frozen, signed_in, db):
    accts = ready(db)
    signed_in.post("/api/b/ours/plan", json={})
    db.add(BalanceSnapshot(account_id=accts["k401"].id, as_of=date(2026, 10, 10), balance_cents=46_230_000))
    db.commit()
    body = signed_in.get("/api/b/ours/plan").json()
    assert body["plan"]["inputs_summary"]["balances_cents"]["taxed_later"] == 45_000_000
    assert body["inputs_summary"]["balances_cents"]["taxed_later"] == 46_230_000


def test_history_carries_each_saves_summary_and_none_before_0026(frozen, signed_in, db):
    ready(db)
    db.add(Plan(saved_at=datetime(2026, 10, 1, tzinfo=timezone.utc), saved_by=1, engine_version="2026.10.2",
                levers='{"assumed": {}, "try": {}, "pinned": []}', comfortable_age=64, likely_age=61))
    db.commit()
    signed_in.post("/api/b/ours/plan", json={})
    history = signed_in.get("/api/b/ours/plan/history").json()["history"]
    assert [h["inputs_summary"] is None for h in history] == [False, True]
    assert history[0]["inputs_summary"]["spending_basis_cents"] == 7_200_000


def _old_plan(db):
    old = Plan(saved_at=datetime(2026, 10, 10, 15, tzinfo=timezone.utc), saved_by=1, engine_version="2026.10.2",
               levers='{"assumed": {}, "try": {}, "pinned": []}', comfortable_age=64, likely_age=61)
    db.add(old)
    db.commit()
    return old


def test_previous_is_the_newest_save_from_before_today(frozen, signed_in, db):
    ready(db)
    old = _old_plan(db)
    assert signed_in.get("/api/b/ours/plan").json()["previous"]["id"] == old.id      # nothing saved today yet
    first = signed_in.post("/api/b/ours/plan", json={}).json()["plan"]["id"]
    second = signed_in.post("/api/b/ours/plan", json={}).json()["plan"]["id"]
    body = signed_in.get("/api/b/ours/plan").json()
    assert body["plan"]["id"] == second and first != second
    assert body["previous"]["id"] == old.id and body["previous"]["current"] is False


def test_previous_is_null_when_everything_was_saved_today(frozen, signed_in, db):
    ready(db)
    assert signed_in.get("/api/b/ours/plan").json()["previous"] is None
    signed_in.post("/api/b/ours/plan", json={})
    assert signed_in.get("/api/b/ours/plan").json()["previous"] is None


def test_previous_is_the_plan_itself_when_it_predates_today(frozen, signed_in, db):
    ready(db)
    old = _old_plan(db)
    body = signed_in.get("/api/b/ours/plan").json()
    assert body["previous"]["id"] == body["plan"]["id"] == old.id


def test_the_plan_sends_the_college_table_with_each_kids_choices(frozen, signed_in, db):
    make_ready_household(db)
    college = signed_in.get("/api/b/ours/plan").json()["inputs"]["college"]
    assert college["growth"] == 0.01
    assert list(college["levels"]) == ["two_year", "in_state_public", "out_of_state_public", "private"]
    assert college["children"] == [{"label": "Oldest", "share_pct": 100, "covers_living": True}]


def test_a_lever_error_names_person_1_by_label(frozen, signed_in, db, books):
    from sqlalchemy.orm import Session as DirSession
    from steward.db.directory_models import User
    ready(db)
    with DirSession(books.directory) as ddb:
        ddb.scalar(select(User).where(User.username == "jr")).display_name = "Alex"
        ddb.commit()
    for path in PATHS:
        r = signed_in.post(path, json={"assumed": {"jr_claim_age": True}})
        assert r.status_code == 422 and r.json() == {"error": "Alex's claiming age must be a whole number."}, path
