import { Check, CircleCheck, Undo2 } from 'lucide-react';
import { useEffect, useId, useLayoutEffect, useRef, useState, type RefObject } from 'react';
import { Button } from '../../ui/Button';
import { Shown } from '../../ui/Live';
import type { SavedPlan } from './api';
import { FIRST_SAVE, PLAN_NOT_UPDATED, PLAN_UPDATED, RESET, SAVE_THE_PLAN, UPDATE_THE_PLAN } from './copy';
import { noteLine, type SavedNote } from './savedNote';

/** Design decision 5: inline under the ages, pinned above the tab bar only while it is out of view. */
export const PIN_WHEN_HIDDEN = true;

export function useOutOfView(ref: RefObject<Element | null>, active: boolean): boolean {
  const [out, setOut] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!active || !el || typeof IntersectionObserver === 'undefined') { setOut(false); return; }
    const io = new IntersectionObserver(([e]) => setOut(!e.isIntersecting));
    io.observe(el);
    return () => io.disconnect();
  }, [ref, active]);
  return out;
}

type Props = {
  plan: SavedPlan | null; dirty: boolean; busy: boolean; error: string | null; note: SavedNote | null;
  onSave: () => void; onReset: () => void; now?: Date;
  /** Told when the bar pins or unpins, so the page can leave room for it at the foot. */
  onPinned?: (pinned: boolean) => void;
};

export function SaveBar({ plan, dirty, busy, error, note, onSave, onReset, now = new Date(), onPinned }: Props) {
  const firstSaveId = useId();
  const slot = useRef<HTMLDivElement>(null);
  const bar = useRef<HTMLDivElement>(null);
  const hidden = useOutOfView(slot, dirty);
  const pinned = PIN_WHEN_HIDDEN && dirty && hidden;
  const [height, setHeight] = useState(0);
  useLayoutEffect(() => { if (!pinned && bar.current) setHeight(bar.current.offsetHeight); }, [pinned, dirty]);
  useEffect(() => { onPinned?.(pinned); }, [pinned, onPinned]);

  if (dirty) {
    return (
      <div ref={slot} className="savebar-slot" style={pinned ? { minHeight: height } : undefined}>
        <div ref={bar} className={`savebar${pinned ? ' savebar--pinned' : ''}`} role="region" aria-label={PLAN_NOT_UPDATED} data-testid="savebar">
          <div className="savebar__text"><span className="savebar__title">{PLAN_NOT_UPDATED}</span></div>
          {error && <p className="savebar__error" role="alert">{error}</p>}
          <div className="savebar__actions">
            <Button variant="quiet" icon={Undo2} onClick={onReset}>{RESET}</Button>
            <Button icon={Check} busy={busy} onClick={onSave}>{UPDATE_THE_PLAN}</Button>
          </div>
        </div>
      </div>
    );
  }
  if (plan === null) {
    return (
      <div className="savebar" role="region" aria-labelledby={firstSaveId} data-testid="savebar">
        <div className="savebar__text"><span id={firstSaveId}>{FIRST_SAVE}</span></div>
        {error && <p className="savebar__error" role="alert">{error}</p>}
        <div className="savebar__actions"><Button icon={Check} busy={busy} onClick={onSave}>{SAVE_THE_PLAN}</Button></div>
      </div>
    );
  }
  if (!note) return null;
  return (
    <Shown as="div" className="savebar savebar--done" data-testid="savebar-done">
      <div className="savebar__text">
        <span><CircleCheck className="i" size={20} aria-hidden="true" />{PLAN_UPDATED}</span>
        <span className="small muted">{noteLine(note, now)}</span>
      </div>
    </Shown>
  );
}
