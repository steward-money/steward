import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { currentBook } from '../../api/book';
import { ApiError } from '../../api/client';
import { setQueryDataIfStillIn } from '../../queryClient';
import { isReady, savePlan, type DeltaBody, type PlanReady, type PlanResponse, type SaveResponse } from './api';
import { getSavedNote, setSavedNote, type SavedNote } from './savedNote';

export type PlanSaver = {
  note: SavedNote | null; saving: boolean; error: string | null;
  /** Saves `body` (the levers someone moved; {} saves the plan as it stands). The answer, or null after an error. */
  save: (body: DeltaBody) => Promise<SaveResponse | null>;
  /** A change ends the "Plan updated." note and any save error. */
  clear: () => void;
};

/** Saving the plan, for Try changes and the Overview's first save (plan subpages). The note outlives a trip to another
 *  page (savedNote.ts) and belongs to one saved plan: a refetch that brings in another plan ends it. */
export function usePlanSave(data: PlanReady, by: string): PlanSaver {
  const qc = useQueryClient();
  const [note, setNote] = useState<SavedNote | null>(() => getSavedNote(data.plan?.id));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const shownPlanId = data.plan?.id;
  useEffect(() => {
    setNote((n) => (n && n.planId !== shownPlanId ? null : n));
  }, [shownPlanId]);

  async function save(body: DeltaBody): Promise<SaveResponse | null> {
    setSaving(true);
    setError(null);
    const book = currentBook();
    try {
      const res = await savePlan(body);
      const n: SavedNote = { planId: res.plan.id, at: res.plan.saved_at, day: res.plan.saved_day, by: res.plan.saved_by.name ?? by,
        previous: data.plan ? { name: data.plan.saved_by.name, saved_day: data.plan.saved_day } : null };
      setSavedNote(n);
      setNote(n);
      setQueryDataIfStillIn<PlanResponse>(qc, book, ['plan'], (old) => (old && isReady(old) ? { ...old, plan: res.plan, levers: res.levers, result: res.result, inputs_summary: res.plan.inputs_summary ?? old.inputs_summary } : old));
      void qc.invalidateQueries({ queryKey: ['plan-history'] });
      return res;
    } catch (e) {
      // A 409 means setup is unfinished again: refetch the plan, and the Plan tab shows the checklist.
      if (e instanceof ApiError && e.status === 409) void qc.invalidateQueries({ queryKey: ['plan'] });
      setError((e as Error).message);
      return null;
    } finally {
      setSaving(false);
    }
  }

  function clear() {
    if (note) { setSavedNote(null); setNote(null); }
    setError(null);
  }

  return { note, saving, error, save, clear };
}
