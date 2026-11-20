import { useQueryClient } from '@tanstack/react-query';
import { Plus, Send } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api } from '../../api/client';
import { useReimbursements } from '../../api/hooks';
import { invalidateReimburse } from '../../api/reimburse';
import type { PayerBalance, SettleReply, UndoReply } from '../../api/types';
import { useCanEdit } from '../../book/BookContext';
import { Link, useNavigate } from '../../book/links';
import { label } from '../../lib/categories';
import { withCelebrate } from '../../lib/celebrations';
import { ALL_SQUARE, SETTLE, TRY_AGAIN, UNDO, UNDONE } from '../../lib/copy';
import { dateLabel, money } from '../../lib/format';
import { rich, type Part } from '../../lib/receipt';
import { Button } from '../../ui/Button';
import { Notice } from '../../ui/Notice';
import { Rich } from '../../ui/Rich';
import { Skeleton } from '../../ui/Skeleton';
import { monthLong } from '../cashflow/months';
import { Pills } from '../spending/parts';
import * as C from './copy';
import { barFill, chargesWord, dayOf, daysBefore, detailLine, owedLine, statusWord, tripDates } from './model';
import { SettleSheet } from './SettleSheet';
import { TripSheet } from './TripSheet';
import '../spending/spending.css';
import './reimburse.css';

type Said = { cheer: string | null; parts: Part[]; undo: string | null };

/** Spending → Reimbursements (reimbursable spec, Where they live): what each payer owes, the trips, the single charges,
 *  Add a trip and Settle. A repayment's receipt links here with ?settle={payer}. */
export function Reimbursements() {
  const q = useReimbursements();
  const qc = useQueryClient();
  const canEdit = useCanEdit();
  const navigate = useNavigate();
  const [sp, setSp] = useSearchParams();
  const [adding, setAdding] = useState(false);
  const [settling, setSettling] = useState<PayerBalance | null>(null);
  const [said, setSaid] = useState<Said | null>(null);
  const [busy, setBusy] = useState(false);
  const blocks = useRef<Record<number, HTMLElement | null>>({});
  const settleParam = sp.get('settle');

  useEffect(() => {
    if (!q.data || settleParam === null) return;
    const p = q.data.payers.find((x) => String(x.id) === settleParam);
    if (p && p.owed_cents > 0 && canEdit) setSettling(p);
    setSp((s) => { const n = new URLSearchParams(s); n.delete('settle'); return n; }, { replace: true, preventScrollReset: true });
  }, [q.data, settleParam, canEdit, setSp]);

  const head = (
    <>
      <Pills on="reimbursements" />
      <header className="sp-head"><h1>{C.REIMBURSEMENTS}</h1></header>
    </>
  );
  if (q.isError) {
    return (
      <div className="screen spending">{head}
        <Notice tone="error"><p>{q.error.message}</p><Button variant="quiet" onClick={() => void q.refetch()}>{TRY_AGAIN}</Button></Notice>
      </div>
    );
  }
  if (!q.data) return <div className="screen spending" aria-busy="true">{head}<Skeleton lines={6} /></div>;
  const data = q.data;
  const today = dayOf(data.as_of);

  async function settled(r: SettleReply, p: PayerBalance) {
    setSettling(null);
    setSaid({ cheer: ALL_SQUARE.replace('{payer}', p.name), undo: r.undo,
      parts: rich(C.SETTLED, { amount: money(r.settled_cents), category: label(r.category), month: monthLong(r.month) }) });
    const host = blocks.current[p.id];
    if (host) void withCelebrate((c) => { c.square({ host }); });
    await invalidateReimburse(qc);
  }

  async function undo() {
    if (!said?.undo || busy) return;
    setBusy(true);
    try {
      await api<UndoReply>(`/api/undo/${said.undo}`, { method: 'POST' });
      setSaid({ cheer: null, parts: [{ text: UNDONE, bold: false }], undo: null });
      await invalidateReimburse(qc);
    } catch (e) {
      setSaid((s) => s && { ...s, parts: [{ text: e instanceof Error ? e.message : 'Something went wrong.', bold: false }] });
    } finally {
      setBusy(false);
    }
  }

  const add = canEdit && <Button variant="quiet" icon={Plus} onClick={() => { setAdding(true); }}>{C.ADD_TRIP}</Button>;
  return (
    <div className="screen spending">
      {head}
      {said && (
        <Notice>
          <div className="rb-said">
            {said.cheer && <strong>{said.cheer}</strong>}
            <p><Rich parts={said.parts} /></p>
            {said.undo && <Button variant="quiet" busy={busy} onClick={() => { void undo(); }}>{UNDO}</Button>}
          </div>
        </Notice>
      )}
      {data.payers.length === 0 ? (
        <section className="rb-empty"><p>{C.EMPTY}</p>{add}</section>
      ) : (
        <>
          <section aria-labelledby="rb-owed-h">
            <h2 id="rb-owed-h" className="eyebrow">{C.OWED_TO_YOU}</h2>
            {data.payers.map((p) => (
              <div key={p.id} ref={(el) => { blocks.current[p.id] = el; }} className="rb-owed" data-testid="rb-owed">
                <p className="rb-owed__big">{owedLine(p)}</p>
                <span className="rb-owed__bar" aria-hidden="true">
                  <span className="rb-owed__fill" style={{ transform: `scaleX(${barFill(p)})` }} />
                </span>
                <p className="rb-owed__line muted">{detailLine(p, today)}</p>
                {p.owed_cents < 0 && (
                  <Link className="link" to={`/transactions?category=travel&dates=custom&from=${daysBefore(data.as_of, 90)}`}>{C.LIKELY_UNMARKED}</Link>
                )}
                {p.owed_cents > 0 && canEdit && <Button variant="quiet" onClick={() => { setSettling(p); }}>{SETTLE}</Button>}
              </div>
            ))}
          </section>
          <section aria-labelledby="rb-trips-h">
            <h2 id="rb-trips-h" className="rb-head"><Send className="i" size={20} aria-hidden="true" />{C.TRIPS}</h2>
            {data.trips.length === 0 ? <p className="muted">{C.NO_TRIPS}</p> : (
              <ul className="rb-list">
                {data.trips.map((t) => (
                  <li key={t.id}>
                    <Link className="rb-item" to={`/spending/reimbursements/trips/${t.id}`} data-testid="rb-trip">
                      <span className="rb-item__name">{t.name}</span>
                      <span>{money(t.total_cents)}</span>
                      <span className="rb-item__meta muted">{tripDates(t, today)} · {chargesWord(t.count)} · {statusWord(t.status)}</span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
            {add}
          </section>
          {data.singles.length > 0 && (
            <section aria-labelledby="rb-singles-h">
              <h2 id="rb-singles-h">{C.SINGLES}</h2>
              <ul className="rb-list">
                {data.singles.map((t) => (
                  <li key={t.id} className="rb-item" data-testid="rb-single">
                    <span className="rb-item__name">{t.merchant || t.raw_description}</span>
                    <span>{money(-t.amount_cents)}</span>
                    <span className="rb-item__meta muted">{dateLabel(t.posted_at, today)} · {statusWord(t.status)}</span>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </>
      )}
      {adding && (
        <TripSheet payers={data.payers} onClose={() => { setAdding(false); }}
          onSaved={(t) => { setAdding(false); void invalidateReimburse(qc); navigate(`/spending/reimbursements/trips/${t.id}`); }} />
      )}
      {settling && (
        <SettleSheet payer={settling} month={data.as_of.slice(0, 7)} onClose={() => { setSettling(null); }}
          onDone={(r) => { void settled(r, settling); }} />
      )}
    </div>
  );
}

export default Reimbursements;
