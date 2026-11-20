import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import type { Account } from '../../api/types';
import { withCelebrate } from '../../lib/celebrations';
import { CANCEL, MERGE_INTO, UNDONE } from '../../lib/copy';
import { money } from '../../lib/format';
import { Button } from '../../ui/Button';
import { Shown } from '../../ui/Live';
import { Notice } from '../../ui/Notice';
import { Sheet } from '../../ui/Sheet';
import { mergeInto, previewMerge, refresh, undoIt, type MergeReply } from './api';
import * as C from './copy';
import { mergedLine, previewLine } from './lines';
import { SaidLine, type Said } from './Said';
import './sameaccount.css';

export type MergeIntoProps = { account: Account; others: Account[]; onClose: () => void; onMerged: (r: MergeReply) => void };
const message = (e: unknown) => (e instanceof Error ? e.message : 'Something went wrong.');

/** Merge into… (same-account spec, Merging): the household's other active bank accounts of the same type, or any of
 *  them for an account whose type isn't set yet. Choosing one previews what matches and what moves; the confirm names
 *  the account that stays (Engineering choice 11). */
export default function MergeInto({ account, others, onClose, onMerged }: MergeIntoProps) {
  const qc = useQueryClient();
  const [into, setInto] = useState<number | null>(others.length === 1 ? others[0].id : null);
  const preview = useQuery({
    queryKey: ['merge-preview', account.id, into], queryFn: () => previewMerge(account.id, into as number),
    enabled: into !== null, staleTime: 0, retry: false,
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const chosen = others.find((o) => o.id === into) ?? null;

  async function confirm() {
    if (!chosen || busy) return;
    setBusy(true);
    setError(null);
    try {
      const r = await mergeInto(account.id, chosen.id);
      onMerged(r);                 // the page moves to the kept account first, then everything reads again
      void refresh(qc);
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Sheet open onClose={onClose} title={C.MERGE_TITLE.replace('{name}', account.name)}>
      <div className="same-merge">
        <p>{C.MERGE_BODY}</p>
        <fieldset className="same-merge__choices">
          <legend>{C.MERGE_CHOOSE}</legend>
          {others.map((o) => (
            <label key={o.id} className="same-merge__choice">
              <input type="radio" name="merge-into" value={o.id} checked={into === o.id} onChange={() => { setInto(o.id); }} />
              <span>{o.name}</span>
              <span className="same-merge__sub">{o.balance_cents === null ? o.institution : `${o.institution} · ${money(o.balance_cents)}`}</span>
            </label>
          ))}
        </fieldset>
        {into !== null && (preview.isPending ? <p className="muted">{C.COUNTING}</p>
          : preview.isError ? <Notice tone="error">{preview.error.message}</Notice>
            : <Shown data-testid="merge-preview">{previewLine(preview.data.merged)}</Shown>)}
        {error && <Notice tone="error">{error}</Notice>}
        <div className="same-merge__acts">
          <Button disabled={!chosen || !preview.isSuccess} busy={busy} onClick={() => { void confirm(); }}>
            {chosen ? C.MERGE_CONFIRM.replace('{name}', chosen.name) : MERGE_INTO}
          </Button>
          <Button variant="quiet" onClick={onClose}>{CANCEL}</Button>
        </div>
      </div>
    </Sheet>
  );
}

/** The kept account's page after Merge into…: what happened, the quiet cheer and its burst, and Undo. */
export function MergedNote({ reply }: { reply: MergeReply }) {
  const qc = useQueryClient();
  const host = useRef<HTMLDivElement>(null);
  const [said, setSaid] = useState<Said>({ parts: mergedLine(reply.merged), cheer: C.CHEER, undo: reply.undo });
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const el = host.current;
    if (el) void withCelebrate((c) => { c.closer({ over: el }); });
  }, []);

  async function undo() {
    if (!said.undo || busy) return;
    setBusy(true);
    try {
      await undoIt(said.undo);
      setSaid({ parts: [{ text: UNDONE, bold: false }], cheer: null, undo: null });
      await refresh(qc);
    } catch (e) {
      setSaid((s) => ({ ...s, parts: [{ text: message(e), bold: false }], error: true }));
    } finally {
      setBusy(false);
    }
  }
  return <div ref={host} className="card same"><SaidLine said={said} busy={busy} onUndo={() => { void undo(); }} /></div>;
}
