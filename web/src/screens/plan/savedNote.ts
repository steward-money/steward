import { dateLabel } from '../../lib/format';
import { savedWhen } from './levers';
import { planMemory } from './planMemory';

/** `at` and `day` are the save's instant and its household day (#52); `previous` is named by its day. */
export type SavedNote = { planId: number; at: string; day: string; by: string; previous: { name: string | null; saved_day: string } | null };

// Module state (planMemory): the confirmation outlives a trip to another tab and stays until the next change (it never
// fades), or a sign-out.
/** The note only counts for the plan it was saved as; another plan on screen (a refetch, another person's save) hides it. */
export const getSavedNote = (planId?: number | null) => {
  const { note } = planMemory;
  return note && note.planId === planId ? note : null;
};
export function setSavedNote(n: SavedNote | null) { planMemory.note = n; }

export function noteLine(n: SavedNote, now: Date): string {
  const head = `Saved by ${n.by} ${savedWhen(n.at, n.day, now)}.`;
  if (!n.previous) return head;
  const when = dateLabel(n.previous.saved_day, now);
  return n.previous.name
    ? `${head} The previous version, saved by ${n.previous.name} on ${when}, is in Earlier versions.`
    : `${head} The previous version, saved on ${when}, is in Earlier versions.`;
}
