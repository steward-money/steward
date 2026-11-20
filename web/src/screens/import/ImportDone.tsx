import { useRef } from 'react';
import { useIsHousehold } from '../../book/BookContext';
import { soundOn } from '../../lib/celebrations';
import { importDoneLine } from '../../lib/lines';
import { momentAllowed } from '../../lib/momentAllowed';
import { Shown } from '../../ui/Live';
import { useWinMoment } from '../../ui/useMoment';

/** Phase 17 spec 3.7: every commit's line beside its receipt, and one coin moment per batch. */
export function ImportDone({ n, m, dupes = 0 }: { n: number; m: number; dupes?: number }) {
  const allowed = momentAllowed('import', { parent: !useIsHousehold() });
  const host = useRef<HTMLParagraphElement>(null);
  useWinMoment(host, allowed.motion ? 'coins' : null, allowed.sound && soundOn());
  return allowed.line ? <Shown ref={host} className="imp-done" data-testid="import-done">{importDoneLine(n, m, dupes)}</Shown> : null;
}
