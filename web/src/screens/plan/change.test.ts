import { describe, expect, it } from 'vitest';
import type { InputsSummary, PlanReady } from './api';
import { changeFacts, changeLine, closerOf, monthsMoved, spanText, type Change } from './change';
import { CLOSER_CHEERS } from './copy';
import { planFixture, summaryFixture } from './fixtures';

const TODAY = new Date(2026, 9, 28);
const ON = 'Oct 12';   // the fixture's saved_day, the household's day the server sends (#52)
/** The fixture, with today's summary (`now`) or the saved one (`then`) changed. */
function plan(f: (d: PlanReady, now: InputsSummary, then: InputsSummary) => void): PlanReady {
  const d = planFixture();
  f(d, d.inputs_summary, d.plan!.inputs_summary!);
  return d;
}
const line = (d: PlanReady) => changeLine(d, TODAY);
const re = (s: string) => new RegExp(`^${s}$`);

describe('the span (plan subpages, What changed since the last saved plan)', () => {
  it('says months under a year, then years rounded half up, as the levers do', () => {
    expect([1, 6, 11, 12, 17, 18, 30].map(spanText)).toEqual(['1 month', '6 months', '11 months', '1 year', '1 year', '2 years', '3 years']);
    expect(spanText(-12)).toBe('1 year');
  });

  it('moves by months when both sides have them, by whole ages otherwise, and never reads 0 when the age changed', () => {
    expect(monthsMoved({ age: 62, months: 738 }, { age: 63, months: 750 })).toBe(-12);
    expect(monthsMoved({ age: 62, months: null }, { age: 63, months: 750 })).toBe(-12);
    expect(monthsMoved({ age: 62, months: 750 }, { age: 63, months: 750 })).toBe(-1);
    expect(monthsMoved({ age: null, months: null }, { age: 63, months: 750 })).toBeNull();
  });
});

describe('the line, in its four cases and without a summary', () => {
  it('Earlier: "▼ 1 year earlier than on Oct 12:" and the two biggest facts', () => {
    const d = plan((p, now) => {
      p.result.comfortable_age = 62;
      now.comfortable_months = 738;
      now.balances_cents.taxed_later += 1_234_500;   // $12,345, said as $12,300
      now.spending_basis_cents -= 41_000 * 12;      // $410 a month
      now.saving_cents += 2_000;                    // $20 a month: too small to say
    });
    expect(line(d)).toMatchObject({ dir: 'earlier', months: 12 });
    expect(line(d)!.text).toMatch(re(`1 year earlier than on ${ON}: retirement accounts grew \\$12,300 and spending is \\$410 a month lower\\.`));
  });

  it('Later: "6 months later than on Oct 12" with what changed', () => {
    const d = plan((_p, now) => { now.comfortable_months = 756; now.saving_cents -= 20_000; });
    expect(line(d)).toMatchObject({ dir: 'later', months: 6 });
    expect(line(d)!.text).toMatch(re(`6 months later than on ${ON}: you're saving \\$200 a month less\\.`));
  });

  it('No change: "No change since Oct 12.", and no facts even when an input moved', () => {
    const d = plan((_p, now) => { now.balances_cents.taxed_later += 5_000_000; });
    expect(line(d)).toMatchObject({ dir: 'same', months: 0 });
    expect(line(d)!.text).toMatch(re(`No change since ${ON}\\.`));
  });

  it('no saved plan yet: no line at all', () => {
    expect(line({ ...planFixture(), plan: null })).toBeNull();
  });

  it('a save from before the summary: the move alone, "1 year earlier than on Oct 12."', () => {
    const d = plan((p, now) => { p.plan!.inputs_summary = null; p.plan!.comfortable_age = 64; now.spending_basis_cents -= 41_000 * 12; });
    expect(line(d)!.text).toMatch(re(`1 year earlier than on ${ON}\\.`));
  });

  it('a move inside the same whole age is said in months', () => {
    const d = plan((_p, now) => { now.comfortable_months = 747; });
    expect(line(d)!.text).toMatch(re(`3 months earlier than on ${ON}\\.`));
  });

  it('"not by 70" on either side reads plainly, never as a number of years', () => {
    const lost = plan((p, now) => { p.result.comfortable_age = null; now.comfortable_months = null; now.spending_basis_cents += 90_000 * 12; });
    expect(line(lost)).toMatchObject({ dir: 'later', months: null });
    expect(line(lost)!.text).toMatch(re(`Not by 70 now, from 63 on ${ON}: spending is \\$900 a month higher\\.`));
    const found = plan((p, _now, then) => { p.plan!.comfortable_age = null; then.comfortable_months = null; });
    expect(line(found)).toMatchObject({ dir: 'earlier', months: null });
    expect(line(found)!.text).toMatch(re(`Comfortably at 63 now, from not by 70 on ${ON}\\.`));
    const neither = plan((p, now, then) => {
      p.result.comfortable_age = null; p.plan!.comfortable_age = null; now.comfortable_months = null; then.comfortable_months = null;
    });
    expect(line(neither)!.dir).toBe('same');
    for (const d of [lost, found, neither]) expect(line(d)!.text).not.toMatch(/NaN|\d+ years?|0 months/);
  });

  it('compares with the newest plan saved before today when the current one was saved today (Engineering choice 8)', () => {
    const d = planFixture();
    d.previous = d.plan;
    d.plan = { ...d.plan!, id: 9, saved_at: '2026-10-28T15:00:00+00:00', saved_day: '2026-10-28', comfortable_age: 62 };
    d.result.comfortable_age = 62;
    d.inputs_summary.comfortable_months = 738;
    expect(line(d)!.text).toMatch(re(`1 year earlier than on ${ON}\\.`));
  });

  it('a plan saved today and none before: "No change since Oct 28."', () => {
    const d = planFixture();
    d.plan = { ...d.plan!, saved_at: '2026-10-28T15:00:00+00:00', saved_day: '2026-10-28', inputs_summary: null };
    d.previous = null;
    expect(line(d)).toMatchObject({ dir: 'same' });
    expect(line(d)!.text).toBe('No change since Oct 28.');
  });
});

describe('the facts', () => {
  it('put the two events first: Social Security from the record, then the earliest new big cost', () => {
    const then = summaryFixture();
    const now = summaryFixture();
    now.social_security.spouse = 'record';
    now.big_costs = [{ name: 'Car', year: 2031 }, { name: 'Roof', year: 2029 }];
    now.balances_cents.tax_free += 5_000_000;
    expect(changeFacts(now, then)).toEqual(['Social Security is now from your record', 'a new big cost: Roof in 2029']);
  });

  it('rank amounts by their size in a year: $410 a month, then $300 a month, then $3,000 in the accounts', () => {
    const then = summaryFixture();
    const now = summaryFixture();
    now.spending_basis_cents += 41_000 * 12;
    now.saving_cents += 30_000;
    now.balances_cents.taxed_later -= 300_000;
    expect(changeFacts(now, then)).toEqual(['spending is $410 a month higher', "you're saving $300 a month more"]);
    now.spending_basis_cents = then.spending_basis_cents;
    now.saving_cents = then.saving_cents;
    expect(changeFacts(now, then)).toEqual(['retirement accounts fell $3,000']);
  });

  it('say nothing of small moves, taxable or cash balances, a record that went back to an estimate, or a big cost already there', () => {
    const then = summaryFixture();
    then.big_costs = [{ name: 'Roof', year: 2029 }];
    const now = summaryFixture();
    now.big_costs = [{ name: 'Roof', year: 2030 }];
    now.social_security.jr = 'estimate';
    now.spending_basis_cents += 4_000 * 12;
    now.saving_cents -= 4_000;
    now.balances_cents.taxed_later += 90_000;
    now.balances_cents.taxable += 9_000_000;
    now.balances_cents.cash -= 2_000_000;
    expect(changeFacts(now, then)).toEqual([]);
  });
});

describe('the celebration band and its cheers (plan subpages decision 7)', () => {
  const up = (months: number | null): Change => ({ dir: 'earlier', months, text: '' });
  it('half a year from 6 months, a year at "1 year", years from "2 years" or up from not by 70; nothing smaller, later or the same', () => {
    expect([up(3), up(6), up(11), up(12), up(17), up(18), up(null)].map(closerOf)).toEqual([null, 'half', 'half', 'year', 'year', 'years', 'years']);
    expect(closerOf({ dir: 'later', months: 12, text: '' })).toBeNull();
    expect(closerOf({ dir: 'same', months: 0, text: '' })).toBeNull();
  });

  it('has its own cheers for half a year, a year (starting "A year closer.") and two or more, all different', () => {
    expect(CLOSER_CHEERS.year[0]).toBe('A year closer.');
    const all = [...CLOSER_CHEERS.half, ...CLOSER_CHEERS.year, ...CLOSER_CHEERS.years];
    expect(new Set(all).size).toBe(all.length);
    expect([CLOSER_CHEERS.half.length, CLOSER_CHEERS.year.length, CLOSER_CHEERS.years.length]).toEqual([6, 6, 6]);
  });
});
