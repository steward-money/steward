// The box and What Claude will see (was Will send; spec 4.3, 5.3; JR decision 1). The preview runs 400 ms after typing stops through the same
// function that makes the sent text. Send goes at once unless the gateway's model check removed something; then a Will
// send card with Send and Edit comes first. A failed preview disables Send: what can't be shown can't be sent.
// Ask about this (phase 13): the about line shows first, as soon as there is one, before anything is typed; the label
// replays the first-use tip; the panel passes `quiet` (no COMPOSER_NOTE), its own box ref, and Open in Ask as `extra`.
import { LockKeyhole, Pencil, Send } from 'lucide-react';
import { useEffect, useId, useRef, useState, type ReactNode, type RefObject } from 'react';
import { useDebouncedRequest } from '../../lib/useDebouncedRequest';
import { Button } from '../../ui/Button';
import { Shown } from '../../ui/Live';
import { fill, isModelChip, preview, type Chip, type Preview } from './askApi';
import { ChipText, Legend } from './Chip';
import * as C from './copy';
import { gw } from '../../lib/gatewayName';
import { TourTip } from './TourTip';

export { chipWhy } from './Chip';
export const PREVIEW_DELAY_MS = 400;

/** POST /chat/about's line, as sent, with the alias as a chip. */
export type AboutLine = { sent: string; chips: Chip[]; labels: Record<string, string> };
export type TourControl = { show: boolean; example: boolean; onToggle: () => void; onDone: () => void };

type Props = {
  value: string; onChange: (v: string) => void; onSend: (text: string) => void; disabled: boolean; placeholder: string;
  note?: string; onFirstKey?: () => void; about?: AboutLine | null; quiet?: boolean;
  boxRef?: RefObject<HTMLTextAreaElement | null>; extra?: ReactNode; tour?: TourControl;
};

function WillSend({ p }: { p: Preview }) {
  if (p.as_written || p.chips.length === 0) return <span className="willsend__text muted">{C.AS_WRITTEN}</span>;
  return <><ChipText text={p.text} chips={p.chips} labels={p.labels} /><Legend /></>;
}

export function Composer({ value, onChange, onSend, disabled, placeholder, note, onFirstKey, about = null, quiet = false, boxRef, extra, tour }: Props) {
  const text = value.trim();
  const req = useDebouncedRequest<Preview>(text && !disabled ? { key: text, run: (signal) => preview(text, signal) } : null, PREVIEW_DELAY_MS);
  const current = req.key === text && !req.pending ? req : null;
  const [confirm, setConfirm] = useState(false);
  const [waiting, setWaiting] = useState(false);
  const typed = useRef(false);
  const own = useRef<HTMLTextAreaElement>(null);
  const box = boxRef ?? own;
  const boxId = useId();
  const tipId = useId();
  const failed = current?.error ? (current.error as Error).message : null;

  // New text cancels the What Claude will see card and a Send that was waiting for a preview: only what was on screen when Send
  // was tapped can go (final review, Task 11).
  useEffect(() => { setConfirm(false); setWaiting(false); }, [text]);
  useEffect(() => {
    if (!waiting || !current) return;
    setWaiting(false);
    if (current.data && !current.error) decide(current.data);
  }, [waiting, current]); // eslint-disable-line react-hooks/exhaustive-deps

  function decide(p: Preview) {
    if (p.chips.some(isModelChip)) setConfirm(true);
    else onSend(value);
  }

  function send() {
    if (!text || disabled || failed) return;
    if (current?.data && !current.error) decide(current.data);
    else setWaiting(true);                 // no preview for exactly this text yet: wait for it (plan B ruling 9)
  }

  const modelParts = current?.data?.chips.filter(isModelChip).length ?? 0;
  const typedPreview = !!text && !disabled;
  const shown = about !== null || typedPreview;
  const chips = (about?.chips.length ?? 0) + (current?.data?.chips.length ?? 0);
  const tip = !!tour?.show && shown;
  const label = <><LockKeyhole size={20} aria-hidden="true" />{C.WILL_SEND}</>;
  return (
    <div className="askp__foot">
      {confirm && current?.data && !failed && (
        <div className="confirm" role="group" aria-label={C.WILL_SEND}>
          <span className="willsend__label"><LockKeyhole size={20} aria-hidden="true" />{C.WILL_SEND}</span>
          <ChipText text={current.data.text} chips={current.data.chips} labels={current.data.labels} />
          <Legend />
          <p className="small muted">{modelParts === 1 ? gw(C.CONFIRM_ONE) : fill(gw(C.CONFIRM_MANY), { n: modelParts })}</p>
          <div className="confirm__acts">
            <Button icon={Send} onClick={() => { setConfirm(false); onSend(value); }}>{C.SEND}</Button>
            <Button variant="quiet" icon={Pencil} onClick={() => { setConfirm(false); box.current?.focus(); }}>{C.EDIT}</Button>
          </div>
        </div>
      )}
      <div className="composer">
        <label className="sr-only" htmlFor={boxId}>{C.BOX_LABEL}</label>
        <textarea id={boxId} ref={box} value={value} disabled={disabled} placeholder={placeholder} aria-describedby={tip ? tipId : undefined}
          onChange={(e) => { if (!typed.current) { typed.current = true; onFirstKey?.(); } onChange(e.target.value); }}
          onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); send(); } }} />
        {tip && tour && <TourTip id={tipId} chips={chips > 0} example={tour.example} onDone={tour.onDone} />}
        {shown && (
          <Shown as="div" className={`willsend${tip ? ' is-tour' : ''}`}>
            {tour
              ? <button type="button" className="willsend__label willsend__replay" aria-expanded={tour.show} onClick={tour.onToggle}>{label}</button>
              : <span className="willsend__label">{label}</span>}
            {about && <span className="willsend__about"><ChipText text={about.sent} chips={about.chips} labels={about.labels} /></span>}
            {typedPreview && (failed ? <span className="willsend__text">{fill(C.PREVIEW_FAILED, { error: failed })}</span>
              : current?.data ? <WillSend p={current.data} /> : <span className="willsend__text muted">…</span>)}
          </Shown>
        )}
        <div className="composer__row">
          <span className="small muted">{note ?? (chips ? C.TAP_NOTE : quiet ? '' : C.COMPOSER_NOTE)}</span>
          <span className="composer__acts">
            {extra}
            <Button icon={Send} busy={waiting} disabled={disabled || !text || !!failed} onClick={send}>{C.SEND}</Button>
          </span>
        </div>
      </div>
    </div>
  );
}
