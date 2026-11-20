import { useQueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { SAVE, SAVED } from '../../lib/copy';
import { Button } from '../../ui/Button';
import { Field, FormError } from '../../ui/Field';
import { Shown } from '../../ui/Live';
import { useSheetSaved } from '../../ui/Sheet';
import { saveBackups, type InstallSettings, type ReadyRow } from './api';
import { COPIES_GO, OFF, PING_URL, REMOVE, REMOVE_PING, SET_IN_FILE, TIME } from './copy';

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** Backups: the time of the nightly copy (or Off), and the URL Steward pings after a good one. The ping URL is never shown, only its masked tail. */
export function BackupsPanel({ s }: { s: InstallSettings['backups']; row: ReadyRow }) {
  const qc = useQueryClient();
  const atEnv = s.at.source === 'env';
  const pingEnv = s.ping_url.source === 'env';
  const wasOff = s.at.value === '';
  const [off, setOff] = useState(wasOff);
  const [time, setTime] = useState(wasOff ? '03:00' : s.at.value);
  const [ping, setPing] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const formRef = useRef<HTMLFormElement>(null);
  const markSaved = useSheetSaved(formRef);   // on a phone this panel is in a sheet, and it stays open after Save

  const change: { at?: string | null; ping_url?: string | null } = {};
  if (!atEnv && off !== wasOff) change.at = off ? null : time;
  else if (!atEnv && !off && time !== s.at.value) change.at = time;
  if (!pingEnv && ping.trim()) change.ping_url = ping.trim();

  async function send(body: typeof change) {
    setBusy(true); setError(null); setSaved(false);
    try {
      await saveBackups(body);
      setSaved(true);
      setPing('');
      markSaved();
      void qc.invalidateQueries({ queryKey: ['ready'] });
      void qc.invalidateQueries({ queryKey: ['install', 'settings'] });
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form ref={formRef} className="ready__form" onSubmit={(e) => { e.preventDefault(); if (Object.keys(change).length) void send(change); }}>
      <Field label={TIME}>
        <input type="time" value={time} disabled={atEnv || off} onChange={(e) => setTime(e.target.value)} />
      </Field>
      <div className="ready__check">
        <button type="button" role="switch" aria-checked={off} aria-label={OFF} className={`switch${off ? ' is-on' : ''}`}
          disabled={atEnv} onClick={() => setOff(!off)} />
        <span>{OFF}</span>
      </div>
      {atEnv && <p className="small muted">{SET_IN_FILE.replace('{env}', s.at.env)}</p>}
      <Field label={PING_URL}>
        <input type="url" value={ping} disabled={pingEnv} placeholder={s.ping_url.shown ?? undefined} autoComplete="off" onChange={(e) => setPing(e.target.value)} />
      </Field>
      {pingEnv && <p className="small muted">{SET_IN_FILE.replace('{env}', s.ping_url.env)}</p>}
      {!pingEnv && s.ping_url.set && <div><Button variant="quiet" busy={busy} aria-label={REMOVE_PING} onClick={() => void send({ ping_url: null })}>{REMOVE}</Button></div>}
      {error && <FormError>{error}</FormError>}
      <div className="ready__check">
        {Object.keys(change).length > 0 && <Button type="submit" busy={busy}>{SAVE}</Button>}
        {saved && <Shown as="span">{SAVED}</Shown>}
      </div>
      <p className="small muted">{COPIES_GO.replace('{dir}', s.dir)}</p>
    </form>
  );
}
