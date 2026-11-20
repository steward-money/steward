import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { BookCtx } from '../../book/BookContext';
import { BOOK, callsTo, reply, stubFetch, wrapBook } from '../../test-utils';
import type { Impact, PlanReady } from './api';
import { CHILDREN, FACTS, impactsFixture, nextDollarFixture, planFixture } from './fixtures';
import { Overview } from './Overview';
import { currentStep, topHelps } from './OverviewCards';
import { setSavedNote } from './savedNote';
import { today } from '../../lib/today';

afterEach(() => { vi.unstubAllGlobals(); setSavedNote(null); });

function routes(extra: Record<string, unknown> = {}) {
  const data = planFixture();
  return stubFetch({
    '/api/facts': FACTS, '/api/children': CHILDREN, '/api/next-dollar': nextDollarFixture(), '/api/plan/history': { history: [] },
    'POST /api/plan/impacts': { levers: data.levers, impacts: impactsFixture() }, ...extra,
  });
}
const open = (data: PlanReady = planFixture()) => wrapBook(<Overview data={data} />, {}, '/b/ours/plan');
const before = (a: Element, b: Element) => expect(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

describe('Overview (plan subpages decision 2): am I on track', () => {
  it('goes headline, ages, chart, the two cards, Big costs ahead, then who saved the plan; no levers', async () => {
    routes();
    const data = planFixture();
    data.inputs.set_asides.one_offs = [{ id: 6, name: 'Water heater', year: 2028, due_year: 2028, due_month: 12, overdue: false, cents: 220_000, monthly_cents: 8_500, is_estimate: true }];
    open(data);
    await screen.findByText('Spending in retirement');
    const order = ['plan-headline', 'age-comfortable', 'change-line', 'band', 'help-card', 'next-card', 'big-costs', 'saved-by'].map((id) => screen.getByTestId(id));
    for (let i = 1; i < order.length; i++) before(order[i - 1], order[i]);
    expect(screen.getByTestId('change-line')).toHaveTextContent(/^No change since Oct 1[23](, 2026)?\.$/);   // the fixture's summaries match
    expect(screen.queryByTestId(/^lever-/)).toBeNull();
    expect(screen.queryByTestId('savebar')).toBeNull();               // a plan is saved and nothing moved
    expect(screen.getByTestId('saved-by')).toHaveTextContent(/^Plan saved by Sam on Oct 1[23]/);
  });

  it('draws the year-by-year chart once', async () => {
    routes();
    const { container } = open();
    await screen.findByTestId('band');
    expect(container.querySelectorAll('svg.chart')).toHaveLength(1);
  });

  it('shows Big costs ahead only when the plan pays a one-off', async () => {
    routes();
    open();
    await screen.findByTestId('band');
    expect(screen.queryByTestId('big-costs')).toBeNull();
  });

  it('What would help most: the top two levers by impact, linking to Try changes', async () => {
    routes();
    open();
    const card = await screen.findByTestId('help-card');
    await within(card).findByText('Spending in retirement');
    expect(within(card).getAllByRole('listitem').map((li) => li.textContent))
      .toEqual(['Spending in retirementabout 2 years earlier', 'Save more each monthabout 1 year earlier']);
    expect(within(card).getByRole('link', { name: 'See them in Try changes' })).toHaveAttribute('href', '/b/ours/plan/try');
  });

  it('says so when nothing on the list brings it earlier', async () => {
    const data = planFixture();
    const none = impactsFixture().map((i) => ({ ...i, delta_months: 0, text: 'No change' }));
    routes({ 'POST /api/plan/impacts': { levers: data.levers, impacts: none } });
    open(data);
    expect(await within(await screen.findByTestId('help-card')).findByText('Nothing on the list brings it earlier right now.')).toBeInTheDocument();
  });

  it('Your next dollar: the first step still to do, linking to Next dollar', async () => {
    routes();
    open();
    const card = await screen.findByTestId('next-card');
    expect(await within(card).findByText('Cash for 6 months of spending')).toBeInTheDocument();
    expect(within(card).getByText('1')).toBeInTheDocument();
    expect(within(card).getByRole('link', { name: 'See every step in Next dollar' })).toHaveAttribute('href', '/b/ours/plan/next');
  });

  it('opens Earlier versions from the saved-by line', async () => {
    routes();
    open();
    fireEvent.click(await screen.findByRole('button', { name: 'Earlier versions' }));
    expect(await screen.findByRole('dialog', { name: 'Earlier versions' })).toBeInTheDocument();
  });

  it('offers the first save when nothing is saved yet, and it saves the plan as it stands', async () => {
    const data = planFixture();
    const saved = { ...data.plan!, id: 8, saved_at: new Date().toISOString(), saved_day: today(), saved_by: { id: 1, name: 'JR' },
      inputs_summary: { ...data.inputs_summary, saving_cents: 250_000 } };
    const fetchMock = routes({ 'POST /api/plan': () => reply({ plan: saved, levers: data.levers, result: data.result }, 201) });
    const { qc, rerender } = open({ ...data, plan: null });
    qc.setQueryData(['plan'], { ...data, plan: null });
    expect(screen.getByTestId('saved-by')).toHaveTextContent('Not saved yet');
    fireEvent.click(screen.getByRole('button', { name: 'Save the plan' }));
    await waitFor(() => expect(callsTo(fetchMock, 'POST', '/api/plan').filter((c) => c.url === '/api/plan')).toHaveLength(1));
    expect(callsTo(fetchMock, 'POST', '/api/plan').find((c) => c.url === '/api/plan')!.body).toEqual({});
    // The cached plan carries the saved summary, so the line compares today's age with today's months.
    await waitFor(() => expect((qc.getQueryData(['plan']) as PlanReady).inputs_summary).toEqual(saved.inputs_summary));
    // The Plan tab's refetch brings the saved plan in; here the test hands it over (same providers, so state is kept).
    rerender(<QueryClientProvider client={qc}><MemoryRouter initialEntries={['/b/ours/plan']}>
      <BookCtx.Provider value={BOOK}><Overview data={{ ...data, plan: saved }} /></BookCtx.Provider>
    </MemoryRouter></QueryClientProvider>);
    expect(await screen.findByTestId('savebar-done')).toHaveTextContent('Plan updated.');
  });
});

describe('the cards, worked out', () => {
  const row = (key: Impact['key'], delta: number | null, text: string): Impact =>
    ({ key, label: key, child_label: null, try_value: 0, delta_months: delta, comfortable_age: 63, text });

  it('takes the first two levers that bring it earlier, one row per lever', () => {
    const list = [row('college', -14, 'about 1 year earlier'), row('college', -9, 'about 9 months earlier'), row('jr_claim', 6, 'about 6 months later'),
      row('gift', null, 'Comfortable by 66'), row('save_more', -2, 'about 2 months earlier')];
    expect(topHelps(list).map((i) => i.key)).toEqual(['college', 'gift']);
    expect(topHelps([row('spending', 0, 'No change')])).toEqual([]);
  });

  it('picks the first step not done, or the last when every one is', () => {
    const steps = nextDollarFixture().steps;
    expect(currentStep(steps)).toEqual({ step: steps[0], n: 1 });
    steps[0].status = 'done';
    expect(currentStep(steps)!.n).toBe(2);
    for (const s of steps) s.status = 'done';
    expect(currentStep(steps)!.n).toBe(5);
    expect(currentStep([])).toBeNull();
  });
});
