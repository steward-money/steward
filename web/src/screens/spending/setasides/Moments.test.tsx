import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { SetAsideRow } from '../../../api/types';
import { wrapBook } from '../../../test-utils';
import { LiveRegions } from '../../../ui/Live';
import { coveredEarly, CoveredLine, FirstLine, freshCover } from './Moments';

const row = (over: Partial<SetAsideRow>): SetAsideRow => ({
  id: 1, name: 'Property tax', kind: 'yearly', amount_cents: 421_000, monthly_cents: 35_100, yearly_cents: 421_000, installment_cents: 421_000,
  due_month: 11, due_day: 20, second_month: null, second_day: null, due_year: null, first_month: '2026-01-01', next_due: '2026-11-20',
  days_until: 21, missed_due: null, is_estimate: false, estimate: null,
  account: { id: 5, name: 'House fund', balance_cents: 500_000, as_of: '2026-10-29', stale: false, shared_with: [] },
  put_away_cents: 421_000, should_cents: 351_000, state: 'on_track', short_cents: 0, behind_cents: 0, counted_from: null,
  payee_key: null, payee_name: null, category: null, notes: null, paid: null, archived_at: null, ...over,
} as SetAsideRow);

describe('set-aside moments (phase 17 spec 3.7)', () => {
  it('covered early: linked, its next installment fully put away, before it is due', () => {
    expect(coveredEarly([row({})]).map((r) => r.id)).toEqual([1]);
    expect(coveredEarly([row({ put_away_cents: 400_000 })])).toEqual([]);
    expect(coveredEarly([row({ account: null })])).toEqual([]);
    expect(coveredEarly([row({ days_until: 0 })])).toEqual([]);
  });

  it('says so once per installment on this device', () => {
    expect(freshCover('ours', [row({})])?.id).toBe(1);
    expect(freshCover('ours', [row({})])).toBeNull();
    expect(freshCover('ours', [row({ next_due: '2027-11-20' })])?.id).toBe(1);   // next year's installment
  });

  it('the line names the set-aside and the time to spare, with the sprout', () => {
    wrapBook(<LiveRegions><CoveredLine rows={[row({})]} /></LiveRegions>);
    expect(screen.getByTestId('sa-covered')).toHaveTextContent('Property tax is covered, with 3 weeks to spare.');
    // Issue #51: said through the page's polite region; the line is not a live region that appears with its words.
    expect(screen.getByTestId('sa-covered')).not.toHaveAttribute('role');
    expect(screen.getByTestId('live-said')).toHaveTextContent('Property tax is covered, with 3 weeks to spare.');
  });

  it("the first set-aside: the month it's due won't sting", () => {
    wrapBook(<FirstLine nextDue="2027-09-01" />);
    expect(screen.getByTestId('sa-first')).toHaveTextContent("September won't sting this year.");
  });
});
