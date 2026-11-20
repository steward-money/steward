"""The household plan: solving through the cache, saving, history, output shapes and the failure path (spec 7, 8)."""
from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Sequence
from dataclasses import replace
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from steward import clock
from steward.books.people import household_labels, names_of, people_of
from steward.db.engine import begin_write
from steward.db.models import EventLog, Plan
from steward.engine import (
    ENGINE_VERSION, ChecklistItem, EngineInputs, LeverImpact, Levers, SolveResult, clamp, digest, lever_impacts, solve,
)
from steward.engine.impacts import round_months
from steward.engine.solver import COMFORTABLE, fractional_age
from steward.events import log_event
from steward.services.plan_cache import PlanCache
from steward.services.plan_levers import LeverState, display_label, levers_dict, state_from_json, state_json

if TYPE_CHECKING:
    from steward.services.plan_inputs import PlanInputs

log = logging.getLogger(__name__)

NOT_WORKED_OUT = "The plan couldn't be worked out right now."
FAILURE_PREFIX = "The retirement plan couldn't be worked out"
BACKDOOR_ROTH_SHEET = (
    "When income is too high to put money into a Roth IRA directly, there is a common way around it, often called a "
    "backdoor Roth: put the money into a traditional IRA without taking a deduction, then convert it to a Roth IRA. "
    "It is simplest when neither of you has other money in a traditional IRA, because a conversion is taxed in "
    "proportion to all of it. Check the year's income limits, and talk it through with a tax professional first."
)


def _clamped(inputs: EngineInputs) -> EngineInputs:
    return replace(inputs, levers=clamp(inputs.levers, inputs.household))


def solve_cached(cache: PlanCache, inputs: EngineInputs) -> SolveResult:
    inputs = _clamped(inputs)
    return cache.once(("solve", digest(inputs)), lambda: solve(inputs, prepared=cache.prepared_for(inputs.household)))


def _levers_digest(lv: Levers) -> str:
    return hashlib.sha256(json.dumps(levers_dict(lv), sort_keys=True).encode()).hexdigest()[:16]


def impacts_cached(cache: PlanCache, inputs: EngineInputs, trial: Levers) -> tuple[LeverImpact, ...]:
    inputs = _clamped(inputs)
    trial = clamp(trial, inputs.household)
    key = ("impacts", digest(inputs), _levers_digest(trial))
    found = cache.results.get(key)
    if found is not None:
        return found
    base = solve_cached(cache, inputs)
    res = lever_impacts(inputs, trial, base=base, prepared=cache.prepared_for(inputs.household))
    cache.results.put(key, res)
    return res


def people_in(text: str, names: dict[str, str]) -> str:
    """An engine line with {person1} and {person2} filled with the household's labels (books/people.py, Task 5's
    household_labels): the Plan screen's own names, never sent anywhere new."""
    return text.replace("{person1}", names["jr"]).replace("{person2}", names["spouse"])


def engine_text(db: Session, exc: BaseException) -> str:
    """An engine refusal as the household reads it (a reason on a card, a line in the event log): people_in with the
    labels of the people attached to the session, "Person 1" and "Person 2" when there are none."""
    return people_in(str(exc), household_labels(people_of(db)))


def result_out(res: SolveResult, names: dict[str, str]) -> dict:
    return {
        "comfortable_age": res.comfortable_age, "likely_age": res.likely_age,
        "comfortable_year": res.comfortable_year, "likely_year": res.likely_year,
        "band_age": res.band_age,
        "band": [{"year": p.year, "jr_age": p.jr_age, "p10_cents": p.p10_cents, "p50_cents": p.p50_cents, "p90_cents": p.p90_cents}
                 for p in res.band],
        "success_by_age": [[age, round(rate, 4)] for age, rate in res.success_by_age],
        "assumptions": [people_in(t, names) for t in res.assumptions], "assumption_groups": list(res.assumption_groups), "input_digest": res.input_digest,
        "engine_version": res.engine_version, "histories": res.histories,
    }


SUMMARY_VERSION = 1


def inputs_summary(pi: PlanInputs, res: SolveResult) -> dict:
    """What a plan was worked out from, for the Overview's "what changed" line (plan subpages spec). Input facts, never
    causes: the yearly spending basis; investable balances by how they're taxed (a Roth and an HSA both come out
    tax-free); the monthly saving, the vault's yearly surplus over 12; each person's Social Security source; the big
    one-off costs with the year the plan pays them. Plus the Comfortably age in months, interpolated as a lever's effect
    is (engine/impacts.delta_months), so a move of half a year can be said; None when not by 70."""
    hh = pi.household
    frac = fractional_age(res.success_by_age, COMFORTABLE)
    return {
        "version": SUMMARY_VERSION,
        "spending_basis_cents": hh.spending_basis_cents,
        "balances_cents": {"taxed_later": hh.traditional.balance_cents,
                           "tax_free": hh.roth.balance_cents + hh.hsa.balance_cents,
                           "taxable": hh.taxable.balance_cents, "cash": hh.cash.balance_cents},
        "saving_cents": round(hh.surplus_cents / 12),
        "social_security": {"jr": "record" if hh.jr.earnings and not hh.jr.estimated else "estimate",
                            "spouse": "record" if hh.spouse.earnings else "estimate"},
        "big_costs": [{"name": o["name"], "year": o["year"]} for o in pi.set_asides.one_offs],
        "comfortable_months": None if frac is None else round_months(frac * 12),
    }


def moves_out(pi, levers: Levers, benefits: dict) -> dict:
    """How it works' What moves your plan most (P27, P30), from what the plan inputs already hold: Social Security at
    67 from Profile's own figures (services/profile_benefits), retirement spending at the assumed spending lever, the
    year the mortgage is paid off while it isn't, health insurance before Medicare, how many kids and their level when
    they share one, and the monthly saving (inputs_summary's figure)."""
    hh = pi.household
    levels = {c.cost_level for c in hh.children}
    paid_off = hh.mortgage_payoff_year
    return {
        "ss_at_67": {"jr_cents": benefits["jr"]["at_67_cents"], "spouse_cents": benefits["spouse"]["at_67_cents"]},
        "retirement_spending_cents": (hh.spending_basis_cents * levers.spending_pct + 600) // 1200,
        "mortgage_payoff_year": paid_off if hh.mortgage_payment_cents and paid_off is not None and paid_off >= hh.plan_year else None,
        "health_premium_cents": hh.health_premium_cents,
        "college": {"count": len(hh.children), "level": next(iter(levels)) if len(levels) == 1 else None},
        "saving_cents": round(hh.surplus_cents / 12),
    }


def impact_out(i: LeverImpact, names: dict[str, str]) -> dict:
    return {"key": i.key, "label": display_label(i.label, names), "child_label": i.child_label, "try_value": i.try_value,
            "delta_months": i.delta_months, "comfortable_age": i.comfortable_age, "text": i.text}


def latest_plan(db: Session) -> Plan | None:
    return db.scalar(select(Plan).order_by(Plan.saved_at.desc(), Plan.id.desc()).limit(1))


def plan_history(db: Session, limit: int) -> list[Plan]:
    return list(db.scalars(select(Plan).order_by(Plan.saved_at.desc(), Plan.id.desc()).limit(limit)))


def saved_state(p: Plan | None) -> LeverState | None:
    return state_from_json(p.levers) if p is not None else None


def newest_before(db: Session, day_start: datetime) -> Plan | None:
    """The newest saved plan from before `day_start` (the start of today, household time): what the Overview's change
    line compares with (plan subpages, Engineering choice 8)."""
    return db.scalar(select(Plan).where(Plan.saved_at < day_start).order_by(Plan.saved_at.desc(), Plan.id.desc()).limit(1))


def save_plan(db: Session, user_id: int, state: LeverState, res: SolveResult, now: datetime,
              summary: dict | None = None) -> Plan:
    p = Plan(saved_at=now, saved_by=user_id, levers=state_json(state), engine_version=ENGINE_VERSION,
             comfortable_age=res.comfortable_age, likely_age=res.likely_age,
             inputs_summary=json.dumps(summary, sort_keys=True) if summary is not None else None)
    db.add(p)
    db.flush()
    return p


def saved_on(at: datetime) -> date:
    """The household's day a plan was saved (#52). Home, Plan and Taxes all name a save by this day, so near midnight
    a device in another zone can't make one screen say Oct 12 and another Oct 13."""
    return at.astimezone(clock.household_tz()).date()


def plan_out(db: Session, p: Plan, current: bool) -> dict:
    return {"id": p.id, "saved_at": p.saved_at.isoformat(), "saved_day": saved_on(p.saved_at).isoformat(), "saved_by": {"id": p.saved_by, "name": names_of(people_of(db), mid=True).get(p.saved_by)},
            "engine_version": p.engine_version, "comfortable_age": p.comfortable_age, "likely_age": p.likely_age,
            "levers": json.loads(p.levers), "current": current,
            "inputs_summary": json.loads(p.inputs_summary) if p.inputs_summary else None}


def record_failure(db: Session, exc: BaseException) -> dict:
    """Spec 8: an event_log entry (one while it stays unresolved) and the last saved plan's ages with their date."""
    log.error("The retirement plan couldn't be worked out", exc_info=exc)
    detail = engine_text(db, exc).rstrip(".") if isinstance(exc, ValueError) and str(exc) else type(exc).__name__
    begin_write(db)                     # GET /plan comes here too: its read transaction can't take the write lock
    still_open = db.scalar(select(EventLog.id).where(
        EventLog.kind == "system", EventLog.resolved_at.is_(None),
        EventLog.message.startswith(FAILURE_PREFIX, autoescape=True)).limit(1))
    if still_open is None:
        log_event(db, "error", "system", f"{FAILURE_PREFIX}: {detail}."[:500])
    db.commit()
    last = latest_plan(db)
    return {"error": NOT_WORKED_OUT,
            "last_saved": None if last is None else {"comfortable_age": last.comfortable_age, "likely_age": last.likely_age,
                                                     "saved_at": last.saved_at.isoformat(),
                                                     "saved_day": saved_on(last.saved_at).isoformat()}}


def checklist_out(items: Sequence[ChecklistItem], names: dict[str, str]) -> list[dict]:
    out = []
    for i in items:
        row = {"key": i.key, "title": i.title, "status": i.status, "why": people_in(i.why, names)}
        if i.key == "roth_ira":
            row["sheet"] = BACKDOOR_ROTH_SHEET
        out.append(row)
    return out
