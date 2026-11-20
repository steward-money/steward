import type { ReactNode } from 'react';
import type { SignsData } from '../../api/home';
import type { BusinessReport } from '../../api/types';
import { Link } from '../../book/links';
import { label } from '../../lib/categories';
import { dateLabel, dollars, money } from '../../lib/format';
import { Skeleton } from '../../ui/Skeleton';
import * as C from './copy';

const fill = (s: string, vars: Record<string, string | number>) => s.replace(/\{(\w+)\}/g, (_, k: string) => String(vars[k]));
type Tone = 'muted' | 'amber';
type Tile = { key: string; label: string; text: string; to: string; tone?: Tone; node?: ReactNode; wide?: boolean };

function flexible(f: SignsData['flexible']): Pick<Tile, 'text' | 'tone'> {
  const spent = dollars(f.spent_cents);
  if (f.target_cents === null) return { text: fill(C.FLEX_SO_FAR, { spent }), tone: 'muted' };
  return { text: fill(C.FLEX_OF, { spent, target: dollars(f.target_cents) }), tone: f.rows.some((r) => r.over_level === 'clear') ? 'amber' : undefined };
}

/** P12: Bills only. A late Bill reads as it does in the headline, and is the one sign that can be amber for a missed
 *  payment. */
function bills(b: SignsData['bills']): Pick<Tile, 'text' | 'tone'> {
  if (b.of === 0) return { text: C.BILLS_NONE, tone: 'muted' };
  if (b.paid === b.of) return { text: C.BILLS_ALL_PAID };
  const paid = fill(C.BILLS_PAID_OF, { paid: b.paid, n: b.of });
  if (b.late.length === 0) return { text: `${paid} · ${C.BILLS_REST_LATER}` };
  const late = b.late.length === 1 ? fill(C.BILLS_NOT_SEEN, { category: label(b.late[0].category) }) : fill(C.BILLS_K_NOT_SEEN, { k: b.late.length });
  return { text: `${paid} · ${late}`, tone: 'amber' };
}

/** #17: the saved plan's age, with when it was saved; Plan itself shows today's. `month` gives the year to leave out. */
function plan(p: SignsData['plan'], month: string): Pick<Tile, 'text' | 'tone'> {
  if (p === null) return { text: C.PLAN_NONE, tone: 'muted' };
  const saved = fill(C.PLAN_SAVED, { date: dateLabel(p.saved_day, `${month}-01`) });
  return { text: `${p.age === null ? C.PLAN_NOT_BY_70 : fill(C.PLAN_AT, { age: p.age })} · ${saved}` };
}

/** JR, Oct 30: the total stays, with this month's change, green only when up. */
function netWorth(n: SignsData['net_worth']): Pick<Tile, 'text' | 'node'> {
  const total = money(n.total_cents, { compact: true });
  const change = n.change_cents === null ? C.NW_NO_CHANGE : fill(C.NW_THIS_MONTH, { amount: dollars(n.change_cents, { sign: 'always' }) });
  const tone = n.change_cents === null ? 'is-muted' : n.change_cents > 0 ? 'is-in' : undefined;
  return { text: `${total} · ${change}`, node: <>{total} · <span className={tone}>{change}</span></> };
}

function Sign({ t }: { t: Tile }) {
  return (
    <div className={`sign${t.wide ? ' is-wide' : ''}`} data-testid={`sign-${t.key}`}>
      <dt><Link className="sign__link" to={t.to} aria-label={`${t.label}: ${t.text}`}>{t.label}</Link></dt>
      <dd className={t.tone ? `is-${t.tone}` : undefined}>{t.node ?? t.text}</dd>
    </div>
  );
}

/** The vital signs (Home spec): a dl, each tile one link (P42). Business is a fifth sign for a household with one
 *  (P15): a skeleton until /api/business answers after ready, gone when it fails. */
export function Signs({ signs, month, business, businessFailed }: { signs: SignsData; month: string; business?: BusinessReport; businessFailed: boolean }) {
  const tiles: Tile[] = [
    { key: 'flexible', label: C.SIGN_FLEXIBLE, to: `/spending?month=${month}#flexible`, ...flexible(signs.flexible) },
    { key: 'bills', label: C.SIGN_BILLS, to: `/spending?month=${month}#fixed`, ...bills(signs.bills) },
    { key: 'plan', label: C.SIGN_PLAN, to: '/plan', ...plan(signs.plan, month) },
    { key: 'net-worth', label: C.SIGN_NET_WORTH, to: '/accounts', ...netWorth(signs.net_worth) },
  ];
  const showBusiness = signs.has_business && !businessFailed && business?.type !== 'none';
  return (
    <dl className="signs" data-testid="signs">
      {tiles.map((t) => <Sign key={t.key} t={t} />)}
      {showBusiness && (business
        ? <Sign t={{ key: 'business', label: C.SIGN_BUSINESS, to: '/business', wide: true, text: fill(C.BUSINESS_PROFIT, { amount: dollars(business.profit_cents) }) }} />
        : <div className="sign is-wide" data-testid="sign-business" aria-busy="true"><dt>{C.SIGN_BUSINESS}</dt><dd><Skeleton lines={1} /></dd></div>)}
    </dl>
  );
}
