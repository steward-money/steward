import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { askState, resetAsk } from '../../ask/store';
import { useFakeTimers } from '../../test-clock';
import type { BookSummary, Txn } from '../../api/types';
import { HouseholdOnly } from '../../book/BookScope';
import { BOOK, callsTo, renderAt, reply, stubFetch, wrapBook } from '../../test-utils';
import { INVESTMENT_UNAVAILABLE } from '../../lib/copy';
import AccountPage, { loadInvestment } from './AccountPage';
import { ACCTS, acct } from './fixtures';
import { page } from './invest/fixtures';

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

const ours: BookSummary = BOOK;
const mom: BookSummary = { ...BOOK, slug: 'mom', name: 'Mom', kind: 'parent' };
const txn = (over: Partial<Txn>): Txn => ({
  id: 1, account_id: 1, account_name: 'Checking ••6789', posted_at: '2026-10-24', amount_cents: -18800, raw_description: 'COSTBOX WHSE #412',
  merchant: 'Costbox', category: 'groceries', is_business: false, is_transfer: false, business_pct: 0, business_line: null,
  business_source: 'none', category_source: 'rule', reviewed_at: null, from_bank: true, ...over,
});

// The book's routes as routes.tsx has them, so the redirects can be followed.
const routes = (
  <Routes>
    <Route path="/b/:slug/accounts/:id" element={<HouseholdOnly to="/inventory"><AccountPage /></HouseholdOnly>} />
    <Route path="*" element={null} />
  </Routes>
);

describe("one account's page", () => {
  it('shows the account and only its transactions, with ‹ Accounts', async () => {
    const fetchMock = stubFetch({ '/api/accounts': { accounts: ACCTS }, '/api/transactions': { transactions: [txn({ id: 9, account_id: 1 })], next_cursor: null } });
    wrapBook(<Routes><Route path="/b/:slug/accounts/:id" element={<AccountPage />} /></Routes>, ours, '/b/ours/accounts/1');
    expect(await screen.findByRole('heading', { level: 1, name: 'Checking ••6789' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Accounts/ })).toHaveAttribute('href', '/b/ours/accounts');
    await waitFor(() => expect(callsTo(fetchMock, 'GET', '/api/transactions')[0]?.url).toContain('account_id=1'));
    expect(screen.queryByRole('button', { name: /^Account/ })).toBeNull();   // no Account chip
    expect(screen.queryByRole('heading', { name: 'All transactions' })).toBeNull();
  });
  it('gives the balance untinted, the bank and when it was last updated', async () => {
    useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date(2026, 9, 25, 12, 0));
    stubFetch({ '/api/accounts': { accounts: ACCTS }, '/api/transactions': { transactions: [], next_cursor: null } });
    wrapBook(<Routes><Route path="/b/:slug/accounts/:id" element={<AccountPage />} /></Routes>, ours, '/b/ours/accounts/1');
    await screen.findByRole('heading', { level: 1, name: 'Checking ••6789' });
    const balance = screen.getByText('$4,210.18');
    expect(balance).toHaveClass('money');
    expect(balance).not.toHaveClass('money--in');
    expect(screen.getByText('Big Bank · Last updated Today')).toBeInTheDocument();
  });
  it('says a manual account is updated manually, with its as-of date, and lists its transactions (one imported from a file)', async () => {
    useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date(2026, 9, 25, 12, 0));
    const mortgage = acct({ id: 7, name: 'Mortgage', type: 'mortgage', source: 'manual', balance_cents: -1450000, institution: 'Manual', as_of: '2026-10-01' });
    const payment = txn({ id: 12, account_id: 7, account_name: 'Mortgage', posted_at: '2026-10-01', amount_cents: 210000, merchant: 'Payment received', raw_description: 'PAYMENT', from_bank: false });
    const fetchMock = stubFetch({ '/api/accounts': { accounts: [...ACCTS, mortgage] }, '/api/transactions': { transactions: [payment], next_cursor: null } });
    wrapBook(<Routes><Route path="/b/:slug/accounts/:id" element={<AccountPage />} /></Routes>, ours, '/b/ours/accounts/7?dates=all');
    await screen.findByRole('heading', { level: 1, name: 'Mortgage' });
    expect(screen.getByText('Updated manually · Oct 1')).toBeInTheDocument();
    expect(screen.getByText('−$14,500.00')).not.toHaveClass('money--out');
    expect(await screen.findByText('Payment received')).toBeInTheDocument();
    expect(callsTo(fetchMock, 'GET', '/api/transactions')[0]?.url).toContain('account_id=7');
  });
  it("can't load the accounts: says so, with Try again and ‹ Accounts", async () => {
    let calls = 0;
    const fetchMock = stubFetch({ '/api/accounts': () => (++calls === 1 ? reply({ error: 'Steward is busy.' }, 500) : reply({ accounts: ACCTS })), '/api/transactions': { transactions: [], next_cursor: null } });
    wrapBook(<Routes><Route path="/b/:slug/accounts/:id" element={<AccountPage />} /></Routes>, ours, '/b/ours/accounts/1');
    expect(await screen.findByText('Steward is busy.')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Accounts/ })).toHaveAttribute('href', '/b/ours/accounts');
    expect(callsTo(fetchMock, 'GET', '/api/transactions')).toHaveLength(0);
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByRole('heading', { level: 1, name: 'Checking ••6789' })).toBeInTheDocument();
  });
  it('reads its filters from the address', async () => {
    const fetchMock = stubFetch({ '/api/accounts': { accounts: ACCTS }, '/api/transactions': { transactions: [], next_cursor: null } });
    wrapBook(<Routes><Route path="/b/:slug/accounts/:id" element={<AccountPage />} /></Routes>, ours, '/b/ours/accounts/1?dates=all&category=groceries');
    expect(await screen.findByRole('button', { name: /^Category: .*, clear$/ })).toBeInTheDocument();
    await waitFor(() => expect(callsTo(fetchMock, 'GET', '/api/transactions')[0]?.url).toContain('category=groceries'));
  });
  it.each(['/b/ours/accounts/99', '/b/ours/accounts/abc'])("an address for an account the book doesn't have goes to Accounts: %s", async (path) => {
    stubFetch({ '/api/accounts': { accounts: ACCTS } });
    const { location } = renderAt(routes, ours, path, [ours]);
    await waitFor(() => expect(location.current.pathname).toBe('/b/ours/accounts'));
  });
  it("a parent book's account address goes to Inventory, where its accounts are", async () => {
    stubFetch({});
    const { location } = renderAt(routes, mom, '/b/mom/accounts/1', [mom]);
    await waitFor(() => expect(location.current.pathname).toBe('/b/mom/inventory'));
  });

  it('an investment account entered by hand says so, with its owner and Update, and loads its sections', async () => {
    useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date(2026, 9, 25, 12, 0));
    const betterment = acct({ id: 8, name: 'Betterment', type: 'brokerage_taxable', source: 'manual', owner: 'jr', institution: 'Manual', as_of: '2026-10-01', balance_cents: 8421000 });
    stubFetch({ '/api/accounts/8/investment': page({ account_id: 8, institution: 'Betterment', manual: true, sends: null }),
      '/api/accounts': { accounts: [...ACCTS, betterment] }, '/api/transactions': { transactions: [], next_cursor: null } });
    wrapBook(<Routes><Route path="/b/:slug/accounts/:id" element={<AccountPage />} /></Routes>, ours, '/b/ours/accounts/8');
    expect(await screen.findByText('Entered by hand · Oct 1 · JR')).toBeInTheDocument();
    expect(await screen.findByTestId('investment')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Update' }));
    expect(screen.getByRole('dialog', { name: 'Update Betterment' })).toBeInTheDocument();
  });

  it("names the owner as the server words it, a member's name, whatever the open book's list holds (issue 31)", async () => {
    useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date(2026, 9, 25, 12, 0));
    const roth = acct({ id: 9, name: 'Roth IRA', type: 'roth_ira', source: 'manual', owner: 'jr', owner_label: 'Alex', institution: 'Manual', as_of: '2026-10-01', balance_cents: 100 });
    stubFetch({ '/api/accounts/9/investment': page({ account_id: 9, institution: 'Robo Invest', manual: true, sends: null }),
      '/api/accounts': { accounts: [...ACCTS, roth] }, '/api/transactions': { transactions: [], next_cursor: null } });
    wrapBook(<Routes><Route path="/b/:slug/accounts/:id" element={<AccountPage />} /></Routes>, { ...ours, owners: [] }, '/b/ours/accounts/9');
    expect(await screen.findByText('Entered by hand · Oct 1 · Alex')).toBeInTheDocument();
  });

  it('a connected investment account names its bank, how fresh it is and its owner; a viewer gets no Update', async () => {
    useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date(2026, 9, 25, 12, 0));
    stubFetch({ '/api/accounts/3/investment': page({}), '/api/accounts': { accounts: ACCTS }, '/api/transactions': { transactions: [], next_cursor: null } });
    wrapBook(<Routes><Route path="/b/:slug/accounts/:id" element={<AccountPage />} /></Routes>, { ...ours, role: 'viewer' }, '/b/ours/accounts/3');
    expect(await screen.findByText('Robo Invest · Last updated Today · Joint')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Update' })).toBeNull();
  });

  it("another type keeps today's page and never asks for investment sections", async () => {
    const fetchMock = stubFetch({ '/api/accounts': { accounts: ACCTS }, '/api/transactions': { transactions: [], next_cursor: null } });
    wrapBook(<Routes><Route path="/b/:slug/accounts/:id" element={<AccountPage />} /></Routes>, ours, '/b/ours/accounts/1');
    await screen.findByRole('heading', { level: 1, name: 'Checking ••6789' });
    expect(callsTo(fetchMock, 'GET', '/api/accounts/1/investment')).toHaveLength(0);
    expect(screen.queryByTestId('investment')).toBeNull();
  });

  it("still shows the page when the investment chunk can't be fetched, and says so", async () => {
    const { default: Missing } = await loadInvestment(() => Promise.reject(new Error('Failed to fetch dynamically imported module')));
    wrapBook(<Missing account={ACCTS[2]} />);
    expect(screen.getByText(INVESTMENT_UNAVAILABLE)).toBeInTheDocument();
  });

  it('offers Merge into… to a manager for a bank account with another of its type, and not otherwise', async () => {
    const accts = [...ACCTS, acct({ id: 5, name: 'Old Roth', type: 'roth_ira', source: 'simplefin', institution: 'Robo Invest' })];
    stubFetch({ '/api/accounts': { accounts: accts }, '/api/transactions': { transactions: [], next_cursor: null } });
    const page = (path: string, book = ours) =>
      wrapBook(<Routes><Route path="/b/:slug/accounts/:id" element={<AccountPage />} /></Routes>, book, path);
    const first = page('/b/ours/accounts/3');
    expect(await screen.findByRole('button', { name: 'Merge into…' })).toBeInTheDocument();
    first.unmount();
    for (const [path, book] of [['/b/ours/accounts/3', { ...ours, role: 'viewer' as const }], ['/b/ours/accounts/1', ours],
                                ['/b/ours/accounts/4', ours]] as const) {
      const view = page(path, book);
      await screen.findByRole('heading', { level: 1 });
      expect(screen.queryByRole('button', { name: 'Merge into…' })).toBeNull();
      view.unmount();
    }
  });

  it('offers every other bank account to one whose type is not set yet: the kept one keeps its type', async () => {
    const accts = [...ACCTS, acct({ id: 5, name: '529 Plan', type: 'unknown', source: 'simplefin', institution: 'Ascensus' })];
    stubFetch({ '/api/accounts': { accounts: accts }, '/api/transactions': { transactions: [], next_cursor: null } });
    wrapBook(<Routes><Route path="/b/:slug/accounts/:id" element={<AccountPage />} /></Routes>, ours, '/b/ours/accounts/5');
    fireEvent.click(await screen.findByRole('button', { name: 'Merge into…' }));
    const sheet = await screen.findByRole('dialog', { name: 'Merge 529 Plan into…' });
    expect(within(sheet).getAllByRole('radio').map((r) => r.closest('label')?.querySelector('span')?.textContent))
      .toEqual(['Checking ••6789', 'Rewards Card ••4402', 'Roth IRA']);
  });
  it("merges into the chosen account and says so on that account's page", async () => {
    localStorage.setItem('steward-celebrations', 'off');
    const accts = [...ACCTS, acct({ id: 5, name: 'Old Roth', type: 'roth_ira', source: 'simplefin', institution: 'Robo Invest' })];
    const done = { merged: { kept: { id: 3, name: 'Roth IRA' }, dropped: 4, kept_new: 0 }, undo: 'tok' };
    stubFetch({
      '/api/accounts/5/merge-preview': { merged: done.merged },
      'POST /api/accounts/5/merge-into': done,
      '/api/accounts': { accounts: accts },
      '/api/transactions': { transactions: [], next_cursor: null },
    });
    wrapBook(<Routes><Route path="/b/:slug/accounts/:id" element={<AccountPage />} /></Routes>, ours, '/b/ours/accounts/5');
    fireEvent.click(await screen.findByRole('button', { name: 'Merge into…' }));
    const sheet = await screen.findByRole('dialog', { name: 'Merge Old Roth into…' });
    expect(await within(sheet).findByTestId('merge-preview')).toHaveTextContent('4 transactions match and will be dropped; nothing will move over.');
    fireEvent.click(within(sheet).getByRole('button', { name: 'Merge into Roth IRA' }));
    expect(await screen.findByRole('heading', { level: 1, name: 'Roth IRA' })).toBeInTheDocument();
    expect(await screen.findByText('Merged. Roth IRA now gets updates from both logins; 4 copies were dropped and nothing new was kept.')).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).toBeNull();
    localStorage.removeItem('steward-celebrations');
  });
});

describe('an account page opens on a range with something in it (roadmap 11)', () => {
  const page = (path: string) => wrapBook(<Routes><Route path="/b/:slug/accounts/:id" element={<AccountPage />} /></Routes>, ours, path);

  it('widens an empty week to 30 days, then to everything, and stops at the first range with transactions', async () => {
    // Nothing in any dated range; one transaction when there is no from date (All).
    const fetchMock = stubFetch({ '/api/accounts': { accounts: ACCTS },
      '/api/transactions': (url: string) => reply({ transactions: url.includes('from=') ? [] : [txn({ id: 9, posted_at: '2026-03-02' })], next_cursor: null }) });
    page('/b/ours/accounts/1');
    expect(await screen.findByText('Costbox')).toBeInTheDocument();
    const urls = callsTo(fetchMock, 'GET', '/api/transactions').map((c) => c.url);
    expect(new Set(urls.filter((u) => u.includes('from='))).size).toBe(2);   // the week, then 30 days
    expect(urls[urls.length - 1]).not.toContain('from=');                     // then everything
  });

  it('widens once on the way in: Last 7 days picked afterwards stays, empty or not (final review 2)', async () => {
    // The week is empty, the 30 days are not.
    let calls = 0;
    stubFetch({ '/api/accounts': { accounts: ACCTS },
      '/api/transactions': () => { calls += 1; return reply({ transactions: calls === 1 || calls > 2 ? [] : [txn({ id: 9 })], next_cursor: null }); } });
    page('/b/ours/accounts/1');
    expect(await screen.findByText('Costbox')).toBeInTheDocument();          // widened to 30 days
    fireEvent.click(screen.getByRole('button', { name: /^Dates/ }));
    fireEvent.click(screen.getByRole('radio', { name: 'Last 7 days' }));
    expect(await screen.findByText('Nothing in the last 7 days.')).toBeInTheDocument();
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.getByText('Nothing in the last 7 days.')).toBeInTheDocument();   // not widened again
    expect(calls).toBe(3);
  });

  it('leaves a range the person chose alone, even when it is empty', async () => {
    const fetchMock = stubFetch({ '/api/accounts': { accounts: ACCTS }, '/api/transactions': { transactions: [], next_cursor: null } });
    page('/b/ours/accounts/1?dates=30');
    await waitFor(() => expect(callsTo(fetchMock, 'GET', '/api/transactions').length).toBeGreaterThan(0));
    await new Promise((r) => setTimeout(r, 50));
    expect(callsTo(fetchMock, 'GET', '/api/transactions').every((c) => c.url.includes('from='))).toBe(true);
  });
});

describe('Ask about this on an account (Surfaces)', () => {
  afterEach(() => { resetAsk(); });
  it("asks about the account by its id, before Merge into…, with its kind's suggestions", async () => {
    stubFetch({ '/api/accounts': { accounts: ACCTS }, '/api/transactions': { transactions: [], next_cursor: null } });
    wrapBook(<Routes><Route path="/b/:slug/accounts/:id" element={<AccountPage />} /></Routes>, { ...ours, chat: true }, '/b/ours/accounts/1');
    const ask = await screen.findByRole('button', { name: `Ask about ${ACCTS[0].name}` });
    fireEvent.click(ask);
    expect(askState().about).toStrictEqual({ kind: 'account', id: 1 });
    expect(askState().surface).toStrictEqual({ kind: 'account', group: 'everyday' });
  });
  it('has no Ask button with chat off', async () => {
    stubFetch({ '/api/accounts': { accounts: ACCTS }, '/api/transactions': { transactions: [], next_cursor: null } });
    wrapBook(<Routes><Route path="/b/:slug/accounts/:id" element={<AccountPage />} /></Routes>, ours, '/b/ours/accounts/1');
    await screen.findByRole('heading', { level: 1 });
    expect(document.querySelector('.ask-about')).toBeNull();
  });
});
