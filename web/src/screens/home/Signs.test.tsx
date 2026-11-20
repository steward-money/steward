import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { SignsData } from '../../api/home';
import type { BusinessReport } from '../../api/types';
import { setHouseholdZone } from '../../lib/today';
import { wrapBook } from '../../test-utils';
import { HOME, SAVED } from './fixtures';
import { Signs } from './Signs';

const signs = (patch: Partial<SignsData>): SignsData => ({ ...HOME.signs, ...patch });
const show = (s: SignsData, business?: Partial<BusinessReport>, businessFailed = false) =>
  wrapBook(<Signs signs={s} month="2026-10" business={business as BusinessReport | undefined} businessFailed={businessFailed} />);
const tile = (name: string) => screen.getByRole('link', { name });
const value = (key: string) => screen.getByTestId(`sign-${key}`).querySelector('dd')!;
const DINING = { category: 'dining', spent_cents: 64_000, target_cents: 50_000, over_cents: 14_000, over_level: 'clear' as const };

describe('Signs (Home spec, Vital signs)', () => {
  it('Flexible spending: spent of target, or so far without one; amber when a category is clearly over', () => {
    const a = show(HOME.signs);
    expect(tile('Flexible spending: $3,140 of $4,200')).toHaveAttribute('href', '/b/ours/spending?month=2026-10#flexible');
    expect(value('flexible')).not.toHaveClass('is-amber');
    a.unmount();
    const b = show(signs({ flexible: { spent_cents: 314_000, target_cents: null, rows: [] } }));
    expect(tile('Flexible spending: $3,140 so far')).toBeInTheDocument();
    expect(value('flexible')).toHaveClass('is-muted');
    b.unmount();
    show(signs({ flexible: { spent_cents: 468_000, target_cents: 420_000, rows: [DINING] } }));
    expect(value('flexible')).toHaveClass('is-amber');
  });

  it('Bills: all paid, waiting, late in amber, and none due; it opens Fixed costs', () => {
    const cases: [SignsData['bills'], string, string | null][] = [
      [{ paid: 5, of: 5, late: [] }, 'All paid', null],
      [{ paid: 2, of: 5, late: [] }, '2 of 5 paid · rest due later', null],
      [{ paid: 2, of: 5, late: [{ category: 'utilities', usual_day: 12 }] }, '2 of 5 paid · Utilities not seen yet', 'is-amber'],
      [{ paid: 1, of: 5, late: [{ category: 'utilities', usual_day: 12 }, { category: 'insurance', usual_day: 3 }] }, '1 of 5 paid · 2 not seen yet', 'is-amber'],
      [{ paid: 0, of: 0, late: [] }, 'None due', 'is-muted'],
    ];
    for (const [bills, text, tone] of cases) {
      const r = show(signs({ bills }));
      expect(tile(`Bills: ${text}`)).toHaveAttribute('href', '/b/ours/spending?month=2026-10#fixed');
      expect(value('bills')).toHaveTextContent(text);
      if (tone) expect(value('bills')).toHaveClass(tone);
      r.unmount();
    }
  });

  it('Plan: the saved plan with its date (#17), Not by 70, or no plan yet; it opens the Plan', () => {
    const a = show(HOME.signs);
    expect(tile('Plan: Comfortably at 54 · saved Oct 12')).toHaveAttribute('href', '/b/ours/plan');
    a.unmount();
    const b = show(signs({ plan: { age: null, still: false, saved_day: SAVED } }));
    expect(tile('Plan: Not by 70 · saved Oct 12')).toBeInTheDocument();
    b.unmount();
    const c = show(signs({ plan: { age: 61, still: false, saved_day: '2025-12-30' } }));
    expect(tile('Plan: Comfortably at 61 · saved Dec 30, 2025')).toBeInTheDocument();
    c.unmount();
    show(signs({ plan: null }));
    expect(tile('Plan: No plan yet')).toBeInTheDocument();
    expect(value('plan')).toHaveClass('is-muted');
  });

  it("Plan: in January the year is the household's, with the household west of the device (#26)", () => {
    // Pago Pago (UTC-11) is west of nearly every device: the device's midnight on Jan 1 is still Dec 31 there, which
    // made this January's save show its year and last December's leave it out.
    setHouseholdZone('Pacific/Pago_Pago');
    const jan = (saved_day: string) => wrapBook(<Signs signs={signs({ plan: { age: 54, still: false, saved_day } })} month="2027-01" businessFailed={false} />);
    const a = jan('2027-01-10');
    expect(tile('Plan: Comfortably at 54 · saved Jan 10')).toBeInTheDocument();
    a.unmount();
    jan('2026-12-20');
    expect(tile('Plan: Comfortably at 54 · saved Dec 20, 2026')).toBeInTheDocument();
  });

  it("Net worth: the total and this month's change, green only when up; it opens Accounts", () => {
    const a = show(HOME.signs);
    expect(tile('Net worth: $1.71M · +$8,400 this month')).toHaveAttribute('href', '/b/ours/accounts');
    expect(screen.getByText('+$8,400 this month')).toHaveClass('is-in');
    a.unmount();
    const b = show(signs({ net_worth: { total_cents: 171_000_000, change_cents: -200_000 } }));
    expect(screen.getByText('−$2,000 this month')).not.toHaveClass('is-in');
    b.unmount();
    show(signs({ net_worth: { total_cents: 171_000_000, change_cents: null } }));
    expect(tile('Net worth: $1.71M · No change yet')).toBeInTheDocument();
  });

  it('Business: a skeleton until /api/business answers, then the profit; gone when it fails or there is none', () => {
    const withBusiness = signs({ has_business: true });
    const a = show(withBusiness);
    expect(screen.getByTestId('sign-business')).toHaveAttribute('aria-busy', 'true');
    a.unmount();
    const b = show(withBusiness, { type: 'sole_prop', profit_cents: 421_000 });
    expect(tile('Business: $4,210 profit this year')).toHaveAttribute('href', '/b/ours/business');
    b.unmount();
    const c = show(withBusiness, undefined, true);
    expect(screen.queryByTestId('sign-business')).toBeNull();
    c.unmount();
    const d = show(withBusiness, { type: 'none', profit_cents: 0 });
    expect(screen.queryByTestId('sign-business')).toBeNull();
    d.unmount();
    show(HOME.signs, { type: 'sole_prop', profit_cents: 421_000 });
    expect(screen.queryByTestId('sign-business')).toBeNull();
  });

  it('is a description list, label then value', () => {
    show(HOME.signs);
    const dl = screen.getByTestId('signs');
    expect(dl.tagName).toBe('DL');
    expect([...dl.querySelectorAll('dt')].map((d) => d.textContent)).toEqual(['Flexible spending', 'Bills', 'Plan', 'Net worth']);
  });
});
