"""Roth or traditional (spec 7.3; rulings 15-18). Rate now: this year's marginal rate from the engine (federal and
state tax on the next $100 of ordinary income). Rate later: a projection from the saved plan's retirement age to 95 with today's balances and this
year's 401(k) and match grown to retirement, spending, the mortgage until it ends, health insurance until 65 and Social
Security at the saved claiming ages; the median marginal rate over the first ten years of required withdrawals. In a
low-income year, the conversion that fills this year's federal bracket and what it would cost. Steward never moves
money."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from statistics import median
from typing import Literal

from sqlalchemy.orm import Session

from steward.engine import Levers
from steward.engine.decisions import Conversion, conversion_room, retirement_choice
from steward.engine.project import Assumptions, Expense, Income, ProjectionInputs, project
from steward.engine.social_security import benefit_schedule, trust_fund_factor
from steward.engine.tax import annual_tax, rules
from steward.services.plan import latest_plan, saved_on, saved_state
from steward.services.plan_inputs import PlanInputs, SetupIncomplete, build_inputs
from steward.taxes.household import household_tax
from steward.taxes.quarterly import NEVER, rules_note

DEFAULT_RETIRE_AGE = 65
END_AGE = 95
RMD_YEARS = 10
MEDICARE_AGE = 65
FINISH_PLAN = "Finish Plan setup to compare with retirement."
SAVED = "the {which} age in the plan saved {date}"   # #17: which age, and when, as Home says it
NOT_SAVED = "65 until a plan is saved"
NOW_ONLY = "Traditional saves about {pct} of each dollar in tax this year; there's no retirement estimate to compare with yet."


@dataclass(frozen=True)
class LaterRate:
    rate: float
    retire_age: int
    retire_year: int
    from_year: int           # the first year of required withdrawals
    to_year: int
    line: str


def _grown(balance: float, yearly: float, grow: float, n: int) -> float:
    return balance * grow ** n + sum(yearly * grow ** i for i in range(n))


def later_rate(pi: PlanInputs, levers: Levers, retire_age: int, source: str, a: Assumptions = Assumptions()) -> LaterRate:
    hh = pi.household
    jr_birth = hh.jr.birth_year
    retire_year = max(jr_birth + retire_age, hh.plan_year)
    n = retire_year - hh.plan_year
    grow = 1.0 + a.real_return
    k401 = hh.salary_cents * hh.k401_pct_bp / 10_000
    roth_part = k401 * hh.k401_roth_share_bp / 10_000
    match = hh.salary_cents * min(hh.k401_pct_bp, hh.match_cap_bp) / 10_000 * hh.match_rate_bp / 10_000
    mfj = hh.filing_status == "mfj"
    sched = benefit_schedule(hh, levers, [retire_age])
    jr_claim = jr_birth + levers.jr_claim_age
    jr_month = max(float(sched.jr_own[0]), sched.jr_spousal if mfj else 0.0)
    incomes = [Income("Social Security", round(jr_month * 1200 * trust_fund_factor(jr_claim, levers.full_ss_benefits)),
                      max(jr_claim, retire_year), kind="social_security", owner=0)]
    if mfj:
        sp_claim = hh.spouse.birth_year + levers.spouse_claim_age
        sp_month = max(sched.sp_own, float(sched.sp_spousal[0]))
        incomes.append(Income("Social Security", round(sp_month * 1200 * trust_fund_factor(sp_claim, levers.full_ss_benefits)),
                              max(sp_claim, retire_year), kind="social_security", owner=1))
    expenses = [Expense("Spending", hh.spending_basis_cents * levers.spending_pct // 100, retire_year)]
    if hh.mortgage_payment_cents and (hh.mortgage_payoff_year is None or hh.mortgage_payoff_year >= retire_year):
        expenses.append(Expense("Mortgage", round(hh.mortgage_payment_cents / (1.0 + a.inflation) ** n), retire_year,
                                hh.mortgage_payoff_year, growth="fixed_dollars", kind="mortgage"))
    medicare_year = jr_birth + MEDICARE_AGE
    if hh.health_premium_cents and retire_year < medicare_year:
        expenses.append(Expense("Health insurance", hh.health_premium_cents, retire_year, medicare_year - 1, kind="insurance"))
    years = max(min(jr_birth + END_AGE - retire_year + 1, 80), 1)
    first_rmd = max(jr_birth + rules.rmd_start_age(jr_birth, retire_year), retire_year)
    p = ProjectionInputs(start_year=retire_year, filing="mfj" if mfj else "single", state=hh.state,
                         birth_years=(jr_birth, hh.spouse.birth_year) if mfj else (jr_birth,),
                         cash_cents=round(hh.cash.balance_cents * grow ** n),
                         taxable_cents=round(hh.taxable.balance_cents * grow ** n),
                         traditional_cents=round(_grown(hh.traditional.balance_cents, k401 - roth_part + match, grow, n)),
                         roth_cents=round(_grown(hh.roth.balance_cents, roth_part, grow, n)),
                         incomes=tuple(incomes), expenses=tuple(expenses))
    last_year = retire_year + years - 1
    rows = project(p, a, years, marginal_from=min(first_rmd, last_year))
    window = [r for r in rows if r.year >= min(first_rmd, last_year)][:RMD_YEARS]
    rate = round(median(r.tax.marginal_rate for r in window), 4)
    return LaterRate(rate, retire_age, retire_year, window[0].year, window[-1].year,
                     f"From retiring at {retire_age} ({source}), with required withdrawals from {first_rmd} and Social "
                     "Security.")


def saved_label(at: datetime, today: date) -> str:
    """"Oct 7", or "Dec 30, 2025" from another year: the household's day of the save (services.plan.saved_on, #52),
    the day Home and Plan name it by too."""
    d = saved_on(at)
    return f"{d:%b} {d.day}" if d.year == today.year else f"{d:%b} {d.day}, {d.year}"


def saved_plan_later_rate(db: Session, today: date, pi: PlanInputs) -> LaterRate:
    """The rate later for the saved plan: its likely retirement age (comfortable age, then 65 when none is saved),
    its claiming ages and its spending (ruling 16). Shared by Roth or traditional and part 4's step 5. The line says
    which age and when the plan was saved (#17): Home shows the saved Comfortably age with its date, Plan today's."""
    row = latest_plan(db)
    state = saved_state(row)
    assumed = state.assumed if state else Levers()   # levers that no longer parse (a renamed lever) fall back to defaults
    age = (row.likely_age or row.comfortable_age) if row is not None else None
    if row is None or age is None:
        return later_rate(pi, assumed, DEFAULT_RETIRE_AGE, NOT_SAVED)
    which = "Likely" if row.likely_age else "Comfortably"
    return later_rate(pi, assumed, age, SAVED.format(which=which, date=saved_label(row.saved_at, today)))


@dataclass(frozen=True)
class RothView:
    year: int
    rate_now: float
    rate_later: float | None
    lean: Literal["roth", "traditional", "either"]
    line: str
    later: LaterRate | None
    later_reason: str | None
    conversion: Conversion | None
    rules_note: str
    notes: tuple[str, ...]


def roth_view(db: Session, today: date) -> RothView:
    year = today.year
    hh = household_tax(db, year, today, point="mid")
    result = annual_tax(hh.facts, year)
    later = reason = None
    traditional = 0
    try:
        pi = build_inputs(db, today)
    except SetupIncomplete:
        reason = FINISH_PLAN
    else:
        later = saved_plan_later_rate(db, today, pi)
        traditional = pi.household.traditional.balance_cents
    if later is not None:
        c = retirement_choice(hh.facts, None, year, rate_later=later.rate)
        rate_now, line = c.now_rate, c.line
        lean = "either" if c.about_same else ("traditional" if c.lean == "pretax" else "roth")
    else:
        rate_now, lean = result.marginal_rate, "either"
        line = NOW_ONLY.format(pct=f"{round(rate_now * 100)}%")
    conv = conversion_room(hh.facts, year, result, traditional_cents=traditional,
                           rate_later=later.rate if later else None)
    note = rules_note(year, result.rules_year, result.state.rules_year if result.state.calculated else None,
                      hh.facts.state)
    return RothView(year, rate_now, later.rate if later else None, lean, line, later, reason, conv, note, hh.notes)


def roth_out(v: RothView) -> dict:
    return {
        "available": True, "year": v.year, "rate_now": v.rate_now, "rate_later": v.rate_later,
        "lean": v.lean, "line": v.line,
        "later_line": v.later.line if v.later else v.later_reason,
        "conversion": None if v.conversion is None else {"amount_cents": v.conversion.amount_cents,
                                                         "tax_cents": v.conversion.tax_cents, "line": v.conversion.line},
        "rules_note": v.rules_note, "notes": list(v.notes), "never": NEVER,
    }
