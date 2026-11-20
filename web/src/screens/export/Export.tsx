import { useQuery } from '@tanstack/react-query';
import { Download } from 'lucide-react';
import { useId, useState, type FormEvent } from 'react';
import { resolveApiPath } from '../../api/book';
import { api, json } from '../../api/client';
import { useBook } from '../../book/BookContext';
import { Button } from '../../ui/Button';
import { Card } from '../../ui/Card';
import { Notice } from '../../ui/Notice';
import { failure } from '../settings/shared';
import './export.css';
import { thisYear } from '../../lib/today';
import { Shown } from '../../ui/Live';

// Import/export spec, Copy: Export (Show is plan-owned, Engineering choice 13).
const C = {
  heading: 'Export', transactions: 'Transactions (CSV)', range: 'Range', from: 'From', to: 'To', notes: 'Include notes',
  business: 'Business report (CSV)', year: 'Year', download: 'Download', backup: 'Encrypted backup of this book',
  make: 'Make one', panel: 'Choose a passphrase for this backup', pass: 'Passphrase', again: 'Type it again',
  hint: "Only this passphrase opens the file. Steward can't recover it if it's lost.", button: 'Download backup',
  busy: 'Making the backup…', short: 'Use at least 12 characters.', mismatch: "The two don't match.", show: 'Show',
};
const RANGES = [['all', 'All time'], ['this', 'This year'], ['last', 'Last year'], ['choose', 'Choose dates']] as const;
type Range = (typeof RANGES)[number][0];

export function transactionsHref(slug: string, range: Range, from: string, to: string, notes: boolean, year: number): string {
  const q = new URLSearchParams();
  if (range === 'this' || range === 'last') {
    const y = range === 'this' ? year : year - 1;
    q.set('from', `${y}-01-01`);
    q.set('to', `${y}-12-31`);
  } else if (range === 'choose' && from && to) {
    q.set('from', from);
    q.set('to', to);
  }
  if (notes) q.set('notes', '1');
  const s = q.toString();
  return resolveApiPath(`/api/export/transactions.csv${s ? `?${s}` : ''}`, slug);
}

function Backup({ slug, go }: { slug: string; go: (url: string) => void }) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [pass, setPass] = useState('');
  const [again, setAgain] = useState('');
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [fieldError, setFieldError] = useState<{ which: 'pass' | 'again'; text: string } | null>(null);
  const [error, setError] = useState('');
  if (!open) return <Button variant="quiet" onClick={() => setOpen(true)}>{C.make}</Button>;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError('');
    if (pass.normalize('NFC').length < 12) { setFieldError({ which: 'pass', text: C.short }); return; }
    if (pass.normalize('NFC') !== again.normalize('NFC')) { setFieldError({ which: 'again', text: C.mismatch }); return; }
    setFieldError(null);
    setBusy(true);
    try {
      const r = await api<{ token: string }>('/api/export/backup', json({ passphrase: pass, again }));
      setPass('');
      setAgain('');
      go(resolveApiPath(`/api/export/backup/${encodeURIComponent(r.token)}`, slug));   // a native download (P29)
    } catch (err) {
      setError(failure(err));
    }
    setBusy(false);
  }

  const input = (which: 'pass' | 'again', label: string, value: string, set: (v: string) => void) => {
    const err = fieldError?.which === which ? `${id}-${which}-err` : '';
    const hint = which === 'pass' ? `${id}-hint` : '';
    return (
      <div className="field">
        <label className="field__label" htmlFor={`${id}-${which}`}>{label}</label>
        <input id={`${id}-${which}`} type={show ? 'text' : 'password'} autoComplete="new-password" value={value}
          aria-describedby={[hint, err].filter(Boolean).join(' ') || undefined} aria-invalid={err ? true : undefined}
          onChange={(e) => set(e.target.value)} />
        {err && <p id={err} className="field__error">{fieldError!.text}</p>}
      </div>
    );
  };
  return (
    <form className="exp-panel" onSubmit={submit} aria-busy={busy || undefined}>
      <fieldset>
        <legend>{C.panel}</legend>
        {input('pass', C.pass, pass, setPass)}
        {input('again', C.again, again, setAgain)}
        <p id={`${id}-hint`} className="small muted">{C.hint}</p>
        <div className="row-actions">
          <Button type="submit" busy={busy}>{C.button}</Button>
          <Button variant="quiet" aria-pressed={show} onClick={() => setShow((s) => !s)}>{C.show}</Button>
        </div>
        {busy && <Shown className="small">{C.busy}</Shown>}
        {error && <Notice tone="error">{error}</Notice>}
      </fieldset>
    </form>
  );
}

/** Settings › Export (import/export spec P23-P30): managers only, its own lazy chunk. */
export default function Export({ go = (url: string) => window.location.assign(url) }: { go?: (url: string) => void }) {
  const book = useBook();
  const slug = book?.slug ?? 'ours';
  const household = (book?.kind ?? 'household') === 'household';
  const year = thisYear();
  const biz = useQuery({ queryKey: ['business', 'export'], queryFn: () => api<{ type: string }>('/api/business'), enabled: household });
  const [range, setRange] = useState<Range>('all');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [notes, setNotes] = useState(false);                 // P24: unticked on every visit, never remembered
  const [bizYear, setBizYear] = useState(year);
  const id = useId();
  return (
    <section className="spage exp" aria-labelledby={`${id}-h`}>
      <h2 id={`${id}-h`}>{C.heading}</h2>
      <Card className="exp-rows">
        <section className="exp-row" aria-labelledby={`${id}-tx`}>
          <h3 id={`${id}-tx`} className="exp-row__name">{C.transactions}</h3>
          <div className="exp-row__controls">
            <label className="sr-only" htmlFor={`${id}-range`}>{C.range}</label>
            <select id={`${id}-range`} value={range} onChange={(e) => setRange(e.target.value as Range)}>
              {RANGES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
            {range === 'choose' && (
              <>
                <label className="exp-date">{C.from}<input type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></label>
                <label className="exp-date">{C.to}<input type="date" value={to} onChange={(e) => setTo(e.target.value)} /></label>
              </>
            )}
            <label className="check-row"><input className="check" type="checkbox" checked={notes} onChange={(e) => setNotes(e.target.checked)} /><span>{C.notes}</span></label>
            <a className="btn btn--quiet" href={transactionsHref(slug, range, from, to, notes, year)} download>
              <span className="btn__content"><Download size={20} aria-hidden="true" />{C.download}</span>
            </a>
          </div>
        </section>
        {household && biz.data && biz.data.type !== 'none' && (
          <section className="exp-row" aria-labelledby={`${id}-biz`}>
            <h3 id={`${id}-biz`} className="exp-row__name">{C.business}</h3>
            <div className="exp-row__controls">
              <label className="sr-only" htmlFor={`${id}-year`}>{C.year}</label>
              <select id={`${id}-year`} value={bizYear} onChange={(e) => setBizYear(Number(e.target.value))}>
                {[year, year - 1].map((y) => <option key={y} value={y}>{y}</option>)}
              </select>
              <a className="btn btn--quiet" href={resolveApiPath(`/api/export/business.csv?year=${bizYear}`, slug)} download>
                <span className="btn__content"><Download size={20} aria-hidden="true" />{C.download}</span>
              </a>
            </div>
          </section>
        )}
        <section className="exp-row" aria-labelledby={`${id}-bk`}>
          <h3 id={`${id}-bk`} className="exp-row__name">{C.backup}</h3>
          <div className="exp-row__controls"><Backup slug={slug} go={go} /></div>
        </section>
      </Card>
    </section>
  );
}
