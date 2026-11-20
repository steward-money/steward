import { Pencil } from 'lucide-react';
import { createContext, useCallback, useContext, useEffect, useRef, useState, type FocusEvent, type KeyboardEvent } from 'react';
import { useQueryClient, type QueryClient } from '@tanstack/react-query';
import { undoToken } from '../../api/profile';
import { invalidatePlan } from '../../api/household';
import { UNDO } from '../../lib/copy';
import { composing } from '../../lib/ime';
import { Affix } from '../../ui/Affix';
import { Field, FormError } from '../../ui/Field';
import { Shown } from '../../ui/Live';
import { MoneyInput } from '../../ui/MoneyInput';
import { failure } from '../settings/shared';
import { EDIT, NOT_SET, SAVED } from './copy';

export type Mode = 'flow' | 'card';
export type Flusher = () => Promise<boolean>;
type Ctx = { mode: Mode; register: (f: Flusher) => () => void; canEdit: boolean };
export const FieldCtx = createContext<Ctx>({ mode: 'card', register: () => () => {}, canEdit: true });
export const useFieldMode = () => useContext(FieldCtx);
export const SAVED_MS = 15 * 60_000;

export type FieldSpec = {
  label: string;
  hint?: string;
  /** The saved value as the input holds it, and as the card reads it ('' reads "Not set"). */
  text: string;
  shown: string;
  input: 'text' | 'money' | 'date' | 'percent' | 'year' | 'select' | 'radios';
  options?: { value: string; label: string }[];
  post?: string;
  maxLength?: number;
  placeholder?: string;
  /** null: fine; otherwise the words to show under the field. */
  check: (text: string) => string | null;
  /** Saves the checked text; resolves with the Undo token, or null for a save that can't be undone. */
  save: (text: string) => Promise<string | null>;
  /** Read-only, with who can change it ("Only Alex can change this.") instead of a pencil. */
  readOnly?: string;
  /** The person's own field, on their partner's first visit (confirmOnly). */
  highlight?: boolean;
  /** Called with each new text, for consequence lines that follow the typing. */
  onText?: (text: string) => void;
  /** The text is a prefill the server doesn't have yet: the flow saves it on Next or Done. */
  unsaved?: boolean;
};

/** After any Profile write: every list a Profile screen, the Plan or Home reads. */
export async function invalidateProfile(qc: QueryClient): Promise<void> {
  await invalidatePlan(qc);
  await Promise.all(['profile', 'profile-home', 'profile-benefits', 'accounts', 'summary', 'me'].map((k) => qc.invalidateQueries({ queryKey: [k] })));
}

function Radios({ f, value, onChange, autoFocus, onBlur, ...rest }: { f: FieldSpec; value: string; onChange: (v: string) => void; autoFocus?: boolean; onBlur?: (e: FocusEvent<HTMLDivElement>) => void } & Record<string, unknown>) {
  const group = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!autoFocus) return;
    const radios = Array.from(group.current?.querySelectorAll<HTMLInputElement>('input[type=radio]') ?? []);
    (radios.find((r) => r.checked) ?? radios[0])?.focus();
  }, [autoFocus]);
  return (
    <div ref={group} className="ob-radios" role="radiogroup" aria-label={f.label} {...rest}
      onBlur={onBlur && ((e) => { if (e.currentTarget.contains(e.relatedTarget as Node | null)) return; onBlur(e); })}>
      {f.options!.map((o) => (
        <label key={o.value} className="radio-row">
          <input type="radio" name={f.label} value={o.value} checked={value === o.value} onChange={() => onChange(o.value)} />
          <span>{o.label}</span>
        </label>
      ))}
    </div>
  );
}

function Control({ f, value, onChange, ...extra }: { f: FieldSpec; value: string; onChange: (v: string) => void } & Record<string, unknown>) {
  const common = { ...extra, readOnly: f.readOnly ? true : undefined };
  switch (f.input) {
    case 'money':
      return <MoneyInput {...common} post={f.post} value={value} onValueChange={onChange} />;
    case 'percent':
      return <Affix {...common} post={f.post ?? '%'} className="affix--post-short" inputMode="decimal" value={value} onChange={(e) => onChange(e.target.value)} />;
    case 'date':
      return <input {...common} type="date" value={value} onChange={(e) => onChange(e.target.value)} />;
    case 'year':
      return <input {...common} inputMode="numeric" autoComplete="off" value={value} onChange={(e) => onChange(e.target.value)} />;
    case 'select':
      return (
        <select {...common} value={value} onChange={(e) => onChange(e.target.value)}>
          {f.options!.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      );
    case 'radios':
      return <Radios f={f} value={value} onChange={onChange} {...extra} />;
    default:
      return <input {...common} maxLength={f.maxLength} placeholder={f.placeholder} autoComplete="off" value={value} onChange={(e) => onChange(e.target.value)} />;
  }
}

/** The flow: every change saves (600 ms after typing stops; at once for a choice), and Next waits for it (spec, Saving). */
function FlowField(f: FieldSpec) {
  const { register } = useFieldMode();
  const qc = useQueryClient();
  const [text, setText] = useState(f.text);
  const [error, setError] = useState<string | null>(null);
  const spec = useRef(f);
  spec.current = f;
  const latest = useRef(f.text);
  const saved = useRef(f.unsaved ? '' : f.text);
  const timer = useRef<number | null>(null);
  const inflight = useRef<Promise<boolean> | null>(null);
  const wait = f.input === 'select' || f.input === 'radios' || f.input === 'date' ? 0 : 600;

  const commit = useCallback(async (): Promise<boolean> => {
    if (timer.current !== null) { window.clearTimeout(timer.current); timer.current = null; }
    if (inflight.current) await inflight.current;
    const t = latest.current;
    if (t === saved.current) return true;
    const problem = spec.current.check(t);
    if (problem) { setError(problem); return false; }
    const run = (async () => {
      try {
        await spec.current.save(t);
        saved.current = t;
        setError(null);
        await invalidateProfile(qc);
        return true;
      } catch (e) {
        setError(failure(e));                 // the value stays as typed
        return false;
      }
    })();
    inflight.current = run;
    const ok = await run;
    inflight.current = null;
    return ok;
  }, [qc]);
  useEffect(() => register(commit), [register, commit]);

  function change(v: string) {
    setText(v);
    latest.current = v;
    f.onText?.(v);
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => { timer.current = null; void commit(); }, wait);
  }
  if (f.input === 'radios') {
    return (
      <fieldset className={`field plain-fieldset${f.highlight ? ' is-mine' : ''}`}>
        <legend className="field__label">{f.label}</legend>
        <Control f={f} value={text} onChange={change} />
        {f.hint && <p className="field__hint">{f.hint}</p>}
        {error && <FormError>{error}</FormError>}
      </fieldset>
    );
  }
  return (
    <div className={f.highlight ? 'is-mine' : undefined}>
      <Field label={f.label} hint={f.readOnly ?? f.hint} error={error}>
        <Control f={f} value={text} onChange={change} />
      </Field>
    </div>
  );
}

/** The card: each value reads as text with a pencil; Enter or leaving the field saves, Escape cancels, and "Saved ·
 *  Undo" stays 15 minutes or until the next edit (spec, The card). */
function CardField(f: FieldSpec) {
  const { canEdit } = useFieldMode();
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(f.text);
  const [error, setError] = useState<string | null>(null);
  const [undo, setUndo] = useState<{ token: string; until: number } | null>(null);
  const saving = useRef(false);
  const cancelled = useRef(false);
  const pencil = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!undo) return;
    const t = window.setTimeout(() => setUndo(null), Math.max(0, undo.until - Date.now()));
    return () => window.clearTimeout(t);
  }, [undo]);

  async function save() {
    if (saving.current || cancelled.current) return;
    if (text === f.text) { setEditing(false); setError(null); return; }
    const problem = f.check(text);
    if (problem) { setError(problem); return; }
    saving.current = true;
    try {
      const token = await f.save(text);
      setEditing(false);
      setError(null);
      setUndo(token ? { token, until: Date.now() + SAVED_MS } : null);
      await invalidateProfile(qc);
      requestAnimationFrame(() => pencil.current?.focus());
    } catch (e) {
      setError(failure(e));
    } finally {
      saving.current = false;
    }
  }
  async function undoIt() {
    if (!undo) return;
    try {
      await undoToken(undo.token);
      setUndo(null);
      await invalidateProfile(qc);
    } catch (e) {
      setError(failure(e));
    }
  }
  function key(e: KeyboardEvent) {
    if (e.key === 'Enter') { e.preventDefault(); if (!composing(e)) void save(); }
    if (e.key === 'Escape') { cancelled.current = true; setText(f.text); setEditing(false); setError(null); }
  }
  if (editing) {
    return (
      <div onKeyDown={key}>
        <Field label={f.label} hint={f.hint} error={error}>
          <Control f={f} value={text} onChange={(v) => { setText(v); f.onText?.(v); }} autoFocus onBlur={() => void save()} />
        </Field>
      </div>
    );
  }
  return (
    <div className={`prof-value${f.highlight ? ' is-mine' : ''}`}>
      <span className="prof-value__label">{f.label}</span>
      <span className="prof-value__text">{f.shown || NOT_SET}</span>
      {f.readOnly ? <span className="small muted">{f.readOnly}</span> : canEdit && (
        <button ref={pencil} type="button" className="prof-icon-btn" aria-label={`${EDIT} ${f.label}`}
          onClick={() => { cancelled.current = false; setText(f.text); setEditing(true); setUndo(null); }}>
          <Pencil size={20} aria-hidden="true" />
        </button>
      )}
      {undo && (
        <Shown as="span" className="prof-saved">{SAVED} · <button type="button" className="link" onClick={() => void undoIt()}>{UNDO}</button></Shown>
      )}
      {error && <FormError>{error}</FormError>}
    </div>
  );
}

export function ProfileField(f: FieldSpec) {
  return useFieldMode().mode === 'flow' ? <FlowField {...f} /> : <CardField {...f} />;
}

/** A choice a viewer reads on the card: the value as text, with no control (viewers read the cards only). */
export function ShownValue({ label, text }: { label: string; text: string }) {
  return (
    <div className="prof-value">
      <span className="prof-value__label">{label}</span>
      <span className="prof-value__text">{text || NOT_SET}</span>
    </div>
  );
}
