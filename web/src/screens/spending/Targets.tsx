import { useQueryClient } from '@tanstack/react-query';
import { Info } from 'lucide-react';
import { useRef, useState, type ReactNode } from 'react';
import { currentBook } from '../../api/book';
import { useSpendingTargets } from '../../api/hooks';
import { saveTargets } from '../../api/spending';
import type { SpendingKind, SpendingTargets, TargetRow } from '../../api/types';
import { useCanEdit } from '../../book/BookContext';
import { label } from '../../lib/categories';
import { TRY_AGAIN } from '../../lib/copy';
import { dollars, localYmd } from '../../lib/format';
import { setQueryDataIfStillIn } from '../../queryClient';
import { Button } from '../../ui/Button';
import { Shown } from '../../ui/Live';
import { MoneyInput } from '../../ui/MoneyInput';
import { Notice } from '../../ui/Notice';
import { Skeleton } from '../../ui/Skeleton';
import { dollarsText } from '../settings/money';
import { failure } from '../settings/shared';
import * as C from './copy';
import { KeptTargets } from './KeptTargets';
import { KindSelect } from './KindSelect';
import {
  acceptAll, changeSummary, dayLabel, draftErrors, draftFrom, everydayTypicalText, fill, fixedTypicalText, hasSuggestions, isWatched, readDraft,
  rowChanged, totals, type Draft, type DraftRow, type ReadDraft,
} from './model';
import { Pills } from './parts';
import './spending.css';

/** Nothing left to save: every field reads, and reads as what is saved. */
function isClean(t: SpendingTargets, d: Draft): boolean {
  const p = readDraft(t, d);
  return 'body' in p && p.changed.length === 0;
}
const errorTitle = (p: Extract<ReadDraft, { error: string }>) => (p.category ? `${label(p.category)}: ${p.error}` : p.error);
const errorId = (field: string) => `sp-t-err-${field}`;
/** A field's input, by category (draftErrors' keys). */
const inputId = (field: string) => `sp-t-in-${field}`;

/** A field's message under it, and the attributes that tie the field to it. */
function fieldError(field: string, error: string | undefined) {
  return {
    props: error ? { 'aria-invalid': true, 'aria-describedby': errorId(field) } : {},
    note: error ? <span className="sp-field-error" id={errorId(field)}>{error}</span> : null,
  };
}

function TargetLine({ r, row, canEdit, error, onChange }:
{ r: TargetRow; row: DraftRow; canEdit: boolean; error: string | undefined; onChange: (row: DraftRow) => void }) {
  const name = label(r.category);
  const changed = rowChanged(r, row);
  const suggestion = r.suggested_cents === null ? null : dollarsText(r.suggested_cents);
  const typ = isWatched(row.kind) ? fixedTypicalText(r) : row.kind === 'everyday' ? everydayTypicalText(r) : C.ONCE_NOTE;
  const empty = isWatched(row.kind) ? C.PLACEHOLDER_TYPICAL : C.PLACEHOLDER_NO_TARGET;
  const field = fieldError(r.category, error);
  return (
    <div className={`sp-trow${row.kind !== 'occasional' && row.text === '' ? ' sp-trow--off' : ''}`} data-testid="target-row" data-category={r.category}>
      <span className="sp-trow__name">{name}</span>
      <span className="sp-trow__typ">{typ}</span>
      {canEdit && (
        <span className="sp-trow__move">
          {changed && row.kind === 'everyday' && suggestion !== null && row.text !== suggestion && (
            <button type="button" className="link is-acc" onClick={() => onChange({ ...row, text: suggestion })}>{fill(C.USE_AMOUNT, { amount: dollars(r.suggested_cents!) })}</button>
          )}
          <KindSelect label={name} value={row.kind} onChange={(kind) => onChange({ ...row, kind })} />
        </span>
      )}
      {/* Once in a while has no amount (the server refuses one), so it has no field. */}
      {row.kind !== 'occasional' && (canEdit
        ? (
          <span className={`sp-trow__input${changed ? ' is-changed' : ''}`}>
            <MoneyInput id={inputId(r.category)} aria-label={fill(isWatched(row.kind) ? C.EXPECTED_OF : C.TARGET_OF, { label: name })} placeholder={empty}
              value={row.text} onValueChange={(text) => onChange({ ...row, text })} {...field.props} />
          </span>
        )
        : <span className="sp-trow__input sp-trow__value">{row.text ? `$${row.text}` : empty}</span>)}
      {canEdit && field.note}
    </div>
  );
}

function Page({ children, busy = false }: { children: ReactNode; busy?: boolean }) {
  return <div className="screen spending" aria-busy={busy || undefined}><Pills on="targets" />{children}</div>;
}

function TargetsForm({ t, canEdit }: { t: SpendingTargets; canEdit: boolean }) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState<Draft>(() => draftFrom(t));
  const [first, setFirst] = useState(!t.has_targets);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const inFlight = useRef(false);
  const latest = useRef(draft);
  latest.current = draft;

  // New targets from the server (a refetch, someone else's save): a draft with nothing in it follows them; one with
  // changes in it is kept, and is now read against the new targets.
  const [base, setBase] = useState(t);
  if (base !== t) {
    setBase(t);
    if (isClean(base, draft)) setDraft(draftFrom(t));
  }

  const parsed = readDraft(t, draft);
  const errors = canEdit ? draftErrors(t, draft) : {};
  const sum = totals(t, draft);
  const edit = (next: (d: Draft) => Draft) => { setSaved(false); setError(null); setDraft(next); };
  const setRow = (c: string, row: DraftRow) => edit((d) => ({ ...d, rows: { ...d.rows, [c]: row } }));

  async function save(next: Draft) {
    const p = readDraft(t, next);
    // A field it can't read is named in the save bar and at the field; nothing goes to the server. Nor does a save with
    // nothing in it, which would otherwise say "Targets saved." for nothing.
    if (inFlight.current || 'error' in p || p.changed.length === 0) return;
    inFlight.current = true;
    setBusy(true);
    setError(null);
    try {
      const book = currentBook();
      const result = await saveTargets(p.body);
      if (setQueryDataIfStillIn<SpendingTargets>(qc, book, ['spending', 'targets'], () => result)) {
        // A field changed while the save was out stays as typed; otherwise the draft is what was saved.
        if (latest.current === next) setDraft(draftFrom(result));
        setFirst(!result.has_targets);   // ruling 11: a kind-only save still has no target
        setSaved(true);
        void qc.invalidateQueries({ queryKey: ['spending'] });
      }
    } catch (e) {
      setError(failure(e));   // the draft is kept, to try again
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }
  function useThese() {
    const next = acceptAll(t, draft);
    edit(() => next);
    // A field that can't be saved stops it: the notice stays, and the field it names gets the focus.
    const bad = Object.keys(draftErrors(t, next))[0];
    if (bad) document.getElementById(inputId(bad))?.focus();
    else void save(next);
  }

  const kindOf = (r: TargetRow) => draft.rows[r.category]?.kind ?? r.kind;
  const rowsOf = (k: SpendingKind) => t.categories.filter((r) => kindOf(r) === k);
  const card = (rows: TargetRow[]) => (
    <div className="card sp-targets">
      {rows.map((r) => (
        <TargetLine key={r.category} r={r} row={draft.rows[r.category]} canEdit={canEdit} error={errors[r.category]} onChange={(row) => setRow(r.category, row)} />
      ))}
    </div>
  );
  const count = 'body' in parsed ? parsed.changed.length : 0;
  const pending = 'error' in parsed || count > 0;
  const title = 'error' in parsed ? errorTitle(parsed) : count === 1 ? C.ONE_CHANGE : fill(C.N_CHANGES, { n: count });
  const changedOn = t.updated_at ? dayLabel(localYmd(t.updated_at)) : null;
  const once = rowsOf('occasional');
  const suggests = hasSuggestions(t);

  return (
    <Page>
      <header className="stack stack--tight">
        <h1>{C.SET_TARGETS}</h1>
        <p className="muted">{C.TARGETS_INTRO}</p>
        {suggests && <p className="muted">{C.SUGGESTION_LINE}</p>}
        <p className="muted">{C.TARGETS_ORDER}</p>
        {changedOn && <p className="small muted">{t.updated_by ? fill(C.LAST_CHANGED, { name: t.updated_by, date: changedOn }) : fill(C.LAST_CHANGED_NO_NAME, { date: changedOn })}</p>}
      </header>
      {first && !suggests && <p className="muted">{C.NO_HISTORY_YET}</p>}
      {canEdit && first && suggests && (
        <Notice tone="info">
          <div className="sp-first">
            <Info className="sp-first__icon" size={20} aria-hidden="true" />
            <div className="sp-first__body">
              <span className="eyebrow">{C.FIRST_EYEBROW}</span>
              <span>{C.FIRST_BODY}</span>
              <span className="row-actions">
                <Button busy={busy} onClick={useThese}>{C.USE_THESE}</Button>
                <Button variant="quiet" disabled={busy} onClick={() => { edit((d) => acceptAll(t, d)); setFirst(false); }}>{C.ADJUST_FIRST}</Button>
              </span>
            </div>
          </div>
        </Notice>
      )}
      <div className="sp-cols">
        <section className="stack" aria-labelledby="sp-t-every-h">
          <div className="section-head">
            <h2 id="sp-t-every-h">{C.FLEXIBLE}</h2>
            {canEdit && !first && suggests ? <Button variant="quiet" onClick={() => edit((d) => acceptAll(t, d))}>{C.ACCEPT_ALL}</Button> : <span className="small muted">{C.BY_TARGET}</span>}
          </div>
          {card(rowsOf('everyday'))}
        </section>
        <div className="stack sp-side">
          <section className="stack" aria-labelledby="sp-t-fixed-h">
            <div className="section-head"><h2 id="sp-t-fixed-h">{C.FIXED_COSTS}</h2><span className="small muted">{C.EXPECTED_NOT_WATCHED}</span></div>
            {card(rowsOf('fixed'))}
            {rowsOf('commitment').length > 0 && <><p className="sp-fixed-label eyebrow">{C.COMMITMENTS}</p>{card(rowsOf('commitment'))}</>}
          </section>
          {once.length > 0 && (
            <section className="stack" aria-labelledby="sp-t-once-h">
              <div className="section-head"><h2 id="sp-t-once-h">{C.ONCE_H}</h2></div>
              {card(once)}
            </section>
          )}
          <p className="small muted">{C.NOT_SPENDING_NOTE}</p>
          <section className="stack" aria-label={C.TOGETHER}>
            <div className="card sp-sumcard" data-testid="sumcard">
              <div className="sp-sumcard__row"><span>{C.FLEXIBLE_TARGETS}</span><span className="money money--m">{dollars(sum.everyday)}</span></div>
              <div className="sp-sumcard__row"><span>{C.FIXED_ABOUT}</span><span className="money money--m">{dollars(sum.fixed)}</span></div>
              <div className="sp-sumcard__row is-total"><span>{C.A_MONTH}</span><span className="money money--m">{dollars(sum.month)}</span></div>
              {sum.income !== null && <div className="sp-sumcard__row"><span className="muted">{C.TYPICAL_INCOME}</span><span className="money money--m money--in">{dollars(sum.income)}</span></div>}
              {sum.leaves !== null && <p className="small muted">{sum.leaves >= 0 ? fill(C.LEAVES, { amount: dollars(sum.leaves) }) : fill(C.MORE_THAN_INCOME, { amount: dollars(-sum.leaves) })}</p>}
            </div>
            {canEdit && pending && (
              <div className="sp-savebar" role="region" aria-label={title}>
                <div className="stack stack--tight">
                  <span className="sp-savebar__title">{title}</span>
                  {'body' in parsed && <span className="small muted">{changeSummary(t, parsed)}</span>}
                </div>
                {error && <p className="sp-savebar__error" role="alert">{error}</p>}
                <div className="sp-savebar__actions">
                  <Button variant="quiet" disabled={busy} onClick={() => edit(() => draftFrom(t))}>{C.RESET}</Button>
                  <Button busy={busy} disabled={'error' in parsed} onClick={() => void save(draft)}>{C.SAVE_TARGETS}</Button>
                </div>
              </div>
            )}
            {canEdit && !pending && saved && <Shown className="small">{C.SAVED}</Shown>}
          </section>
        </div>
      </div>
      <KeptTargets kept={t.kept} canEdit={canEdit} />
    </Page>
  );
}

export function Targets() {
  const q = useSpendingTargets();
  const canEdit = useCanEdit();
  const h1 = <h1>{C.SET_TARGETS}</h1>;
  if (q.isError) {
    return <Page>{h1}<Notice tone="error"><p>{q.error.message}</p><Button variant="quiet" onClick={() => q.refetch()}>{TRY_AGAIN}</Button></Notice></Page>;
  }
  if (!q.data) return <Page busy>{h1}<Skeleton lines={10} /></Page>;
  return <TargetsForm t={q.data} canEdit={canEdit} />;
}

export default Targets;
