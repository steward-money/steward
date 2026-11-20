import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useFakeTimers } from '../../../test-clock';
import { callsTo, reply, stubFetch, unbook, type Route } from '../../../test-utils';
import { ME, atProfile, atProfileLive, profileState } from '../fixtures';

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });
beforeEach(() => { useFakeTimers(); });
const loan = (over = {}) => ({ account_id: 2, name: 'Chase mortgage', balance_cents: 57_130_000, as_of: '2026-10-01', manual: false,
  institution: 'Chase', rate_bp: 287, payment_cents: null, payoff_date: null, detected: null, ...over });
const home = (over = {}) => ({ owns_home: true, where_you_live: 1, properties: [{ account_id: 1, name: 'Home', value_cents: 125_000_000, as_of: '2026-10-01', mortgage: loan() }],
  cars: [], loans: [], mortgages: [], ...over });
const base = (h: Route) => ({ '/api/profile': profileState(), '/api/auth/me': ME, '/api/profile/home': h,
  'POST /api/facts': () => reply({ undo: 'f' }, 201), 'PATCH /api/accounts': () => reply({ id: 1, undo: 'a' }), 'PUT /api/profile/home/where': () => reply({ ...home(), undo: 'w' }) });

describe('Home and cars', () => {
  it('No saves home.owns, and an open mortgage says to close it or choose Yes', async () => {
    const fetchMock = stubFetch(base(home({ owns_home: false })));
    atProfile('/profile/home/own');
    expect(await screen.findByRole('link', { name: 'Chase mortgage is still open. Close it in Accounts, or choose Yes.' })).toHaveAttribute('href', '/b/ours/accounts/2');
    fireEvent.click(screen.getByRole('radio', { name: 'Yes' }));
    await waitFor(() => expect(callsTo(fetchMock, 'POST', '/api/facts')[0]?.body).toMatchObject({ key: 'home.owns', value: 'yes' }));
  });

  it('No skips Homes and Mortgages: Next from Your home goes to Cars', async () => {
    stubFetch({ ...base(home({ owns_home: false })), 'PUT /api/profile/sections/home/resume': () => reply(profileState()) });
    const { location } = atProfile('/profile/home/own');
    await screen.findByRole('radio', { name: 'No' });
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    await waitFor(() => expect(location.current.pathname).toBe('/b/ours/profile/home/cars'));
  });

  it('with two homes, Where you live is a choice, and choosing one moves the link', async () => {
    const two = home({ properties: [...home().properties, { account_id: 3, name: 'Cabin', value_cents: 40_000_000, as_of: '2026-10-01', mortgage: null }] });
    const fetchMock = stubFetch(base(two));
    atProfile('/profile/home/homes');
    expect(await screen.findByText('The Plan and Care use where you live.')).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole('radio', { name: 'Where you live' })[1]);
    await waitFor(() => expect(callsTo(fetchMock, 'PUT', '/api/profile/home/where')[0]?.body).toEqual({ account_id: 3 }));
    await act(async () => {});
  });

  it('the payment is prefilled from the transfer with its source, says when it varied, and the source goes once typed over', async () => {
    const detected = { posted_at: '2026-10-01', amount_cents: 268_000, varied: { lo: 268_000, hi: 318_000 } };
    stubFetch(base(home({ properties: [{ ...home().properties[0], mortgage: loan({ detected }) }] })));
    atProfile('/profile/home/mortgages');
    expect(await screen.findByLabelText('Monthly payment')).toHaveValue('2,680');
    expect(screen.getByText(/^From your Oct 1 transfer to Chase mortgage\. It varied from \$2,680 to \$3,180\.$/)).toBeInTheDocument();
    expect(screen.getByText("Principal and interest only. Property tax and insurance don't end with the mortgage.")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Monthly payment'), { target: { value: '2700' } });
    await waitFor(() => expect(screen.queryByText(/^From your Oct 1 transfer/)).toBeNull());
  });

  it('another home\'s mortgage saves only its rate and says its payments stay in spending', async () => {
    const two = home({ properties: [...home().properties, { account_id: 3, name: 'Cabin', value_cents: 40_000_000, as_of: '2026-10-01',
      mortgage: loan({ account_id: 4, name: 'Cabin loan' }) }] });
    stubFetch(base(two));
    atProfile('/profile/home/mortgages');
    expect(await screen.findByText('Its payments stay in your spending in the Plan.')).toBeInTheDocument();
    expect(screen.getAllByLabelText('Monthly payment')).toHaveLength(1);
  });

  it('cars: Yes shows a row with the Kelley Blue Book link and a loan select', async () => {
    stubFetch(base(home({ cars: [{ account_id: 5, name: 'Subaru', value_cents: 2_000_000, as_of: '2026-10-01', loan: null }],
      loans: [loan({ account_id: 6, name: 'Auto loan' })] })));
    atProfile('/profile/home/cars');
    expect(await screen.findByRole('link', { name: 'Look it up on Kelley Blue Book' })).toHaveAttribute('target', '_blank');
    expect(screen.getByRole('option', { name: 'Auto loan' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('radio', { name: 'No' }));
    expect(screen.queryByRole('link', { name: 'Look it up on Kelley Blue Book' })).toBeNull();
  });

  it('a viewer reads the card: no select, radio, button, input or Add, only the back, Redfin and Kelley Blue Book links', async () => {
    const fetchMock = stubFetch(base(home({
      properties: [{ ...home().properties[0], mortgage: loan({ payment_cents: 268_000, payoff_date: '2030-01-01' }) },
        { account_id: 3, name: 'Cabin', value_cents: 40_000_000, as_of: '2026-10-01', mortgage: null }],
      cars: [{ account_id: 5, name: 'Subaru', value_cents: 2_000_000, as_of: '2026-10-01', loan: loan({ account_id: 6, name: 'Auto loan' }) }],
      loans: [loan({ account_id: 7, name: 'Boat loan' })] })));
    atProfile('/profile/home', { role: 'viewer' });
    const payoff = await screen.findByTestId('payoff');
    expect(payoff).toHaveTextContent('Paid off by');
    expect(screen.getByText('Auto loan')).toBeInTheDocument();
    expect(screen.getByText('Any cars?').nextElementSibling).toHaveTextContent('Yes');
    expect(document.querySelectorAll('select, input, textarea, button')).toHaveLength(0);
    expect(screen.queryByRole('combobox')).toBeNull();
    expect(screen.queryByRole('radio')).toBeNull();
    expect(screen.queryByRole('button')).toBeNull();
    expect(screen.getAllByRole('link').map((l) => l.textContent)).toEqual(['Profile', 'Look it up on Redfin', 'Look it up on Kelley Blue Book']);
    expect(fetchMock.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method && (init as RequestInit).method !== 'GET')).toHaveLength(0);
  });

  const cabin = (over = {}) => ({ account_id: 3, name: 'Cabin', value_cents: 40_000_000, as_of: '2026-10-01', mortgage: null, ...over });
  const undoCalls = (fetchMock: ReturnType<typeof stubFetch>) => fetchMock.mock.calls.map(([u]) => unbook(String(u))).filter((u) => u.startsWith('/api/undo/'));
  const toast = () => document.querySelector('.undo-toast') as HTMLElement;
  const clickUndo = () => fireEvent.click(within(toast()).getByRole('button', { name: 'Undo' }));
  const undoRoutes = { 'POST /api/undo/w': () => reply({}), 'POST /api/undo/a': () => reply({}), 'POST /api/undo/f': () => reply({}), 'POST /api/undo/add': () => reply({}), 'POST /api/undo/link': () => reply({}) };

  it('choosing where you live offers "Where you live is now Cabin · Undo", and Undo posts the token', async () => {
    const fetchMock = stubFetch({ ...base(home({ properties: [...home().properties, cabin()] })), ...undoRoutes });
    atProfile('/profile/home/homes');
    fireEvent.click((await screen.findAllByRole('radio', { name: 'Where you live' }))[1]);
    await waitFor(() => expect(toast()).not.toBeNull());
    expect(toast()).toHaveTextContent('Where you live is now Cabin · Undo');
    clickUndo();
    await waitFor(() => expect(undoCalls(fetchMock)).toEqual(['/api/undo/w']));
    await act(async () => {});
  });

  it("changing a home's mortgage offers Undo", async () => {
    const fetchMock = stubFetch({ ...base(home()), ...undoRoutes });
    atProfile('/profile/home/mortgages');
    fireEvent.change(await screen.findByLabelText('Mortgage'), { target: { value: '' } });
    await waitFor(() => expect(callsTo(fetchMock, 'PATCH', '/api/accounts/1')[0]?.body).toEqual({ loan_id: null }));
    await waitFor(() => expect(toast()).not.toBeNull());
    expect(toast()).toHaveTextContent("Changed Home's mortgage · Undo");
    clickUndo();
    await waitFor(() => expect(undoCalls(fetchMock)).toEqual(['/api/undo/a']));
  });

  it("changing a car's loan offers Undo", async () => {
    const fetchMock = stubFetch({ ...base(home({ cars: [{ account_id: 5, name: 'Subaru', value_cents: 2_000_000, as_of: '2026-10-01', loan: null }],
      loans: [loan({ account_id: 6, name: 'Auto loan' })] })), ...undoRoutes });
    atProfile('/profile/home/cars');
    fireEvent.change(await screen.findByLabelText('Loan'), { target: { value: '6' } });
    await waitFor(() => expect(callsTo(fetchMock, 'PATCH', '/api/accounts/5')[0]?.body).toEqual({ loan_id: 6 }));
    await waitFor(() => expect(toast()).not.toBeNull());
    expect(toast()).toHaveTextContent("Changed Subaru's loan · Undo");
    clickUndo();
    await waitFor(() => expect(undoCalls(fetchMock)).toEqual(['/api/undo/a']));
    await act(async () => {});
  });

  it('a new car takes its name and value 600 ms after typing, offers Undo, and says what is missing', async () => {
    const fetchMock = stubFetch({ ...base(home({ cars: [] })), 'POST /api/accounts': () => reply({ id: 9, undo: 'add' }, 201), ...undoRoutes });
    atProfile('/profile/home/cars');
    fireEvent.click(await screen.findByRole('radio', { name: 'Yes' }));
    fireEvent.change(await screen.findByLabelText('Label'), { target: { value: 'Civic' } });
    fireEvent.change(screen.getByLabelText('What it would sell for'), { target: { value: '0' } });
    expect(callsTo(fetchMock, 'POST', '/api/accounts')).toHaveLength(0);
    expect(await screen.findByText('Enter what it would sell for.')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Label'), { target: { value: '' } });
    fireEvent.change(screen.getByLabelText('What it would sell for'), { target: { value: '9000' } });
    expect(await screen.findAllByText('Label')).not.toHaveLength(0);
    expect(callsTo(fetchMock, 'POST', '/api/accounts')).toHaveLength(0);
    fireEvent.change(screen.getByLabelText('Label'), { target: { value: 'Civic' } });
    await waitFor(() => expect(callsTo(fetchMock, 'POST', '/api/accounts')[0]?.body).toMatchObject({ name: 'Civic', type: 'vehicle', balance_cents: 900_000 }));
    await waitFor(() => expect(toast()).not.toBeNull());
    expect(toast()).toHaveTextContent('Added Civic · Undo');
    clickUndo();
    await waitFor(() => expect(undoCalls(fetchMock)).toEqual(['/api/undo/add']));
    await act(async () => {});
  });

  it('adding and linking a mortgage offers one Undo that reverts the link, then the add', async () => {
    const fetchMock = stubFetch({ ...base(home({ properties: [cabin()], where_you_live: 3 })), 'POST /api/accounts': () => reply({ id: 9, undo: 'add' }, 201),
      'PATCH /api/accounts': () => reply({ id: 3, undo: 'link' }), ...undoRoutes });
    atProfile('/profile/home/mortgages');
    fireEvent.change(await screen.findByLabelText('Mortgage'), { target: { value: '__add' } });
    fireEvent.change(await screen.findByLabelText('Balance'), { target: { value: '100000' } });
    await waitFor(() => expect(callsTo(fetchMock, 'PATCH', '/api/accounts/3')[0]?.body).toEqual({ loan_id: 9 }));
    expect(callsTo(fetchMock, 'POST', '/api/accounts')[0]?.body).toMatchObject({ name: 'Mortgage', type: 'mortgage', balance_cents: -10_000_000 });
    await waitFor(() => expect(toast()).not.toBeNull());
    expect(toast()).toHaveTextContent('Added Mortgage · Undo');
    clickUndo();
    await waitFor(() => expect(undoCalls(fetchMock)).toEqual(['/api/undo/link', '/api/undo/add']));
  });

  it('Remove folds a home away with "Removed Cabin · Undo", and Undo posts the token', async () => {
    const fetchMock = stubFetch({ ...base(home({ properties: [...home().properties, cabin()] })), ...undoRoutes });
    atProfile('/profile/home/homes');
    fireEvent.click(await screen.findByRole('button', { name: 'Remove Cabin' }));   // each Remove names its home (THO-12)
    await waitFor(() => expect(toast()).not.toBeNull());
    expect(toast()).toHaveTextContent('Removed Cabin · Undo');
    expect(callsTo(fetchMock, 'PATCH', '/api/accounts/3')[0]?.body).toEqual({ is_active: false });
    clickUndo();
    await waitFor(() => expect(undoCalls(fetchMock)).toEqual(['/api/undo/a']));
    await act(async () => {});
  });

  it('opening the Mortgages step writes nothing; changing the payment saves the payoff date with Undo', async () => {
    const fetchMock = stubFetch({ ...base(home({ properties: [{ ...home().properties[0], mortgage: loan({ payment_cents: 268_000 }) }] })), ...undoRoutes,
      'POST /api/facts': (_u: string, init: RequestInit) => reply({ undo: JSON.parse(init.body as string).key === 'mortgage.payoff_date' ? 'f' : 'a' }, 201) });
    atProfile('/profile/home/mortgages');
    await screen.findByLabelText('Monthly payment');
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(callsTo(fetchMock, 'POST', '/api/facts')).toHaveLength(0);
    fireEvent.change(screen.getByLabelText('Monthly payment'), { target: { value: '3500' } });
    await waitFor(() => expect(callsTo(fetchMock, 'POST', '/api/facts').map((c) => (c.body as { key: string }).key)).toContain('mortgage.payoff_date'));
    expect(await screen.findByText('Payoff date set')).toBeInTheDocument();
    clickUndo();
    await waitFor(() => expect(undoCalls(fetchMock)).toContain('/api/undo/f'));
  });

  // Review of #51: the payoff readout shows the saved loan, so it is quiet when the step opens; a change is said.
  it('the payoff readout is quiet when the step opens and said when the payment changes it', async () => {
    stubFetch({ ...base(home({ properties: [{ ...home().properties[0], mortgage: loan({ payment_cents: 268_000 }) }] })), ...undoRoutes });
    atProfileLive('/profile/home/mortgages');
    await screen.findByLabelText('Monthly payment');
    const payoff = screen.getByTestId('payoff');
    expect(payoff).toHaveTextContent('Paid off by');
    expect(screen.getByTestId('live-said')).not.toHaveTextContent('Paid off by');
    fireEvent.change(screen.getByLabelText('Monthly payment'), { target: { value: '3500' } });
    await waitFor(() => expect(screen.getByTestId('live-said')).toHaveTextContent('Paid off by'));
    expect(screen.getByTestId('live-said').textContent).toContain(payoff.querySelector('.calc__value')?.textContent ?? 'none');
  });

  it('where you live: a rate saves the account and the fact, and one Undo reverts both, fact first', async () => {
    const fetchMock = stubFetch({ ...base(home({ properties: [{ ...home().properties[0], mortgage: loan({ rate_bp: null }) }] })), ...undoRoutes,
      '/api/profile': profileState({ home: { status: 'done' } }) });
    atProfile('/profile/home');
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Interest rate' }));
    const input = screen.getByLabelText('Interest rate');
    fireEvent.change(input, { target: { value: '3.25' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(callsTo(fetchMock, 'POST', '/api/facts')[0]?.body).toMatchObject({ key: 'mortgage.rate_bp' }));
    expect(callsTo(fetchMock, 'PATCH', '/api/accounts/2')[0]?.body).toEqual({ rate_bp: 325 });
    fireEvent.click(await screen.findByRole('button', { name: 'Undo' }));
    await waitFor(() => expect(undoCalls(fetchMock)).toEqual(['/api/undo/f', '/api/undo/a']));
  });

  it("another home's mortgage posts only its rate: no payment or payoff facts", async () => {
    const two = home({ properties: [...home().properties, cabin({ mortgage: loan({ account_id: 4, name: 'Cabin loan', rate_bp: null }) })] });
    const fetchMock = stubFetch(base(two));
    atProfile('/profile/home/mortgages');
    const rates = await screen.findAllByLabelText('Interest rate');
    fireEvent.change(rates[1], { target: { value: '4' } });
    await waitFor(() => expect(callsTo(fetchMock, 'PATCH', '/api/accounts/4')[0]?.body).toEqual({ rate_bp: 400 }));
    await act(async () => { await vi.advanceTimersByTimeAsync(800); });
    expect(callsTo(fetchMock, 'POST', '/api/facts')).toHaveLength(0);
  });

  it('Back from Cars skips Homes and Mortgages when the answer is No', async () => {
    stubFetch(base(home({ owns_home: false })));
    const { location } = atProfile('/profile/home/cars');
    fireEvent.click(await screen.findByRole('link', { name: 'Back' }));
    await waitFor(() => expect(location.current.pathname).toBe('/b/ours/profile/home/own'));
  });

  it('a mortgage with no rate, payment or payoff date still opens the step', async () => {
    stubFetch(base(home({ properties: [{ ...home().properties[0], mortgage: loan({ rate_bp: null }) }] })));
    atProfile('/profile/home/mortgages');
    expect(await screen.findByLabelText('Interest rate')).toHaveValue('');
    expect(screen.getByTestId('payoff')).toBeInTheDocument();
  });

  it('the card lines: where you live with its full mortgage, another home, a car with its loan', async () => {
    const full = loan({ rate_bp: 287, payment_cents: 268_000, payoff_date: '2046-03-01' });
    stubFetch({ ...base(home({ properties: [{ ...home().properties[0], mortgage: full }, cabin({ mortgage: loan({ account_id: 4, name: 'Cabin loan' }) })],
      cars: [{ account_id: 5, name: 'Subaru', value_cents: 2_000_000, as_of: '2026-10-01', loan: loan({ account_id: 6, name: 'Auto loan', balance_cents: 500_000 }) }] })),
      '/api/profile': profileState({ home: { status: 'done' } }) });
    atProfile('/profile/home');
    await screen.findByText('Where you live: Home · $1,250,000.00');
    const sums = Array.from(document.querySelectorAll('.prof-card__sum')).map((e) => e.textContent);
    expect(sums).toEqual(['Where you live: Home · $1,250,000.00', 'Chase mortgage · 2.87% · $2,680 a month · paid off by March 2046', 'Cabin · $400,000.00',
      'Cabin loan', 'Subaru · $20,000.00 · Auto loan $5,000.00']);
  });

  it('a rate-less where-you-live mortgage reads as its name, with no empty parts', async () => {
    stubFetch({ ...base(home({ properties: [{ ...home().properties[0], mortgage: loan({ rate_bp: null }) }] })), '/api/profile': profileState({ home: { status: 'done' } }) });
    atProfile('/profile/home');
    await screen.findByText('Where you live: Home · $1,250,000.00');
    expect(Array.from(document.querySelectorAll('.prof-card__sum')).map((e) => e.textContent)).toEqual(['Where you live: Home · $1,250,000.00', 'Chase mortgage']);
  });
});
