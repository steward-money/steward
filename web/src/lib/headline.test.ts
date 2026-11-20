import { describe, expect, it } from 'vitest';
import type { Clause, VerdictData } from '../api/home';
import { headline, joinClauses, ordinal } from './headline';

const v = (p: Partial<VerdictData>): VerdictData => ({ opener: 'steady', problems: [], clauses: [], stale: null, best_month: false, ...p });
const one = (c: Clause) => headline(v({ clauses: [c] }));
const STALE_ONE = { n: 1, oldest_days: 29, newest_days: 29, account: 'Joint checking' };
const SAVED = '2026-10-12';   // the household's day, as the server sends it (#52)

describe('headline() (Home spec, The headline)', () => {
  it.each([
    [v({ clauses: [{ kind: 'spending_on_track' }, { kind: 'bills_paid' }, { kind: 'plan', age: 54, still: true, saved_day: SAVED }] }), "A steady month. Spending's on track, bills are paid, and the plan saved Oct 12 still says 54."],
    [v({ opener: 'mostly', clauses: [{ kind: 'behind_until_payday', little: true }, { kind: 'over_one', category: 'dining' }] }), 'Mostly steady. A little behind until payday, and Eating out is ahead of its target.'],
    [v({ opener: 'problem', problems: [{ kind: 'late_bill', category: 'utilities', usual_day: 12 }], clauses: [{ kind: 'spending_on_track' }, { kind: 'plan', age: 54, still: false, saved_day: SAVED }] }), "Utilities hasn't come out yet; it's usually by the 12th. Otherwise, spending's on track, and the plan saved Oct 12 says 54."],
    [v({ opener: 'problem', problems: [{ kind: 'overdrawn', account: 'Checking', cents: -12000 }] }), 'Checking is overdrawn by $120.'],
    [v({ clauses: [{ kind: 'spending_on_track' }], stale: { n: 3, oldest_days: 29, newest_days: 29, account: null } }), "A steady month. Spending's on track. 3 accounts haven't updated in 29 days."],
    [v({ opener: 'new_month', clauses: [{ kind: 'plan', age: 54, still: true, saved_day: SAVED }] }), 'A new month. The plan saved Oct 12 still says 54.'],
  ])('says the examples table word for word (%#)', (data, text) => {
    expect(headline(data)).toBe(text);
  });

  it('words every copy row', () => {
    expect(one({ kind: 'behind_until_payday', little: false })).toBe('A steady month. Behind until payday.');
    expect(one({ kind: 'more_out_than_in' })).toBe('A steady month. More out than in so far.');
    expect(one({ kind: 'over_many', n: 3 })).toBe('A steady month. 3 categories are ahead of their targets.');
    expect(one({ kind: 'bill_more', category: 'utilities' })).toBe('A steady month. Utilities came in higher than usual.');
    expect(one({ kind: 'spending_close' })).toBe("A steady month. Spending's close to its targets.");
    expect(one({ kind: 'bills_paid' })).toBe('A steady month. Bills are paid.');
    expect(headline(v({ opener: 'problem', problems: [{ kind: 'late_bills', n: 2 }] }))).toBe("2 bills haven't come out yet.");
    expect(headline(v({ opener: 'problem', problems: [{ kind: 'late_bills', n: 2 }, { kind: 'overdrawn', account: 'Joint checking', cents: -9050 }] })))
      .toBe("2 bills haven't come out yet. Joint checking is overdrawn by $91.");
    expect(headline(v({ opener: 'mostly' }))).toBe('Mostly steady.');
    expect(headline(v({ opener: 'empty', stale: STALE_ONE }))).toBe('Nothing to go on yet.');
    expect(headline(v({ opener: 'problem', problems: [{ kind: 'overdrawn', account: 'Checking', cents: -100 }], stale: STALE_ONE })))
      .toBe("Checking is overdrawn by $1. Joint checking hasn't updated in 29 days.");
  });

  it("names the saved plan by its date, since Plan shows today's figure (#17), with the year when it's another year", () => {
    const plan = (saved_day: string) => v({ clauses: [{ kind: 'plan', age: 66, still: false, saved_day }] });
    expect(headline(plan(SAVED), new Date(2026, 10, 16))).toBe('A steady month. The plan saved Oct 12 says 66.');
    expect(headline(plan('2025-12-30'), new Date(2026, 0, 9))).toBe('A steady month. The plan saved Dec 30, 2025 says 66.');
  });

  it('says how many accounts are behind and how long, by name when there is one alone (#15)', () => {
    expect(headline(v({ stale: STALE_ONE }))).toBe("A steady month. Joint checking hasn't updated in 29 days.");
    expect(headline(v({ stale: { n: 2, oldest_days: 29, newest_days: 29, account: null } }))).toBe("A steady month. 2 accounts haven't updated in 29 days.");
    expect(headline(v({ stale: { n: 2, oldest_days: 29, newest_days: 4, account: null } }))).toBe("A steady month. 2 accounts haven't updated in 4 to 29 days.");
  });

  it('joins 1, 2 and 3 clauses with a comma before "and", and capitalizes the first letter', () => {
    expect(joinClauses(['a'])).toBe('a');
    expect(joinClauses(['a', 'b'])).toBe('a, and b');
    expect(joinClauses(['a', 'b', 'c'])).toBe('a, b, and c');
    expect(headline(v({ clauses: [{ kind: 'spending_on_track' }, { kind: 'bills_paid' }] }))).toBe("A steady month. Spending's on track, and bills are paid.");
  });

  it('says a day of the month as people do', () => {
    expect([1, 2, 3, 4, 11, 12, 13, 21, 22, 23, 31].map(ordinal)).toEqual(['1st', '2nd', '3rd', '4th', '11th', '12th', '13th', '21st', '22nd', '23rd', '31st']);
  });
});
