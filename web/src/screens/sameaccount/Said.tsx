import { UNDO } from '../../lib/copy';
import type { Part } from '../../lib/receipt';
import { Button } from '../../ui/Button';
import { Shown } from '../../ui/Live';
import { Rich } from '../../ui/Rich';

export type Said = { parts: Part[]; cheer: string | null; undo: string | null; error?: boolean };

/** What an answer did, in plain words, with Undo while it can (JR: nothing vanishes unseen, always Undo). */
export function SaidLine({ said, busy, onUndo }: { said: Said; busy: boolean; onUndo: () => void }) {
  const body = (
    <>
      {said.cheer && <strong className="same__cheer">{said.cheer}</strong>}
      <p><Rich parts={said.parts} /></p>
      {said.undo && <Button variant="quiet" busy={busy} onClick={onUndo}>{UNDO}</Button>}
    </>
  );
  // An error is an alert; any other answer is said through the polite region (issue #51).
  return said.error ? <div className="same__said" role="alert">{body}</div> : <Shown as="div" className="same__said">{body}</Shown>;
}
