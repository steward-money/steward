import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ChevronDown, Trash2 } from 'lucide-react';
import { useEffect, useState, type FormEvent } from 'react';
import { api, del, json } from '../../api/client';
import { useAccounts } from '../../api/hooks';
import type { BusinessRule, RuleReply } from '../../api/types';
import { useCanEdit } from '../../book/BookContext';
import { lineLabel } from '../../lib/businessLines';
import { radioKeys, radioTab } from '../../lib/radioKeys';
import { ACCOUNT, SAVED, TAGGED_ONE_TOO, TAGGED_TOO, VIEWER_CANT_CHANGE } from '../../lib/copy';
import { money } from '../../lib/format';
import { useRemoval } from '../../lib/useRemoval';
import { Button } from '../../ui/Button';
import { Card } from '../../ui/Card';
import { ConfirmSheet } from '../../ui/ConfirmSheet';
import { Field } from '../../ui/Field';
import { Shown } from '../../ui/Live';
import { MoneyInput } from '../../ui/MoneyInput';
import { Notice } from '../../ui/Notice';
import { RemovalLine } from '../../ui/RemovalLine';
import { Skeleton } from '../../ui/Skeleton';
import { failure } from '../settings/shared';
import { fromHundredths, toHundredths } from '../settings/money';
import { businessSettingsKey, saveBusinessSettings, useBusinessSettings, type BusinessSettingsOut, type HomeOfficeOut } from './api';
import * as C from './copy';
import './business.css';

export function ruleSentence(r: BusinessRule): string {
  if (r.kind === 'client') return C.RULE_CLIENT.replace('{label}', r.label);
  const account = r.account_name ?? r.label;
  if (r.kind === 'account') return (r.pct === 100 ? C.RULE_ACCOUNT_ALL : C.RULE_ACCOUNT_PCT.replace('{pct}', String(r.pct))).replace('{account}', account);
  const share = r.pct === 100 ? C.SHARE_ALL : C.SHARE_PCT.replace('{pct}', String(r.pct));
  return C.RULE_MERCHANT.replace('{label}', r.label).replace('{account}', account).replace('{share}', share) + (r.line ? `, ${lineLabel(r.line)}` : '');
}

const shortDate = (iso: string) => new Date(`${iso}T12:00:00`).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
const rate = (tenths: number) => `${tenths % 10 === 0 ? tenths / 10 : (tenths / 10).toFixed(1)}¢`;
const whole = (s: string) => (/^\d{1,6}$/.test(s.trim()) ? Number(s.trim()) : null);

function useRules() {
  return useQuery({ queryKey: ['business', 'rules'], queryFn: () => api<{ rules: BusinessRule[] }>('/api/business/rules') });
}

function Saving({ error, done }: { error: string | null; done: string | null }) {
  return <>{error && <Notice tone="error">{error}</Notice>}{done && <Shown className="small muted">{done}</Shown>}</>;
}

function TypeCard({ s, onSaved }: { s: BusinessSettingsOut; onSaved: (n: BusinessSettingsOut) => void }) {
  const [error, setError] = useState<string | null>(null);
  async function pick(type: BusinessSettingsOut['type']) {
    setError(null);
    try { onSaved(await saveBusinessSettings({ type })); } catch (e) { setError(failure(e)); }
  }
  const options: [BusinessSettingsOut['type'], string][] = [['none', C.TYPE_NONE], ['sole_prop', C.TYPE_SOLE], ['s_corp', C.TYPE_SCORP]];
  return (
    <Card className="stack">
      <h2>{C.TYPE_TITLE}</h2>
      <div role="radiogroup" aria-label={C.TYPE_TITLE} className="stack stack--tight">
        {options.map(([v, name]) => (
          <label key={v} className="line radio-row">
            <input type="radio" name="biz-type" checked={s.type === v} onChange={() => void pick(v)} />{name}
          </label>
        ))}
      </div>
      {s.type === 's_corp' && <p className="small muted">{C.TYPE_SCORP_HINT}</p>}
      <Saving error={error} done={null} />
    </Card>
  );
}

function OwnerCard({ s, onSaved }: { s: BusinessSettingsOut; onSaved: (n: BusinessSettingsOut) => void }) {
  const [error, setError] = useState<string | null>(null);
  async function pick(owner: BusinessSettingsOut['owner']) {
    setError(null);
    try { onSaved(await saveBusinessSettings({ owner })); } catch (e) { setError(failure(e)); }
  }
  return (
    <Card className="stack">
      <h2>{C.OWNER_TITLE}</h2>
      <div role="radiogroup" aria-label={C.OWNER_TITLE} className="stack stack--tight">
        {s.adults.map((a) => (
          <label key={a.key} className="line radio-row">
            <input type="radio" name="biz-owner" checked={s.owner === a.key} onChange={() => void pick(a.key)} />{a.name}
          </label>
        ))}
      </div>
      <p className="small muted">{C.OWNER_HINT}</p>
      <Saving error={error} done={null} />
    </Card>
  );
}

function HomeOfficeCard({ s, onSaved }: { s: BusinessSettingsOut; onSaved: (n: BusinessSettingsOut) => void }) {
  const [method, setMethod] = useState<HomeOfficeOut['method']>(s.home_office.method);
  const [office, setOffice] = useState(String(s.home_office.office_sqft || ''));
  const [home, setHome] = useState(String(s.home_office.home_sqft || ''));
  const [costs, setCosts] = useState(s.home_office.home_costs_cents ? fromHundredths(s.home_office.home_costs_cents) : '');
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function save(e: FormEvent) {
    e.preventDefault();
    const o = method === 'none' ? 0 : whole(office);
    const h = method === 'actual' ? whole(home) : 0;
    const c = method === 'actual' ? toHundredths(costs || '0') : { value: 0 };
    if (o === null || h === null) { setError(C.WHOLE_NUMBER); return; }
    if ('error' in c) { setError(c.error); return; }
    setBusy(true); setError(null); setDone(null);
    try {
      onSaved(await saveBusinessSettings({ year: s.year, home_office: { method, office_sqft: o, home_sqft: h, home_costs_cents: c.value } }));
      setDone(SAVED);
    } catch (err) { setError(failure(err)); } finally { setBusy(false); }
  }
  const options: [HomeOfficeOut['method'], string, string | null][] = [
    ['none', C.HO_NONE, null],
    ['simplified', C.HO_SIMPLE, C.HO_SIMPLE_HINT.replace('{rate}', money(s.rules.home_office_rate_cents).replace(/\.00$/, '')).replace('{max}', String(s.rules.home_office_max_sqft)).replace('{year}', String(s.rules.year))],
    ['actual', C.HO_ACTUAL, C.HO_ACTUAL_HINT],
  ];
  return (
    <Card>
      <form className="stack" onSubmit={save}>
        <h2>{C.HO_TITLE}</h2>
        <div role="radiogroup" aria-label={C.HO_TITLE} className="stack stack--tight">
          {options.map(([v, name, hint]) => (
            <div key={v}>
              <label className="line radio-row"><input type="radio" name="biz-ho" checked={method === v} onChange={() => setMethod(v)} />{name}</label>
              {hint && <p className="small muted biz-hint">{hint}</p>}
            </div>
          ))}
        </div>
        {method !== 'none' && <Field label={C.OFFICE_SQFT}><input inputMode="numeric" value={office} onChange={(e) => setOffice(e.target.value)} /></Field>}
        {method === 'actual' && (
          <div className="biz-pair">
            <Field label={C.HOME_SQFT}><input inputMode="numeric" value={home} onChange={(e) => setHome(e.target.value)} /></Field>
            <Field label={C.HOME_COSTS}><MoneyInput value={costs} onValueChange={setCosts} post="a year" /></Field>
          </div>
        )}
        <Saving error={error} done={done} />
        <Button type="submit" busy={busy}>{C.SAVE_HOME_OFFICE}</Button>
      </form>
    </Card>
  );
}

function CarCard({ s, onSaved }: { s: BusinessSettingsOut; onSaved: (n: BusinessSettingsOut) => void }) {
  const [method, setMethod] = useState(s.car_method);
  const [miles, setMiles] = useState(s.miles.map((m) => String(m.miles)));
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { setMiles(s.miles.map((m) => String(m.miles))); }, [s.miles]);
  async function save(e: FormEvent) {
    e.preventDefault();
    const counts = miles.map(whole);
    if (method === 'standard' && counts.some((n) => n === null)) { setError(C.WHOLE_NUMBER); return; }
    setBusy(true); setError(null); setDone(null);
    try {
      onSaved(await saveBusinessSettings(method === 'standard' ? { year: s.year, car_method: method, miles: counts } : { year: s.year, car_method: method }));
      setDone(SAVED);
    } catch (err) { setError(failure(err)); } finally { setBusy(false); }
  }
  return (
    <Card>
      <form className="stack" onSubmit={save}>
        <h2>{C.CAR_TITLE}</h2>
        <div role="radiogroup" aria-label={C.CAR_TITLE} className="stack stack--tight">
          <label className="line radio-row"><input type="radio" name="biz-car" checked={method === 'standard'} onChange={() => setMethod('standard')} />{C.CAR_STANDARD}</label>
          <label className="line radio-row"><input type="radio" name="biz-car" checked={method === 'actual'} onChange={() => setMethod('actual')} />{C.CAR_ACTUAL}</label>
        </div>
        {method === 'standard' ? s.miles.map((p, i) => (
          <Field key={p.from} label={C.MILES_FOR.replace('{from}', shortDate(p.from)).replace('{to}', shortDate(p.to)).replace('{rate}', rate(p.rate_tenths))}>
            <input inputMode="numeric" value={miles[i] ?? ''} onChange={(e) => setMiles((m) => m.map((x, j) => (j === i ? e.target.value : x)))} />
          </Field>
        )) : <p className="small muted">{C.CAR_ACTUAL_HINT}</p>}
        <Saving error={error} done={done} />
        <Button type="submit" busy={busy}>{C.SAVE_MILES}</Button>
      </form>
    </Card>
  );
}

function RulesCard() {
  const qc = useQueryClient();
  const canEdit = useCanEdit();
  const rules = useRules();
  const accounts = useAccounts();
  const [account, setAccount] = useState('');
  const [pct, setPct] = useState('100');
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [removing, setRemoving] = useState<BusinessRule | null>(null);
  const [removingBusy, setRemovingBusy] = useState(false);
  const removal = useRemoval();
  const refresh = () => {
    for (const key of ['business', 'transactions', 'spending', 'plan']) void qc.invalidateQueries({ queryKey: [key] });
  };
  async function add(e: FormEvent) {
    e.preventDefault();
    const n = whole(pct);
    if (n === null || n < 1 || n > 100) { setError(C.PCT_1_100); return; }
    setBusy(true); setError(null); setDone(null); removal.clear();
    try {
      const r = await api<RuleReply>('/api/business/rules', json({ kind: 'account', account_id: Number(account), pct: n, line: null }));
      setDone(r.also_tagged === 0 ? C.RULE_ADDED : r.also_tagged === 1 ? TAGGED_ONE_TOO : TAGGED_TOO.replace('{n}', String(r.also_tagged)));
      refresh();
    } catch (err) { setError(failure(err)); } finally { setBusy(false); }
  }
  async function remove(r: BusinessRule) {
    if (removingBusy) return;
    setRemovingBusy(true); setError(null); setDone(null);
    try {
      await api<void>(`/api/business/rules/${r.id}`, del());
      removal.removed(C.RULE_REMOVED.replace('{rule}', ruleSentence(r)));
      refresh();
    } catch (err) { setError(failure(err)); } finally { setRemovingBusy(false); }
    setRemoving(null);
  }
  const active = (accounts.data?.accounts ?? []).filter((a) => a.is_active);
  return (
    <Card className="stack">
      <h2 ref={removal.anchor} tabIndex={-1}>{C.RULES_TITLE}</h2>
      <RemovalLine removal={removal} />
      {rules.isError ? <Notice tone="error">{rules.error.message}</Notice> : !rules.data ? <Skeleton lines={2} /> : rules.data.rules.length === 0 ? <p className="muted">{C.NO_RULES}</p> : (
        <ul className="biz-rules">
          {rules.data.rules.map((r) => (
            <li key={r.id} className="biz-rule">
              <span>{ruleSentence(r)}</span>
              {canEdit && <Button variant="quiet" icon={Trash2} aria-label={C.REMOVE_RULE_LABEL.replace('{rule}', ruleSentence(r))} onClick={() => setRemoving(r)}>{C.REMOVE}</Button>}
            </li>
          ))}
        </ul>
      )}
      <ConfirmSheet open={removing !== null} onClose={() => setRemoving(null)} title={C.REMOVE_RULE_TITLE} body={C.REMOVE_RULE_BODY}
        action={C.REMOVE} icon={Trash2} busy={removingBusy} onConfirm={() => removing && void remove(removing)}
        returnTo={removal.returnTo}>
        {removing && <p className="small muted">{ruleSentence(removing)}</p>}
      </ConfirmSheet>
      <p className="small muted">{C.RULES_HINT}</p>
      {canEdit && (
        <form className="stack" onSubmit={add}>
          <div className="biz-pair">
            <div className="select">
              <Field label={ACCOUNT}>
                <select value={account} onChange={(e) => setAccount(e.target.value)}>
                  <option value="" />
                  {active.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                </select>
              </Field>
              <ChevronDown size={20} aria-hidden="true" />
            </div>
            <Field label={C.ACCOUNT_SHARE}><input inputMode="numeric" value={pct} onChange={(e) => setPct(e.target.value)} maxLength={3} /></Field>
          </div>
          <Saving error={error} done={done} />
          <Button type="submit" busy={busy} disabled={!account}>{C.ADD_RULE}</Button>
        </form>
      )}
    </Card>
  );
}

/** The home office and car are per tax year, so last year can be finished (final review ruling I2). */
function YearSwitch({ s, onPick }: { s: BusinessSettingsOut; onPick: (y: number) => void }) {
  return (
    <div className="line biz-year-switch">
      <span id="biz-settings-year">{C.SETTINGS_YEAR}</span>
      <div className="seg biz-years" role="radiogroup" aria-labelledby="biz-settings-year" onKeyDown={radioKeys}>
        {[s.this_year, s.this_year - 1].map((y) => (
          <button key={y} type="button" role="radio" aria-checked={s.year === y} tabIndex={radioTab(s.year === y)} className={s.year === y ? 'is-on' : ''} onClick={() => onPick(y)}>{y}</button>
        ))}
      </div>
    </div>
  );
}

function SCorpSettingsCard({ s, onSaved }: { s: BusinessSettingsOut; onSaved: (n: BusinessSettingsOut) => void }) {
  const [pct, setPct] = useState(String(s.s_corp.salary_pct));
  const [cost, setCost] = useState(fromHundredths(s.s_corp.cost_cents));
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function save(e: FormEvent) {
    e.preventDefault();
    const p = whole(pct);
    const c = toHundredths(cost || '0');
    if (p === null || p < 1 || p > 100) { setError(C.PCT_1_100); return; }
    if ('error' in c) { setError(c.error); return; }
    setBusy(true); setError(null); setDone(null);
    try { onSaved(await saveBusinessSettings({ s_corp: { salary_pct: p, cost_cents: c.value } })); setDone(SAVED); }
    catch (err) { setError(failure(err)); } finally { setBusy(false); }
  }
  return (
    <Card>
      <form className="stack" onSubmit={save}>
        <h2>{C.SCORP_SETTINGS}</h2>
        <div className="biz-pair">
          <Field label={C.SALARY_PCT}><input inputMode="numeric" value={pct} onChange={(e) => setPct(e.target.value)} maxLength={3} /></Field>
          <Field label={C.PAYROLL_COST}><MoneyInput value={cost} onValueChange={setCost} post="a year" /></Field>
        </div>
        <Saving error={error} done={done} />
        <Button type="submit" busy={busy}>{C.SAVE_SCORP}</Button>
      </form>
    </Card>
  );
}

/** Settings, Business (business plan Task 13). Managers change it; viewers read it. */
export default function BusinessSettings() {
  const [year, setYear] = useState<number | null>(null);      // null: the household's this year, from the server
  const q = useBusinessSettings(year);
  const qc = useQueryClient();
  const canEdit = useCanEdit();
  if (q.isError) return <Notice tone="error">{q.error.message}</Notice>;
  if (!q.data) return <Card><Skeleton lines={6} /></Card>;
  const onSaved = (n: BusinessSettingsOut) => {
    qc.setQueryData(businessSettingsKey(year), n);
    void qc.invalidateQueries({ queryKey: ['business'] });
    void qc.invalidateQueries({ queryKey: ['plan'] });
    void qc.invalidateQueries({ queryKey: ['next-dollar'] });
  };
  const s = q.data;
  return (
    <div className="business-settings stack">
      {!canEdit && <Notice>{VIEWER_CANT_CHANGE}</Notice>}
      {s.type !== 'none' && <YearSwitch s={s} onPick={setYear} />}
      <fieldset className="readonly stack" disabled={!canEdit}>
        <TypeCard s={s} onSaved={onSaved} />
        {s.type !== 'none' && s.adults.length > 1 && <OwnerCard s={s} onSaved={onSaved} />}
        {s.type !== 'none' && <HomeOfficeCard key={`ho-${s.year}`} s={s} onSaved={onSaved} />}
        {s.type !== 'none' && <CarCard key={`car-${s.year}`} s={s} onSaved={onSaved} />}
        {s.type !== 'none' && <SCorpSettingsCard s={s} onSaved={onSaved} />}
      </fieldset>
      {q.data.type !== 'none' && <RulesCard />}
    </div>
  );
}
