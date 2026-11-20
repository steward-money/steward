import { Car, House, TriangleAlert } from 'lucide-react';
import { useEffect, useMemo, useRef, useState, type FormEvent, type RefObject } from 'react';
import { isAbort } from '../../../api/client';
import { estimateSetAside } from '../../../api/setasides';
import type { Account, EstimatePreview, PreviewIn, SetAsideBody, SetAsideKind } from '../../../api/types';
import { label } from '../../../lib/categories';
import { dollars } from '../../../lib/format';
import { radioKeys, radioTab } from '../../../lib/radioKeys';
import { Button } from '../../../ui/Button';
import { Field } from '../../../ui/Field';
import { Shown } from '../../../ui/Live';
import { MoneyInput } from '../../../ui/MoneyInput';
import { Notice } from '../../../ui/Notice';
import { dollarsText } from '../../settings/money';
import { joinNames } from '../model';
import * as C from './copy';
import { changedBody, estimateWords, fill, hasEstimate, monthWord, previewLines, previewRequest, readDraft, sourceNotes, type Draft } from './model';
import '../../review.css';

const MONTHS = Array.from({ length: 12 }, (_, i) => i + 1);
const ITEMS = ['roof', 'water_heater', 'hvac', 'car', 'appliance', 'other'] as const;
const CASH = new Set(['checking', 'savings']);
/** The wait after the last keystroke before the server is asked for the figures (the preview takes a write lock). */
export const PREVIEW_MS = 400;

type Props = {
  initial: Draft; asOf: string; accounts: Account[]; busy: boolean; error: string | null; submitLabel: string;
  /** The whole body (Add), and what changed from the form's start (the Edit sheet's PUT is partial). */
  onSubmit: (body: SetAsideBody, changes: Partial<SetAsideBody>) => void; onCancel: () => void; sharedWith: (accountId: number) => string[];
  /** Set for the Edit sheet: the set-aside's id (so the preview is the edit's) and whether it carries the ESTIMATE tag now. */
  editing?: { id: number; isEstimate: boolean };
};

function MonthSelect({ value, onChange, label: aria, blank = false }: { value: string; onChange: (v: string) => void; label: string; blank?: boolean }) {
  return (
    <Field label={aria}>
      <select value={value} onChange={(e) => onChange(e.target.value)}>{blank && <option value="">—</option>}{MONTHS.map((m) => <option key={m} value={m}>{monthWord(m)}</option>)}</select>
    </Field>
  );
}

export function SetAsideForm({ initial, asOf, accounts, busy, error, submitLabel, onSubmit, onCancel, sharedWith, editing }: Props) {
  const [d, setD] = useState<Draft>(initial);
  // What the person has typed or chosen for the amount and the year: the estimate fills only the ones they have not.
  const touched = useRef({ amount: initial.amount !== '', year: initial.dueYear !== '' });
  const [pv, setPv] = useState<EstimatePreview | null>(null);
  const [pvError, setPvError] = useState<string | null>(null);
  const [showYear, setShowYear] = useState(initial.kind === 'one_off' && initial.item === 'other');
  const [fieldError, setFieldError] = useState<{ error: string; field: string } | null>(null);
  const yearBox = useRef<HTMLDivElement>(null);
  const amountBox = useRef<HTMLDivElement>(null);
  const set = (patch: Partial<Draft>) => { setD((prev) => ({ ...prev, ...patch })); setFieldError(null); };
  // An edit is a partial PUT against what the set-aside is now. If that does not read, it cannot be compared against:
  // say so and block Save, rather than fall back to sending the whole body as an Add would.
  const start = useMemo(() => readDraft(initial, asOf, initial), [initial, asOf]);
  const broken = editing && 'error' in start ? start.error : null;
  const edit = editing && 'body' in start ? { id: editing.id, was: start.body, initial } : undefined;

  // The figures under the form are the server's: asked 400 ms after the last change, never worked out here.
  const request = broken ? null : previewRequest(d, asOf, edit);
  const key = request ? JSON.stringify(request) : '';
  useEffect(() => {
    if (!key) { setPv(null); setPvError(null); return; }
    const ctrl = new AbortController();
    const t = setTimeout(async () => {
      try {
        const out = await estimateSetAside(JSON.parse(key) as PreviewIn, ctrl.signal);
        setPv(out);
        setPvError(null);
        if (!editing && hasEstimate(out)) {
          // A new one-off with an item and an age: the estimate's amount and year stand until the person says otherwise.
          setD((prev) => ({ ...prev,
            amount: touched.current.amount ? prev.amount : dollarsText(out.amount_cents),
            dueYear: touched.current.year ? prev.dueYear : String(out.due_year),
            dueMonth: touched.current.year ? prev.dueMonth : String(out.due_month) }));
        }
      } catch (err) {
        if (!isAbort(err)) { setPv(null); setPvError((err as Error).message || C.PREVIEW_FAILED); }
      }
    }, PREVIEW_MS);
    return () => { clearTimeout(t); ctrl.abort(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- key is the request
  }, [key]);

  function submit(e: FormEvent) {
    e.preventDefault();
    if (broken) return;
    const read = readDraft(d, asOf, initial);
    if ('error' in read) { setFieldError(read); return; }
    const changes = edit ? changedBody(read.body, edit.was) : read.body;
    if (edit && Object.keys(changes).length === 0) onCancel();
    else onSubmit(read.body, changes);
  }
  const err = (field: string) => (fieldError?.field === field ? fieldError.error : undefined);
  const focusIn = (box: RefObject<HTMLDivElement | null>) => setTimeout(() => box.current?.querySelector('input')?.focus(), 0);
  const lines = pv ? previewLines(pv) : null;
  const est = pv && hasEstimate(pv) && d.kind === 'one_off' ? pv : null;
  const cash = accounts.filter((a) => a.is_active && CASH.has(a.type));
  const linked = d.accountId && !cash.some((a) => String(a.id) === d.accountId) ? accounts.find((a) => String(a.id) === d.accountId) : undefined;
  const acct = [...cash, ...(linked ? [linked] : [])].find((a) => String(a.id) === d.accountId);
  const shared = d.accountId ? sharedWith(Number(d.accountId)) : [];
  const amountHint = d.kind === 'twice_yearly' && pv?.kind === 'twice_yearly' && pv.yearly_cents !== undefined ? fill(C.A_YEAR_MONTH_HINT, { amount: dollars(pv.yearly_cents), monthly: dollars(pv.monthly_cents) })
    : est ? fill(C.TYPICAL_HINT, { lo: dollars(est.cost_range[0]), hi: dollars(est.cost_range[1]) }) : undefined;
  const dropsTag = editing?.isEstimate && pv?.is_estimate === false;
  const yearOpen = showYear || d.item === 'other';

  return (
    <form className="sa-form" onSubmit={submit} data-testid="sa-form" noValidate>
      <Field label={C.WHAT_IS_IT} error={err('name')}><input value={d.name} maxLength={120} onChange={(e) => set({ name: e.target.value })} /></Field>
      <div className="seg" role="radiogroup" aria-label={C.HOW_OFTEN} onKeyDown={radioKeys}>
        {(['yearly', 'twice_yearly', 'one_off'] as SetAsideKind[]).map((k) => (
          <button key={k} type="button" role="radio" aria-checked={d.kind === k} tabIndex={radioTab(d.kind === k)} className={d.kind === k ? 'is-on' : ''} onClick={() => set({ kind: k })}>{C.KIND_LABEL[k]}</button>
        ))}
      </div>
      {d.kind === 'one_off' && (
        <div className="sa-kinds chips" role="group" aria-label={C.WHAT_KIND}>
          <span className="field__label">{C.ESTIMATE_FROM_AGE}</span>
          {ITEMS.map((item) => {
            const Icon = item === 'car' ? Car : House;
            return (
              <button key={item} type="button" className={`chip${item === 'other' ? ' chip--more' : ''}${d.item === item ? ' is-on' : ''}`} aria-pressed={d.item === item}
                onClick={() => { set({ item }); if (item === 'other') setShowYear(true); }}>
                {item !== 'other' && <Icon className="i" size={18} aria-hidden="true" />}{C.ITEM_LABEL[item]}
              </button>
            );
          })}
        </div>
      )}
      <div className="sa-pair">
        {d.kind === 'one_off' && d.item !== 'other' && d.item !== 'car' && (
          <Field label={C.INSTALLED_IN} hint={C.INSTALLED_HINT} error={err('installedYear')}><input inputMode="numeric" value={d.installedYear} onChange={(e) => set({ installedYear: e.target.value })} /></Field>
        )}
        {d.kind === 'one_off' && d.item === 'car' && (
          <>
            <Field label={C.MODEL_YEAR} error={err('modelYear')}><input inputMode="numeric" value={d.modelYear} onChange={(e) => set({ modelYear: e.target.value })} /></Field>
            <Field label={C.MILES} hint={est?.miles_per_year ? fill(C.MILES_HINT, { n: est.miles_per_year.toLocaleString('en-US') }) : undefined} error={err('miles')}>
              <input inputMode="numeric" value={d.miles} onChange={(e) => set({ miles: e.target.value })} />
            </Field>
          </>
        )}
        <div className="sa-amount" ref={amountBox}>
          <Field label={d.kind === 'one_off' ? C.ABOUT_HOW_MUCH : d.kind === 'twice_yearly' ? C.EACH_INSTALLMENT : C.HOW_MUCH} hint={amountHint} error={err('amount')}>
            <MoneyInput value={d.amount} onValueChange={(v) => { touched.current.amount = true; set({ amount: v }); }} />
          </Field>
        </div>
      </div>
      {d.kind === 'yearly' && (
        <div className="sa-pair" role="group" aria-label={C.DUE_IN_LABEL}>
          <MonthSelect label={C.DUE_IN_LABEL} value={d.dueMonth} onChange={(v) => set({ dueMonth: v })} />
          <Field label={C.DUE_DAY_LABEL} error={err('dueDay') ?? err('dueMonth')}><input inputMode="numeric" value={d.dueDay} onChange={(e) => set({ dueDay: e.target.value })} /></Field>
        </div>
      )}
      {d.kind === 'twice_yearly' && (
        <>
          <div className="sa-pair" role="group" aria-label={C.FIRST_DUE}>
            <MonthSelect label={`${C.FIRST_DUE}: ${C.MONTH_LABEL.toLowerCase()}`} value={d.dueMonth} onChange={(v) => set({ dueMonth: v })} />
            <Field label={`${C.FIRST_DUE}: ${C.DUE_DAY_LABEL.toLowerCase()}`} error={err('dueDay') ?? err('dueMonth')}><input inputMode="numeric" value={d.dueDay} onChange={(e) => set({ dueDay: e.target.value })} /></Field>
          </div>
          <div className="sa-pair" role="group" aria-label={C.SECOND_DUE}>
            <MonthSelect label={`${C.SECOND_DUE}: ${C.MONTH_LABEL.toLowerCase()}`} value={d.secondMonth} blank onChange={(v) => set({ secondMonth: v })} />
            <Field label={`${C.SECOND_DUE}: ${C.DUE_DAY_LABEL.toLowerCase()}`} error={err('secondDay') ?? err('secondMonth')}><input inputMode="numeric" value={d.secondDay} onChange={(e) => set({ secondDay: e.target.value })} /></Field>
          </div>
        </>
      )}
      {est && (
        <div className="sa-est" data-testid="sa-est">
          <TriangleAlert className="i" size={20} aria-hidden="true" />
          <div>
            <strong>{fill(C.LIKELY, { year: est.planned_year, monthly: dollars(est.monthly_cents) })}</strong>
            {estimateWords(est)}{' '}
            <button type="button" className="link sa-link--s" onClick={() => { setShowYear(true); touched.current.year = true; focusIn(yearBox); }}>{C.CHANGE_YEAR}</button>
            {est.item === 'car' && (
              <div className="sa-est__sum">
                <span>{C.REPLACEMENT}</span><span className="money">{dollars(est.replacement_cents)}</span>
                <span>{fill(C.LESS_SALE, { year: est.planned_year })}</span><span className="money">{`−${dollars(est.sale_cents)}`}</span>
                <span className="is-total">{fill(C.SET_ASIDE_OVER, { n: est.months })}</span><span className="money is-total">{dollars(est.amount_cents)}</span>
              </div>
            )}
            {sourceNotes(est).map((n) => <p key={n} className="sa-est__src">{n}</p>)}
            <div className="sa-est__edit"><button type="button" className="link" onClick={() => focusIn(amountBox)}>{C.CHANGE_AMOUNT}</button></div>
          </div>
        </div>
      )}
      {d.kind === 'one_off' && yearOpen && (
        <div className="sa-pair" ref={yearBox}>
          <Field label={C.YEAR_LABEL} error={err('dueYear')}><input inputMode="numeric" value={d.dueYear} onChange={(e) => { touched.current.year = true; set({ dueYear: e.target.value }); }} /></Field>
          <MonthSelect label={C.MONTH_LABEL} value={d.dueMonth} onChange={(v) => { touched.current.year = true; set({ dueMonth: v }); }} />
        </div>
      )}
      <Field label={C.PUT_AWAY_IN} hint={`${C.ACCOUNT_HINT}${acct && shared.length ? fill(C.ACCOUNT_SHARED_HINT, { name: acct.name, names: joinNames(shared) }) : ''}`}>
        <select value={d.accountId} onChange={(e) => set({ accountId: e.target.value })}>
          <option value="">{C.NO_ACCOUNT_OPTION}</option>
          {[...cash, ...(linked ? [linked] : [])].map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select>
      </Field>
      <Field label={C.MATCHES_LABEL} hint={d.payeeKey ? C.MATCHES_HINT : C.MATCHES_NONE}>
        <input value={d.payeeKey ? `${d.payeeName ?? d.payeeKey}${d.category ? ` · ${label(d.category)}` : ''}` : ''} readOnly />
      </Field>
      <Field label={C.NOTES_LABEL}><input value={d.notes} maxLength={500} onChange={(e) => set({ notes: e.target.value })} /></Field>
      {lines && (
        <Shown as="div" className="sa-preview" data-testid="sa-preview">
          <span>{lines.when}</span>
          <span className="money money--m">{lines.monthly}</span>
          {lines.more.length > 0 && <span className="sa-preview__more small muted">{lines.more.join(' · ')}</span>}
        </Shown>
      )}
      {dropsTag && <p className="small muted" data-testid="sa-tag-drops">{C.TAG_DROPS}</p>}
      {pvError && <p className="sa-field-error" role="alert">{pvError}</p>}
      {broken && <Notice tone="error"><p data-testid="sa-unreadable">{fill(C.CANNOT_EDIT, { why: broken })}</p></Notice>}
      {error && <Notice tone="error">{error}</Notice>}
      <div className="row-actions">
        <Button type="submit" busy={busy} disabled={broken !== null}>{submitLabel}</Button>
        <button type="button" className="link" onClick={onCancel}>{C.CANCEL}</button>
      </div>
    </form>
  );
}
