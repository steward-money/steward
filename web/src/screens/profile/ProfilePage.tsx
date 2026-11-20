import { Check, GraduationCap, HeartPulse, House, Landmark, ShieldCheck, Users, X, type LucideIcon } from 'lucide-react';
import { Suspense, useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useLocation } from 'react-router-dom';
import { markSeen, refreshProfile, type ProfileState, type SectionState } from '../../api/profile';
import { useCanEdit } from '../../book/BookContext';
import { Link, useNavigate } from '../../book/links';
import { withCelebrate } from '../../lib/celebrations';
import { nextLine } from '../../lib/lines';
import { momentAllowed } from '../../lib/momentAllowed';
import { SECTION_NAME, SECTION_STEPS, isFinished, sectionPath, type SectionKey } from '../../lib/profileSections';
import { Button } from '../../ui/Button';
import { Shown, useArriving } from '../../ui/Live';
import { Notice } from '../../ui/Notice';
import { failure } from '../settings/shared';
import {
  CHEER_ALL, CHEER_HOME, CHEER_KIDS, CONTINUE, DISMISS, FIRST_VISIT, GO_HOME, METER, MINUTES, OPENED_FROM_HOME, SKIPPED, START, STILL_NEEDED,
  TITLE, WHY,
} from './copy';
import { ProfileGrowth } from '../home/lazyMoments';
import { SECTIONS } from './registry';
import type { Finished } from './Flow';
import type { SectionDef } from './sections';

export const ICON: Record<SectionKey, LucideIcon> = { you: Users, kids: GraduationCap, home: House, benefits: HeartPulse, banks: Landmark };

function Summary({ def, profile }: { def: SectionDef; profile: ProfileState }) {
  const lines = def.useSummary(profile);
  return <span className="prof-row__sum">{lines?.[0] ?? ''}</span>;
}

function Row({ s, profile, canEdit }: { s: SectionState; profile: ProfileState; canEdit: boolean }) {
  const def = SECTIONS[s.key];
  const done = isFinished(s.status);
  const Icon = ICON[s.key];
  const body = (
    <>
      <Icon className="i" size={24} aria-hidden="true" />
      <span className="prof-row__text">
        <span className="prof-row__name">{SECTION_NAME[s.key]}</span>
        <span className="prof-row__why">{WHY[s.key]}</span>
        {s.status === 'skipped' && s.missing && <span className="prof-row__missing">{STILL_NEEDED.replace('{what}', s.missing)}</span>}
      </span>
      <span className="prof-row__state">
        {done ? <Summary def={def} profile={profile} /> : (
          <>
            {s.status === 'skipped' && <span className="tag">{SKIPPED}</span>}
            <span className="muted">{MINUTES.replace('{n}', String(s.minutes))}</span>
            {canEdit && <span className="btn btn--quiet prof-row__go">{s.resume_step ? CONTINUE : START}</span>}
          </>
        )}
      </span>
    </>
  );
  const to = done || !canEdit ? sectionPath(s.key) : sectionPath(s.key, s.resume_step ?? SECTION_STEPS[s.key][0]);
  return <li><Link className="prof-row" to={to} data-testid={`row-${s.key}`}>{body}</Link></li>;
}

/** /profile: the meter and the five rows (spec, The page), and the first-visit band (P3). */
export function ProfilePage({ profile }: { profile: ProfileState }) {
  const canEdit = useCanEdit();
  const qc = useQueryClient();
  const nav = useNavigate();
  const first = profile.sections.find((s) => !isFinished(s.status));
  const loc = useLocation();
  const sent = (loc.state as (Partial<Finished> & { fromHome?: boolean }) | null) ?? null;
  const [fromHome] = useState(() => sent?.fromHome === true);         // kept after the state is cleared below
  const came = sent && !sent.fromHome ? (sent as Finished) : null;
  const [cheer] = useState(() => (came?.allDone ? CHEER_ALL : came?.finished === 'kids' ? CHEER_KIDS : came?.finished === 'home' ? CHEER_HOME : null));
  const [doneLine] = useState(() => nextLine('profileDone'));
  const full = profile.done === profile.total && momentAllowed('profileDone', { parent: false }).line;
  // Review of #51: the done line is quiet when the page opens on a profile that was already full, and said when it
  // fills while the page is open. The cheer is what the person just did, so it is always said.
  const arriving = useArriving();
  const meter = useRef<HTMLDivElement>(null);
  const rows = useRef<HTMLOListElement>(null);
  useEffect(() => {
    if (fromHome) nav(loc.pathname, { replace: true, state: null });   // once: a reload doesn't say it again
    if (!came) return;
    nav(loc.pathname, { replace: true, state: null });       // once: a reload doesn't play it again
    const kind = came.allDone ? 'all' : came.finished === 'kids' ? 'kids' : came.finished === 'home' ? 'home' : null;
    const host = kind === 'all' ? meter.current : rows.current?.querySelector<HTMLElement>(`[data-testid="row-${came.finished}"]`) ?? null;
    if (kind && host) void withCelebrate((c) => c.profile({ host, kind }));   // nothing moves when off or reduced
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const [problem, setProblem] = useState<string | null>(null);
  async function seen(go: boolean) {
    try {
      await refreshProfile(qc, markSeen());
      setProblem(null);
    } catch (e) {
      setProblem(failure(e));
      return;
    }
    if (go && first) nav(sectionPath(first.key, first.resume_step ?? SECTION_STEPS[first.key][0]));
  }

  return (
    <div className="screen profile">
      <h1>{TITLE}</h1>
      {problem && <Notice tone="error">{problem}</Notice>}
      {fromHome && <p className="muted" data-testid="opened-from-home">{OPENED_FROM_HOME} <Link to="/">{GO_HOME}</Link></p>}
      {!profile.me.seen && canEdit && (
        <section className="card prof-band" aria-label={TITLE}>
          <ShieldCheck className="i" size={24} aria-hidden="true" />
          <p>{FIRST_VISIT}</p>
          <Button onClick={() => void seen(true)}>{START}</Button>
          <button type="button" className="prof-icon-btn prof-band__close" aria-label={DISMISS} onClick={() => void seen(false)}>
            <X size={20} aria-hidden="true" />
          </button>
        </section>
      )}
      <div className="prof-meter" ref={meter}>
        <span className="prof-meter__bar" aria-hidden="true"><span style={{ width: `${(profile.done / profile.total) * 100}%` }} /></span>
        {full && <Check className="prof-meter__check" size={20} aria-hidden="true" data-testid="profile-check" />}
        <span className="prof-meter__text">{METER.replace('{n}', String(profile.done))}</span>
      </div>
      {cheer ? <Shown className="prof-cheer">{cheer}</Shown>
        : full && <Shown className="prof-cheer" data-testid="profile-done" quietOnMount={arriving}>{doneLine}</Shown>}
      <ol className="card prof-rows" ref={rows}>
        {profile.sections.map((s) => <Row key={s.key} s={s} profile={profile} canEdit={canEdit} />)}
      </ol>
      <Suspense fallback={null}><ProfileGrowth /></Suspense>
    </div>
  );
}
