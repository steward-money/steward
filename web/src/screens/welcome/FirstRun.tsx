import { useQueryClient } from '@tanstack/react-query';
import { Check } from 'lucide-react';
import { useEffect, useRef, useState, type RefObject } from 'react';
import { useSearchParams } from 'react-router-dom';
import { freshMe } from '../../api/hooks';
import { bookHref, useBook } from '../../book/BookContext';
import { Link, useNavigate } from '../../book/links';
import { soundOn } from '../../lib/celebrations';
import { LINK } from '../../lib/copy';
import { importDoneLine, nextLine } from '../../lib/lines';
import { momentAllowed } from '../../lib/momentAllowed';
import { Acorn } from '../../ui/Acorn';
import { Button } from '../../ui/Button';
import { Card } from '../../ui/Card';
import { Shown } from '../../ui/Live';
import { Notice } from '../../ui/Notice';
import { StepBar } from '../../ui/StepBar';
import { useAcornMotion } from '../../ui/useMoment';
import { FIRST_VISIT, STEP_N_OF } from '../profile/copy';
import { saveGeneral } from '../ready/api';
import { LinkForm } from '../settings/LinkForm';
import { failure } from '../settings/shared';
import { makeSample, markWelcome, type WelcomeState } from './api';
import * as C from './copy';
import { Extras } from './Extras';
import { Planted } from './Planted';
import './welcome.css';

type Step = 'hello' | 'money' | 'sort' | 'extras' | 'planted';
const STEPS: readonly Step[] = ['hello', 'money', 'sort', 'extras', 'planted'];
const MIDDLE: readonly Step[] = ['money', 'sort', 'extras'];

// Which steps this page load has seen not done, and which it has already cheered. Module scope, as welcomeVisit.ts:
// coming back from Import remounts the welcome with the money already in, and the hop is still owed.
const unsettled = new Set<string>();
const cheered = new Set<string>();
export function forgetStepMoments(): void { unsettled.clear(); cheered.clear(); }

/** The hop and a setupStep line, once, the first time a step this page load saw not done is done (spec 2.3, 9).
 *  `ref` goes on the Acorn that hops; `line` is the words to show beside it (null until then, or where lines are off). */
export function useStepMoment(done: boolean, key: string, owed = false): { ref: RefObject<SVGSVGElement | null>; line: string | null; played: boolean } {
  const ref = useRef<SVGSVGElement>(null);
  const allowed = momentAllowed('setupStep', { parent: false });
  const [played, setPlayed] = useState(false);
  const [line, setLine] = useState<string | null>(null);
  useEffect(() => {
    if (!done) { unsettled.add(key); return; }
    if ((!owed && !unsettled.has(key)) || cheered.has(key)) return;   // `owed`: it was done elsewhere (the sorts, in Review)
    cheered.add(key);
    setPlayed(true);
    if (allowed.line) setLine(nextLine('setupStep'));
  }, [done, key, owed, allowed.line]);
  useAcornMotion(ref, played && allowed.motion ? 'hop' : null, allowed.sound && soundOn());
  return { ref, line, played };
}

const zoneOf = (): string | null => {
  try {
    const z = Intl.DateTimeFormat().resolvedOptions().timeZone;
    return typeof z === 'string' && z ? z : null;
  } catch { return null; }
};
const zonesOf = (): string[] => {
  try { return Intl.supportedValuesOf('timeZone'); } catch { return []; }
};

/** The first run (guided setup spec 2.2 to 2.4): Welcome, Bring in your money, Sort a few; Task 8 adds the extras and
 *  the planting. The step is the address's `?step=`, else where this person left off (D7); a replay opens on Welcome. */
export function FirstRun({ state, again }: { state: WelcomeState; again: boolean }) {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const book = useBook();
  const asked = params.get('step');
  const step: Step = STEPS.find((s) => s === asked)
    ?? (again || (state.step === 'money' && !state.money.in) ? 'hello' : state.step);
  const go = (to: Step) => navigate(`?step=${to}${again ? '&again=1' : ''}`, { replace: true });
  const later = async () => {
    if (!again && book) { try { await markWelcome(book.slug); } catch { /* the welcome comes back next time */ } }
    navigate('/', { replace: true });
  };
  const n = MIDDLE.indexOf(step) + 1;
  return (
    <div className="screen welcome">
      {n > 0 && (
        <header className="welcome__head">
          <StepBar label={STEP_N_OF.replace('{n}', String(n)).replace('{m}', String(MIDDLE.length))} current={step}
            states={MIDDLE.map((s, i) => ({ key: s, status: i < n - 1 ? 'done' : 'todo' }))} />
        </header>
      )}
      {step === 'hello' && <Hello state={state} go={go} />}
      {step === 'money' && <Money state={state} go={go} />}
      {step === 'sort' && <Sort state={state} go={go} />}
      {step === 'extras' && <Extras again={again} go={go} />}
      {step === 'planted' && <Planted />}
      {step !== 'planted' && (
        <p className="welcome__later"><Link className="link" to="/" onClick={(e) => { e.preventDefault(); void later(); }}>{C.LATER}</Link></p>
      )}
    </div>
  );
}

type Go = { go: (to: Step) => void };

function Hello({ state, go }: Go & { state: WelcomeState }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const ref = useRef<SVGSVGElement>(null);
  const allowed = momentAllowed('setupStep', { parent: false });
  useAcornMotion(ref, allowed.motion ? 'hop' : null, allowed.sound && soundOn());
  const browserZone = zoneOf();
  const showZone = state.zone_needed && browserZone !== null;
  const [zone, setZone] = useState(browserZone ?? '');
  const [changing, setChanging] = useState(false);
  const [busy, setBusy] = useState<'start' | 'sample' | null>(null);
  const [error, setError] = useState('');

  // A time zone the server refuses never blocks the first run (the person can set it in Settings later).
  async function saveZone() {
    if (!showZone || !zone) return;
    try { await saveGeneral({ timezone: zone }); } catch { /* never blocks */ }
  }
  async function start() {
    setBusy('start');
    await saveZone();
    go('money');
  }
  async function look() {
    setBusy('sample');
    setError('');
    try {
      await saveZone();
      const sample = await makeSample();
      // BookScope sends an address it cannot find in /me to another book: /me must know the Sample first.
      await freshMe(qc);
      navigate(bookHref(sample.slug, '/'));
    } catch (err) {
      setError(failure(err));
      setBusy(null);
    }
  }

  return (
    <section className="welcome__hello" aria-labelledby="welcome-h">
      {allowed.face && <Acorn ref={ref} face="win" size={72} className="welcome__acorn" />}
      <h1 id="welcome-h" className="welcome__title">{C.HELLO_TITLE}</h1>
      <p className="welcome__lede">{C.HELLO_LEDE}</p>
      <p className="muted">{FIRST_VISIT}</p>
      {showZone && (
        <p className="welcome__zone muted">
          {changing ? (
            <select aria-label="Time zone" value={zone} onChange={(e) => setZone(e.target.value)}>
              {[...new Set([zone, ...zonesOf()])].map((z) => <option key={z} value={z}>{z}</option>)}
            </select>
          ) : (
            <>
              {C.ZONE_LINE.replace('{zone}', zone)}{' '}
              <button type="button" className="link" onClick={() => setChanging(true)}>{C.CHANGE}</button>
            </>
          )}
        </p>
      )}
      {error && <Notice tone="error">{error}</Notice>}
      <div className="welcome__actions">
        <Button busy={busy === 'start'} disabled={busy !== null} onClick={() => void start()}>{C.SET_UP}</Button>
        <Button variant="quiet" busy={busy === 'sample'} disabled={busy !== null} onClick={() => void look()}>
          {busy === 'sample' ? C.MAKING_SAMPLE : C.LOOK_AROUND}
        </Button>
      </div>
    </section>
  );
}

function Footer({ go, to }: Go & { to: Step }) {
  return (
    <div className="welcome__foot">
      <button type="button" className="link" onClick={() => go(to)}>{C.SKIP_FOR_NOW}</button>
      <Button onClick={() => go(to)}>{C.NEXT}</Button>
    </div>
  );
}

function Money({ state, go }: Go & { state: WelcomeState }) {
  const qc = useQueryClient();
  const [linking, setLinking] = useState(false);
  const { money, waiting } = state;
  const { ref, line } = useStepMoment(money.in, 'money');
  const allowed = momentAllowed('setupStep', { parent: false });
  const imported = money.in && !money.linked;
  const linked = () => {
    setLinking(false);
    for (const k of ['welcome', 'simplefin', 'institutions', 'accounts', 'review', 'summary']) void qc.invalidateQueries({ queryKey: [k] });
  };
  return (
    <section className="welcome__step" aria-labelledby="welcome-money-h">
      <div className="welcome__headline">
        {allowed.face && <Acorn ref={ref} face={money.in ? 'win' : 'resting'} size={48} className="welcome__acorn" />}
        <h1 id="welcome-money-h" className="welcome__title">{C.MONEY_TITLE}</h1>
      </div>
      <Shown as="div" className="welcome__said">{line && <p data-testid="step-line">{line}</p>}</Shown>
      <div className="welcome__cards">
        <Card className="welcome__card">
          <h2 className="subhead">{C.LINK_TITLE}</h2>
          {money.linked ? (
            <p className="welcome__done"><Check className="i" size={20} aria-hidden="true" />{C.linkedLine(money.accounts)}</p>
          ) : (
            <>
              <p className="muted">{C.LINK_LINE}</p>
              {linking ? <LinkForm onLinked={linked} /> : <Button variant="quiet" onClick={() => setLinking(true)}>{LINK}</Button>}
            </>
          )}
        </Card>
        <Card className="welcome__card">
          <h2 className="subhead">{C.IMPORT_TITLE}</h2>
          {imported ? (
            <p className="welcome__done"><Check className="i" size={20} aria-hidden="true" />{importDoneLine(money.rows, waiting)}</p>
          ) : (
            <>
              <p className="muted">{C.IMPORT_LINE}</p>
              <Link className="btn btn--quiet" to="/settings/import?from=welcome">{C.IMPORT}</Link>
            </>
          )}
        </Card>
      </div>
      <Footer go={go} to="sort" />
    </section>
  );
}

function Sort({ state, go }: Go & { state: WelcomeState }) {
  const { waiting, money } = state;
  return (
    <section className="welcome__step" aria-labelledby="welcome-sort-h">
      <h1 id="welcome-sort-h" className="welcome__title">{C.SORT_TITLE}</h1>
      {waiting > 0 ? (
        <>
          <p>{C.sortWait(waiting)}</p>
          <Link className="btn btn--primary welcome__start" to="/review?from=welcome">{C.START_SORTING}</Link>
        </>
      ) : <p className="muted">{money.in ? C.NOTHING_TO_SORT : C.NOTHING_YET}</p>}
      <Footer go={go} to="extras" />
    </section>
  );
}
