import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { callsTo, reply, stubFetch, wrap } from '../../test-utils';
import { today } from '../../lib/today';
import { CHILDREN, FACTS, impactsFixture, nextDollarFixture, planFixture } from './fixtures';
import type { PlanReady } from './api';
import { TryChanges } from './TryChanges';
import { clearKeptDraft } from './usePlanDraft';
import { setSavedNote } from './savedNote';

afterEach(() => { vi.unstubAllGlobals(); clearKeptDraft(); });

function routes(extra: Record<string, unknown> = {}) {
  const data = planFixture();
  return stubFetch({
    '/api/facts': FACTS, '/api/children': CHILDREN, '/api/accounts': { accounts: [] }, '/api/next-dollar': nextDollarFixture(),
    '/api/summary/history': { points: [] }, '/api/ss': { records: { jr: null, spouse: null } },
    'POST /api/plan/impacts': (_u: string, init: RequestInit) => reply({ levers: data.levers, impacts: impactsFixture(), _body: init.body }),
    'POST /api/plan/preview': (_u: string, init: RequestInit) => {
      const b = JSON.parse(init.body as string);
      return reply({ levers: { ...data.levers, assumed: { ...data.levers.assumed, ...b.assumed } },
        result: { ...data.result, comfortable_age: 62, likely_age: 59, comfortable_year: 2047, likely_year: 2044 } });
    },
    ...extra,
  });
}

async function openSaveMore() {
  const row = await screen.findByTestId('lever-save_more');
  fireEvent.click(within(row).getByRole('button', { expanded: false }));
  return row;
}

describe('Try changes: live levers', () => {
  it('ranks the rows by the first impacts answer', async () => {
    routes();
    wrap(<TryChanges data={planFixture()} />, '/plan/try');
    await waitFor(() => expect(screen.getAllByTestId(/^lever-/)[0]).toHaveAttribute('data-testid', 'lever-spending'));
  });

  it('a move sends only that field, once, 150 ms after the last change, and shows the new ages beside the old', async () => {
    const fetchMock = routes();
    wrap(<TryChanges data={planFixture()} />, '/plan/try');
    await openSaveMore();
    fireEvent.change(screen.getByTestId('slider-save_more'), { target: { value: '25000' } });
    fireEvent.change(screen.getByTestId('slider-save_more'), { target: { value: '75000' } });
    await waitFor(() => expect(screen.getByTestId('age-comfortable')).toHaveTextContent('62'));
    expect(screen.getByTestId('age-comfortable')).toHaveTextContent('was 63');
    const previews = callsTo(fetchMock, 'POST', '/api/plan/preview');
    expect(previews).toHaveLength(1);
    expect(previews[0].body).toEqual({ assumed: { save_more_monthly_cents: 75000 } });
    await waitFor(() => expect(callsTo(fetchMock, 'POST', '/api/plan/impacts').at(-1)!.body).toEqual({ assumed: { save_more_monthly_cents: 75000 } }));
    expect(screen.getByTestId('lever-save_more')).toHaveTextContent('Changed');
  });

  it('Try this moves the slider to the suggestion as an assumed value, and the row reads In use', async () => {
    const fetchMock = routes();
    wrap(<TryChanges data={planFixture()} />, '/plan/try');
    const row = await openSaveMore();
    fireEvent.click(within(row).getByRole('button', { name: 'Try this' }));
    await waitFor(() => expect(callsTo(fetchMock, 'POST', '/api/plan/preview')).toHaveLength(1));
    expect(callsTo(fetchMock, 'POST', '/api/plan/preview')[0].body).toEqual({ assumed: { save_more_monthly_cents: 50000 } });
    expect(screen.getByTestId('slider-save_more')).toHaveValue('50000');
    expect(row).toHaveTextContent('In use');
  });

  it('after Try this, a preview that suggests something else leaves the row In use at the saved suggestion', async () => {
    const data = planFixture();
    const moved = { ...data.levers, try: { ...data.levers.try, save_more_monthly_cents: 100000 } };
    routes({
      'POST /api/plan/preview': (_u: string, init: RequestInit) => reply({
        levers: { ...moved, assumed: { ...moved.assumed, ...JSON.parse(init.body as string).assumed } },
        result: { ...data.result, comfortable_age: 62 } }),
      'POST /api/plan/impacts': () => reply({ levers: moved, impacts: impactsFixture() }),
    });
    wrap(<TryChanges data={planFixture()} />, '/plan/try');
    const row = await openSaveMore();
    fireEvent.click(within(row).getByRole('button', { name: 'Try this' }));
    await waitFor(() => expect(screen.getByTestId('age-comfortable')).toHaveTextContent('62'));
    await waitFor(() => expect(row.querySelector('.lever__effect')).not.toHaveClass('is-pending'));
    expect(row).toHaveTextContent('In use');
    expect(screen.getByTestId('trythis-save_more')).toBeDisabled();
    expect(screen.getByTestId('slider-save_more')).toHaveValue('50000');
  });

  it('Use last year\'s amount shows the vault suggestion at once, its effect matches, and Try this moves the slider to it', async () => {
    const data = planFixture();
    const pinned = { ...data.levers, try: { ...data.levers.try, gift_cents: 1500000 }, pinned: ['gift' as const] };
    const vault = { ...data.levers, try: { ...data.levers.try, gift_cents: 1000000 } };
    routes({
      'POST /api/plan/impacts': (_u: string, init: RequestInit) => reply({ levers: (JSON.parse(init.body as string).pinned ?? ['gift']).includes('gift') ? pinned : vault, impacts: impactsFixture() }),
    });
    wrap(<TryChanges data={{ ...data, levers: pinned }} />, '/plan/try');
    const row = await screen.findByTestId('lever-gift');
    fireEvent.click(within(row).getByRole('button', { expanded: false }));
    expect(row).toHaveTextContent('Try $15,000 a year');
    fireEvent.click(within(row).getByRole('button', { name: 'Change suggestion' }));
    fireEvent.click(within(row).getByRole('button', { name: "Use last year's amount" }));
    await waitFor(() => expect(row).toHaveTextContent('Your gift last year was $10,000'));
    await waitFor(() => expect(row.querySelector('.lever__effect')).not.toHaveClass('is-pending'));
    expect(row).not.toHaveTextContent('$15,000');
    fireEvent.click(within(row).getByRole('button', { name: 'Done' }));
    fireEvent.click(within(row).getByRole('button', { name: 'Try this' }));
    await waitFor(() => expect(screen.getByTestId('slider-gift')).toHaveValue('1000000'));
  });

  it('Change suggestion asks only for impacts, with every field of that lever', async () => {
    const fetchMock = routes();
    wrap(<TryChanges data={planFixture()} />, '/plan/try');
    const row = await screen.findByTestId('lever-consulting');
    fireEvent.click(within(row).getByRole('button', { expanded: false }));
    fireEvent.click(within(row).getByRole('button', { name: 'Change suggestion' }));
    fireEvent.change(screen.getByTestId('suggest-consulting'), { target: { value: '2400000' } });
    await waitFor(() => expect(callsTo(fetchMock, 'POST', '/api/plan/impacts').at(-1)!.body)
      .toEqual({ try: { consulting_cents: 2400000, consulting_until_age: 60 } }));
    expect(callsTo(fetchMock, 'POST', '/api/plan/preview')).toHaveLength(0);
  });
});

describe('Try changes: saving and errors', () => {
  it('Update the plan saves the delta, and the confirmation stays', async () => {
    const data = planFixture();
    const fetchMock = routes({
      'POST /api/plan': (_u: string, init: RequestInit) => reply({
        plan: { ...data.plan!, id: 8, saved_at: new Date().toISOString(), saved_day: today(), saved_by: { id: 1, name: 'JR' } },
        levers: { ...data.levers, assumed: { ...data.levers.assumed, ...JSON.parse(init.body as string).assumed } },
        result: { ...data.result, comfortable_age: 62 },
      }, 201),
    });
    wrap(<TryChanges data={data} />, '/plan/try');
    await openSaveMore();
    fireEvent.change(screen.getByTestId('slider-save_more'), { target: { value: '50000' } });
    fireEvent.click(await screen.findByRole('button', { name: 'Update the plan' }));
    expect(await screen.findByTestId('savebar-done')).toHaveTextContent('Plan updated.');
    expect(callsTo(fetchMock, 'POST', '/api/plan').filter((c) => c.url === '/api/plan')[0].body).toEqual({ assumed: { save_more_monthly_cents: 50000 } });
  });

  it('a move made while the save is in flight is kept, and only the saved lever is cleared', async () => {
    const data = planFixture();
    let finish!: () => void;
    const fetchMock = routes({
      'POST /api/plan': (_u: string, init: RequestInit) => new Promise<Response>((resolve) => { finish = () => resolve(reply({
        plan: { ...data.plan!, id: 8, saved_at: new Date().toISOString(), saved_day: today(), saved_by: { id: 1, name: 'JR' } },
        levers: { ...data.levers, assumed: { ...data.levers.assumed, ...JSON.parse(init.body as string).assumed } },
        result: { ...data.result, comfortable_age: 62 },
      }, 201)); }),
    });
    wrap(<TryChanges data={data} />, '/plan/try');
    await openSaveMore();
    fireEvent.change(screen.getByTestId('slider-save_more'), { target: { value: '50000' } });
    fireEvent.click(await screen.findByRole('button', { name: 'Update the plan' }));
    await waitFor(() => expect(callsTo(fetchMock, 'POST', '/api/plan').filter((c) => c.url === '/api/plan')).toHaveLength(1));
    fireEvent.change(screen.getByTestId('slider-save_more'), { target: { value: '75000' } });
    finish();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Update the plan' })).not.toHaveAttribute('aria-busy'));
    expect(screen.getByTestId('slider-save_more')).toHaveValue('75000');
    expect(screen.getByTestId('savebar')).toBeInTheDocument();
    expect(screen.getByTestId('lever-save_more')).toHaveTextContent('Changed');
    await waitFor(() => expect(callsTo(fetchMock, 'POST', '/api/plan/preview').at(-1)!.body).toEqual({ assumed: { save_more_monthly_cents: 75000 } }));
  });

  it('Reset returns to the saved plan without asking the server', async () => {
    const fetchMock = routes();
    wrap(<TryChanges data={planFixture()} />, '/plan/try');
    await openSaveMore();
    fireEvent.change(screen.getByTestId('slider-save_more'), { target: { value: '50000' } });
    await waitFor(() => expect(screen.getByTestId('age-comfortable')).toHaveTextContent('62'));
    const before = callsTo(fetchMock, 'POST', '/api/plan/preview').length;
    fireEvent.click(screen.getByRole('button', { name: 'Reset' }));
    expect(screen.getByTestId('age-comfortable')).toHaveTextContent('63');
    expect(screen.queryByTestId('savebar')).toBeNull();
    expect(screen.getByTestId('lever-save_more')).not.toHaveTextContent('Changed');
    expect(callsTo(fetchMock, 'POST', '/api/plan/preview')).toHaveLength(before);
  });

  it('a 503 after a good preview shows the saved ages, not the earlier preview', async () => {
    const data = planFixture();
    let n = 0;
    routes({ 'POST /api/plan/preview': () => (++n === 1
      ? reply({ levers: data.levers, result: { ...data.result, comfortable_age: 61, likely_age: 58 } })
      : reply({ error: "The plan couldn't be worked out right now.",
        last_saved: { comfortable_age: 63, likely_age: 60, saved_at: '2026-10-12T19:00:00+00:00', saved_day: '2026-10-12' } }, 503)) });
    wrap(<TryChanges data={planFixture()} />, '/plan/try');
    await openSaveMore();
    fireEvent.change(screen.getByTestId('slider-save_more'), { target: { value: '25000' } });
    await waitFor(() => expect(screen.getByTestId('age-comfortable')).toHaveTextContent('61'));
    fireEvent.change(screen.getByTestId('slider-save_more'), { target: { value: '75000' } });
    expect(await screen.findByRole('alert')).toHaveTextContent("The plan couldn't be worked out right now.");
    const age = screen.getByTestId('age-comfortable');
    expect(age).toHaveTextContent('63');
    expect(age).not.toHaveTextContent('61');
    expect(age).not.toHaveTextContent('was');
  });

  it('a 503 while dragging keeps the last saved ages with their date', async () => {
    routes({ 'POST /api/plan/preview': () => reply({ error: "The plan couldn't be worked out right now.",
      last_saved: { comfortable_age: 63, likely_age: 60, saved_at: '2026-10-12T19:00:00+00:00', saved_day: '2026-10-12' } }, 503) });
    wrap(<TryChanges data={planFixture()} />, '/plan/try');
    await openSaveMore();
    fireEvent.change(screen.getByTestId('slider-save_more'), { target: { value: '50000' } });
    expect(await screen.findByRole('alert')).toHaveTextContent("The plan couldn't be worked out right now.");
    expect(screen.getByTestId('last-saved')).toHaveTextContent('Comfortably at 63 · Likely at 60');
    expect(screen.getByTestId('age-comfortable')).toHaveTextContent('63');
  });
});

describe('Try changes: new plan inputs', () => {
  function rendered(data: PlanReady) {
    const r = wrap(<TryChanges data={data} />, '/plan/try');
    const again = (next: PlanReady) => r.rerender(
      <QueryClientProvider client={r.qc}><MemoryRouter initialEntries={['/plan/try']}><TryChanges data={next} /></MemoryRouter></QueryClientProvider>);
    return again;
  }
  const refetched = () => {
    const next = planFixture();
    next.result.input_digest = 'def456';
    next.levers.try.save_more_monthly_cents = 70000;
    return next;
  };

  it('a clean plan asks for impacts again when the inputs change, and shows the new suggestion', async () => {
    const fetchMock = routes();
    const again = rendered(planFixture());
    await waitFor(() => expect(callsTo(fetchMock, 'POST', '/api/plan/impacts')).toHaveLength(1));
    again(refetched());
    await waitFor(() => expect(callsTo(fetchMock, 'POST', '/api/plan/impacts')).toHaveLength(2));
    const row = await openSaveMore();
    expect(row).toHaveTextContent('Try $700 a month');
    expect(row).not.toHaveTextContent('Try $500 a month');
  });

  it('a moved lever asks for a new preview when the inputs change', async () => {
    const fetchMock = routes();
    const again = rendered(planFixture());
    await openSaveMore();
    fireEvent.change(screen.getByTestId('slider-save_more'), { target: { value: '25000' } });
    await waitFor(() => expect(screen.getByTestId('age-comfortable')).toHaveTextContent('62'));
    expect(callsTo(fetchMock, 'POST', '/api/plan/preview')).toHaveLength(1);
    again(refetched());
    await waitFor(() => expect(callsTo(fetchMock, 'POST', '/api/plan/preview')).toHaveLength(2));
    expect(callsTo(fetchMock, 'POST', '/api/plan/preview')[1].body).toEqual({ assumed: { save_more_monthly_cents: 25000 } });
  });
});

describe('Try changes: the saved-plan note belongs to one plan', () => {
  const note = (planId: number) => ({ planId, at: new Date().toISOString(), day: today(), by: 'JR', previous: null });

  it('shows the note for the plan on screen', async () => {
    routes();
    setSavedNote(note(7));
    wrap(<TryChanges data={planFixture()} />, '/plan/try');
    expect(await screen.findByTestId('savebar-done')).toHaveTextContent('Plan updated.');
  });

  it('does not show a note left from another plan', async () => {
    routes();
    setSavedNote(note(3));
    wrap(<TryChanges data={planFixture()} />, '/plan/try');
    await screen.findByTestId('lever-spending');
    expect(screen.queryByTestId('savebar-done')).not.toBeInTheDocument();
  });

  it('drops the note when a refetch brings in a different plan', async () => {
    routes();
    setSavedNote(note(7));
    const { qc, rerender } = wrap(<TryChanges data={planFixture()} />, '/plan/try');
    expect(await screen.findByTestId('savebar-done')).toBeInTheDocument();
    const other = planFixture();
    other.plan = { ...other.plan!, id: 9, saved_by: { id: 3, name: 'Sam' } };
    rerender(<QueryClientProvider client={qc}><MemoryRouter initialEntries={['/plan/try']}><TryChanges data={other} /></MemoryRouter></QueryClientProvider>);
    await waitFor(() => expect(screen.queryByTestId('savebar-done')).not.toBeInTheDocument());
  });
});

describe('Try changes: the pinned bar, and nothing else drawn (plan subpages, Try changes and speed)', () => {
  it('pins the Comfortably age with its was, and the amount and odds, and a lever move updates them', async () => {
    routes();
    const { container } = wrap(<TryChanges data={planFixture()} />, '/plan/try');
    const bar = screen.getByTestId('trybar');
    expect(within(bar).getByTestId('age-comfortable')).toHaveTextContent('Comfortably at63');
    expect(bar).toHaveTextContent("$7,400 a month in today's dollars · the money lasts in 9 of 10 market histories");
    await openSaveMore();
    fireEvent.change(screen.getByTestId('slider-save_more'), { target: { value: '50000' } });
    await waitFor(() => expect(within(bar).getByTestId('age-comfortable')).toHaveTextContent('62'));
    expect(bar).toHaveTextContent('was 63');
    expect(container.querySelector('svg.chart')).toBeNull();
    expect(screen.queryByRole('heading', { name: 'Money for retirement, year by year' })).toBeNull();
    expect(screen.queryByTestId('big-costs')).toBeNull();
    expect(screen.queryByRole('heading', { name: 'Next dollar' })).toBeNull();
    expect(screen.queryByRole('heading', { name: "Your money, by how it's taxed" })).toBeNull();
  });

  it('says the amount alone when no age works by 70', () => {
    routes();
    const data = planFixture();
    data.result.comfortable_age = null;
    wrap(<TryChanges data={data} />, '/plan/try');
    expect(screen.getByTestId('age-comfortable')).toHaveTextContent('Not by 70');
    expect(screen.getByTestId('trybar')).not.toHaveTextContent('market histories');
  });

  it('shows the business profit line above the levers', async () => {
    routes();
    const data = planFixture();
    data.business = { year: 2026, ytd_cents: 600_000, low_cents: 1_000_000, high_cents: 1_600_000, mid_cents: 1_300_000, entered: true,
      cash_ytd_cents: 600_000, cash_low_cents: 1_000_000, cash_high_cents: 1_600_000, cash_mid_cents: 1_300_000, ask_expect: false };
    wrap(<TryChanges data={data} />, '/plan/try');
    const line = screen.getByText(/^Business profit for 2026/);
    const lever = await screen.findByTestId('lever-save_more');
    expect(line.compareDocumentPosition(lever) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('a move is still there when you come back from another Plan page', async () => {
    routes();
    const first = wrap(<TryChanges data={planFixture()} />, '/plan/try');
    await openSaveMore();
    fireEvent.change(screen.getByTestId('slider-save_more'), { target: { value: '50000' } });
    await waitFor(() => expect(screen.getByTestId('age-comfortable')).toHaveTextContent('62'));
    first.unmount();
    wrap(<TryChanges data={planFixture()} />, '/plan/try');
    expect(screen.getByTestId('savebar')).toHaveTextContent('Plan not updated');
    expect(await screen.findByTestId('lever-save_more')).toHaveTextContent('Changed');
    await waitFor(() => expect(screen.getByTestId('age-comfortable')).toHaveTextContent('62'));
  });
});
