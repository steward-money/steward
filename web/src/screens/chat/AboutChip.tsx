// The chip (Ask about this P7, Copy): "About: {thing}", with " · sent as {alias}" muted for an account, and ✕ (44 px).
// ✕ turns it into a dashed "+ About: {thing}" in the same place, which adds it back: nothing vanishes unseen. A row
// that's gone says so in the chip's place (P28).
import { X } from 'lucide-react';
import { useEffect, useRef } from 'react';
import { Shown } from '../../ui/Live';
import { aboutThing } from './aboutChip';
import { fill } from './askApi';
import * as C from './copy';
import type { AboutState } from './useAboutReply';

type Props = { state: AboutState; removed: boolean; onRemove: () => void; onAddBack: () => void; today?: Date };

export function AboutChip({ state, removed, onRemove, onAddBack, today = new Date() }: Props) {
  // Toggling swaps one control for the other; focus follows to the replacement when it would otherwise drop to <body>.
  const ctl = useRef<HTMLButtonElement>(null);
  const first = useRef(true);
  useEffect(() => {
    if (first.current) { first.current = false; return; }
    const a = document.activeElement;
    if (!a || a === document.body) ctl.current?.focus();
  }, [removed]);
  if (state.status === 'gone') return <Shown className="ask-gone">{state.line}</Shown>;
  if (!state.reply) return null;
  const { thing, alias } = aboutThing(state.reply.about, state.reply.labels, today);
  if (removed) {
    return (
      <button type="button" ref={ctl} className="ask-chip ask-chip--removed" aria-label={fill(C.ABOUT_ADD_BACK, { thing })} onClick={onAddBack}>
        {fill(C.ABOUT_REMOVED, { thing })}
      </button>
    );
  }
  return (
    <span className="ask-chip">
      <span>{fill(C.ABOUT_CHIP, { thing })}{alias && <span className="muted">{fill(C.ABOUT_SENT_AS, { alias })}</span>}</span>
      <button type="button" ref={ctl} className="ask-chip__x" aria-label={fill(C.ABOUT_REMOVE, { thing })} onClick={onRemove}>
        <X size={20} aria-hidden="true" />
      </button>
    </span>
  );
}
