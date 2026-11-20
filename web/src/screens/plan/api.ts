// Shapes of the Plan API (src/steward/api/{plan,children,ss}.py). Money is integer cents; dates are ISO strings.
import { useQuery } from '@tanstack/react-query';
import { api, json } from '../../api/client';
import type { PlanLevel, PlanLevel as CostLevel, Names, SetupItem } from '../../api/household';
import type { ProfitRangeOut } from '../../api/types';

export type { PlanLevel as CostLevel } from '../../api/household';
export type { Child, ChildInput, EarningsRecord, EarningsSummary, Names, Person, SetupItem, SetupKey, SetupList } from '../../api/household';
export { invalidatePlan, useChildren, useEarnings, usePlanSetup } from '../../api/household';

export type LeverValues = {
  save_more_monthly_cents: number; spending_pct: number;
  consulting_cents: number; consulting_until_age: number;
  gift_cents: number; gift_until_year: number;
  spouse_earnings_cents: number; spouse_start_year: number; spouse_stop_age: number;
  jr_claim_age: number; spouse_claim_age: number;
  college_levels: CostLevel[] | null; full_ss_benefits: boolean;
};
export type LeverField = keyof LeverValues;
export type LeverValue = LeverValues[LeverField];
export type LeverKey = 'save_more' | 'spending' | 'consulting' | 'gift' | 'spouse_earnings' | 'jr_claim' | 'spouse_claim' | 'college' | 'full_ss';
export type LeverState = { assumed: LeverValues; try: LeverValues; pinned: LeverKey[]; try_notes: Partial<Record<LeverKey, string>> };
export type Scale = { lo: number; hi: number; step: number };
export type LeverSpec = { key: LeverKey; label: string; kind: 'money' | 'pct' | 'age' | 'level' | 'switch'; fields: LeverField[]; scale: Scale | null };
export type BandPoint = { year: number; jr_age: number; p10_cents: number; p50_cents: number; p90_cents: number };
export type PlanResult = {
  comfortable_age: number | null; likely_age: number | null; comfortable_year: number | null; likely_year: number | null;
  band_age: number | null; band: BandPoint[]; success_by_age: [number, number][];
  assumptions: string[]; assumption_groups: string[]; input_digest: string; engine_version: string; histories: number;
};
/** `saved_day`: the household's day it was saved, YYYY-MM-DD, as the server reads it (#52); every screen names a save by
 *  it. `saved_at` is the instant, for the time of a save made today. */
export type SavedPlan = {
  id: number; saved_at: string; saved_day: string; saved_by: { id: number; name: string | null }; engine_version: string;
  comfortable_age: number | null; likely_age: number | null; levers: unknown; current: boolean; inputs_summary: InputsSummary | null;
};
/** A one-off the engine pays: `year` is the plan's payment year (the plan year when overdue), `due_year` the original. */
export type PlanOneOff = { id: number; name: string; year: number; due_year: number; due_month: number; overdue: boolean; cents: number; monthly_cents: number; is_estimate: boolean };
export type PlanSetAsides = { updated_at: string | null; updated_by: string | null; yearly_count: number; one_offs: PlanOneOff[] };
export type PlanCollege = {
  growth: number; levels: Record<CostLevel, { years: number; tuition_fees_cents: number; living_cents: number }>;
  children: { label: string; share_pct: number; covers_living: boolean }[];
};
/** How it works' What moves your plan most (P30): the server's numbers, in cents a month (Social Security, spending,
 *  saving) or a year (health); null where it doesn't apply. */
export type PlanMoves = {
  ss_at_67: { jr_cents: number | null; spouse_cents: number | null }; retirement_spending_cents: number;
  mortgage_payoff_year: number | null; health_premium_cents: number; college: { count: number; level: PlanLevel | null };
  saving_cents: number;
};
export type PlanInputsOut = { spending_months: number; spending_basis_cents: number; surplus_cents: number; facts_as_of: Record<string, string>; set_asides: PlanSetAsides; college?: PlanCollege; moves?: PlanMoves };
/** What a saved plan was worked out from (plan subpages; services/plan.inputs_summary). Spending is a year; saving is a
 *  month; comfortable_months is the interpolated Comfortably age in months, null when not by 70. */
export type InputsSummary = {
  version: number; spending_basis_cents: number;
  balances_cents: { taxed_later: number; tax_free: number; taxable: number; cash: number };
  saving_cents: number; social_security: { jr: 'record' | 'estimate'; spouse: 'record' | 'estimate' };
  big_costs: { name: string; year: number }[]; comfortable_months: number | null;
};
/** `previous`: the newest plan saved before today, which the change line compares with (Engineering choice 8). */
export type PlanReady = { plan: SavedPlan | null; previous?: SavedPlan | null; levers: LeverState; result: PlanResult; inputs_summary: InputsSummary; lever_specs: LeverSpec[]; inputs: PlanInputsOut; names: Names; business?: ProfitRangeOut | null };
export type PlanResponse = PlanReady | { setup: SetupItem[] };
export type Impact = {
  key: LeverKey; label: string; child_label: string | null; try_value: number | boolean | string;
  delta_months: number | null; comfortable_age: number | null; text: string;
};
export type DeltaBody = { assumed?: Partial<LeverValues>; try?: Partial<LeverValues>; pinned?: LeverKey[] };
export type PreviewResponse = { levers: LeverState; result: PlanResult };
export type ImpactsResponse = { levers: LeverState; impacts: Impact[] };
export type SaveResponse = { plan: SavedPlan; levers: LeverState; result: PlanResult };
export type LastSaved = { comfortable_age: number | null; likely_age: number | null; saved_at: string; saved_day: string };

export const isReady = (r: PlanResponse): r is PlanReady => !('setup' in r);

export const usePlanHistory = (enabled: boolean) =>
  useQuery({ queryKey: ['plan-history'], queryFn: () => api<{ history: SavedPlan[] }>('/api/plan/history?limit=20'), enabled });

export const postPreview = (body: DeltaBody, signal: AbortSignal) => api<PreviewResponse>('/api/plan/preview', { ...json(body), signal });
export const postImpacts = (body: DeltaBody, signal: AbortSignal) => api<ImpactsResponse>('/api/plan/impacts', { ...json(body), signal });
export const savePlan = (body: DeltaBody) => api<SaveResponse>('/api/plan', json(body));

export type NextStepKey = 'cash' | 'match' | 'hsa' | 'debt' | 'retirement' | 'invest';
export type NextStepStatus = 'done' | 'to_do' | 'later' | 'ask' | 'no_room' | 'cant_tell';
export type NextStep = {
  key: NextStepKey; title: string; status: NextStepStatus; why: string;
  in_cents: number | null; room_cents: number | null; rules_year: number | null;
  choice: 'business_plan' | 'roth_ira' | 'mortgage' | 'invest' | null;
  needs_rate: { account_id: number; name: string }[];
  /** What the paycheck put in this year, a fact the household enters (final review ruling I3); null: not asked. */
  payroll: { key: string; cents: number | null } | null;
};
export type NextDollarData = { year: number; settings: { cash_months: number; debt_rate_bp: number }; steps: NextStep[]; backdoor_sheet: string };

export const useNextDollar = () => useQuery({ queryKey: ['next-dollar'], queryFn: () => api<NextDollarData>('/api/next-dollar') });
