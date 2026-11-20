import type { Clause, Problem, Stale, VerdictData } from '../api/home';
import { label } from './categories';
import {
  H_BEHIND, H_BEHIND_LITTLE, H_BILL_MORE, H_BILLS_PAID, H_EMPTY, H_LATE_BILL, H_LATE_BILLS, H_MORE_OUT, H_MOSTLY, H_NEW_MONTH,
  H_OTHERWISE, H_OVER_MANY, H_OVER_ONE, H_OVERDRAWN, H_PLAN, H_PLAN_STILL, H_SPENDING_CLOSE, H_SPENDING_ON_TRACK, H_STALE_N, H_STALE_ONE, H_STALE_RANGE, H_STEADY,
} from './copy';
import { dateLabel, dollars } from './format';

const fill = (s: string, vars: Record<string, string | number>) => s.replace(/\{(\w+)\}/g, (_, k: string) => String(vars[k]));
const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
const OPENER = { steady: H_STEADY, mostly: H_MOSTLY, new_month: H_NEW_MONTH } as const;
const SUFFIX: Record<number, string> = { 1: 'st', 2: 'nd', 3: 'rd' };

/** 1st, 2nd, 3rd, 4th … 11th, 12th, 13th … 21st. */
export function ordinal(n: number): string {
  const tens = n % 100;
  return `${n}${tens >= 11 && tens <= 13 ? 'th' : SUFFIX[n % 10] ?? 'th'}`;
}

/** "X", "X, and Y", "X, Y, and Z" (spec, Joining). */
export function joinClauses(parts: string[]): string {
  if (parts.length <= 1) return parts.join('');
  return `${parts.slice(0, -1).join(', ')}, and ${parts[parts.length - 1]}`;
}

function problem(p: Problem): string {
  switch (p.kind) {
    case 'late_bill': return fill(H_LATE_BILL, { category: label(p.category), day: ordinal(p.usual_day) });
    case 'late_bills': return fill(H_LATE_BILLS, { n: p.n });
    case 'overdrawn': return fill(H_OVERDRAWN, { account: p.account, amount: dollars(-p.cents) });
  }
}

/** A category's label as it is ("Eating out is ahead…", P2): labels like "Childcare and school" don't take "'s". The
 *  plan is the saved one, so it says when it was saved (#17). */
function clause(c: Clause, today: Date): string {
  switch (c.kind) {
    case 'behind_until_payday': return c.little ? H_BEHIND_LITTLE : H_BEHIND;
    case 'more_out_than_in': return H_MORE_OUT;
    case 'over_one': return fill(H_OVER_ONE, { category: label(c.category) });
    case 'over_many': return fill(H_OVER_MANY, { n: c.n });
    case 'bill_more': return fill(H_BILL_MORE, { category: label(c.category) });
    case 'spending_close': return H_SPENDING_CLOSE;
    case 'spending_on_track': return H_SPENDING_ON_TRACK;
    case 'bills_paid': return H_BILLS_PAID;
    case 'plan': return fill(c.still ? H_PLAN_STILL : H_PLAN, { age: c.age, date: dateLabel(c.saved_day, today) });
  }
}

/** #15: the account by name when it's the only one (Home lists it below), else how many; their age, or its range. */
function stale(s: Stale): string {
  if (s.n === 1 && s.account) return fill(H_STALE_ONE, { account: s.account, days: s.oldest_days });
  return fill(s.newest_days === s.oldest_days ? H_STALE_N : H_STALE_RANGE, { n: s.n, days: s.oldest_days, newest: s.newest_days });
}

/** The calm check-in sentence (P1, P2): problems said plainly, each its own sentence, then "Otherwise, …"; or the
 *  opener and its clauses; then the stale caveat, last, whatever came before. An empty book says only that. `today`
 *  is the household's day, for the year in a saved plan's date. */
export function headline(v: VerdictData, today: Date = new Date()): string {
  if (v.opener === 'empty') return H_EMPTY;
  const said = v.clauses.map((c) => clause(c, today));
  const out: string[] = [];
  if (v.opener === 'problem') {
    out.push(...v.problems.map(problem));
    if (said.length > 0) out.push(fill(H_OTHERWISE, { clauses: joinClauses(said) }));
  } else {
    out.push(OPENER[v.opener]);
    if (said.length > 0) out.push(`${cap(joinClauses(said))}.`);
  }
  if (v.stale) out.push(stale(v.stale));
  return out.join(' ');
}
