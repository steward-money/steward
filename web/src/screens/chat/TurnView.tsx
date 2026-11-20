// One question and its answer (design README "The voice"): the question in a quiet block on the right with what was
// sent, the ticked steps, then a store-names card or its approval line, a working line while answering, the answer with names put back, What Claude saw with the
// exact JSON, the time and cost, and one plain line for how it ended.
import {
  Ban, Check, ChevronDown, ChevronRight, CircleAlert, CircleCheck, CircleDashed, CircleStop, Clock, Flag, Gauge, LockKeyhole,
  OctagonX, PowerOff, RefreshCw, ScanEye, ServerOff, Store, WifiOff, type LucideIcon,
} from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useBookTo } from '../../book/BookContext';
import { Button } from '../../ui/Button';
import { Shown } from '../../ui/Live';
import { AnswerText } from './aliasText';
import { ApprovalCard, ViewerCard } from './ApprovalCard';
import { aboutLine } from './aboutChip';
import { fill, type AboutWire, type Card, type Chip, type Saw, type Turn } from './askApi';
import { ChipText } from './Chip';
import * as C from './copy';
import type { LiveTurn } from './useAsk';

export type View = {
  turnId: string | null; asked: string; about?: AboutWire | null; sent: string | null; chips: Chip[]; asWritten: boolean; who: string; when: string;
  steps: string[]; working: string | null; answer: string; stop: string | null; line: string | null; meta: string | null;
  saw: Saw[]; approval: { state: string; line: string; logKey: string | null } | null; card: Card | null;
  viewer: { managers: string[]; where: string } | null; lookedInto: boolean; note: string | null; error: string | null;
  running: boolean; stopping: boolean;
};

const END: Record<string, { icon: LucideIcon; tone: string }> = {
  dropped: { icon: WifiOff, tone: 'danger' }, stopped_check: { icon: OctagonX, tone: 'danger' }, record_failed: { icon: OctagonX, tone: 'danger' },
  busy: { icon: Clock, tone: '' }, approval_timeout: { icon: Clock, tone: '' }, api_error: { icon: CircleAlert, tone: '' },
  bad_request: { icon: CircleAlert, tone: '' }, refusal: { icon: CircleAlert, tone: '' }, cancelled: { icon: CircleAlert, tone: '' },
  max_tokens: { icon: CircleAlert, tone: 'muted' }, privacy_not_ready: { icon: LockKeyhole, tone: '' }, steps: { icon: ScanEye, tone: 'muted' },
  cap: { icon: Gauge, tone: '' }, cap_mid: { icon: Gauge, tone: '' }, stopped: { icon: CircleStop, tone: 'muted' },
  away: { icon: ServerOff, tone: 'danger' }, not_set_up: { icon: ServerOff, tone: '' }, stopped_by_gateway: { icon: PowerOff, tone: '' },
};
const ASK_AGAIN_STOPS = new Set(['dropped', 'busy', 'api_error']);
const APPROVAL: Record<string, { icon: LucideIcon; tone: string }> = {
  approved: { icon: CircleCheck, tone: 'ok' }, declined: { icon: Ban, tone: 'muted' }, viewer: { icon: Store, tone: 'muted' },
};

export function fromTurn(t: Turn): View {
  return {
    turnId: t.turn_id, asked: t.asked, about: t.about?.chip ?? null, sent: t.sent, chips: t.chips, asWritten: t.as_written, who: t.who, when: t.when,
    steps: t.steps.map((s) => s.done), working: null, answer: t.answer, stop: t.stop, line: t.line ?? (t.unfinished ? C.DIDNT_FINISH : null), meta: t.meta, saw: t.saw,
    approval: t.approval ? { state: t.approval.state, line: t.approval.line, logKey: t.approval.log_key } : null, card: null,
    viewer: null, lookedInto: t.looked_into, note: null, error: null, running: false, stopping: false,
  };
}

const clockNow = () => new Date().toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });

export function fromLive(l: LiveTurn, who: string): View {
  const running = !l.done && !l.error;
  const answering = l.text.length > 0 || !running;
  const steps = answering ? l.steps : l.steps.slice(0, -1);
  const last = l.steps[l.steps.length - 1];
  const working = running && !l.text && !l.card ? (last ? `${last.text}…` : l.turnId ? C.SENDING : null) : null;
  return {
    turnId: l.turnId, asked: l.asked, about: l.about ?? null, sent: l.sent, chips: l.chips, asWritten: l.sent !== null && l.chips.length === 0,
    who, when: `Today ${clockNow()}`, steps: steps.map((s) => s.done), working, answer: l.text,
    stop: l.done?.stop ?? null, line: l.done?.line ?? null, meta: l.done?.meta ?? null, saw: [],
    approval: l.approval ? { state: l.approval.state, line: l.approval.line, logKey: null } : null,
    card: l.card, viewer: l.viewer, lookedInto: false, note: l.note, error: l.error, running, stopping: l.stopping,
  };
}

function Working({ text }: { text: string }) {
  return <Shown className="working"><CircleDashed size={20} aria-hidden="true" />{text}</Shown>;
}

function SawList({ items }: { items: Saw[] }) {
  const [open, setOpen] = useState<number | null>(null);
  return (
    <div className="card saw">
      {items.map((s, i) => (
        <div key={`${s.key}-${i}`} className="saw__row">
          <ScanEye size={20} aria-hidden="true" />
          <span className="saw__what">{s.title}</span>
          <span className="saw__sum">{s.summary}</span>
          <button type="button" className="link" aria-expanded={open === i} onClick={() => setOpen(open === i ? null : i)}>
            {open === i ? C.HIDE_SENT : C.SHOW_SENT}
            {open === i ? <ChevronDown size={20} aria-hidden="true" /> : <ChevronRight size={20} aria-hidden="true" />}
          </button>
          {open === i && <pre className="json">{JSON.stringify(s.payload, null, 2)}</pre>}
        </div>
      ))}
    </div>
  );
}

type Props = { view: View; labels: Record<string, string>; onStop?: () => void; onAskAgain: (q: string) => void };

export function TurnView({ view: v, labels, onStop, onAskAgain }: Props) {
  const to = useBookTo();
  const [sawOpen, setSawOpen] = useState(false);
  const end = v.stop ? END[v.stop] : undefined;
  const ap = v.approval ? APPROVAL[v.approval.state] : undefined;
  return (
    <article className="turn">
      {v.about && <p className="qabout small muted">{aboutLine(v.about, labels, new Date())}</p>}
      <div className="qmsg">
        <span className="qmsg__text">{v.asked}</span>
        {v.sent !== null && (
          <span className="qmsg__sent">{v.asWritten ? C.SENT_AS_WRITTEN : <>{C.SENT_AS}<ChipText text={v.sent} chips={v.chips} labels={labels} /></>}</span>
        )}
      </div>
      {v.who && <p className="qmeta">{v.when ? `${v.who} · ${v.when}` : v.who}</p>}
      {v.note && <p className="small muted">{v.note}</p>}
      {/* Steps first, then the card or the approval line, then the answer (JR's decision; mockup chat-approve). */}
      {v.steps.length > 0 && (
        <ul className="steps">{v.steps.map((s, i) => <li key={i} className="is-done"><Check size={20} aria-hidden="true" />{s}</li>)}</ul>
      )}
      {v.card && v.turnId && <ApprovalCard turnId={v.turnId} card={v.card} />}
      {v.viewer && <ViewerCard managers={v.viewer.managers} where={v.viewer.where} />}
      {v.approval && ap && (
        <p className={`endline endline--${ap.tone}`}>
          <ap.icon size={20} aria-hidden="true" />
          <span>{v.approval.line}{v.approval.logKey && <> <Link to={to(`/settings/chat/sent?open=${encodeURIComponent(v.approval.logKey)}`)}>{C.SEE_IN_LOG}</Link></>}</span>
        </p>
      )}
      <div className="amsg">
        {v.working && <Working text={v.working} />}
        {v.answer && <AnswerText text={v.answer} labels={labels} />}
        {v.running && v.turnId && onStop && (
          <Button variant="quiet" icon={CircleStop} busy={v.stopping} onClick={onStop}>{C.STOP}</Button>
        )}
        {(v.meta || v.saw.length > 0) && (
          <div className="amsg__foot">
            {v.saw.length > 0 && (
              <button type="button" className="link" aria-expanded={sawOpen} onClick={() => setSawOpen(!sawOpen)}>
                <ScanEye size={20} aria-hidden="true" />{fill(C.WHAT_SAW, { n: v.saw.length })}
                {sawOpen ? <ChevronDown size={20} aria-hidden="true" /> : <ChevronRight size={20} aria-hidden="true" />}
              </button>
            )}
            {v.meta && <span className="amsg__meta">{v.meta}</span>}
          </div>
        )}
        {sawOpen && <SawList items={v.saw} />}
        {v.lookedInto && <Link className="link" to={to('/look-into')}><Flag size={20} aria-hidden="true" />{C.OPEN_LOOK_INTO}</Link>}
      </div>
      {v.line && (
        <p className={`endline${end?.tone ? ` endline--${end.tone}` : ''}`}>
          {end ? <end.icon size={20} aria-hidden="true" /> : <CircleAlert size={20} aria-hidden="true" />}<span>{v.line}</span>
        </p>
      )}
      {v.error && <p className="endline endline--danger"><CircleAlert size={20} aria-hidden="true" /><span>{v.error}</span></p>}
      {v.stop && ASK_AGAIN_STOPS.has(v.stop) && (
        <Button variant="quiet" icon={RefreshCw} onClick={() => onAskAgain(v.asked)}>{C.ASK_AGAIN}</Button>
      )}
    </article>
  );
}
