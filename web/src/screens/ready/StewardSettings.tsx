import { useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { SAVE, SAVED } from '../../lib/copy';
import { Button } from '../../ui/Button';
import { Card } from '../../ui/Card';
import { Field, FormError } from '../../ui/Field';
import { Shown } from '../../ui/Live';
import { saveGeneral, type InstallSettings } from './api';
import { GATEWAY_NAME, SET_IN_FILE, STEWARD_SETTINGS, TIME_ZONE } from './copy';

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** Every zone the browser knows, plus the one Steward runs in when that one isn't on the list ("UTC" often isn't). */
function zones(running: string): string[] {
  const all: string[] = (Intl as { supportedValuesOf?: (k: string) => string[] }).supportedValuesOf?.('timeZone') ?? [];
  return all.includes(running) ? all : [running, ...all];
}

/** The foot card of Get Steward ready: the time zone and the gateway's name. A setting the settings file owns shows read-only. */
export function StewardSettings({ s }: { s: InstallSettings['general'] }) {
  const qc = useQueryClient();
  const zoneEnv = s.timezone.source === 'env';
  const nameEnv = s.gateway_name.source === 'env';
  const [zone, setZone] = useState(s.timezone.running);
  const [name, setName] = useState(s.gateway_name.value);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const change: { timezone?: string; gateway_name?: string } = {};
  if (!zoneEnv && zone !== s.timezone.running) change.timezone = zone;
  if (!nameEnv && name !== s.gateway_name.value) change.gateway_name = name;
  const changed = Object.keys(change).length > 0;

  async function save() {
    setBusy(true); setError(null); setSaved(false);
    try {
      await saveGeneral(change);
      setSaved(true);
      void qc.invalidateQueries({ queryKey: ['me'] });
      void qc.invalidateQueries({ queryKey: ['ready'] });
      void qc.invalidateQueries({ queryKey: ['install', 'settings'] });
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="stack">
      <h3>{STEWARD_SETTINGS}</h3>
      <form className="ready__form" onSubmit={(e) => { e.preventDefault(); if (changed) void save(); }}>
        <Field label={TIME_ZONE}>
          <select value={zone} disabled={zoneEnv} onChange={(e) => setZone(e.target.value)}>
            {zones(s.timezone.running).map((z) => <option key={z} value={z}>{z}</option>)}
          </select>
        </Field>
        {zoneEnv && <p className="small muted">{SET_IN_FILE.replace('{env}', s.timezone.env)}</p>}
        <Field label={GATEWAY_NAME}>
          <input value={name} maxLength={40} disabled={nameEnv} onChange={(e) => setName(e.target.value)} autoComplete="off" />
        </Field>
        {nameEnv && <p className="small muted">{SET_IN_FILE.replace('{env}', s.gateway_name.env)}</p>}
        {error && <FormError>{error}</FormError>}
        <div className="ready__check">
          {changed && <Button type="submit" busy={busy}>{SAVE}</Button>}
          {saved && <Shown as="span">{SAVED}</Shown>}
        </div>
      </form>
    </Card>
  );
}
