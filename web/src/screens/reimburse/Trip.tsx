import { useQueryClient } from '@tanstack/react-query';
import { Pencil } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { api, patch } from '../../api/client';
import { usePayers, useTrip } from '../../api/hooks';
import { invalidateReimburse, markTrip } from '../../api/reimburse';
import type { TripRow, TxnPatchReply, UndoReply } from '../../api/types';
import { useCanEdit } from '../../book/BookContext';
import { useItemTitle } from '../../frame/itemTitle';
import { withCelebrate } from '../../lib/celebrations';
import { SAVED, TRY_AGAIN, UNDO, UNDONE } from '../../lib/copy';
import { dateLabel, money } from '../../lib/format';
import { reimburseFacts, rich, type Part } from '../../lib/receipt';
import { Button } from '../../ui/Button';
import { Money } from '../../ui/Money';
import { Notice } from '../../ui/Notice';
import { Rich } from '../../ui/Rich';
import { Skeleton } from '../../ui/Skeleton';
import { BackLink } from '../spending/parts';
import * as C from './copy';
import { fill, GROUP_TITLES, GROUPS, markTotals, reasonText, tripDates } from './model';
import { TripSheet } from './TripSheet';
import '../spending/spending.css';
import './reimburse.css';

type Said = { parts: Part[]; undo: string | null };

/** A trip's review list (reimbursable spec, The review list and The button): the charges around its dates in four
 *  groups, each saying why; the ticked ones are marked in one request with one Undo. */
export function TripPage() {
  const { id = '' } = useParams();
  const q = useTrip(Number(id));
  const payersQ = usePayers();
  const qc = useQueryClient();
  const canEdit = useCanEdit();
  const [ticked, setTicked] = useState<Set<number>>(new Set());
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [said, setSaid] = useState<Said | null>(null);
  const head = useRef<HTMLElement>(null);
  const rows = q.data?.rows;
  // The server's ticks: the trip's own rows stay ticked when the dates change (spec, Editing).
  useEffect(() => { if (rows) setTicked(new Set(rows.filter((r) => r.ticked).map((r) => r.id))); }, [rows]);
  useItemTitle(q.data?.trip.name);

  const back = <BackLink to="/spending/reimbursements">{C.REIMBURSEMENTS}</BackLink>;
  if (q.isError) {
    return (
      <div className="screen spending">{back}
        <Notice tone="error"><p>{q.error.message}</p><Button variant="quiet" onClick={() => void q.refetch()}>{TRY_AGAIN}</Button></Notice>
      </div>
    );
  }
  if (!q.data) return <div className="screen spending" aria-busy="true">{back}<Skeleton lines={8} /></div>;
  const { trip } = q.data;
  const all = q.data.rows;
  const today = new Date();
  const { n, total } = markTotals(all, ticked);

  function toggle(rid: number) {
    setTicked((s) => {
      const next = new Set(s);
      if (next.has(rid)) next.delete(rid); else next.add(rid);
      return next;
    });
  }

  async function run(fn: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError(null);
    try { await fn(); } catch (e) { setError(e instanceof Error ? e.message : 'Something went wrong.'); } finally { setBusy(false); }
  }

  const mark = () => run(async () => {
    const r = await markTrip(trip.id, [...ticked]);
    setSaid({ undo: r.undo, parts: rich(r.count === 1 ? C.MARKED_TRIP_ONE : C.MARKED_TRIP,
      { trip: r.trip.name, n: r.count, total: money(r.total_cents), payer: r.payer, owed: money(r.owed_cents) }) });
    const host = head.current;
    if (host) void withCelebrate((c) => { c.trip({ host }); });
    await invalidateReimburse(qc);
  });

  const notReimbursed = (row: TripRow) => run(async () => {
    const r = await api<TxnPatchReply>(`/api/transactions/${row.id}`, patch({ reimbursable: false }));
    const rr = r.result?.reimburse;
    setSaid({ undo: r.result?.undo ?? null, parts: rr ? reimburseFacts(rr, r.category, row.amount_cents) : [{ text: SAVED, bold: false }] });
    await invalidateReimburse(qc);
  });

  const undo = () => run(async () => {
    if (!said?.undo) return;
    await api<UndoReply>(`/api/undo/${said.undo}`, { method: 'POST' });
    setSaid({ parts: [{ text: UNDONE, bold: false }], undo: null });
    await invalidateReimburse(qc);
  });

  return (
    <div className="screen spending">
      {back}
      <header ref={head} className="sp-head">
        <h1>{trip.name}</h1>
        <p className="muted">{[tripDates(trip, today), trip.city, trip.payer].filter(Boolean).join(' · ')}</p>
        {/* The sheet names the payer when there are two or more, so it opens once the payers are in. */}
        {canEdit && payersQ.data && <Button variant="quiet" icon={Pencil} onClick={() => { setEditing(true); }}>{C.EDIT}</Button>}
      </header>
      {said && (
        <Notice>
          <div className="rb-said">
            <p><Rich parts={said.parts} /></p>
            {said.undo && <Button variant="quiet" busy={busy} onClick={() => { void undo(); }}>{UNDO}</Button>}
          </div>
        </Notice>
      )}
      {error && <Notice tone="error">{error}</Notice>}
      {GROUPS.map((g) => {
        const these = all.filter((r) => r.group === g);
        if (these.length === 0) return null;
        return (
          <section key={g} className="rb-group" aria-labelledby={`rb-g-${g}`}>
            <h2 id={`rb-g-${g}`}>{GROUP_TITLES[g]}</h2>
            <ul className="rb-list">
              {these.map((r) => {
                const name = r.merchant || r.raw_description;
                return (
                  <li key={r.id} className="rb-row" data-testid="trip-row" data-id={r.id}>
                    <label className="check-row rb-row__check">
                      <input type="checkbox" checked={ticked.has(r.id)} disabled={!canEdit} onChange={() => { toggle(r.id); }} />
                      <span>{name}</span>
                    </label>
                    <Money cents={r.amount_cents} />
                    <span className="rb-row__meta muted">{dateLabel(r.posted_at, today)} · {reasonText(r.reason)}</span>
                    {canEdit && r.reimburser_id != null && (
                      <Button variant="quiet" aria-label={fill(C.NOT_REIMBURSED_FOR, { name })} onClick={() => { void notReimbursed(r); }}>
                        {C.NOT_REIMBURSED}
                      </Button>
                    )}
                  </li>
                );
              })}
            </ul>
          </section>
        );
      })}
      {canEdit && (
        <div className="rb-mark">
          <Button busy={busy} onClick={() => { void mark(); }}>{fill(C.MARK_N, { n, total: money(total) })}</Button>
        </div>
      )}
      {editing && (
        <TripSheet trip={trip} payers={payersQ.data?.payers ?? []} onClose={() => { setEditing(false); }}
          onSaved={() => { setEditing(false); void invalidateReimburse(qc); }} />
      )}
    </div>
  );
}

export default TripPage;
