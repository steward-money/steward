import { useQueryClient } from '@tanstack/react-query';
import { Briefcase, ChevronDown, Percent, User, X } from 'lucide-react';
import { Suspense, useEffect, useRef, useState, type FormEvent } from 'react';
import { lazyScreen } from '../../lib/lazyScreen';
import { api, ApiError, json, patch, put } from '../../api/client';
import { useMatching, usePayers } from '../../api/hooks';
import { addReimburseRule } from '../../api/reimburse';
import type { RuleReply, SortResult, Txn, TxnPatchReply, UndoReply } from '../../api/types';
import { aboutFor } from '../../ask/about';
import { useCanEdit, useIsHousehold } from '../../book/BookContext';
import { TXN_LINES } from '../../lib/businessLines';
import { radioKeys, radioTab } from '../../lib/radioKeys';
import {
  ACCOUNT, ALSO_MARKED, ALWAYS_FOR, ALWAYS_REIMBURSABLE, BUSINESS, BUSINESS_OR_PERSONAL, BUSINESS_SHARE, CLOSE, DATE, EITHER_LINE, FROM_THE_BANK, IS_A_CLIENT, MERCHANT,
  PCT_RULE, PERSONAL, REIMBURSABLE, SAME_AS_CATEGORY, SAVE, SAVED, SCHEDULE_C_LINE, SPLIT, TAGGED_ONE_TOO, TAGGED_TOO, UNDO, UNDONE,
  VIEWER_CANT_CHANGE,
} from '../../lib/copy';
import { dateLabel } from '../../lib/format';
import { alsoLine, factsOf, reimburseFacts, type Part } from '../../lib/receipt';
import { AskAbout } from '../../ui/AskAbout';
import { Button } from '../../ui/Button';
import { CategoryField } from '../../ui/CategoryMenu';
import { Field } from '../../ui/Field';
import { Shown } from '../../ui/Live';
import { Money } from '../../ui/Money';
import { Notice } from '../../ui/Notice';
import { Rich } from '../../ui/Rich';
import { useSheetSaved } from '../../ui/Sheet';
import { txnLabel } from '../../ui/TxnName';
import { Notes } from './Notes';
import { PaidFrom } from './PaidFrom';

const PayerInline = lazyScreen(() => import('../reimburse/PayerInline'));
const CameFrom = lazyScreen(() => import('./CameFrom'));

type Props = { txn: Txn; today: Date; onSaved: (t: Txn) => void; onClose: () => void; showHeader: boolean };
type Mode = 'personal' | 'business' | 'split';
/** `undo` holds the tokens still to reverse, newest first: the Always rule (with its bulk marks), then the PATCH. */
type Saved = { parts: Part[]; undo: string[] };

const modeOf = (pct: number): Mode => (pct === 100 ? 'business' : pct > 0 ? 'split' : 'personal');
const REFRESH = ['transactions', 'review', 'business', 'spending', 'plan', 'matching', 'reimburse'];

export function TxnDetail({ txn, today, onSaved, onClose, showHeader }: Props) {
  const qc = useQueryClient();
  const formRef = useRef<HTMLFormElement>(null);
  const markSaved = useSheetSaved(formRef);   // this form only: the note form above keeps its own unsaved text
  const canEdit = useCanEdit();
  const household = useIsHousehold();
  const heading = useRef<HTMLHeadingElement>(null);
  // In the desktop pane nothing else moves focus, so land on the heading when a row is opened.
  useEffect(() => { if (showHeader) heading.current?.focus(); }, [showHeader]);
  const [category, setCategory] = useState(txn.category);
  const [merchant, setMerchant] = useState(txn.merchant ?? '');
  const [mode, setMode] = useState<Mode>(modeOf(txn.business_pct));
  const [pct, setPct] = useState(txn.business_pct > 0 && txn.business_pct < 100 ? String(txn.business_pct) : '50');
  const [line, setLine] = useState(txn.business_line ?? '');
  const [always, setAlways] = useState(false);
  const [also, setAlso] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<Saved | null>(null);
  const payersQ = usePayers(household && canEdit);
  const payers = payersQ.data?.payers ?? [];
  const wasReimb = txn.reimburser_id != null;
  const [reimb, setReimb] = useState(wasReimb);
  const [payer, setPayer] = useState<number | null>(txn.reimburser_id ?? null);
  const [alwaysR, setAlwaysR] = useState(false);
  const [either, setEither] = useState(false);

  const categoryChanged = category !== txn.category;
  const match = useMatching(txn.id, canEdit && categoryChanged);
  const matching = categoryChanged ? (match.data?.count ?? 0) : 0;
  const showBusiness = household && !txn.is_transfer && txn.category !== 'transfer';
  const pctOk = mode !== 'split' || (/^\d{1,2}$/.test(pct) && Number(pct) >= 1 && Number(pct) <= 99);
  const wantPct = mode === 'personal' ? 0 : mode === 'business' ? 100 : Number(pct);
  const moneyIn = txn.amount_cents > 0;
  // A client's payments are income at 100% (final review M2, M5).
  const lineValue = wantPct > 0 ? (line || (moneyIn && always ? 'income' : null)) : null;
  const textChanged = categoryChanged || merchant.trim() !== (txn.merchant ?? '');
  const bizChanged = showBusiness && (wantPct !== txn.business_pct || (wantPct > 0 && lineValue !== txn.business_line));
  const ruleWanted = showBusiness && always && wantPct > 0;
  const who = txn.merchant || txn.raw_description;
  const ruleLabel = moneyIn ? IS_A_CLIENT.replace('{payer}', who) : ALWAYS_FOR.replace('{merchant}', who).replace('{account}', txn.account_name);
  // Reimbursable spec, Marking: the switch for money out, R2's refunds and a row already marked; never a transfer or a
  // repayment. A repayment (category Reimbursement) asks who paid when there is no payer, or two.
  const repaying = categoryChanged && category === 'reimbursement';
  const showReimb = household && !txn.is_transfer && category !== 'transfer' && category !== 'reimbursement'
    && (txn.amount_cents < 0 || txn.reimburse_ok === true || wasReimb);
  const chosen = payers.length === 1 ? payers[0].id : payer;
  const reimbChanged = showReimb && (reimb !== wasReimb || (reimb && chosen !== null && chosen !== txn.reimburser_id));
  const alwaysWanted = showReimb && reimb && alwaysR;
  const wantsPayer = (showReimb && reimb && (reimbChanged || alwaysWanted)) || repaying;
  const asking = ((showReimb && reimb) || repaying) && payersQ.data !== undefined && payers.length !== 1;
  const payerMissing = wantsPayer && chosen === null;
  const changed = textChanged || bizChanged || ruleWanted || reimbChanged || alwaysWanted;

  function refill(next: Txn) {
    onSaved(next);
    setCategory(next.category);
    setMerchant(next.merchant ?? '');
    setMode(modeOf(next.business_pct));
    setLine(next.business_line ?? '');
    setReimb(next.reimburser_id != null);
    setPayer(next.reimburser_id ?? null);
    setEither(false);
    markSaved();                      // in a sheet: the boxes now show what is saved, so closing asks nothing
    for (const key of REFRESH) void qc.invalidateQueries({ queryKey: [key] });
  }

  function toggleReimb() {
    const on = !reimb;
    setReimb(on);
    if (!on) setAlwaysR(false);
    if (on && mode !== 'personal') { setMode('personal'); setAlways(false); setEither(true); }
  }

  async function save(e: FormEvent) {
    e.preventDefault();
    if (busy || !changed || !pctOk || payerMissing) return;
    setBusy(true);
    setError(null);
    try {
      let next = txn;
      let result: SortResult | undefined;
      const sent = categoryChanged ? category : null;
      if (textChanged || reimbChanged) {
        const body: Record<string, unknown> = {};
        if (sent) {
          body.category = sent;
          if (matching > 0) body.apply_to_matching = also;
        }
        if (merchant.trim() !== (txn.merchant ?? '')) body.merchant = merchant.trim();
        if (reimbChanged) body.reimbursable = reimb;
        if (((reimbChanged && reimb) || repaying) && chosen !== null && payers.length !== 1) body.reimburser_id = chosen;
        const reply = await api<TxnPatchReply>(`/api/transactions/${txn.id}`, patch(body));
        next = reply;
        result = reply.result;
      }
      // With Reimbursable on, the PATCH already turned business off: no business PUT.
      if (bizChanged && !reimb) next = await api<Txn>(`/api/business/transactions/${txn.id}`, put({ pct: wantPct, line: lineValue }));
      let tagged = '';
      if (ruleWanted) {
        const r = await api<RuleReply>('/api/business/rules', json({ kind: moneyIn ? 'client' : 'merchant', transaction_id: txn.id, pct: wantPct, line: lineValue }));
        const others = Math.max(0, r.also_tagged);
        tagged = others === 0 ? '' : ` ${others === 1 ? TAGGED_ONE_TOO : TAGGED_TOO.replace('{n}', String(others))}`;
        setAlways(false);
      }
      let marked = '';
      const undo: string[] = result?.undo ? [result.undo] : [];
      if (alwaysWanted) {
        const r = await addReimburseRule(txn.id, chosen !== null && payers.length !== 1 ? chosen : undefined);
        marked = r.also_marked > 0 ? ` ${ALSO_MARKED.replace('{n}', String(r.also_marked))}` : '';
        undo.unshift(r.undo);
        setAlwaysR(false);
      }
      // Sorting redesign, The transaction detail: the facts line (no cheer, no celebration: it's a form) with Undo.
      const rr = result?.reimburse ?? null;
      const facts: Part[] = sent ? factsOf(sent, result, who, { matching: matching > 0 ? matching : null, ticked: also })
        : [{ text: SAVED, bold: false }];
      const parts: Part[] = rr
        ? [...reimburseFacts(rr, next.category, txn.amount_cents), ...(sent && (result?.applied ?? 0) > 0 ? [{ text: ' ', bold: false }, ...facts] : [])]
        : facts;
      if (tagged) parts.push({ text: tagged, bold: false });
      if (marked) parts.push({ text: marked, bold: false });
      setSaved({ parts, undo });
      setAlso(true);
      refill(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Something went wrong.');
    } finally {
      setBusy(false);
    }
  }

  async function undoSave() {
    if (!saved?.undo.length || busy) return;
    setBusy(true);
    setError(null);
    const todo = [...saved.undo];
    try {
      let last: UndoReply | undefined;
      while (todo.length > 0) {
        last = await api<UndoReply>(`/api/undo/${todo[0]}`, { method: 'POST' });
        todo.shift();
      }
      setSaved({ parts: [{ text: UNDONE, bold: false }], undo: [] });
      refill(last?.transaction ?? txn);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Something went wrong.');
      // A network failure keeps Undo for what is left, to try again; a refusal (409, 410) ends it.
      const keep = err instanceof ApiError && err.status === 0 ? todo : [];
      setSaved((s) => s && { ...s, undo: keep });
    } finally {
      setBusy(false);
    }
  }

  const modes: [Mode, string, typeof User][] = [['personal', PERSONAL, User], ['business', BUSINESS, Briefcase], ['split', SPLIT, Percent]];

  return (
    <div className="detail" data-testid="txn-detail">
      {showHeader && (
        <div className="detail__head">
          <h2 ref={heading} tabIndex={-1} className="detail__title">{txnLabel(txn)}</h2>
          <Button variant="quiet" icon={X} onClick={onClose}>{CLOSE}</Button>
        </div>
      )}
      <Money cents={txn.amount_cents} size="l" />
      <dl className="detail-facts">
        <dt>{FROM_THE_BANK}</dt><dd className="raw">{txn.raw_description}</dd>
        <dt>{DATE}</dt><dd>{dateLabel(txn.posted_at, today)}</dd>
        <dt>{ACCOUNT}</dt><dd>{txn.account_name}</dd>
      </dl>
      <Suspense fallback={null}><CameFrom txnId={txn.id} /></Suspense>
      <AskAbout about={aboutFor.transaction(txn)} surface={{ kind: 'transaction', category: txn.category }} thing={txnLabel(txn)} />
      <PaidFrom txn={txn} onSaved={onSaved} />
      <Notes txn={txn} today={today} />
      {!canEdit && <Notice>{VIEWER_CANT_CHANGE}</Notice>}
      <form ref={formRef} className="stack" onSubmit={save}>
        <fieldset className="readonly" disabled={!canEdit}>
        <CategoryField value={category} onChange={setCategory} disabled={!canEdit} />
        <Field label={MERCHANT}>
          <input value={merchant} onChange={(e) => { setMerchant(e.target.value) }} maxLength={200} />
        </Field>
        {showBusiness && (
          <div className="stack stack--tight">
            <div className="seg" role="radiogroup" aria-label={BUSINESS_OR_PERSONAL} onKeyDown={radioKeys}>
              {modes.map(([m, name, Icon]) => (
                <button key={m} type="button" role="radio" aria-checked={mode === m} tabIndex={radioTab(mode === m)} className={mode === m ? 'is-on' : ''} onClick={() => {
                  setMode(m);
                  if (moneyIn && m === 'split') setAlways(false);
                  if (m !== 'personal' && reimb) { setReimb(false); setAlwaysR(false); setEither(true); }
                }}>
                  <Icon size={20} aria-hidden="true" />{name}
                </button>
              ))}
            </div>
            {mode === 'split' && (
              <Field label={BUSINESS_SHARE} error={pctOk ? undefined : PCT_RULE}>
                <input className="pct-input" inputMode="numeric" value={pct} onChange={(e) => { setPct(e.target.value.trim()) }} maxLength={3} />
              </Field>
            )}
            {mode !== 'personal' && (
              <>
                <div className="select">
                  <Field label={SCHEDULE_C_LINE}>
                    <select value={line} onChange={(e) => { setLine(e.target.value) }}>
                      <option value="">{SAME_AS_CATEGORY}</option>
                      {TXN_LINES.map((l) => <option key={l.key} value={l.key}>{`${l.label} (line ${l.number})`}</option>)}
                    </select>
                  </Field>
                  <ChevronDown size={20} aria-hidden="true" />
                </div>
                <label className="check-row">
                  <input type="checkbox" checked={always} onChange={(e) => { setAlways(e.target.checked); if (moneyIn && e.target.checked) setMode('business'); }} />
                  <span>{ruleLabel}</span>
                </label>
              </>
            )}
          </div>
        )}
        {showReimb && (
          <div className="stack stack--tight">
            <button type="button" role="switch" aria-checked={reimb} className={`rev-switch${reimb ? ' is-on' : ''}`} onClick={toggleReimb}>
              <span className="rev-switch__track" aria-hidden="true" />{REIMBURSABLE}
            </button>
            {reimb && (
              <label className="check-row">
                <input type="checkbox" checked={alwaysR} onChange={(e) => { setAlwaysR(e.target.checked); }} />
                <span>{ALWAYS_REIMBURSABLE.replace('{merchant}', who)}</span>
              </label>
            )}
          </div>
        )}
        {either && <Shown className="muted detail__either">{EITHER_LINE}</Shown>}
        {asking && (
          <Suspense fallback={null}>
            <PayerInline payers={payers} value={chosen} onChoose={setPayer} />
          </Suspense>
        )}
        {matching > 0 && (
          <label className="check-row">
            <input type="checkbox" checked={also} onChange={(e) => { setAlso(e.target.checked); }} />
            <span>{alsoLine(matching)}</span>
          </label>
        )}
        {error && <Notice tone="error">{error}</Notice>}
        {saved && (
          <Notice>
            <p className="detail__receipt"><Rich parts={saved.parts} /></p>
            {saved.undo.length > 0 && <Button variant="quiet" busy={busy} onClick={() => { void undoSave(); }}>{UNDO}</Button>}
          </Notice>
        )}
        <Button type="submit" busy={busy} disabled={!changed || !pctOk || payerMissing} className="btn--block">{SAVE}</Button>
        </fieldset>
      </form>
    </div>
  );
}
