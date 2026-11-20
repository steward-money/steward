import { HeartPulse, ShieldCheck, Upload } from 'lucide-react';
import { useRef, useState, type ChangeEvent } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { ApiError, api } from '../../../api/client';
import type { Person } from '../../../api/household';
import type { ProfileState } from '../../../api/profile';
import { dollars } from '../../../lib/format';
import { Button } from '../../../ui/Button';
import { Notice } from '../../../ui/Notice';
import { Skeleton } from '../../../ui/Skeleton';
import { dollarsText, toHundredths } from '../../settings/money';
import { type BenefitPerson, type Benefits, saveFact, uploadEarnings, useProfileBenefits } from '../api';
import {
  A_YEAR, ABOUT_MONTHLY, ADD_RECORD, CARD_ESTIMATE, CARD_HEALTH, CARD_RECORD, CHOOSE_FILE_FOR, ESTIMATE_KEPT, ESTIMATE_LINE, ESTIMATE_SOURCE, ESTIMATE_USED, HEALTH_PREMIUM, HEALTH_WHY, NOTHING_WAS_SAVED, PREMIUM_NEEDED, PRIVACY, RECORD_ADDED, RECORD_LINE, SPOUSAL_LINE, SS_WHY, SSA_STEPS, STEP_HEALTH, STEP_SS, USE_ESTIMATE,
} from '../copy';
import { FormError } from '../../../ui/Field';
import { Shown, useArriving } from '../../../ui/Live';
import { ProfileField, invalidateProfile, useFieldMode } from '../field';
import { useOffer } from '../home/offer';
import type { BodyProps, SectionDef } from '../sections';

const SUGGESTED_NOTE = (name: string) => `Confirmed by ${name}`;   // today's confirmedNote: the estimate kept as it is

function line(b: BenefitPerson, person: Person, names: ProfileState['names']): string | null {
  if (b.at_67_cents === null) return null;
  const amount = dollars(b.at_67_cents);
  if (b.source === 'record') return RECORD_LINE.replace('{n}', String(b.years)).replace('{first}', String(b.first)).replace('{last}', String(b.last)).replace('{amount}', amount);
  if (person === 'jr') return ESTIMATE_LINE.replace('{jr}', names.jr).replace('{amount}', amount);
  return SPOUSAL_LINE.replace('{partner}', names.spouse).replace('{amount}', amount);
}

function PersonBlock({ person, b, names }: { person: Person; b: BenefitPerson; names: ProfileState['names'] }) {
  const qc = useQueryClient();
  const file = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const name = names[person];
  const { canEdit } = useFieldMode();
  const { offer, view } = useOffer();
  // Review of #51: the line shows the saved record or estimate, so it is quiet when the step opens; a line that changes
  // or appears after that (a record just added) is said.
  const arriving = useArriving();
  async function pick(e: ChangeEvent<HTMLInputElement>) {
    const chosen = e.target.files?.[0];
    e.target.value = '';
    if (!chosen) return;
    setBusy(true);
    setError(null);
    try {
      const r = await uploadEarnings(person, chosen);
      await invalidateProfile(qc);
      offer(RECORD_ADDED.replace('{name}', name), r.undo ?? null);
    } catch (err) {
      const m = (err as Error).message;
      setError(err instanceof ApiError && err.status === 422 ? `${m} ${NOTHING_WAS_SAVED}` : m);
    } finally {
      setBusy(false);
    }
  }
  async function estimate() {
    setBusy(true);
    setError(null);
    try {
      const token = await saveFact(`${person}.ss_estimate`, 'yes');
      await invalidateProfile(qc);
      offer(ESTIMATE_USED.replace('{name}', name), token);
    } catch (err) { setError((err as Error).message); } finally { setBusy(false); }
  }
  const said = line(b, person, names);
  return (
    <section className="card prof-card__step" aria-labelledby={`ss-${person}-h`}>
      <h3 id={`ss-${person}-h`}>{name}</h3>
      {said && <Shown className="kid-line" quietOnMount={arriving}>{said}</Shown>}
      {canEdit && (
        <>
          <div className="ob-form-actions">
            {b.source !== 'record' && b.source !== 'estimate' && <Button variant="quiet" busy={busy} onClick={() => void estimate()}>{USE_ESTIMATE}</Button>}
            <Button variant="quiet" icon={Upload} busy={busy} onClick={() => file.current?.click()}>{ADD_RECORD}</Button>
          </div>
          <input ref={file} type="file" hidden tabIndex={-1} accept=".xml,application/xml,text/xml" aria-label={CHOOSE_FILE_FOR.replace('{who}', name)}
            data-testid={`earnings-file-${person}`} onChange={pick} />
        </>
      )}
      {error && <FormError>{error}</FormError>}
      {view}
    </section>
  );
}

function SocialSecurity({ profile }: BodyProps) {
  const q = useProfileBenefits();
  if (q.error) return <Notice tone="error">{q.error.message}</Notice>;
  if (!q.data) return <Skeleton lines={4} />;
  return (
    <div className="stack">
      <p className="prof-why">{SS_WHY}</p>
      <ol className="steps-list">{SSA_STEPS.map((s) => <li key={s}>{s}</li>)}</ol>
      <PersonBlock person="jr" b={q.data.jr} names={profile.names} />
      <PersonBlock person="spouse" b={q.data.spouse} names={profile.names} />
      <p className="privacy"><ShieldCheck className="i" size={20} aria-hidden="true" /><span>{PRIVACY}</span></p>
    </div>
  );
}

function Health(_: BodyProps) {
  const q = useProfileBenefits();
  const [text, setText] = useState<string | null>(null);
  if (q.error) return <Notice tone="error">{q.error.message}</Notice>;
  if (!q.data) return <Skeleton lines={3} />;
  const h = q.data.health;
  const current = text ?? dollarsText(h.premium_cents ?? h.suggested_cents);
  const typed = toHundredths(current);
  const monthly = !('error' in typed) && typed.value > 0 ? ABOUT_MONTHLY.replace('{amount}', dollars(Math.round(typed.value / 12))) : undefined;
  return (
    <div className="stack">
      <p className="prof-why">{HEALTH_WHY.replace('{part_b}', dollars(h.part_b_monthly_cents)).replace('{year}', String(h.rules_year))}</p>
      {h.premium_cents === null && <p className="small muted">{ESTIMATE_SOURCE}</p>}
      <ProfileField label={HEALTH_PREMIUM} input="money" post={A_YEAR} hint={monthly} onText={setText}
        text={dollarsText(h.premium_cents ?? h.suggested_cents)} shown={h.premium_cents === null ? '' : `${dollars(h.premium_cents)} ${A_YEAR}`}
        check={(t) => { const p = toHundredths(t); return 'error' in p || p.value < 0 ? PREMIUM_NEEDED : null; }}
        save={(t) => saveFact('health.premium_cents', String((toHundredths(t) as { value: number }).value))} />
    </div>
  );
}

/** Keeping the estimate and pressing Done confirms it, as today (spec, Step 2). */
async function confirmEstimate(profile: ProfileState): Promise<{ text: string; token: string } | void> {
  const b = await fetchBenefits();
  if (b.health.premium_cents !== null) return;
  const token = await saveFact('health.premium_cents', String(b.health.suggested_cents), SUGGESTED_NOTE(profile.names[profile.me.role ?? 'jr']));
  return token ? { text: ESTIMATE_KEPT.replace('{amount}', dollars(b.health.suggested_cents)), token } : undefined;
}
const fetchBenefits = () => api<Benefits>('/api/profile/benefits');

function useBenefitsSummary(profile: ProfileState): string[] | null {
  const b = useProfileBenefits().data;
  if (!b) return null;
  const who = (person: Person, x: BenefitPerson) => {
    if (x.at_67_cents === null) return null;
    const base = x.source === 'record' ? CARD_RECORD.replace('{first}', String(x.first)).replace('{last}', String(x.last)) : CARD_ESTIMATE;
    return base.replace('{name}', profile.names[person]).replace('{amount}', dollars(x.at_67_cents));
  };
  return [who('jr', b.jr), who('spouse', b.spouse),
    b.health.premium_cents === null ? null : CARD_HEALTH.replace('{premium}', dollars(b.health.premium_cents))].filter((l): l is string => l !== null);
}

export const BENEFITS_SECTION: SectionDef = {
  icon: HeartPulse,
  steps: [
    { key: 'ss', title: () => STEP_SS, Body: SocialSecurity },
    { key: 'health', title: () => STEP_HEALTH, Body: Health, beforeDone: confirmEstimate },
  ],
  useSummary: useBenefitsSummary,
};
