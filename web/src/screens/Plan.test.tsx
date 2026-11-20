import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useFakeTimers } from '../test-clock';
import { reply, stubFetch, wrapBook } from '../test-utils';
import Plan, { HowRoute, NextRoute, OverviewRoute, TryRoute } from './Plan';
import { clearKeptDraft } from './plan/usePlanDraft';
import { CHILDREN, FACTS, impactsFixture, nextDollarFixture, planFixture } from './plan/fixtures';
import Taxes from './taxes/Taxes';
import { NOT_ADVICE } from '../ui/NotAdvice';

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); clearKeptDraft(); });
beforeEach(() => { useFakeTimers(); });

const NAMES = { jr: 'JR', spouse: 'Sam', joint: 'Joint' };
const SETUP = [{ key: 'children', title: 'Children', done: false, required: true, detail: 'Say which child 529 — kid 2 belongs to.', suggested_cents: null }];

/** The Plan routes as routes.tsx has them, in the household book. */
function openPlan(path: string) {
  return wrapBook(
    <Routes>
      <Route path="/b/:slug/plan" element={<Plan />}>
        <Route index element={<OverviewRoute />} />
        <Route path="try" element={<TryRoute />} />
        <Route path="next" element={<NextRoute />} />
        <Route path="how" element={<HowRoute />} />
      </Route>
      <Route path="/b/:slug/taxes" element={<Taxes />} />
    </Routes>, {}, `/b/ours${path}`);
}

function readyRoutes(extra: Record<string, unknown> = {}) {
  const data = planFixture();
  return stubFetch({
    '/api/plan': data, '/api/plan/setup': { ready: true, setup: [], names: NAMES }, '/api/plan/history': { history: [] },
    '/api/facts': FACTS, '/api/children': CHILDREN, '/api/accounts': { accounts: [] }, '/api/next-dollar': nextDollarFixture(),
    '/api/summary/history': { points: [] }, '/api/ss': { records: { jr: null, spouse: null } },
    'POST /api/plan/impacts': { levers: data.levers, impacts: impactsFixture() },
    ...extra,
  });
}
const row = () => within(screen.getByRole('navigation', { name: 'Plan pages' }));
const labels = () => row().getAllByRole('link').map((a) => a.textContent);

describe('Plan: five pages in one row (plan subpages decision 1)', () => {
  it('each tab opens its page and is marked as the current one', async () => {
    readyRoutes();
    openPlan('/plan');
    await screen.findByTestId('age-comfortable');
    expect(labels()).toEqual(['Overview', 'Try changes', 'Next dollar', 'How it works', 'Taxes']);
    expect(row().getByRole('link', { name: 'Overview' })).toHaveAttribute('aria-current', 'page');
    expect(row().getByRole('link', { name: 'Try changes' })).toHaveAttribute('href', '/b/ours/plan/try');

    fireEvent.click(row().getByRole('link', { name: 'Next dollar' }));
    expect(await screen.findByText('Cash for 6 months of spending')).toBeInTheDocument();
    expect(row().getByRole('link', { name: 'Next dollar' })).toHaveAttribute('aria-current', 'page');

    fireEvent.click(row().getByRole('link', { name: 'How it works' }));
    expect(await screen.findByRole('heading', { name: 'What moves your plan most' })).toBeInTheDocument();

    fireEvent.click(row().getByRole('link', { name: 'Try changes' }));
    expect(await screen.findByTestId('lever-save_more')).toBeInTheDocument();
    expect(row().getByRole('link', { name: 'Try changes' })).toHaveAttribute('aria-current', 'page');

    fireEvent.click(row().getByRole('link', { name: 'Taxes' }));
    expect(await screen.findByRole('heading', { name: 'Taxes', level: 1 })).toBeInTheDocument();
    expect(row().getByRole('link', { name: 'Taxes' })).toHaveAttribute('aria-current', 'page');
  });

  it('shows the two ages with their years and a tooltip each', async () => {
    readyRoutes();
    openPlan('/plan');
    const comfortable = await screen.findByTestId('age-comfortable');
    expect(comfortable).toHaveTextContent('Comfortably at');
    expect(comfortable).toHaveTextContent('63');
    expect(comfortable).toHaveTextContent('in 2048');
    expect(screen.getByTestId('age-likely')).toHaveTextContent('60');
    expect(screen.getAllByRole('button', { name: 'What this means' }).length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText(/^Plan saved by Sam on Oct 1[23]/)).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 1, name: 'Plan' })).toHaveClass('sr-only');
  });

  it('a 503 shows the message and the last saved ages with their date', async () => {
    stubFetch({ '/api/plan': () => reply({ error: "The plan couldn't be worked out right now.",
      last_saved: { comfortable_age: 63, likely_age: 60, saved_at: '2026-10-12T19:00:00+00:00', saved_day: '2026-10-12' } }, 503) });
    openPlan('/plan');
    expect(await screen.findByRole('alert')).toHaveTextContent("The plan couldn't be worked out right now.");
    const last = screen.getByTestId('last-saved');
    expect(last).toHaveTextContent('Comfortably at 63 · Likely at 60');
    expect(last).toHaveTextContent('From the plan saved Oct 12');
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });
});

describe('Plan: setup unfinished', () => {
  const unfinished = () => stubFetch({
    '/api/plan/setup': { ready: false, setup: SETUP, names: NAMES }, '/api/plan': { setup: SETUP },
    '/api/accounts': { accounts: [] }, '/api/children': { children: [] },
  });

  it('ends with the not-advice line, set up or not', async () => {
    unfinished();
    const { unmount } = openPlan('/plan');
    await screen.findByRole('heading', { name: 'Required' });
    expect(screen.getByText(NOT_ADVICE)).toBeInTheDocument();
    unmount();
    readyRoutes();
    openPlan('/plan');
    await screen.findByTestId('age-comfortable');
    expect(screen.getByText(NOT_ADVICE)).toBeInTheDocument();
  });

  it('shows the setup checklist until setup is done', async () => {
    unfinished();
    openPlan('/plan');
    expect(await screen.findByRole('heading', { name: 'Required' })).toBeInTheDocument();
  });

  it('a deep link to Try changes while setup is unfinished shows the checklist, with only Overview and Taxes in the row', async () => {
    unfinished();
    openPlan('/plan/try');
    expect(await screen.findByRole('heading', { name: 'Required' })).toBeInTheDocument();
    expect(labels()).toEqual(['Overview', 'Taxes']);
    expect(row().getByRole('link', { name: 'Overview' })).toHaveAttribute('aria-current', 'page');
    expect(screen.queryByTestId('lever-save_more')).toBeNull();
  });

  it('a 409 while dragging sends you back to the setup checklist', async () => {
    const data = planFixture();
    let ready = true;
    stubFetch({
      '/api/plan/setup': { ready: false, setup: SETUP, names: data.names },
      '/api/plan': () => reply(ready ? data : { setup: SETUP }),
      '/api/facts': FACTS, '/api/children': CHILDREN, '/api/accounts': { accounts: [] },
      'POST /api/plan/impacts': { levers: data.levers, impacts: impactsFixture() },
      'POST /api/plan/preview': () => { ready = false; return reply({ error: 'Finish setting up the plan first.', setup: SETUP }, 409); },
    });
    openPlan('/plan/try');
    const lever = await screen.findByTestId('lever-save_more');
    fireEvent.click(within(lever).getByRole('button', { expanded: false }));
    fireEvent.change(screen.getByTestId('slider-save_more'), { target: { value: '50000' } });
    expect(await screen.findByRole('heading', { name: 'Required' })).toBeInTheDocument();
  });

  it('a 409 on Update the plan sends you back to the setup checklist', async () => {
    const data = planFixture();
    let ready = true;
    stubFetch({
      '/api/plan/setup': { ready: false, setup: SETUP, names: data.names },
      '/api/plan': () => reply(ready ? data : { setup: SETUP }),
      '/api/facts': FACTS, '/api/children': CHILDREN, '/api/accounts': { accounts: [] },
      'POST /api/plan/impacts': { levers: data.levers, impacts: impactsFixture() },
      'POST /api/plan/preview': { levers: data.levers, result: data.result },
      'POST /api/plan': () => { ready = false; return reply({ error: 'Finish setting up the plan first.', setup: SETUP }, 409); },
    });
    openPlan('/plan/try');
    const lever = await screen.findByTestId('lever-save_more');
    fireEvent.click(within(lever).getByRole('button', { expanded: false }));
    fireEvent.change(screen.getByTestId('slider-save_more'), { target: { value: '50000' } });
    fireEvent.click(await screen.findByRole('button', { name: 'Update the plan' }));
    expect(await screen.findByRole('heading', { name: 'Required' })).toBeInTheDocument();
    await waitFor(() => expect(labels()).toEqual(['Overview', 'Taxes']));
  });
});
