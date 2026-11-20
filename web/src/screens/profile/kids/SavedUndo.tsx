import { UNDO } from '../../../lib/copy';
import { Shown } from '../../../ui/Live';
import { SAVED } from '../copy';

/** "Saved · Undo" after a kid's edit (spec, The card). */
export function SavedUndo({ show, onUndo }: { show: boolean; onUndo: () => void }) {
  if (!show) return null;
  return <Shown className="prof-saved">{SAVED} · <button type="button" className="link" onClick={onUndo}>{UNDO}</button></Shown>;
}
