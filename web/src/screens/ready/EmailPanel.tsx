import { useQueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { SAVE, SAVED } from '../../lib/copy';
import { useRemoval } from '../../lib/useRemoval';
import { Button } from '../../ui/Button';
import { ConfirmSheet } from '../../ui/ConfirmSheet';
import { Field, FormError } from '../../ui/Field';
import { Shown } from '../../ui/Live';
import { RemovalLine } from '../../ui/RemovalLine';
import { useSheetSaved } from '../../ui/Sheet';
import { removeEmail, saveEmail, sendTest, type InstallSettings } from './api';
import { EMAIL_REMOVED, FROM, HOST, PASSWORD, PASSWORD_AGAIN, PORT, REMOVE, REMOVE_EMAIL, REMOVE_EMAIL_BODY, REMOVE_EMAIL_LABEL, SEND_TEST, SET, SET_IN_FILE, USER } from './copy';

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** Email: the five lines Steward sends invites and resets with. A settings file that owns them shows read-only. */
export function EmailPanel({ s }: { s: InstallSettings['email'] }) {
  const qc = useQueryClient();
  const env = s.source === 'env';
  const [host, setHost] = useState(s.host);
  const [port, setPort] = useState(String(s.port));
  const [user, setUser] = useState(s.user);
  const [from, setFrom] = useState(s.from);
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState<'save' | 'test' | 'remove' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const formRef = useRef<HTMLFormElement>(null);
  const markSaved = useSheetSaved(formRef);   // on a phone this panel is in a sheet, and it stays open after Save
  const [tested, setTested] = useState<{ text: string; ok: boolean } | null>(null);
  const [removing, setRemoving] = useState(false);
  // Remove takes its own button away, so the line holds focus (THO-1).
  const removal = useRemoval();
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ['ready'] });
    void qc.invalidateQueries({ queryKey: ['install', 'settings'] });
  };

  async function save() {
    setBusy('save'); setError(null); setSaved(false); removal.clear();
    try {
      await saveEmail({ host, port: Number(port), user, from, ...(password ? { password } : {}) });
      setSaved(true);
      setPassword('');
      markSaved();
      refresh();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(null);
    }
  }
  async function test() {
    setBusy('test'); setTested(null);
    try {
      setTested({ text: (await sendTest()).line, ok: true });
    } catch (e) {
      setTested({ text: message(e), ok: false });
    } finally {
      setBusy(null);
      void qc.invalidateQueries({ queryKey: ['ready'] }); // every test, good or bad, is what the Email row reads
    }
  }
  async function remove() {
    setBusy('remove'); setError(null);
    try {
      const r = await removeEmail();
      // Remove is the one change that resets the form (no remount, so Saved. after a save stays put).
      const e = r.email;
      setHost(e?.host ?? ''); setPort(String(e?.port ?? 465)); setUser(e?.user ?? ''); setFrom(e?.from ?? '');
      setPassword(''); setSaved(false);
      removal.removed(EMAIL_REMOVED);
      setRemoving(false);
      refresh();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <form ref={formRef} className="ready__form" onSubmit={(e) => { e.preventDefault(); if (!env) void save(); }}>
      <Field label={HOST}><input value={host} disabled={env} onChange={(e) => setHost(e.target.value)} autoComplete="off" /></Field>
      <Field label={PORT}><input type="number" value={port} disabled={env} onChange={(e) => setPort(e.target.value)} /></Field>
      <Field label={USER}><input value={user} disabled={env} onChange={(e) => setUser(e.target.value)} autoComplete="off" /></Field>
      <Field label={PASSWORD} error={s.password_unreadable ? PASSWORD_AGAIN : undefined}>
        <input type="password" autoComplete="new-password" value={password} disabled={env} placeholder={s.password_set ? SET : undefined}
          onChange={(e) => setPassword(e.target.value)} />
      </Field>
      <Field label={FROM}><input value={from} disabled={env} onChange={(e) => setFrom(e.target.value)} autoComplete="off" /></Field>
      {env && s.password_set && <p className="small muted">{SET}</p>}
      {env && <p className="small muted">{SET_IN_FILE.replace('{env}', s.env)}</p>}
      {error && <FormError>{error}</FormError>}
      <div className="ready__check">
        {!env && <Button type="submit" busy={busy === 'save'}>{SAVE}</Button>}
        <Button variant="quiet" busy={busy === 'test'} onClick={() => void test()}>{SEND_TEST}</Button>
        {s.source === 'app' && <Button variant="quiet" aria-label={REMOVE_EMAIL_LABEL} onClick={() => setRemoving(true)}>{REMOVE}</Button>}
        {saved && <Shown as="span">{SAVED}</Shown>}
      </div>
      <RemovalLine removal={removal} holdsFocus />
      {tested && <Shown className={tested.ok ? undefined : 'field__error'}>{tested.text}</Shown>}
      <ConfirmSheet open={removing} title={REMOVE_EMAIL} body={REMOVE_EMAIL_BODY} action={REMOVE} variant="danger" busy={busy === 'remove'}
        onConfirm={() => void remove()} onClose={() => setRemoving(false)} returnTo={removal.returnTo} />
    </form>
  );
}
