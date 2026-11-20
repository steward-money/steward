import { useEffect, useRef, useState } from 'react';
import { api, ApiError } from '../../api/client';
import type { SortManyReply, UndoReply } from '../../api/types';
import { label } from '../../lib/categories';
import { type Celebrate, celebrationsOn, soundOn, withCelebrate } from '../../lib/celebrations';
import { CANCEL, N_SELECTED, NOT_SAVED, SORT_AS, SORT_TOO_MANY, SORTED_N_AS, SORTED_N_PARTIAL, UNDO, UNDONE } from '../../lib/copy';
import { rich, type Part } from '../../lib/receipt';
import { SORT_MAX } from '../../lib/selection';
import { Button } from '../../ui/Button';
import { CategoryMenu } from '../../ui/CategoryMenu';
import { Rich } from '../../ui/Rich';
import { UNDONE_MS, useHoldableTimer } from '../../lib/useHoldableTimer';
import { Shown } from '../../ui/Live';
import './bulk.css';

/** The result line stays this long, held while the pointer is on it or focus is in it. */
export const SAID_MS = 8000;

type Props = {
  /** Selection mode is on: the bar shows. */
  selecting: boolean;
  /** The picked rows on screen, top to bottom. */
  ids: readonly number[];
  onCancel: () => void;
  /** The sort is in: the rows it sorted, and any it couldn't (they stay picked). */
  onSorted: (r: SortManyReply) => void;
  /** Undo put every row back. */
  onUndone: (r: SortManyReply) => void;
  /** Where the focus goes when the bar it was in goes away (Cancel, Escape, Undo): the Select toggle. */
  home?: () => HTMLElement | null;
};

type Said = { reply: SortManyReply | null; parts: Part[]; undo: string | null; stamp: boolean };

const messageOf = (err: unknown) => (err instanceof Error ? err.message : 'Something went wrong.');

/** Bulk sort (profile spec, Sorting several rows at once): the pinned bar ("{n} selected · Sort as… · Cancel") and,
 *  after a sort, the result line with Undo. Its own chunk: Transactions and Review load it at the first Select. One
 *  request sorts every picked row; the category's moment plays once, on the result line. */
export default function BulkSort({ selecting, ids, onCancel, onSorted, onUndone, home }: Props) {
  const [menu, setMenu] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [said, setSaid] = useState<Said | null>(null);
  const [run, setRun] = useState<number | null>(null);
  const [held, setHeld] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const line = useRef<HTMLDivElement>(null);
  const stampAt = useRef<HTMLSpanElement>(null);
  const undoBtn = useRef<HTMLButtonElement>(null);
  const fx = useRef<(() => void) | null>(null);
  const celebrate = useRef<string | null>(null);
  const menuOpen = useRef(false);
  menuOpen.current = menu;
  const n = ids.length;
  /** The focus is in the bar or the result line (or nowhere): hand it to the Select toggle before they go. */
  const giveBack = () => {
    const at = document.activeElement;
    if (!at || at === document.body || at.closest('.bulk')) home?.()?.focus();
  };
  const end = () => { giveBack(); onCancel(); };
  const endRef = useRef(end);
  endRef.current = end;

  // A new selection starts clean: no error or open menu from the last one.
  useEffect(() => { setError(null); setMenu(false); }, [selecting]);

  useHoldableTimer(run, SAID_MS, held || busy, () => { setRun(null); setSaid(null); });

  // Escape ends selection mode. An Escape meant for something else is left alone: the menu, a sheet or any modal
  // dialog open (a filter, say), or a key pressed inside one.
  useEffect(() => {
    if (!selecting) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || e.defaultPrevented || menuOpen.current) return;
      const t = e.target instanceof Element ? e.target : null;
      if (document.querySelector('[aria-modal="true"], dialog[open]') || t?.closest('[role="dialog"], dialog')) return;
      endRef.current();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [selecting]);

  useEffect(() => {
    const at = document.activeElement;
    // The bar the focus was in has gone (or the focus is still in the bulk area): Undo takes it.
    if (said?.undo && (!at || at === document.body || at.closest('.bulk'))) undoBtn.current?.focus();
  }, [said]);

  useEffect(() => {
    const category = celebrate.current;
    celebrate.current = null;
    const host = line.current;
    if (category && host) void withCelebrate((c: Celebrate) => { fx.current = c.sorted({ card: host, stamp: stampAt.current, category, applied: 0, sound: soundOn() }); });
  }, [said]);

  useEffect(() => () => { fx.current?.(); }, []);

  function sort(category: string) {
    setMenu(false);
    if (busy || n === 0 || n > SORT_MAX) return;
    setBusy(true);
    setError(null);
    fx.current?.(); fx.current = null;
    void api<SortManyReply>('/api/transactions/sort', { method: 'POST', body: JSON.stringify({ ids, category }) }).then((r) => {
      const name = label(r.category);
      const why = r.skipped[0]?.reason ?? '';
      const parts = r.sorted === 0 ? rich(why)
        : r.skipped.length ? rich(SORTED_N_PARTIAL, { n: r.sorted, category: name, m: r.skipped.length, reason: why })
          : rich(SORTED_N_AS, { n: r.sorted, category: name });
      if (r.sorted > 0) celebrate.current = r.category;
      setSaid({ reply: r, parts, undo: r.undo, stamp: r.sorted > 0 && celebrationsOn() });
      setRun((x) => (x ?? 0) + 1);
      onSorted(r);
    }).catch((err: unknown) => { setError(messageOf(err)); }).finally(() => { setBusy(false); });
  }

  function undo() {
    const r = said?.reply;
    if (!said?.undo || !r || busy) return;
    setBusy(true);
    void api<UndoReply>(`/api/undo/${said.undo}`, { method: 'POST' }).then(() => {
      fx.current?.(); fx.current = null;
      setSaid({ reply: null, parts: rich(UNDONE), undo: null, stamp: false });
      setRun(null);
      window.setTimeout(() => { setSaid((s) => (s?.reply === null && s.undo === null ? null : s)); }, UNDONE_MS);
      giveBack();                        // Undo is about to go: the focus goes back to Select
      onUndone(r);
    }).catch((err: unknown) => {
      // A refusal (409, 410) ends Undo; a network failure keeps it.
      const offline = err instanceof ApiError && err.status === 0;
      setSaid((s) => s && { ...s, parts: rich(messageOf(err)), undo: offline ? s.undo : null, stamp: false });
    }).finally(() => { setBusy(false); });
  }

  if (!selecting && !said) return null;
  const tooMany = n > SORT_MAX;
  return (
    <div className="bulk">
      {said && (
        <div ref={line} className="bulk__said" data-testid="bulk-said"
          onPointerEnter={(e) => { if (e.pointerType === 'mouse') setHeld(true); }}
          onPointerLeave={(e) => { if (e.pointerType === 'mouse') setHeld(false); }}
          onFocus={() => { setHeld(true); }}
          onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setHeld(false); }}>
          <Shown className="bulk__facts"><Rich parts={said.parts} /></Shown>
          {said.stamp && <span ref={stampAt} className="bulk__stamp" aria-hidden="true" />}
          {said.undo && <Button ref={undoBtn} variant="quiet" busy={busy} onClick={undo}>{UNDO}</Button>}
        </div>
      )}
      {selecting && (
        <div className="bulk__bar" data-testid="bulk-bar">
          <Shown className="bulk__count">
            <span className="strong">{N_SELECTED.replace('{n}', String(n))}</span>
            {tooMany && <span className="bulk__warn">{SORT_TOO_MANY}</span>}
          </Shown>
          {error && <p className="bulk__error" role="alert"><Rich parts={rich(NOT_SAVED, { message: error })} /></p>}
          <div className="bulk__actions">
            <Button variant="quiet" onClick={end}>{CANCEL}</Button>
            <Button ref={trigger} busy={busy} disabled={n === 0 || tooMany} aria-haspopup="dialog" aria-expanded={menu}
              onClick={() => { setMenu((m) => !m); }}>{SORT_AS}</Button>
          </div>
          {menu && (
            <CategoryMenu anchor={trigger.current} value={null} title={SORT_AS}
              onPick={sort} onClose={() => { setMenu(false); }} />
          )}
        </div>
      )}
    </div>
  );
}
