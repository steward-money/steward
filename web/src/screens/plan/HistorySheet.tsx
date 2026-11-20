import { dateLabel } from '../../lib/format';
import { Notice } from '../../ui/Notice';
import { Sheet } from '../../ui/Sheet';
import { Skeleton } from '../../ui/Skeleton';
import { usePlanHistory } from './api';
import { CURRENT_TAG, EARLIER_VERSIONS } from './copy';
import { agesLine } from './PlanFailed';

export function HistorySheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const h = usePlanHistory(open);
  return (
    <Sheet open={open} onClose={onClose} title={EARLIER_VERSIONS}>
      {h.isError ? <Notice tone="error">{h.error.message}</Notice> : !h.data ? <Skeleton lines={3} /> : (
        <ul className="history">
          {h.data.history.map((p) => (
            <li key={p.id} className="history__row">
              <span className="strong">{dateLabel(p.saved_day, new Date())}{p.saved_by.name ? ` · ${p.saved_by.name}` : ''}</span>
              <span className="small muted">{agesLine(p.comfortable_age, p.likely_age)}</span>
              {p.current && <span className="tag">{CURRENT_TAG}</span>}
            </li>
          ))}
        </ul>
      )}
    </Sheet>
  );
}
