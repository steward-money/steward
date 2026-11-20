import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useRef } from 'react';
import { useLocation } from 'react-router-dom';
import { api, json } from './client';
import type { FlowMonth } from './types';

/** GET /api/home (Home spec, API): the server picks, the web words (P1). Cents throughout. */
export type Problem =
  | { kind: 'late_bill'; category: string; usual_day: number }
  | { kind: 'late_bills'; n: number }
  | { kind: 'overdrawn'; account: string; cents: number };
export type Clause =
  | { kind: 'behind_until_payday'; little: boolean }
  | { kind: 'more_out_than_in' }
  | { kind: 'over_one'; category: string }
  | { kind: 'over_many'; n: number }
  | { kind: 'bill_more'; category: string }
  | { kind: 'spending_close' }
  | { kind: 'spending_on_track' }
  | { kind: 'bills_paid' }
  | { kind: 'plan'; age: number; still: boolean; saved_day: string };
export type Opener = 'steady' | 'mostly' | 'new_month' | 'problem' | 'empty';
/** The bank-fed day-to-day accounts 3 days old or more (#15): how many, their oldest and newest age in days, and the
 *  name when there is one alone. */
export type Stale = { n: number; oldest_days: number; newest_days: number; account: string | null };
export type VerdictData = { opener: Opener; problems: Problem[]; clauses: Clause[]; stale: Stale | null; best_month: boolean };
/** A Flexible row a little or clearly over its target, in Spending's order. */
export type FlexRow = { category: string; spent_cents: number; target_cents: number; over_cents: number; over_level: 'little' | 'clear' };
export type SignsData = {
  flexible: { spent_cents: number; target_cents: number | null; rows: FlexRow[] };
  bills: { paid: number; of: number; late: { category: string; usual_day: number }[] };
  /** The newest saved plan, never today's solve (#17: that would cost Home its budget), so it is shown with its date:
   *  the household's day it was saved, YYYY-MM-DD, as the server reads it (#52). */
  plan: { age: number | null; still: boolean; saved_day: string } | null;
  net_worth: { total_cents: number; change_cents: number | null };
  has_business: boolean;
};
export type ForYouItem =
  | { kind: 'event'; id: number; event_kind: string; level: string; message: string }
  | { kind: 'same_account'; n: number; name: string | null }
  | { kind: 'untyped'; n: number }
  | { kind: 'owed'; payers: { name: string; cents: number }[]; others: { n: number; cents: number } };
export type SinceLine = { kind: 'pay' | 'bill' | 'large' | 'group'; category: string; name: string | null; n: number; places: number; cents: number };
export type SinceData = { from: string; from_day: string; over_month: boolean; lines: SinceLine[]; more: number; net_worth_cents: number | null };
export type HomeData = {
  as_of: string; month: string; day: number; days: number; mark: string;
  verdict: VerdictData;
  flow: { in_cents: number; out_cents: number; net_cents: number; payday: { day: number } | null; payday_came?: { day: number } | null; months: FlowMonth[] };
  signs: SignsData;
  for_you: ForYouItem[];
  since: SinceData | null;
};

/** Phase 17 spec 3.5: money news is never playful. A red month (more out than in so far) or a problem in the verdict
 *  (a late bill, an overdrawn account) hides Home's greeting and payday moment, and Review's sort cheers. */
export const redMonth = (d: HomeData): boolean => d.flow.net_cents < 0 || d.verdict.opener === 'problem';

/** Whether this month is red, for a screen other than Home (Review's sort cheers, claim T6): Home's own answer when
 *  it has one, else GET /api/home once a minute at most. Undefined until known, and after a failed ask, so a caller
 *  stays quiet then. Household books only (Home is theirs alone). */
export function useRedMonth(enabled: boolean): boolean | undefined {
  const qc = useQueryClient();
  return useQuery({
    queryKey: ['home', 'red'],
    queryFn: async () => redMonth(await api<HomeData>('/api/home')),
    enabled,
    staleTime: 60_000,
    retry: false,
    initialData: () => { const home = qc.getQueryData<HomeData>(['home']); return home ? redMonth(home) : undefined; },
    initialDataUpdatedAt: () => qc.getQueryState(['home'])?.dataUpdatedAt,
  }).data;
}

let early: { key: string; answer: Promise<HomeData> } | null = null;

/** Phase 18: the warm-up (routes.tsx) asks for Home as its address is reached, beside /me. The Home mounted at that
 *  same history entry takes this answer as its first, so P29's since.from comes from it; any other mount asks for
 *  itself. */
export function warmHome(key: string): void {
  const answer = api<HomeData>('/api/home');
  answer.catch(() => undefined);                    // the mount that takes it sees the error; nobody else needs to
  early = { key, answer };
}

/** Tests: drop a warm-up answer no mount took. */
export function forgetHome(): void {
  early = null;
}

function takeEarly(key: string): Promise<HomeData> | null {
  const e = early;
  early = null;
  return e !== null && e.key === key ? e.answer : null;
}

/** GET /api/home. Not refetched on focus while Home is mounted, so For you changes on the next visit, not while you
 *  look. Every refetch in this mount (a Dismiss, an Undo) sends the first answer's since.from (P29), so saving the
 *  visit never empties the list on screen; a mount whose first answer had no Since (a first visit) keeps none. The
 *  first answer may be the warm-up's, for this history entry; a mount always fetches, so the warm-up's answer is
 *  only ever taken by the mount's own first fetch. */
export function useHome() {
  const { key } = useLocation();
  // undefined until this mount's first answer; then its since.from, or null on a first visit.
  const from = useRef<string | null | undefined>(undefined);
  return useQuery({
    queryKey: ['home'],
    queryFn: async () => {
      const first = from.current === undefined;
      const data = await ((first ? takeEarly(key) : null)
        ?? api<HomeData>(from.current ? `/api/home?from=${encodeURIComponent(from.current)}` : '/api/home'));
      if (first) {
        from.current = data.since?.from ?? null;
        return data;
      }
      return from.current === null ? { ...data, since: null } : data;
    },
    refetchOnWindowFocus: false,
    refetchOnMount: 'always',
  });
}

/** POST /api/home/seen (P27, P28): the server's own mark, sent back, for a glance or a full look. */
export const seeHome = (mark: string, glance: boolean) => api<void>('/api/home/seen', json({ mark, glance }));
/** Dismiss on For you (P20): resolves the event and returns its undo token. */
export const dismissEvent = (id: number) => api<{ undo: string }>(`/api/events/${id}/resolve`, { method: 'POST' });
