import { ApiError } from '../../api/client';
import { TRY_AGAIN } from '../../lib/copy';
import { dateLabel } from '../../lib/format';
import { Button } from '../../ui/Button';
import { Notice } from '../../ui/Notice';
import type { LastSaved } from './api';
import { COMFORTABLY_AT, COMFORTABLY_NOT_BY_70, FROM_SAVED, LIKELY_AT, LIKELY_NOT_BY_70 } from './copy';

/** Spec 8: a 503 carries the last saved plan's ages and date. */
export function lastSavedOf(error: unknown): LastSaved | null {
  if (!(error instanceof ApiError)) return null;
  return (error.body as { last_saved?: LastSaved | null } | null)?.last_saved ?? null;
}

const part = (label: string, age: number | null, none: string) => (age === null ? none : `${label} ${age}`);
export const agesLine = (c: number | null, l: number | null) => `${part(COMFORTABLY_AT, c, COMFORTABLY_NOT_BY_70)} · ${part(LIKELY_AT, l, LIKELY_NOT_BY_70)}`;

export function LastSavedAges({ last }: { last: LastSaved }) {
  return (
    <div className="last-saved" data-testid="last-saved">
      <p className="eyebrow">{FROM_SAVED.replace('{date}', dateLabel(last.saved_day, new Date()))}</p>
      <p className="last-saved__ages">{agesLine(last.comfortable_age, last.likely_age)}</p>
    </div>
  );
}

export function PlanFailed({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const last = lastSavedOf(error);
  return (
    <div className="stack">
      <Notice tone="error">
        <p>{(error as Error).message}</p>
        <Button variant="quiet" onClick={onRetry}>{TRY_AGAIN}</Button>
      </Notice>
      {last && <LastSavedAges last={last} />}
    </div>
  );
}
