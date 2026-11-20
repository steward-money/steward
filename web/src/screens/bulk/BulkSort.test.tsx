import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useFakeTimers } from '../../test-clock';
import { LONG_PRESS_MS } from '../../lib/selection';
import { callsTo, inList, reply, stubFetch, wrapBook } from '../../test-utils';
import { LiveRegions } from '../../ui/Live';
import { ACCTS } from '../accounts/fixtures';
import Review from '../Review';
import Transactions from '../Transactions';
import BulkSort from './BulkSort';
import { loadBulkSort } from './load';

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });
beforeEach(() => { localStorage.clear(); localStorage.setItem('steward-celebrations', 'off'); });

const ROW = { id: 1, account_id: 1, account_name: 'Checking', posted_at: '2026-10-10', amount_cents: -1200, raw_description: 'SQ *VENDOR', merchant: null, category: 'uncategorized', is_business: false, is_transfer: false, business_pct: 0, business_line: null, business_source: 'none', category_source: 'none', reviewed_at: null, from_bank: true, set_aside: null, note_count: 0 };
const ROWS = [1, 2, 3, 4].map((id) => ({ ...ROW, id, raw_description: `SHOP ${id}` }));
const SORTED = (ids: number[], category = 'gifts') => ({ sorted: ids.length, ids, category, skipped: [], undo: 'tok' });

/** The facts line holds bold runs, so match its whole text. */
const said = () => screen.getByTestId('bulk-said');
const pickRow = (id: number, shiftKey = false) => {
  fireEvent.click(screen.getAllByTestId('txn-row').find((r) => r.dataset.id === String(id))!, { shiftKey });
};
const sortAs = (name: string) => {
  fireEvent.click(within(screen.getByTestId('bulk-bar')).getByRole('button', { name: 'Sort as…' }));
  fireEvent.click(within(screen.getByRole('dialog', { name: 'Sort as…' })).getByRole('option', { name }));
};

describe('Bulk sort on Transactions', () => {
  const routes = (extra: Record<string, unknown> = {}) => ({
    '/api/accounts': { accounts: ACCTS }, '/api/transactions': { transactions: ROWS, next_cursor: null },
    'POST /api/transactions/sort': SORTED([1, 2, 3]), 'POST /api/undo/tok': { undone: 3, transaction: null }, ...extra,
  });

  it('Select turns the rows into checkboxes; a click picks one and Shift-click a range', async () => {
    stubFetch(routes());
    wrapBook(<Transactions />, {}, '/b/ours/transactions?dates=all');
    fireEvent.click(await screen.findByRole('button', { name: 'Select' }));
    expect(screen.getByRole('button', { name: 'Select' })).toHaveAttribute('aria-pressed', 'true');
    expect(await screen.findByTestId('bulk-bar')).toHaveTextContent('0 selected');
    expect(screen.getAllByRole('checkbox')).toHaveLength(4);
    pickRow(1);
    pickRow(3, true);
    const checked = screen.getAllByRole('checkbox').map((c) => c.getAttribute('aria-checked'));
    expect(checked).toEqual(['true', 'true', 'true', 'false']);
    expect(screen.getByTestId('bulk-bar')).toHaveTextContent('3 selected');
    pickRow(2);
    expect(screen.getByTestId('bulk-bar')).toHaveTextContent('2 selected');
    // A pick never opens the row.
    expect(screen.queryByRole('complementary', { name: 'Transaction detail' })).toBeNull();
  });

  // Review of #51: the count appears with the bar, so it is said through the page's region, not as a live region of its own.
  it('the count is said when the bar appears and when it changes', async () => {
    stubFetch(routes());
    wrapBook(<LiveRegions><Transactions /></LiveRegions>, {}, '/b/ours/transactions?dates=all');
    fireEvent.click(await screen.findByRole('button', { name: 'Select' }));
    const count = within(await screen.findByTestId('bulk-bar')).getByText('0 selected').parentElement as HTMLElement;
    expect(count).not.toHaveAttribute('aria-live');
    expect(screen.getByTestId('live-said')).toHaveTextContent('0 selected');
    pickRow(1);
    expect(screen.getByTestId('live-said')).toHaveTextContent('1 selected');
  });

  it('sorts the picked rows in one request, says so with Undo, and Undo puts them back', async () => {
    const fetchMock = stubFetch(routes());
    wrapBook(<Transactions />, {}, '/b/ours/transactions?dates=all');
    fireEvent.click(await screen.findByRole('button', { name: 'Select' }));
    await screen.findByTestId('bulk-bar');
    pickRow(1); pickRow(2); pickRow(3);
    sortAs('Gifts');
    await waitFor(() => expect(said()).toHaveTextContent('Sorted 3 as Gifts'));
    expect(callsTo(fetchMock, 'POST', '/api/transactions/sort').map((c) => c.body)).toEqual([{ ids: [1, 2, 3], category: 'gifts' }]);
    expect(screen.queryByTestId('bulk-bar')).toBeNull();                // selection mode ends
    expect(screen.queryAllByRole('checkbox')).toHaveLength(0);
    expect(within(said()).getByRole('button', { name: 'Undo' })).toHaveFocus();
    fireEvent.click(within(said()).getByRole('button', { name: 'Undo' }));
    await waitFor(() => expect(said()).toHaveTextContent('Undone.'));
    expect(callsTo(fetchMock, 'POST', '/api/undo/tok')).toHaveLength(1);
  });

  it('keeps the rows it could not sort picked and says why', async () => {
    stubFetch(routes({ 'POST /api/transactions/sort': { sorted: 1, ids: [1], category: 'reimbursement', skipped: [{ id: 2, reason: 'Choose who pays you back.' }], undo: 'tok' } }));
    wrapBook(<Transactions />, {}, '/b/ours/transactions?dates=all');
    fireEvent.click(await screen.findByRole('button', { name: 'Select' }));
    await screen.findByTestId('bulk-bar');
    pickRow(1); pickRow(2);
    sortAs('Reimbursement');
    await waitFor(() => expect(said()).toHaveTextContent("Sorted 1 as Reimbursement. 1 couldn't be: Choose who pays you back."));
    expect(screen.getByTestId('bulk-bar')).toHaveTextContent('1 selected');
    expect(screen.getAllByRole('checkbox').map((c) => c.getAttribute('aria-checked'))).toEqual(['false', 'true', 'false', 'false']);
  });

  it('Cancel and Escape end selection mode', async () => {
    stubFetch(routes());
    wrapBook(<Transactions />, {}, '/b/ours/transactions?dates=all');
    fireEvent.click(await screen.findByRole('button', { name: 'Select' }));
    fireEvent.click(within(await screen.findByTestId('bulk-bar')).getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByTestId('bulk-bar')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Select' }));
    await screen.findByTestId('bulk-bar');
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByTestId('bulk-bar')).toBeNull();
    expect(screen.queryAllByRole('checkbox')).toHaveLength(0);
  });

  it('a long press on a phone starts a selection with that row picked, and the press opens nothing', async () => {
    stubFetch(routes());
    wrapBook(<Transactions />, {}, '/b/ours/transactions?dates=all');
    const row = (await screen.findAllByTestId('txn-row'))[1];
    useFakeTimers();
    fireEvent.pointerDown(row, { pointerType: 'touch' });
    act(() => { vi.advanceTimersByTime(LONG_PRESS_MS); });
    fireEvent.pointerUp(row, { pointerType: 'touch' });
    fireEvent.click(row);
    vi.useRealTimers();
    expect(await screen.findByTestId('bulk-bar')).toHaveTextContent('1 selected');
    expect(screen.getAllByRole('checkbox').map((c) => c.getAttribute('aria-checked'))).toEqual(['false', 'true', 'false', 'false']);
  });

  it('a viewer has no Select', async () => {
    stubFetch(routes());
    wrapBook(<Transactions />, { role: 'viewer' }, '/b/ours/transactions?dates=all');
    await screen.findAllByTestId('txn-row');
    expect(screen.queryByRole('button', { name: 'Select' })).toBeNull();
  });

  it('an Escape that closes a filter sheet leaves the selection alone', async () => {
    stubFetch(routes());
    wrapBook(<Transactions />, {}, '/b/ours/transactions?dates=all');
    fireEvent.click(await screen.findByRole('button', { name: 'Select' }));
    await screen.findByTestId('bulk-bar');
    pickRow(1); pickRow(2);
    fireEvent.click(screen.getByRole('button', { name: /^Dates/ }));
    const sheet = screen.getByRole('dialog', { name: 'Dates' });
    fireEvent.keyDown(within(sheet).getAllByRole('radio')[0], { key: 'Escape' });
    expect(screen.queryByRole('dialog', { name: 'Dates' })).toBeNull();          // the sheet took it
    expect(screen.getByTestId('bulk-bar')).toHaveTextContent('2 selected');
    fireEvent.keyDown(document, { key: 'Escape' });                              // the next one ends selection
    expect(screen.queryByTestId('bulk-bar')).toBeNull();
  });

  it('Cancel and Undo hand the focus back to Select, never to the page', async () => {
    stubFetch(routes());
    wrapBook(<Transactions />, {}, '/b/ours/transactions?dates=all');
    fireEvent.click(await screen.findByRole('button', { name: 'Select' }));
    const cancel = within(await screen.findByTestId('bulk-bar')).getByRole('button', { name: 'Cancel' });
    cancel.focus();
    fireEvent.click(cancel);
    expect(screen.getByRole('button', { name: 'Select' })).toHaveFocus();
    fireEvent.click(screen.getByRole('button', { name: 'Select' }));
    await screen.findByTestId('bulk-bar');
    pickRow(1); pickRow(2); pickRow(3);
    within(screen.getByTestId('bulk-bar')).getByRole('button', { name: 'Sort as…' }).focus();   // as a real click does
    sortAs('Gifts');
    const undo = await within(await screen.findByTestId('bulk-said')).findByRole('button', { name: 'Undo' });
    expect(undo).toHaveFocus();
    fireEvent.click(undo);
    await waitFor(() => expect(said()).toHaveTextContent('Undone.'));
    expect(screen.getByRole('button', { name: 'Select' })).toHaveFocus();
  });
});

describe('the bar', () => {
  it('a new selection starts without the last one’s error', async () => {
    stubFetch({ 'POST /api/transactions/sort': () => reply({ error: 'No such transaction.' }, 404) });
    const props = { ids: [1, 2], onCancel: () => {}, onSorted: () => {}, onUndone: () => {} };
    const { rerender } = wrapBook(<BulkSort selecting {...props} />);
    sortAs('Gifts');
    expect(await screen.findByRole('alert')).toHaveTextContent('Not saved.');
    rerender(<BulkSort selecting={false} {...props} />);
    rerender(<BulkSort selecting {...props} />);
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('says so in one line when its chunk can’t load', async () => {
    const { default: Missing } = await loadBulkSort(() => Promise.reject(new Error('gone')));
    const props = { ids: [1], onCancel: () => {}, onSorted: () => {}, onUndone: () => {} };
    const { rerender } = wrapBook(<Missing selecting {...props} />);
    expect(screen.getByText("Sort as… couldn't load. Reload the page to try again.")).toBeInTheDocument();
    rerender(<Missing selecting={false} {...props} />);
    expect(screen.queryByText(/couldn't load/)).toBeNull();
  });

  it('refuses more than 200 in plain words', () => {
    stubFetch({});
    wrapBook(<BulkSort selecting ids={Array.from({ length: 201 }, (_, i) => i + 1)} onCancel={() => {}} onSorted={() => {}} onUndone={() => {}} />);
    expect(screen.getByTestId('bulk-bar')).toHaveTextContent('201 selectedSort up to 200 at a time.');
    expect(screen.getByRole('button', { name: 'Sort as…' })).toBeDisabled();
  });
  it('says a refusal and keeps the selection', async () => {
    stubFetch({ 'POST /api/transactions/sort': () => reply({ error: 'No such transaction.' }, 404) });
    const onSorted = vi.fn();
    wrapBook(<BulkSort selecting ids={[1, 2]} onCancel={() => {}} onSorted={onSorted} onUndone={() => {}} />);
    sortAs('Gifts');
    expect(await screen.findByRole('alert')).toHaveTextContent('Not saved. No such transaction. Nothing changed.');
    expect(onSorted).not.toHaveBeenCalled();
    expect(screen.getByTestId('bulk-bar')).toHaveTextContent('2 selected');
  });
});

describe('Bulk sort on Review', () => {
  const CARD = { ...ROW, merchant: null, reason: 'unknown', guess: null };
  const CARDS = [7, 8, 9].map((id) => ({ ...CARD, id, raw_description: `MYSTERY ${id}` }));
  const Q = { uncategorized: CARDS, count: 3, duplicates: [], duplicate_count: 0, top_categories: ['groceries'], business: [], business_count: 0 };
  const card = (id: number) => screen.getAllByTestId('review-card').find((c) => c.dataset.id === String(id))!;

  it('select mode turns each card into a compact row, and its controls come back as they were when it ends', async () => {
    stubFetch({ ...inList(), '/api/review': { ...Q, uncategorized: CARDS.map((c) => ({ ...c, matching: 4 })) } });
    wrapBook(<Review />);
    await screen.findAllByTestId('review-card');
    expect(card(7).querySelector('.rcard__reason')).not.toBeNull();
    const also = within(card(7)).getByRole('checkbox', { name: /^Also the / });
    fireEvent.click(also);                                   // a choice made before Select is kept through it
    expect(also).not.toBeChecked();
    fireEvent.click(screen.getByRole('button', { name: 'Select' }));
    await screen.findByTestId('bulk-bar');
    for (const id of [7, 8, 9]) {
      const c = card(id);
      expect(c).toHaveClass('is-compact');
      expect(within(c).getAllByRole('checkbox')).toHaveLength(1);     // only the pick box
      expect(within(c).getByRole('checkbox')).toHaveAccessibleName(`MYSTERY ${id}, −$12.00`);
      expect(within(c).getByText(`MYSTERY ${id}`)).toBeInTheDocument();
      expect(within(c).getByText(/ · Checking$/)).toBeInTheDocument();
      for (const name of ['Groceries', 'More…', 'Snooze', 'Ignore', 'Business', 'Reimbursable']) {
        expect(within(c).queryByRole('button', { name })).toBeNull();
        expect(within(c).queryByRole('switch', { name })).toBeNull();
      }
      expect(within(c).queryByText(/Also the /)).toBeNull();
      expect(c.querySelector('.rcard__reason')).toBeNull();
    }
    // A tap anywhere on the row picks it.
    fireEvent.click(within(card(8)).getByText('MYSTERY 8'));
    expect(within(card(8)).getByRole('checkbox')).toBeChecked();
    fireEvent.click(within(screen.getByTestId('bulk-bar')).getByRole('button', { name: 'Cancel' }));
    const back = card(7);
    expect(back).not.toHaveClass('is-compact');
    expect(within(back).getByRole('button', { name: 'Groceries' })).toBeInTheDocument();
    expect(within(back).getByRole('button', { name: 'Snooze' })).toBeInTheDocument();
    expect(within(back).getByRole('checkbox', { name: /^Also the / })).not.toBeChecked();
  });

  it('keeps the first card on screen in its place as the cards collapse and come back', async () => {
    stubFetch({ ...inList(), '/api/review': Q });
    // Full cards: 7 is mostly scrolled past, 8 is the first whose top is on screen. Compact rows are shorter.
    const tops: Record<string, [number, number]> = { 7: [-400, -100], 8: [100, 30], 9: [900, 110] };
    const rect = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      const t = tops[this.dataset.id ?? ''];
      const top = t ? t[this.classList.contains('is-compact') ? 1 : 0] : 0;
      return { top, bottom: top + 50, left: 0, right: 0, width: 0, height: 50, x: 0, y: top, toJSON() {} } as DOMRect;
    });
    const scrollBy = vi.fn();
    vi.stubGlobal('scrollBy', scrollBy);
    try {
      wrapBook(<Review />);
      fireEvent.click(await screen.findByRole('button', { name: 'Select' }));
      expect(scrollBy).toHaveBeenLastCalledWith(0, -70);
      fireEvent.click(within(await screen.findByTestId('bulk-bar')).getByRole('button', { name: 'Cancel' }));
      expect(scrollBy).toHaveBeenLastCalledWith(0, 70);           // and back: card 8 stays where it was
    } finally { rect.mockRestore(); }
  });

  it('picks cards with a click and sorts them at once; they become slim rows in place, and the bar’s Undo grows them back', async () => {
    let left = CARDS;
    const fetchMock = stubFetch({
      ...inList(),
      '/api/review': () => reply({ ...Q, uncategorized: left, count: left.length }),
      'POST /api/transactions/sort': () => { left = CARDS.filter((c) => c.id === 9); return reply(SORTED([7, 8])); },
      'POST /api/undo/tok': () => { left = CARDS; return reply({ undone: 2, transaction: null }); },
    });
    wrapBook(<Review />);
    fireEvent.click(await screen.findByRole('button', { name: 'Select' }));
    await screen.findByTestId('bulk-bar');
    fireEvent.click(card(7));
    fireEvent.click(within(card(8)).getByRole('checkbox', { name: /^MYSTERY 8, / }));
    expect(screen.getByTestId('bulk-bar')).toHaveTextContent('2 selected');
    expect(callsTo(fetchMock, 'PATCH', '/api/transactions')).toHaveLength(0);
    sortAs('Gifts');
    await waitFor(() => expect(said()).toHaveTextContent('Sorted 2 as Gifts'));
    expect(callsTo(fetchMock, 'POST', '/api/transactions/sort')[0].body).toEqual({ ids: [7, 8], category: 'gifts' });
    const slims = () => screen.queryAllByTestId('slim-row').map((s) => s.textContent);
    await waitFor(() => expect(slims()).toEqual(['SORTED · MYSTERY 7 as Gifts · Undo', 'SORTED · MYSTERY 8 as Gifts · Undo']));
    expect([...document.querySelectorAll<HTMLElement>('.rcards > [data-id]')].map((e) => e.dataset.id)).toEqual(['7', '8', '9']);
    fireEvent.click(within(said()).getByRole('button', { name: 'Undo' }));
    await waitFor(() => expect(screen.getAllByTestId('review-card').map((c) => c.dataset.id)).toEqual(['7', '8', '9']));
    expect(screen.queryByTestId('slim-row')).toBeNull();
  });

  it('the last cards going brings All caught up, with their slim rows still in place', async () => {
    let left = CARDS;
    stubFetch({
      ...inList(),
      '/api/review': () => reply({ ...Q, uncategorized: left, count: left.length }),
      'POST /api/transactions/sort': () => { left = []; return reply(SORTED([7, 8, 9])); },
    });
    wrapBook(<Review />);
    fireEvent.click(await screen.findByRole('button', { name: 'Select' }));
    await screen.findByTestId('bulk-bar');
    fireEvent.click(card(7)); fireEvent.click(card(9), { shiftKey: true });
    sortAs('Gifts');
    expect(await screen.findByText('All caught up!')).toBeInTheDocument();
    expect(screen.getAllByTestId('slim-row')).toHaveLength(3);
  });
});
