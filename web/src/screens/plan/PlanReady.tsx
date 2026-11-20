import { Check } from 'lucide-react';
import { useEffect, useState } from 'react';
import { currentBook } from '../../api/book';
import { nextLine } from '../../lib/lines';
import { momentAllowed } from '../../lib/momentAllowed';
import { Shown } from '../../ui/Live';

// Phase 17 spec 3.7, ruling R2: the checklist page goes the moment it is complete (the overview replaces it), so the
// Setup page leaves a note on this device, per book, and the first overview after says it, once. One count-free line,
// right for 6 required items or 7.
const KEY = 'steward-plan-setup';
const key = () => `${KEY}:${currentBook() ?? ''}`;

export function markSetupOpen(): void {
  try { localStorage.setItem(key(), '1'); } catch { /* private mode: no line later */ }
}
export function peekSetupDone(): boolean {
  try { return localStorage.getItem(key()) === '1'; } catch { return false; }
}
export function forgetSetupDone(): void {
  try { localStorage.removeItem(key()); } catch { /* private mode */ }
}

export function PlanReadyLine() {
  const [done] = useState(peekSetupDone);
  const [line] = useState(() => (done ? nextLine('planChecklistDone') : null));
  useEffect(() => { if (done) forgetSetupDone(); }, [done]);
  if (!line || !momentAllowed('planDone', { parent: false }).line) return null;
  return <Shown className="plan-ready" data-testid="plan-ready"><Check className="i" size={20} aria-hidden="true" />{line}</Shown>;
}
