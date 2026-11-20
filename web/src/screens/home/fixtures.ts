// Unit-test data only; nothing in the app imports this file. Made-up figures for Friday, Oct 30 2026 (TEST_NOW).
import type { HomeData } from '../../api/home';

/** The household's day the plan was saved, as the server sends it (#52). */
export const SAVED = '2026-10-12';

export const HOME: HomeData = {
  as_of: '2026-10-30', month: '2026-10', day: 30, days: 31, mark: '2026-10-30T19:00:00+00:00',
  verdict: { opener: 'steady', problems: [], clauses: [{ kind: 'spending_on_track' }, { kind: 'bills_paid' }, { kind: 'plan', age: 54, still: true, saved_day: SAVED }], stale: null, best_month: false },
  flow: {
    in_cents: 638_000, out_cents: 514_000, net_cents: 124_000, payday: null,
    months: [
      { month: '2026-09', in_cents: 952_000, out_cents: 719_000, net_cents: 233_000 },
      { month: '2026-10', in_cents: 638_000, out_cents: 514_000, net_cents: 124_000 },
    ],
  },
  signs: {
    flexible: { spent_cents: 314_000, target_cents: 420_000, rows: [] },
    bills: { paid: 5, of: 5, late: [] },
    plan: { age: 54, still: true, saved_day: SAVED },
    net_worth: { total_cents: 171_000_000, change_cents: 840_000 },
    has_business: false,
  },
  for_you: [],
  since: null,
};

export const homeWith = (patch: Partial<HomeData>): HomeData => ({ ...HOME, ...patch });

export const ACCOUNTS = { accounts: [{ id: 1, name: 'Checking', type: 'checking', owner: 'joint', source: 'manual', institution: 'Manual', is_active: true, balance_cents: 100_000, as_of: '2026-10-29', stale_days: 1 }] };
