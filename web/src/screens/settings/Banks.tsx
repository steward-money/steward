import { useQueryClient } from '@tanstack/react-query';
import { Link as RouterLink } from '../../book/links';
import { RefreshCw, Unlink } from 'lucide-react';
import { Suspense, useEffect, useState, type FormEvent } from 'react';
import { api, del, patch } from '../../api/client';
import { useAccounts, useInstitutions, useSimplefin } from '../../api/hooks';
import { useBook, useIsHousehold } from '../../book/BookContext';
import type { Account, AccountType, LinkResult, Owner, SyncResult } from '../../api/types';
import { localYmd, relativeDays } from '../../lib/format';
import { useRemoval } from '../../lib/useRemoval';
import {
  IMPORT_A_FILE, LAST_UPDATED, NEW_ACCOUNTS, NEW_ACCOUNTS_HINT, OWNER, S_BANKS_AND_ACCOUNTS, SAVE, SIGN_IN_NEEDED, SIGN_IN_STEP,
  SIGN_IN_STEP_UNLINKED, SIGN_IN_TAG, SIMPLEFIN_90_DAYS, TYPE, UNLINK, UNLINK_BODY, UNLINK_TITLE, UNLINKED, UPDATE_NOW, UPDATING,
} from '../../lib/copy';
import { Button } from '../../ui/Button';
import { Card } from '../../ui/Card';
import { ConfirmSheet } from '../../ui/ConfirmSheet';
import { Field } from '../../ui/Field';
import { Notice } from '../../ui/Notice';
import { RemovalLine } from '../../ui/RemovalLine';
import { LazyOopsAcorn } from '../../ui/LazyOopsAcorn';
import { Skeleton } from '../../ui/Skeleton';
import { ManualAccountsList } from '../accounts/ManualAccounts';
import { lazyScreen } from '../../lib/lazyScreen';
import { Shown } from '../../ui/Live';
import { BANK_TYPE_OPTIONS, failure, ownerOptions } from './shared';

// The setup-token form is a lazy chunk shared with the first run (guided setup spec 11): only an unlinked card shows it.
const LinkForm = lazyScreen(() => import('./LinkForm').then((m) => ({ default: m.LinkForm })));

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;
const syncLine = (r: SyncResult) => `Updated ${plural(r.institutions_ok, 'bank', 'banks')}, ${r.transactions_new} new transactions`;

function useRefreshAll() {
  const qc = useQueryClient();
  return () => Promise.all(['simplefin', 'institutions', 'accounts', 'summary', 'history', 'events', 'review', 'transactions', 'spending'].map((k) => qc.invalidateQueries({ queryKey: [k] })));
}

export function SimplefinCard() {
  const status = useSimplefin();
  const refresh = useRefreshAll();
  const [busy, setBusy] = useState<'sync' | 'unlink' | null>(null);
  const [error, setError] = useState('');
  const [result, setResult] = useState('');
  const [confirming, setConfirming] = useState(false);
  // Unlink takes its own button away; the line says so and focus goes to the card's heading (THO-1).
  const removal = useRemoval();
  const today = new Date();
  useEffect(() => { void LinkForm.preload(); }, []);   // alongside the status fetch, so an unlinked card opens whole

  function linked(r: LinkResult) {
    setError('');
    removal.clear();
    setResult(r.sync ? syncLine(r.sync) : '');
    void refresh();
  }

  async function sync() {
    setBusy('sync');
    setError('');
    setResult('');
    try {
      const r = await api<SyncResult>('/api/sync', { method: 'POST' });
      setResult(syncLine(r));
      await refresh();
    } catch (err) {
      setError(failure(err));
    }
    setBusy(null);
  }

  async function unlink() {
    setBusy('unlink');
    setError('');
    try {
      await api('/api/simplefin', del());
      setResult('');
      removal.removed(UNLINKED);
      setConfirming(false);
      await refresh();
    } catch (err) {
      setError(failure(err));
      setConfirming(false);
    }
    setBusy(null);
  }

  if (status.isPending) return <Card><Skeleton lines={3} /></Card>;
  if (status.isError) return <Card><Notice tone="error">{status.error.message}</Notice></Card>;
  const s = status.data;
  return (
    <Card className="status">
      <h3 className="subhead" ref={removal.anchor} tabIndex={-1}>SimpleFIN</h3>
      <RemovalLine removal={removal} />
      {(s.sign_in ?? []).length > 0 && (
        <Notice tone="warn">
          <div data-testid="sign-in-needed">
            {s.sign_in!.map((bank) => <p key={bank}>{SIGN_IN_NEEDED.replace('{bank}', bank)}</p>)}
            <p>{s.linked ? SIGN_IN_STEP : SIGN_IN_STEP_UNLINKED}</p>
          </div>
        </Notice>
      )}
      {s.linked ? (
        <>
          <p className="status__line"><span className="dot" aria-hidden="true" />
            {s.last_sync_at ? `Linked · ${LAST_UPDATED.replace('{when}', relativeDays(localYmd(s.last_sync_at), today))}` : 'Linked · Not updated yet'}
          </p>
          {s.last_error && <p className="small warn"><LazyOopsAcorn size={18} /> {s.last_error}</p>}
          <p className="small muted">{SIMPLEFIN_90_DAYS} <RouterLink to="/settings/import">{IMPORT_A_FILE}</RouterLink></p>
          <div className="row-actions">
            <Button variant="quiet" icon={RefreshCw} busy={busy === 'sync'} onClick={sync}>{busy === 'sync' ? UPDATING : UPDATE_NOW}</Button>
            <Button variant="quiet" icon={Unlink} onClick={() => setConfirming(true)}>{UNLINK}</Button>
          </div>
        </>
      ) : (
        <>
          <p className="status__line muted"><span className="dot dot--off" aria-hidden="true" />Not linked</p>
          <Suspense fallback={<Skeleton lines={2} />}><LinkForm onLinked={linked} /></Suspense>
        </>
      )}
      {result && <Shown>{result}</Shown>}
      {error && <Notice tone="error">{error}</Notice>}
      <ConfirmSheet open={confirming} onClose={() => setConfirming(false)} title={UNLINK_TITLE} body={UNLINK_BODY}
        action={UNLINK} icon={Unlink} busy={busy === 'unlink'} onConfirm={() => void unlink()} returnTo={removal.returnTo} />
    </Card>
  );
}

export function NewAccount({ account }: { account: Account }) {
  const refresh = useRefreshAll();
  const book = useBook();
  const [type, setType] = useState<AccountType | ''>('');
  const [owner, setOwner] = useState<Owner>(account.owner);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function save(e: FormEvent) {
    e.preventDefault();
    if (!type) { setError('Choose what this account is.'); return; }
    setBusy(true);
    setError('');
    try {
      await api(`/api/accounts/${account.id}`, patch({ type, owner }));
      await refresh();
    } catch (err) {
      setError(failure(err));
    }
    setBusy(false);
  }

  return (
    <li className="line line--form">
      <form className="new-account" onSubmit={save} aria-label={account.name}>
        <p className="new-account__name">{account.name}<span className="small muted"> · {account.institution}</span></p>
        <div className="grid-2">
          <Field label={TYPE} error={error}>
            <select value={type} onChange={(e) => setType(e.target.value as AccountType)}>
              <option value="">Choose…</option>
              {BANK_TYPE_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </Field>
          <Field label={OWNER}>
            <select value={owner} onChange={(e) => setOwner(e.target.value as Owner)}>
              {ownerOptions(book?.owners, owner).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </Field>
        </div>
        <div className="row-actions"><Button type="submit" busy={busy}>{SAVE}</Button></div>
      </form>
    </li>
  );
}

export default function Banks() {
  const household = useIsHousehold();   // the server keeps manual accounts to household books
  const institutions = useInstitutions();
  const accounts = useAccounts();
  const signIn = new Set(useSimplefin().data?.sign_in ?? []);   // the query SimplefinCard already holds
  const today = new Date();
  const unknown = (accounts.data?.accounts ?? []).filter((a) => a.type === 'unknown');
  return (
    <section className="spage" aria-labelledby="banks-h">
      <h2 id="banks-h">{S_BANKS_AND_ACCOUNTS}</h2>
      <SimplefinCard />
      {institutions.isPending && <Card><Skeleton lines={3} /></Card>}
      {institutions.isError && <Notice tone="error">{institutions.error.message}</Notice>}
      {institutions.isSuccess && (
        <Card className="lines">
          <ul className="plain">
            {institutions.data.institutions.map((i) => (
              <li className="line" key={i.id}>
                <div className="line__main">
                  <span>{i.name}</span>
                  {/* A bank the SimpleFIN notice names gets a short tag: the notice already says the sentence. */}
                  {signIn.has(i.name)
                    ? <span className="small warn" data-testid="bank-needs-sign-in"><LazyOopsAcorn size={18} /> {SIGN_IN_TAG}</span>
                    : i.last_error
                    ? <span className="small warn"><LazyOopsAcorn size={18} /> {i.last_error}</span>
                    : i.last_sync_at && <span className="small muted">{LAST_UPDATED.replace('{when}', relativeDays(localYmd(i.last_sync_at), today))}</span>}
                </div>
                <span className="small muted">{plural(i.account_count, 'account', 'accounts')}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}
      {household && <ManualAccountsList />}
      {unknown.length > 0 && (
        <>
          <h3 className="subhead">{NEW_ACCOUNTS}</h3>
          <p className="muted">{NEW_ACCOUNTS_HINT}</p>
          <Card className="lines"><ul className="plain">{unknown.map((a) => <NewAccount key={a.id} account={a} />)}</ul></Card>
        </>
      )}
    </section>
  );
}
