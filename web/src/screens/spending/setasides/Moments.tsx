import { useRef, useState } from 'react';
import { currentBook } from '../../../api/book';
import type { SetAsideRow } from '../../../api/types';
import { soundOn } from '../../../lib/celebrations';
import { nextLine, spareWeeks } from '../../../lib/lines';
import { momentAllowed } from '../../../lib/momentAllowed';
import { Shown } from '../../../ui/Live';
import { useWinMoment } from '../../../ui/useMoment';

const KEY = 'steward-sa-covered';

/** Phase 17 spec 3.7: a linked account covers the next installment before it is due (the same rule as the growth
 *  milestone, services/book_growth.set_aside_covered). */
export function coveredEarly(rows: SetAsideRow[]): SetAsideRow[] {
  return rows.filter((r) => r.account !== null && r.account.balance_cents !== null && r.paid === null && r.next_due !== null
    && (r.days_until ?? 0) > 0 && r.installment_cents > 0 && r.put_away_cents >= r.installment_cents);
}

/** The soonest covered installment this device hasn't said yet, now recorded as said. */
export function freshCover(book: string, rows: SetAsideRow[]): SetAsideRow | null {
  const key = `${KEY}:${book}`;
  let said: string[] = [];
  try { const v: unknown = JSON.parse(localStorage.getItem(key) ?? '[]'); said = Array.isArray(v) ? v.map(String) : []; } catch { said = []; }
  const r = coveredEarly(rows).find((x) => !said.includes(`${x.id}:${x.next_due}`)) ?? null;
  if (r) { try { localStorage.setItem(key, JSON.stringify([...said, `${r.id}:${r.next_due}`].slice(-50))); } catch { /* private mode */ } }
  return r;
}

/** On the next visit after an installment is covered early: the sprout and its line. */
export function CoveredLine({ rows }: { rows: SetAsideRow[] }) {
  const allowed = momentAllowed('setAside', { parent: false });
  const [r] = useState(() => freshCover(currentBook() ?? '', rows));
  const [line] = useState(() => (r ? nextLine('setAsideCovered', { name: r.name, weeks: spareWeeks(r.days_until ?? 0) }) : null));
  const host = useRef<HTMLParagraphElement>(null);
  useWinMoment(host, line && allowed.motion ? 'sprout' : null, allowed.sound && soundOn());
  if (!line || !allowed.line) return null;
  return <Shown ref={host} className="sa-moment" data-testid="sa-covered">{line}</Shown>;
}

const MONTH = new Intl.DateTimeFormat('en-US', { month: 'long' });

/** The household's first set-aside, on its page once: the month it is due won't sting. */
export function FirstLine({ nextDue }: { nextDue: string | null }) {
  const [line] = useState(() => {
    if (!nextDue) return null;
    const [y, m] = nextDue.split('-').map(Number);
    return nextLine('setAsideFirst', { month: MONTH.format(new Date(y, m - 1, 1)) });
  });
  if (!line || !momentAllowed('setAside', { parent: false }).line) return null;
  return <Shown className="sa-moment" data-testid="sa-first">{line}</Shown>;
}
