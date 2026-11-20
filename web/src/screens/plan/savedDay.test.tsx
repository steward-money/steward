// Issue #52: Home, Plan and Taxes name a saved plan by the same day. The server sends that day in the household's zone
// (saved_day, YYYY-MM-DD; Taxes builds its whole sentence there), and every screen shows it as sent. Here the device is
// in Los Angeles (vitest's TZ) and the household in Honolulu, where the plan was saved at 23:30 on Oct 11: already
// Oct 12 on the device and in UTC.
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { Clause, VerdictData } from '../../api/home';
import { headline } from '../../lib/headline';
import { forgetHouseholdZone, setHouseholdZone } from '../../lib/today';
import { stubFetch, wrapBook } from '../../test-utils';
import { HOME } from '../home/fixtures';
import { Signs } from '../home/Signs';
import type { SavedPlan } from './api';
import { changeLine } from './change';
import { planFixture } from './fixtures';
import { HistorySheet } from './HistorySheet';
import { savedByText } from './Overview';
import { LastSavedAges } from './PlanFailed';
import { noteLine } from './savedNote';

const AT = '2026-10-12T09:30:00+00:00';   // 23:30 on Oct 11 in Honolulu, 2:30 on Oct 12 in Los Angeles
const DAY = '2026-10-11';
const NOW = new Date('2026-10-30T19:00:00Z');

function saved(): SavedPlan {
  return { ...planFixture().plan!, saved_at: AT, saved_day: DAY };
}

// The browser either knows the household's zone (from /me) or has only its own: before /me answers, or with a zone
// name it doesn't know. Either way the day is the server's.
const ZONES: [string, () => void][] = [
  ["the device's zone only", () => forgetHouseholdZone()],
  ["the household's zone from /me", () => setHouseholdZone('Pacific/Honolulu')],
];

describe.each(ZONES)("a plan saved at 23:30 household time, with %s (#52)", (_what, zone) => {
  it("Home's headline and its Plan sign say Oct 11", () => {
    zone();
    const clause: Clause = { kind: 'plan', age: 54, still: false, saved_day: DAY };
    const v: VerdictData = { opener: 'steady', problems: [], clauses: [clause], stale: null, best_month: false };
    expect(headline(v, NOW)).toBe('A steady month. The plan saved Oct 11 says 54.');
    wrapBook(<Signs signs={{ ...HOME.signs, plan: { age: 54, still: false, saved_day: DAY } }} month="2026-10" businessFailed={false} />);
    expect(screen.getByRole('link', { name: 'Plan: Comfortably at 54 · saved Oct 11' })).toBeInTheDocument();
  });

  it("Plan's saved-by line, its change line, its saved note and a failure's last save say Oct 11", () => {
    zone();
    expect(savedByText(saved())).toBe('Plan saved by Sam on Oct 11');
    const data = { ...planFixture(), plan: saved(), previous: saved() };
    expect(changeLine(data, NOW)!.text).toContain('Oct 11');
    expect(noteLine({ planId: 8, at: '2026-10-30T19:00:00Z', day: '2026-10-30', by: 'JR', previous: { name: 'Sam', saved_day: DAY } }, NOW))
      .toMatch(/^Saved by JR today at \d+:00 [ap]m\. The previous version, saved by Sam on Oct 11, is in Earlier versions\.$/);
    render(<LastSavedAges last={{ comfortable_age: 63, likely_age: 60, saved_at: AT, saved_day: DAY }} />);
    expect(screen.getByText('From the plan saved Oct 11')).toBeInTheDocument();
  });

  it("Plan's Earlier versions says Oct 11", async () => {
    zone();
    stubFetch({ '/api/plan/history': { history: [saved()] } });
    wrapBook(<HistorySheet open onClose={() => {}} />, {}, '/b/ours/plan');
    expect(await screen.findByText('Oct 11 · Sam')).toBeInTheDocument();
  });
});
