import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useFakeTimers } from '../../../test-clock';
import { callsTo, renderAt, reply, stubFetch } from '../../../test-utils';
import { LiveRegions } from '../../../ui/Live';
import { HEATER_DETAIL, LIST, PROPTAX_DETAIL } from './fixtures';
import { AddSetAside } from './AddSetAside';
import { PREVIEW_MS } from './SetAsideForm';

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });
// Every preview waits PREVIEW_MS after the last change: the whole file drives it on fake time (flaky-test audit W3).
beforeEach(() => { useFakeTimers(); });
const ACCOUNTS = { accounts: [
  { id: 21, name: 'House fund', type: 'savings', owner: 'joint', source: 'manual', institution: 'Manual', is_active: true, balance_cents: 410_000, as_of: '2026-10-24', stale_days: 1 },
  { id: 22, name: 'Kids fund', type: 'savings', owner: 'joint', source: 'manual', institution: 'Manual', is_active: true, balance_cents: 60_000, as_of: '2026-10-24', stale_days: 1 },
] };
const EST = { item: 'water_heater', label: 'Water heater', installed_year: 2014, model_year: null, miles: null, age: 12, miles_per_year: null, lifespan: [10, 15], lifespan_miles: null,
  range: [2024, 2029], decided_by: 'age', planned_year: 2028, due_year: 2028, due_month: 12, cost_range: [150_000, 300_000], replacement_cents: 225_000, sale_cents: 0,
  amount_cents: 225_000, months: 26, first_month: '2026-11-01', monthly_cents: 8_654, sources: ['Typical water heater lifespan', 'Typical water heater cost'], to_check: false, cost_to_check: true,
  kind: 'one_off', next_due: '2028-12-01', is_estimate: true };
const CAR = { ...EST, item: 'car', label: 'Car', installed_year: null, model_year: 2016, miles: 118_000, age: 10, miles_per_year: 11_800, lifespan: [12, 15], lifespan_miles: [150_000, 200_000],
  range: [2028, 2031], planned_year: 2030, due_year: 2030, cost_range: [2_000_000, 3_600_000], replacement_cents: 2_800_000, sale_cents: 644_000, amount_cents: 2_156_000, months: 50,
  monthly_cents: 43_120, next_due: '2030-12-01', to_check: true, cost_to_check: false, sources: ['Typical car lifespan', 'Typical car cost'] };

type Body = Record<string, unknown>;
/** Stands in for the server's POST /setasides/estimate: the figures are canned per request, the web must not work them out. */
const server = (_url: string, init: RequestInit) => {
  const b = JSON.parse(String(init.body)) as Body;
  if (b.estimate && b.kind === 'one_off' || (b.item !== undefined && b.kind === undefined)) {
    const car = (b.estimate as Body | undefined)?.item === 'car' || b.item === 'car';
    const base = car ? CAR : EST;
    const amount = typeof b.amount_cents === 'number' ? b.amount_cents : base.amount_cents;
    return reply({ ...base, amount_cents: amount, monthly_cents: Math.round(amount / base.months), due_year: b.due_year ?? base.due_year, next_due: `${b.due_year ?? base.due_year}-12-01` });
  }
  if (b.kind === 'one_off') return reply({ kind: 'one_off', amount_cents: b.amount_cents, monthly_cents: 11_400, first_month: '2026-11-01', next_due: '2030-06-01', months: 44, is_estimate: false });
  if (b.kind === 'twice_yearly') return reply({ kind: 'twice_yearly', amount_cents: b.amount_cents, monthly_cents: 7_500, yearly_cents: 90_000, first_month: '2026-11-01', next_due: '2027-01-15', months: 3 });
  return reply({ kind: 'yearly', amount_cents: b.amount_cents, monthly_cents: 11_500, yearly_cents: b.amount_cents, first_month: '2026-11-01', next_due: '2027-05-12', months: 7 });
};
const routes = { '/api/setasides': LIST, '/api/accounts': ACCOUNTS, 'POST /api/setasides/estimate': server };
const saves = (f: ReturnType<typeof stubFetch>) => callsTo(f, 'POST', '/api/setasides').filter((c) => !c.url.includes('/estimate'));
const settle = () => act(async () => { await vi.advanceTimersByTimeAsync(PREVIEW_MS); });

describe('Add a set-aside', () => {
  // Review of #51: the preview appears with its words, so it is said through the page's region, not as a live region.
  it('the preview is said when it appears', async () => {
    stubFetch(routes);
    renderAt(<LiveRegions><AddSetAside /></LiveRegions>, {}, '/spending/setasides/new?name=Home+insurance&kind=yearly&amount=138000&due_month=5&due_day=12');
    await waitFor(() => expect(screen.getByTestId('sa-preview')).toHaveTextContent('From November · next due May 12, 2027'));
    expect(screen.getByTestId('sa-preview')).not.toHaveAttribute('aria-live');
    expect(screen.getByTestId('live-said')).toHaveTextContent('From November · next due May 12, 2027');
  });

  it('a yearly cost from a suggestion is filled in, previews, and saves, then opens its page', async () => {
    const fetchMock = stubFetch({ ...routes, 'POST /api/setasides': { ...PROPTAX_DETAIL, id: 9 } });
    const { location } = renderAt(<AddSetAside />, {}, '/spending/setasides/new?name=Home+insurance&kind=yearly&amount=138000&due_month=5&due_day=12&payee_key=BIG+MUTUAL&category=insurance');
    expect(await screen.findByLabelText('What is it')).toHaveValue('Home insurance');
    expect(screen.getByRole('heading', { level: 1, name: 'Add a set-aside' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: 'Every year' })).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByLabelText('How much')).toHaveValue('1,380');
    expect(screen.getByRole('combobox', { name: 'Due in' })).toHaveValue('5');
    expect(screen.getByLabelText('Matches payments to')).toHaveValue('BIG MUTUAL · Insurance');
    await waitFor(() => expect(screen.getByTestId('sa-preview')).toHaveTextContent('From November · next due May 12, 2027'));
    expect(screen.getByTestId('sa-preview')).toHaveTextContent('$115 a month');
    expect(callsTo(fetchMock, 'POST', '/api/setasides/estimate').at(-1)?.body).toMatchObject({ kind: 'yearly', amount_cents: 138_000, due_month: 5, due_day: 12 });
    fireEvent.change(screen.getByLabelText('Put away in'), { target: { value: '21' } });
    expect(screen.getByText(/House fund is shared with Property tax, Water heater and Roof; the soonest due gets the money first\./)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Set it aside' }));
    await waitFor(() => expect(saves(fetchMock)).toHaveLength(1));
    expect(saves(fetchMock)[0].body).toEqual({ name: 'Home insurance', kind: 'yearly', amount_cents: 138_000, due_month: 5, due_day: 12, second_month: null,
      second_day: null, due_year: null, account_id: 21, payee_key: 'BIG MUTUAL', category: 'insurance', estimate: null, notes: null });
    await waitFor(() => expect(location.current.pathname).toBe('/b/ours/spending/setasides/9'));
  });

  it('twice a year asks for two dates and shows the yearly figure the server worked out', async () => {
    stubFetch(routes);
    renderAt(<AddSetAside />, {}, '/spending/setasides/new');
    fireEvent.click(await screen.findByRole('radio', { name: 'Twice a year' }));
    fireEvent.change(screen.getByLabelText('What is it'), { target: { value: 'HOA dues' } });
    fireEvent.change(screen.getByLabelText('Each installment'), { target: { value: '450' } });
    fireEvent.change(screen.getByLabelText('First due: month'), { target: { value: '1' } });
    fireEvent.change(screen.getByLabelText('First due: day'), { target: { value: '15' } });
    fireEvent.change(screen.getByLabelText('Second due: month'), { target: { value: '7' } });
    fireEvent.change(screen.getByLabelText('Second due: day'), { target: { value: '15' } });
    expect(await screen.findByText('$900 a year · $75 a month')).toBeInTheDocument();
    expect(screen.getByTestId('sa-preview')).toHaveTextContent('From November · next due Jan 15, 2027');
    fireEvent.change(screen.getByLabelText('Second due: month'), { target: { value: '1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Set it aside' }));
    expect(await screen.findByText('The two due dates must differ.')).toBeInTheDocument();
  });

  it('a one-off estimates from its age, fills the amount and the year, and explains itself', async () => {
    const fetchMock = stubFetch({ ...routes, 'POST /api/setasides': HEATER_DETAIL });
    renderAt(<AddSetAside />, {}, '/spending/setasides/new');
    fireEvent.click(await screen.findByRole('radio', { name: 'Once' }));
    fireEvent.change(screen.getByLabelText('What is it'), { target: { value: 'Water heater' } });
    fireEvent.click(screen.getByRole('button', { name: 'Water heater', pressed: false }));
    fireEvent.change(screen.getByLabelText('Installed in'), { target: { value: '2014' } });
    await settle();
    await waitFor(() => expect(screen.getByTestId('sa-est')).toHaveTextContent('Likely by 2028 · $87 a month'));
    const est = screen.getByTestId('sa-est');
    expect(callsTo(fetchMock, 'POST', '/api/setasides/estimate').at(-1)?.body).toMatchObject({ kind: 'one_off', estimate: { item: 'water_heater', installed_year: 2014, model_year: null, miles: null } });
    expect(est).toHaveTextContent('A water heater usually lasts 10 to 15 years, so 2024 to 2029 from 2014. Steward plans for Dec 2028, a year before the end: $2,250 over the 26 months from November.');
    expect(est).toHaveTextContent('Typical water heater lifespan');
    expect(est).toHaveTextContent('to be checked');
    expect(screen.getByLabelText('About how much')).toHaveValue('2,250');
    expect(screen.getByText('Typical: $1,500 to $3,000.')).toBeInTheDocument();
    expect(screen.getByTestId('sa-preview')).toHaveTextContent('From November · planned for Dec 2028');
    fireEvent.change(screen.getByLabelText('About how much'), { target: { value: '2,200' } });
    await settle();
    await waitFor(() => expect(screen.getByTestId('sa-est')).toHaveTextContent('Likely by 2028 · $85 a month'));
    fireEvent.click(screen.getByRole('button', { name: 'Change the year' }));
    fireEvent.change(screen.getByLabelText('Year'), { target: { value: '2029' } });
    await settle();
    await waitFor(() => expect(screen.getByTestId('sa-preview')).toHaveTextContent('planned for Dec 2029'));
    fireEvent.click(screen.getByRole('button', { name: 'Set it aside' }));
    await waitFor(() => expect(saves(fetchMock)).toHaveLength(1));
    expect(saves(fetchMock)[0].body).toMatchObject({ kind: 'one_off', amount_cents: 220_000, due_year: 2029, due_month: 12, estimate: { item: 'water_heater', installed_year: 2014, model_year: null, miles: null } });
  });

  it('a car sends its model year and miles, and shows the replacement less the sale value the server worked out', async () => {
    const fetchMock = stubFetch(routes);
    renderAt(<AddSetAside />, {}, '/spending/setasides/new');
    fireEvent.click(await screen.findByRole('radio', { name: 'Once' }));
    fireEvent.click(screen.getByRole('button', { name: 'Car', pressed: false }));
    fireEvent.change(screen.getByLabelText('Model year'), { target: { value: '2016' } });
    fireEvent.change(screen.getByLabelText('Miles'), { target: { value: '118,000' } });
    await settle();
    await waitFor(() => expect(screen.getByTestId('sa-est')).toHaveTextContent('Likely by 2030 · $431 a month'));
    const est = screen.getByTestId('sa-est');
    expect(callsTo(fetchMock, 'POST', '/api/setasides/estimate').at(-1)?.body).toMatchObject({ estimate: { item: 'car', installed_year: null, model_year: 2016, miles: 118_000 } });
    expect(est).toHaveTextContent('A car usually goes 12 to 15 years or 150,000 to 200,000 miles, whichever comes first. From 2016 that is 2028 to 2031; at 11,800 miles a year it would pass 200,000 miles later, so age decides. Steward plans for Dec 2030, a year before the end.');
    expect(est).toHaveTextContent('A replacement, typical for this kind of car$28,000');
    expect(est).toHaveTextContent('Less what this one should sell for in 2030−$6,440');
    expect(est).toHaveTextContent('Set aside, over 50 months$21,560');
    expect(est).toHaveTextContent('Lifespan to be checked');
    expect(screen.getByText('About 11,800 a year.')).toBeInTheDocument();
  });

  it('Something else has no estimate: the year and the amount are typed, and the server previews them', async () => {
    stubFetch(routes);
    renderAt(<AddSetAside />, {}, '/spending/setasides/new');
    fireEvent.click(await screen.findByRole('radio', { name: 'Once' }));
    fireEvent.click(screen.getByRole('button', { name: 'Something else' }));
    expect(screen.queryByTestId('sa-est')).toBeNull();
    fireEvent.change(screen.getByLabelText('What is it'), { target: { value: 'Piano' } });
    fireEvent.change(screen.getByLabelText('About how much'), { target: { value: '5,000' } });
    fireEvent.change(screen.getByLabelText('Year'), { target: { value: '2030' } });
    fireEvent.change(screen.getByLabelText('Month'), { target: { value: '6' } });
    await settle();
    await waitFor(() => expect(screen.getByTestId('sa-preview')).toHaveTextContent('From November · planned for Jun 2030'));
    expect(screen.getByTestId('sa-preview')).toHaveTextContent('$114 a month');
    expect(screen.queryByTestId('sa-est')).toBeNull();
  });

  it('a refusal from the server shows under the form', async () => {
    stubFetch({ ...routes, 'POST /api/setasides': () => reply({ error: 'Choose a checking or savings account.' }, 422) });
    renderAt(<AddSetAside />, {}, '/spending/setasides/new?name=X&kind=yearly&amount=10000&due_month=1&due_day=1');
    fireEvent.click(await screen.findByRole('button', { name: 'Set it aside' }));
    expect(await screen.findByText('Choose a checking or savings account.')).toBeInTheDocument();
  });

  it('a hostile address falls back to the empty defaults and posts nothing', async () => {
    const fetchMock = stubFetch(routes);
    renderAt(<AddSetAside />, {}, '/spending/setasides/new?kind=bogus&amount=-5&due_month=99&due_day=zz&second_month=0&payee_key=&category=');
    expect(await screen.findByLabelText('What is it')).toHaveValue('');
    expect(screen.getByRole('radio', { name: 'Every year' })).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByLabelText('How much')).toHaveValue('');
    expect(screen.getByRole('combobox', { name: 'Due in' })).toHaveValue('10');
    expect(screen.getByLabelText('Matches payments to')).toHaveValue('');
    fireEvent.click(screen.getByRole('button', { name: 'Set it aside' }));
    expect(await screen.findByText('Give it a name of up to 120 characters.')).toBeInTheDocument();
    expect(saves(fetchMock)).toHaveLength(0);
  });

  it('an amount above the form cap is dropped', async () => {
    stubFetch(routes);
    renderAt(<AddSetAside />, {}, '/spending/setasides/new?name=X&amount=100000001');
    expect(await screen.findByLabelText('How much')).toHaveValue('');
  });

  it('a viewer is sent back to the list', async () => {
    stubFetch(routes);
    const { location } = renderAt(<AddSetAside />, { role: 'viewer' }, '/spending/setasides/new');
    await waitFor(() => expect(location.current.pathname).toBe('/b/ours/spending/setasides'));
  });
});
