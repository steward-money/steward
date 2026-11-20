import { dateLabel, dollars } from '../../lib/format';
import type { InputsSummary, PlanReady, SavedPlan } from './api';
import * as C from './copy';

/** Which way the Comfortably age moved: earlier is the good direction. */
export type ChangeDir = 'earlier' | 'later' | 'same';
/** `months`: how far the Comfortably age moved; null when one side is "not by 70". */
export type Change = { dir: ChangeDir; months: number | null; text: string };
export type Closer = 'half' | 'year' | 'years';
type Age = { age: number | null; months: number | null };

const MONTHLY_MIN = 5_000;               // $50 a month
const BALANCE_MIN = 100_000;             // $1,000
const EVENT = Number.MAX_SAFE_INTEGER;   // an event outranks any amount
const roundTo = (cents: number, step: number) => Math.round(Math.abs(cents) / step) * step;

/** Years as the levers count them: rounded half up. */
export const yearsOf = (months: number) => Math.floor((Math.abs(months) + 6) / 12);

/** "1 month", "6 months", "1 year", "2 years": months under a year, then years. */
export function spanText(months: number): string {
  const m = Math.abs(months);
  if (m < 12) return m === 1 ? C.SPAN_MONTH : C.SPAN_MONTHS.replace('{n}', String(m));
  const y = yearsOf(m);
  return y === 1 ? C.SPAN_YEAR : C.SPAN_YEARS.replace('{n}', String(y));
}

/** Months the Comfortably age moved, negative = earlier: by the interpolated months when both sides have them, else by
 *  whole ages. A changed whole age never reads as 0 (engine/impacts.delta_months does the same). Null when either side
 *  is not by 70. */
export function monthsMoved(now: Age, then: Age): number | null {
  if (now.age === null || then.age === null) return null;
  let d = now.months !== null && then.months !== null ? now.months - then.months : (now.age - then.age) * 12;
  if (d === 0 && now.age !== then.age) d = now.age < then.age ? -1 : 1;
  return d;
}

/** Up to two input facts, biggest first (Engineering choice 5): what changed, not why the age moved. */
export function changeFacts(now: InputsSummary, then: InputsSummary): string[] {
  const facts: { size: number; text: string }[] = [];
  const toRecord = (['jr', 'spouse'] as const).some((who) => now.social_security[who] === 'record' && then.social_security[who] === 'estimate');
  if (toRecord) facts.push({ size: EVENT, text: C.FACT_SS_RECORD });
  const had = new Set(then.big_costs.map((b) => b.name));
  const added = now.big_costs.filter((b) => !had.has(b.name)).sort((a, b) => a.year - b.year)[0];
  if (added) facts.push({ size: EVENT, text: C.FACT_BIG_COST.replace('{name}', added.name).replace('{year}', String(added.year)) });
  const spend = Math.round((now.spending_basis_cents - then.spending_basis_cents) / 12);
  if (Math.abs(spend) >= MONTHLY_MIN) {
    facts.push({ size: Math.abs(spend) * 12, text: (spend < 0 ? C.FACT_SPENDING_LOWER : C.FACT_SPENDING_HIGHER).replace('{amount}', dollars(roundTo(spend, 1_000))) });
  }
  const retirement = (s: InputsSummary) => s.balances_cents.taxed_later + s.balances_cents.tax_free;
  const grew = retirement(now) - retirement(then);
  if (Math.abs(grew) >= BALANCE_MIN) {
    facts.push({ size: Math.abs(grew), text: (grew > 0 ? C.FACT_RETIREMENT_GREW : C.FACT_RETIREMENT_FELL).replace('{amount}', dollars(roundTo(grew, 10_000))) });
  }
  const save = now.saving_cents - then.saving_cents;
  if (Math.abs(save) >= MONTHLY_MIN) {
    facts.push({ size: Math.abs(save) * 12, text: (save > 0 ? C.FACT_SAVING_MORE : C.FACT_SAVING_LESS).replace('{amount}', dollars(roundTo(save, 1_000))) });
  }
  return facts.sort((a, b) => b.size - a.size).slice(0, 2).map((f) => f.text);
}

/** The plan the line compares with: the newest saved before today (`previous`, which the server makes the current plan
 *  itself when that one predates today), else the current one. Engineering choice 8. */
export const baseOf = (data: PlanReady): SavedPlan | null => data.previous ?? data.plan;

/** The Overview's line (plan subpages, What changed since the last saved plan): today's result against the newest plan
 *  saved before today (Engineering choice 8), so a save made today shows what it changed. Null when nothing is saved
 *  yet. */
export function changeLine(data: PlanReady, today: Date): Change | null {
  const saved = baseOf(data);
  if (!saved) return null;
  const date = dateLabel(saved.saved_day, today);
  const now: Age = { age: data.result.comfortable_age, months: data.inputs_summary?.comfortable_months ?? null };
  const then: Age = { age: saved.comfortable_age, months: saved.inputs_summary?.comfortable_months ?? null };
  const facts = saved.inputs_summary && data.inputs_summary ? changeFacts(data.inputs_summary, saved.inputs_summary) : [];
  const tail = facts.length === 0 ? '.' : `: ${facts.length === 2 ? C.FACTS_AND.replace('{a}', facts[0]).replace('{b}', facts[1]) : facts[0]}.`;
  const same: Change = { dir: 'same', months: 0, text: C.CHANGE_SAME.replace('{date}', date) };
  if (now.age === null && then.age === null) return same;
  if (now.age === null) return { dir: 'later', months: null, text: C.CHANGE_NOT_NOW.replace('{age}', String(then.age)).replace('{date}', date) + tail };
  if (then.age === null) return { dir: 'earlier', months: null, text: C.CHANGE_NOW_ABLE.replace('{age}', String(now.age)).replace('{date}', date) + tail };
  const d = monthsMoved(now, then)!;
  if (d === 0) return same;
  if (d < 0) return { dir: 'earlier', months: -d, text: C.CHANGE_EARLIER.replace('{span}', spanText(d)).replace('{date}', date) + tail };
  return { dir: 'later', months: d, text: C.CHANGE_LATER.replace('{span}', spanText(d)).replace('{date}', date) + tail };
}

/** Which cheers a change earns (Engineering choice 7): none under 6 months earlier, later or the same. */
export function closerOf(c: Change): Closer | null {
  if (c.dir !== 'earlier') return null;
  if (c.months === null) return 'years';            // from "not by 70" to an age
  if (c.months < 6) return null;
  if (c.months < 12) return 'half';
  return yearsOf(c.months) === 1 ? 'year' : 'years';
}

const CLOSER_KEY = 'steward-closer';
let closerMemory: string | null = null;   // when storage is blocked: this visit only
/** For tests: forget the change this visit remembers when storage is blocked. */
export function forgetCloser(): void { closerMemory = null; }

/** True the first time this device sees this change (one saved plan against one set of inputs), and remembers it, so a
 *  second visit, a refetch or a double effect never celebrates it again. */
export function firstTimeFor(key: string): boolean {
  let last = closerMemory;
  try { last = localStorage.getItem(CLOSER_KEY); } catch { /* blocked: the memory above */ }
  if (last === key) return false;
  closerMemory = key;
  try { localStorage.setItem(CLOSER_KEY, key); } catch { /* private mode: this visit only */ }
  return true;
}
