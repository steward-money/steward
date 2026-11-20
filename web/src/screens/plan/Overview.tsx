import { History } from 'lucide-react';
import { useState } from 'react';
import { useFacts } from '../../api/hooks';
import { dateLabel } from '../../lib/format';
import { Ages } from './Ages';
import type { PlanReady, SavedPlan } from './api';
import { BandChart } from './BandChart';
import { BigCosts } from './BigCosts';
import { ChangeLine } from './ChangeLine';
import { EARLIER_VERSIONS, NOT_SAVED_YET, PLAN_SAVED_BY, SAVED_ON, SOMEONE } from './copy';
import { Headline } from './Headline';
import { HistorySheet } from './HistorySheet';
import { OverviewCards } from './OverviewCards';
import { peopleFrom } from './people';
import { PlanReadyLine } from './PlanReady';
import { SaveBar } from './SaveBar';
import { usePlanSave } from './usePlanSave';
import { thisYear } from '../../lib/today';

export function savedByText(plan: SavedPlan | null): string {
  if (!plan) return NOT_SAVED_YET;
  return PLAN_SAVED_BY.replace('{name}', plan.saved_by.name ?? SOMEONE).replace('{when}', SAVED_ON.replace('{date}', dateLabel(plan.saved_day, new Date())));
}

/** The Overview (plan subpages decision 2): "am I on track", in order. It shows the saved plan as today's inputs answer
 *  it; moving a lever is Try changes' job. With nothing saved yet, the foot offers the first save. */
export function Overview({ data }: { data: PlanReady }) {
  const facts = useFacts();
  const planYear = data.result.band[0]?.year ?? thisYear();
  const people = peopleFrom(data.names, facts.data?.facts, planYear, data.result.band[0]?.jr_age ?? 40);
  const saver = usePlanSave(data, people.names.jr);
  const [history, setHistory] = useState(false);
  return (
    <div className="plan plan-overview">
      <PlanReadyLine />
      <div className="ages-wrap">
        <Headline result={data.result} basisCents={data.inputs.spending_basis_cents} spendingPct={data.levers.assumed.spending_pct} younger={people.younger} />
        <Ages result={data.result} younger={people.younger} />
        <ChangeLine data={data} />
      </div>
      <BandChart result={data.result} people={people} />
      <OverviewCards data={data} />
      <BigCosts inputs={data.inputs} />
      <div className="plan-saved" data-testid="saved-by">
        <p className="saved-by">
          <History className="i" size={20} aria-hidden="true" /><span>{savedByText(data.plan)}</span>
          {data.plan && <button type="button" className="link" onClick={() => setHistory(true)}>{EARLIER_VERSIONS}</button>}
        </p>
        <SaveBar plan={data.plan} dirty={false} busy={saver.saving} error={saver.error} note={saver.note}
          onSave={() => { void saver.save({}); }} onReset={() => {}} />
      </div>
      <HistorySheet open={history} onClose={() => setHistory(false)} />
    </div>
  );
}
