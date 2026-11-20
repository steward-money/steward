// Fictional, demo-like household for unit tests. The spouse is Sam.
import type { Impact, InputsSummary, LeverValues, NextDollarData, PlanReady } from './api';

const levels = ['in_state_public', 'in_state_public', 'in_state_public'] as LeverValues['college_levels'];
export const ASSUMED: LeverValues = {
  save_more_monthly_cents: 0, spending_pct: 100, consulting_cents: 0, consulting_until_age: 60, gift_cents: 0, gift_until_year: 0,
  spouse_earnings_cents: 0, spouse_start_year: 0, spouse_stop_age: 65, jr_claim_age: 67, spouse_claim_age: 67,
  college_levels: levels, full_ss_benefits: false,
};
export const TRY: LeverValues = {
  save_more_monthly_cents: 50000, spending_pct: 90, consulting_cents: 1800000, consulting_until_age: 60, gift_cents: 1000000,
  gift_until_year: 2036, spouse_earnings_cents: 100000, spouse_start_year: 2027, spouse_stop_age: 65, jr_claim_age: 70,
  spouse_claim_age: 70, college_levels: levels, full_ss_benefits: true,
};

/** A saved plan's inputs summary that goes with planFixture(): Comfortably at 62 and a half, so 63 in whole years. */
export function summaryFixture(): InputsSummary {
  return structuredClone({
    version: 1, spending_basis_cents: 8_840_000,
    balances_cents: { taxed_later: 45_000_000, tax_free: 9_000_000, taxable: 15_000_000, cash: 4_000_000 },
    saving_cents: 200_000, social_security: { jr: 'record', spouse: 'estimate' }, big_costs: [], comfortable_months: 750,
  } satisfies InputsSummary);
}

export function planFixture(): PlanReady {
  return structuredClone({
    plan: { id: 7, saved_at: '2026-10-12T19:00:00+00:00', saved_day: '2026-10-12', saved_by: { id: 2, name: 'Sam' }, engine_version: '2026.10.2',
      comfortable_age: 63, likely_age: 60, levers: {}, current: true, inputs_summary: summaryFixture() },
    previous: null,
    levers: { assumed: ASSUMED, try: TRY, pinned: [], try_notes: { spouse_earnings: 'No history yet' } },
    result: {
      comfortable_age: 63, likely_age: 60, comfortable_year: 2048, likely_year: 2045, band_age: 63,
      band: [
        { year: 2026, jr_age: 41, p10_cents: 100_000_000, p50_cents: 100_000_000, p90_cents: 100_000_000 },
        { year: 2034, jr_age: 49, p10_cents: 150_000_000, p50_cents: 190_000_000, p90_cents: 240_000_000 },
        { year: 2042, jr_age: 57, p10_cents: 200_000_000, p50_cents: 300_000_000, p90_cents: 450_000_000 },
        { year: 2048, jr_age: 63, p10_cents: 230_000_000, p50_cents: 380_000_000, p90_cents: 600_000_000 },
        { year: 2062, jr_age: 77, p10_cents: 120_000_000, p50_cents: 330_000_000, p90_cents: 700_000_000 },
        { year: 2082, jr_age: 97, p10_cents: 5_000_000, p50_cents: 240_000_000, p90_cents: 800_000_000 },
      ],
      success_by_age: [[60, 0.81], [63, 0.91]],
      assumptions: [
        'Social Security benefits are cut 17% from 2034 (2026 Trustees Report).',
        "Acme Stable Value Fund has no holdings listed, so it uses the default mix for its type.",
        'Based on 7 months of spending history, annualized.',
      ],
      assumption_groups: ['ss', 'notice', 'notice'],
      input_digest: 'abc123', engine_version: '2026.10.2', histories: 2000,
    },
    inputs_summary: summaryFixture(),
    lever_specs: [
      { key: 'save_more', label: 'Save more each month', kind: 'money', fields: ['save_more_monthly_cents'], scale: { lo: 0, hi: 300000, step: 5000 } },
      { key: 'spending', label: 'Spending in retirement', kind: 'pct', fields: ['spending_pct'], scale: { lo: 60, hi: 130, step: 5 } },
      { key: 'consulting', label: 'Consulting income, a year', kind: 'money', fields: ['consulting_cents', 'consulting_until_age'], scale: { lo: 0, hi: 15000000, step: 100000 } },
      { key: 'gift', label: 'Yearly gift', kind: 'money', fields: ['gift_cents', 'gift_until_year'], scale: { lo: 0, hi: 5000000, step: 50000 } },
      { key: 'spouse_earnings', label: "Sam's earnings, a year", kind: 'money', fields: ['spouse_earnings_cents', 'spouse_start_year', 'spouse_stop_age'], scale: { lo: 0, hi: 12000000, step: 100000 } },
      { key: 'jr_claim', label: 'JR claims Social Security at', kind: 'age', fields: ['jr_claim_age'], scale: { lo: 62, hi: 70, step: 1 } },
      { key: 'spouse_claim', label: 'Sam claims Social Security at', kind: 'age', fields: ['spouse_claim_age'], scale: { lo: 62, hi: 70, step: 1 } },
      { key: 'college', label: 'College type', kind: 'level', fields: ['college_levels'], scale: null },
      { key: 'full_ss', label: 'Full Social Security benefits', kind: 'switch', fields: ['full_ss_benefits'], scale: null },
    ],
    inputs: { spending_months: 7, spending_basis_cents: 8840000, surplus_cents: 2400000,
      facts_as_of: { 'jr.salary_cents': '2026-10-12', 'health.premium_cents': '2026-10-12', 'mortgage.payment_cents': '2026-10-12' },
      set_asides: { updated_at: null, updated_by: null, yearly_count: 0, one_offs: [] },
      moves: { ss_at_67: { jr_cents: 335_400, spouse_cents: 167_700 }, retirement_spending_cents: 740_000, mortgage_payoff_year: 2050,
        health_premium_cents: 1_680_000, college: { count: 3, level: 'in_state_public' }, saving_cents: 215_000 } },
    names: { jr: 'JR', spouse: 'Sam', joint: 'Joint' },
  } satisfies PlanReady);
}

export function impactsFixture(): Impact[] {
  const row = (key: Impact['key'], label: string, delta: number, text: string, child: string | null = null): Impact =>
    ({ key, label, child_label: child, try_value: 0, delta_months: delta, comfortable_age: 63, text });
  return [
    row('spending', 'Spending in retirement', -24, 'about 2 years earlier'),
    row('save_more', 'Save more each month', -12, 'about 1 year earlier'),
    row('consulting', 'Consulting income, a year', -10, 'about 10 months earlier'),
    row('full_ss', 'Full Social Security benefits', -8, 'about 8 months earlier'),
    row('gift', 'Yearly gift', -7, 'about 7 months earlier'),
    row('jr_claim', 'JR claims Social Security at', -3, 'about 3 months earlier'),
    row('spouse_earnings', "Sam's earnings, a year", -1, 'about 1 month earlier'),
    row('spouse_claim', 'Sam claims Social Security at', 0, 'No change'),
    row('college', 'College type', 0, 'No change', 'Oldest'),
    row('college', 'College type', 0, 'No change', 'Middle'),
    row('college', 'College type', 0, 'No change', 'Youngest'),
  ];
}

/** GET /api/facts and /api/children answers that go with planFixture(). */
export const FACTS = { facts: {
  'jr.dob': { value: '1985-03-02', as_of: '2026-10-01', note: null },
  'spouse.dob': { value: '1987-06-09', as_of: '2026-10-01', note: null },
} };
export const CHILDREN = { children: [
  { id: 1, label: 'Oldest', birth_year: 2015, cost_level: 'in_state_public', account_id: 11, monthly_contribution_cents: 25000 },
  { id: 2, label: 'Middle', birth_year: 2018, cost_level: 'in_state_public', account_id: 12, monthly_contribution_cents: 25000 },
  { id: 3, label: 'Youngest', birth_year: 2021, cost_level: 'in_state_public', account_id: null, monthly_contribution_cents: 0 },
] };

export function nextDollarFixture(): NextDollarData {
  return structuredClone({
    year: 2026,
    settings: { cash_months: 6, debt_rate_bp: 700 },
    backdoor_sheet: 'When income is too high to put money into a Roth IRA directly, there is a common way around it, often called a backdoor Roth.',
    steps: [
      { key: 'cash', title: 'Cash for 6 months of spending', status: 'to_do', why: 'Cash accounts hold 3.3 months of spending; the target is 6, because business income is uncertain.', in_cents: 2_000_000, room_cents: 1_600_000, rules_year: null, choice: null, needs_rate: [], payroll: null },
      { key: 'hsa', title: 'HSA', status: 'ask', why: 'Does your health plan qualify for an HSA?', in_cents: null, room_cents: null, rules_year: null, choice: null, needs_rate: [], payroll: null },
      { key: 'debt', title: 'Debt above 7% interest', status: 'to_do', why: 'Highest rate first: Visa (22.9%), Store card (rate not entered). $4,800 owed in all.', in_cents: null, room_cents: 480_000, rules_year: null, choice: null, needs_rate: [{ account_id: 7, name: 'Store card' }], payroll: null },
      { key: 'retirement', title: 'Roth IRA', status: 'no_room', why: 'Your income is over the 2026 Roth IRA limit.', in_cents: 0, room_cents: 0, rules_year: 2026, choice: 'roth_ira', needs_rate: [], payroll: null },
      { key: 'invest', title: 'Taxable investing', status: 'to_do', why: 'No mortgage to pay down. Investing is expected to earn about 5.8% a year after tax (2026 rules).', in_cents: 250_000, room_cents: null, rules_year: 2026, choice: 'invest', needs_rate: [], payroll: null },
    ],
  });
}
