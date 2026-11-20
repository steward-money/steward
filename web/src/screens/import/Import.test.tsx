import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { letTestingLibraryDriveFakeTimers, useFakeTimers } from '../../test-clock';
import { callsTo, reply, renderAt, stubFetch, type Route } from '../../test-utils';
import { ACCT_BANK, ACCT_CARD, PREVIEW, staged, statementLine } from './fixtures';
import Import from './Import';

const fx = vi.hoisted(() => ({ acorn: vi.fn(() => () => {}), winMoment: vi.fn(() => () => {}) }));
vi.mock('../../celebrate/celebrate', () => fx);

beforeEach(() => { vi.clearAllMocks(); useFakeTimers(); letTestingLibraryDriveFakeTimers(); sessionStorage.clear(); localStorage.clear(); });
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

const COMMIT = { set_id: 5, imported: 2486, accounts: 3, account: null, skipped: 416, months: 22, batch_ids: [9, 10, 11], statements: 0, added_up: 0, kept_balances: [] };
const byName: Record<string, ReturnType<typeof staged>> = {
  'chase.csv': staged({ name: 'chase.csv', state: 'known', label: 'Chase checking', rows: 1204, account_id: ACCT_BANK.id, suggested_by: 'name' }),
  'activity.csv': staged({ name: 'activity.csv', state: 'new' }),
};
function routes(extra: Record<string, Route> = {}): Record<string, Route> {
  return {
    '/api/accounts': { accounts: [ACCT_CARD, ACCT_BANK] }, '/api/import/batches': { imports: [] },
    '/api/import/statements': { statements: [], gateway: 'answering' },
    'POST /api/import/files': (_u, init) => {
      const name = decodeURIComponent((init.headers as Record<string, string>)['X-Filename']);
      return reply({ batch: 'b_1', files: [{ ...(byName[name] ?? staged({ name })), name }] }, 201);
    },
    // The preview's Look rows name the token the request sent, as the server's do.
    'POST /api/import/batch/preview': (_u, init) => {
      const token = (JSON.parse(String(init.body)) as { files: { token: string }[] }).files[0].token;
      return reply({ ...PREVIEW, look: { ...PREVIEW.look, same: PREVIEW.look.same.map((s) => ({ ...s, token })) } });
    },
    'POST /api/import/batch/commit': () => reply(COMMIT, 201),
    'POST /api/import/sets/5/rollback': { deleted: 2486 },
    ...extra,
  };
}
const file = (name: string, size = 100) => { const f = new File(['x'], name, { type: 'text/csv' }); Object.defineProperty(f, 'size', { value: size }); return f; };
function mount(book = {}) {
  const r = renderAt(<Import />, book, '/settings/import');
  const input = r.container.querySelector('input[type=file]') as HTMLInputElement;
  return { ...r, add: (...files: File[]) => fireEvent.change(input, { target: { files } }) };
}

describe('Import from the first run', () => {
  it('shows Back to setup to the money step from welcome, and not otherwise', () => {
    stubFetch(routes());
    const r = renderAt(<Import />, {}, '/settings/import?from=welcome');
    expect(screen.getByRole('link', { name: 'Back to setup' })).toHaveAttribute('href', '/b/ours/welcome?step=money');
    r.unmount();
    renderAt(<Import />, {}, '/settings/import');
    expect(screen.queryByRole('link', { name: 'Back to setup' })).toBeNull();
  });
});

describe('Import, files', () => {
  it('starts with the drop zone, a real button and the formats line', async () => {
    stubFetch(routes());
    mount();
    expect(screen.getByRole('heading', { name: 'Import', level: 2 })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Drop bank files here' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Choose files' })).toBeInTheDocument();
    expect(screen.getByText('CSV, OFX, QFX or PDF · 5 MB each, PDFs 15 MB · up to 30 files')).toBeInTheDocument();
    expect(await screen.findByText('Nothing imported yet.')).toBeInTheDocument();
  });

  it('refuses a 6 MB file and a 31st file with no request', async () => {
    const fn = stubFetch(routes());
    const { add } = mount();
    add(file('big.csv', 6 * 1024 * 1024));
    expect(await screen.findByText('Bigger than 5 MB. Download a shorter date range and try again.')).toBeInTheDocument();
    add(...Array.from({ length: 31 }, (_, i) => file(`f${i}.csv`)));
    expect(await screen.findByText('A batch holds up to 30 files. Import these, then add the rest.')).toBeInTheDocument();
    await waitFor(() => expect(callsTo(fn, 'POST', '/api/import/files')).toHaveLength(30));
    expect(callsTo(fn, 'POST', '/api/import/files').some((c) => (c.init.headers as Record<string, string>)['X-Filename'] === 'big.csv')).toBe(false);
  });

  it('reads the first file alone, then three at a time, and says so once', async () => {
    const waiting: (() => void)[] = [];
    const fn = stubFetch(routes({ 'POST /api/import/files': (_u, init) => new Promise((done) => {
      const name = decodeURIComponent((init.headers as Record<string, string>)['X-Filename']);
      waiting.push(() => done(reply({ batch: 'b_1', files: [staged({ name, state: 'known', label: 'PayPal' })] }, 201)));
    }) }));
    const { add } = mount();
    add(...['a', 'b', 'c', 'd', 'e'].map((n) => file(`${n}.csv`)));
    expect(screen.getByRole('status')).toHaveTextContent('Reading 5 files…');
    await waitFor(() => expect(callsTo(fn, 'POST', '/api/import/files')).toHaveLength(1));
    act(() => waiting.shift()!());
    await waitFor(() => expect(callsTo(fn, 'POST', '/api/import/files')).toHaveLength(4));
    expect(callsTo(fn, 'POST', '/api/import/files').slice(1).every((c) => c.url.includes('?batch=b_1'))).toBe(true);
    while (waiting.length) act(() => waiting.shift()!());
    await waitFor(() => expect(callsTo(fn, 'POST', '/api/import/files')).toHaveLength(5));
    while (waiting.length) act(() => waiting.shift()!());
    expect(await screen.findByText('5 files read')).toBeInTheDocument();
  });

  it('tags each file and pre-selects only the account the server suggested', async () => {
    stubFetch(routes());
    const { add } = mount();
    add(file('chase.csv'), file('activity.csv'));
    expect(await screen.findByText('Chase checking · 1,204 rows')).toBeInTheDocument();
    expect(await screen.findByText('New layout · 1 question')).toBeInTheDocument();
    expect((screen.getByLabelText('Account for chase.csv') as HTMLSelectElement).value).toBe(String(ACCT_BANK.id));
    expect((screen.getByLabelText('Account for activity.csv') as HTMLSelectElement).value).toBe('');
    expect(within(screen.getByLabelText('Account for activity.csv')).getByRole('option', { name: 'Choose an account' })).toBeInTheDocument();
  });

  it('an ambiguous sign keeps Preview disabled and says why', async () => {
    stubFetch(routes());
    const { add } = mount();
    add(file('activity.csv'));
    fireEvent.change(await screen.findByLabelText('Account for activity.csv'), { target: { value: String(ACCT_BANK.id) } });
    const preview = screen.getByRole('button', { name: 'Preview' });
    expect(preview).toBeDisabled();
    expect(preview).toHaveAccessibleDescription('Answer the question for activity.csv');
    fireEvent.click(screen.getByRole('radio', { name: 'Money out is positive' }));
    fireEvent.click(screen.getByRole('button', { name: 'Use this layout' }));
    expect(screen.getByText('First CU · new layout')).toBeInTheDocument();
    expect(preview).toBeEnabled();
  });

  it('offers New account… in a household book and the inventory link in a parent book', async () => {
    stubFetch(routes());
    const first = mount();
    first.add(file('activity.csv'));
    const select = await screen.findByLabelText('Account for activity.csv');
    expect(within(select).getByRole('option', { name: 'New account…' })).toBeInTheDocument();
    fireEvent.change(select, { target: { value: 'new' } });
    const sheet = screen.getByRole('dialog', { name: 'Add account' });
    expect((within(sheet).getByLabelText('Name') as HTMLInputElement).value).toBe('activity');
    first.unmount();
    sessionStorage.clear();
    const parent = mount({ slug: 'mom', kind: 'parent' });
    parent.add(file('activity.csv'));
    const again = await screen.findByLabelText('Account for activity.csv');
    expect(within(again).queryByRole('option', { name: 'New account…' })).toBeNull();
    expect(screen.getByRole('link', { name: 'Add an account in Inventory' })).toBeInTheDocument();
  });

  it('remove collapses the row, says so with Undo, and lets the staging go when the toast does', async () => {
    const fn = stubFetch(routes({ 'DELETE /api/import/files/': () => new Response(null, { status: 204 }) }));
    const { add } = mount();
    add(file('chase.csv'));
    await screen.findByText('Chase checking · 1,204 rows');
    fireEvent.click(screen.getByRole('button', { name: 'Remove chase.csv' }));
    expect(screen.getByText('Removed chase.csv')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
    expect(screen.getByText('Chase checking · 1,204 rows')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Remove chase.csv' }));
    act(() => { vi.advanceTimersByTime(10_000); });
    await waitFor(() => expect(callsTo(fn, 'DELETE', '/api/import/files/')).toHaveLength(1));
    expect(screen.queryByText('chase.csv')).toBeNull();
  });
});

async function toPreview() {
  const r = mount();
  r.add(file('chase.csv'));
  await screen.findByText('Chase checking · 1,204 rows');
  fireEvent.click(await screen.findByRole('button', { name: 'Preview' }));
  await screen.findByRole('heading', { name: 'Preview', level: 3 });
  return r;
}

describe('Import, preview and after', () => {
  it('Preview moves focus to step 2, and Back keeps everything', async () => {
    stubFetch(routes());
    await toPreview();
    expect(screen.getByRole('heading', { name: 'Preview', level: 3 })).toHaveFocus();
    fireEvent.click(screen.getByRole('button', { name: 'Back' }));
    expect((screen.getByLabelText('Account for chase.csv') as HTMLSelectElement).value).toBe(String(ACCT_BANK.id));
    expect(screen.getByRole('button', { name: 'Preview' })).toHaveFocus();
  });

  it('Import anyway sends the chosen row and reloads the preview', async () => {
    const fn = stubFetch(routes());
    await toPreview();
    fireEvent.click(screen.getByRole('button', { name: 'Look: Looks the same, check' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Import anyway' }));
    fireEvent.click(screen.getByRole('button', { name: 'Done' }));
    await waitFor(() => expect(callsTo(fn, 'POST', '/api/import/batch/preview')).toHaveLength(2));
    const body = callsTo(fn, 'POST', '/api/import/batch/preview')[1].body as { files: { include: number[] }[] };
    expect(body.files[0].include).toEqual([17]);
  });

  it('a 409 re-renders with the new numbers and says something changed', async () => {
    stubFetch(routes({ 'POST /api/import/batch/commit': () => reply({ error: 'Something changed since the preview. Check the new numbers.',
      preview: { ...PREVIEW, new: 2480, digest: 'beef' } }, 409) }));
    await toPreview();
    fireEvent.click(screen.getByRole('button', { name: 'Import 2,486' }));
    expect(await screen.findByText('Something changed since the preview. Check the new numbers.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Import 2,480' })).toBeInTheDocument();
  });

  it('imports, says how many into how many accounts with Undo and the joy line, and Undo undoes the batch', async () => {
    const fn = stubFetch(routes());
    await toPreview();
    fireEvent.click(screen.getByRole('button', { name: 'Import 2,486' }));
    expect(await screen.findByText('Imported 2,486 into 3 accounts')).toBeInTheDocument();
    expect(screen.getByText("That's 22 months of history in one go.")).toHaveClass('imp-joy--lively');
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
    expect(await screen.findByText('Import undone.')).toBeInTheDocument();
    expect(callsTo(fn, 'POST', '/api/import/sets/5/rollback')).toHaveLength(1);
  });

  it('one account reads by name; 11 months has no joy line; Celebrations off keeps it still', async () => {
    stubFetch(routes({ 'POST /api/import/batch/commit': () => reply({ ...COMMIT, accounts: 1, account: 'First CU', months: 11 }, 201) }));
    await toPreview();
    fireEvent.click(screen.getByRole('button', { name: 'Import 2,486' }));
    expect(await screen.findByText('Imported 2,486 into First CU')).toBeInTheDocument();
    expect(screen.queryByText(/months of history/)).toBeNull();
  });

  it('the joy line is static with Celebrations off', async () => {
    localStorage.setItem('steward-celebrations', 'off');
    stubFetch(routes());
    await toPreview();
    fireEvent.click(screen.getByRole('button', { name: 'Import 2,486' }));
    expect(await screen.findByText("That's 22 months of history in one go.")).not.toHaveClass('imp-joy--lively');
  });

  it('an expired batch says so and keeps the names', async () => {
    stubFetch(routes({ 'POST /api/import/batch/preview': () => reply({ error: 'These files expired. Add them again.' }, 410) }));
    const r = mount();
    r.add(file('chase.csv'));
    await screen.findByText('Chase checking · 1,204 rows');
    fireEvent.click(await screen.findByRole('button', { name: 'Preview' }));
    expect(await screen.findByText('These files expired. Add them again.')).toBeInTheDocument();
    expect(screen.getByText('chase.csv')).toBeInTheDocument();
    expect(screen.queryByLabelText('Account for chase.csv')).toBeNull();
  });
});

const pdfFile = (name: string, size = 1000) => { const f = new File(['%PDF-'], name, { type: 'application/pdf' }); Object.defineProperty(f, 'size', { value: size }); return f; };

describe('Import, statements', () => {
  it('sends a PDF raw with its name, two at a time, and refuses one over 15 MB with no request', async () => {
    let id = 0;
    const fn = stubFetch(routes({
      'POST /api/import/statements': () => reply({ statement: { id: (id += 1) } }, 201),
    }));
    const { add } = mount();
    add(pdfFile('huge.pdf', 16 * 1024 * 1024), pdfFile('a.pdf'), pdfFile('b.pdf'), pdfFile('c.pdf'));
    expect(await screen.findByText('Bigger than 15 MB. Download it again or split it.')).toBeInTheDocument();
    await waitFor(() => expect(callsTo(fn, 'POST', '/api/import/statements')).toHaveLength(3));
    const sent = callsTo(fn, 'POST', '/api/import/statements');
    expect(sent.map((c) => (c.init.headers as Record<string, string>)['Content-Type'])).toEqual(['application/pdf', 'application/pdf', 'application/pdf']);
    expect(sent.map((c) => decodeURIComponent((c.init.headers as Record<string, string>)['X-Filename']))).toEqual(['a.pdf', 'b.pdf', 'c.pdf']);
    expect(sent[0].init.body).toBeInstanceOf(File);
  });

  it('a 31st file, PDFs counted, is refused with no request', async () => {
    const fn = stubFetch(routes({ 'POST /api/import/statements': () => reply({ statement: { id: 1 } }, 201) }));
    const { add } = mount();
    add(...Array.from({ length: 31 }, (_, i) => pdfFile(`s${i}.pdf`)));
    expect(await screen.findByText('A batch holds up to 30 files. Import these, then add the rest.')).toBeInTheDocument();
    await waitFor(() => expect(callsTo(fn, 'POST', '/api/import/statements')).toHaveLength(30));
    expect(callsTo(fn, 'POST', '/api/import/statements').some((c) => decodeURIComponent((c.init.headers as Record<string, string>)['X-Filename']) === 's30.pdf')).toBe(false);
  });

  it('shows a Reading… line while a PDF goes up, and counts the first CSV in the batch it sends', async () => {
    const fn = stubFetch(routes({ 'POST /api/import/statements': () => new Promise(() => {}) }));
    const { add } = mount();
    add(file('chase.csv'), pdfFile('a.pdf'));
    await waitFor(() => expect(callsTo(fn, 'POST', '/api/import/statements')).toHaveLength(1));
    expect(callsTo(fn, 'POST', '/api/import/statements')[0].url).toContain('?batch=b_1');
    const line = screen.getByText('a.pdf').closest('li') as HTMLElement;
    expect(line).toHaveAttribute('aria-busy', 'true');
    expect(within(line).getByText('Reading…')).toBeInTheDocument();
  });

  it('polls every 3 seconds while a line reads and only while the tab is visible', async () => {
    let list = { statements: [statementLine({ state: 'reading', pages: null })], gateway: 'answering' };
    const fn = stubFetch(routes({ '/api/import/statements': () => reply(list) }));
    mount();
    expect(await screen.findByText('Reading…')).toBeInTheDocument();
    const before = callsTo(fn, 'GET', '/api/import/statements').length;
    await act(async () => { vi.advanceTimersByTime(3000); });
    expect(callsTo(fn, 'GET', '/api/import/statements').length).toBe(before + 1);
    Object.defineProperty(document, 'visibilityState', { value: 'hidden', configurable: true });
    document.dispatchEvent(new Event('visibilitychange', { bubbles: true }));
    await act(async () => { vi.advanceTimersByTime(9000); });
    expect(callsTo(fn, 'GET', '/api/import/statements').length).toBe(before + 1);
    Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true });
    list = { statements: [statementLine()], gateway: 'answering' };
    document.dispatchEvent(new Event('visibilitychange', { bubbles: true }));
    expect(await screen.findByText('Summit Card Services · Mar 2026 · 24 rows · adds up ✓')).toBeInTheDocument();
    const settled = callsTo(fn, 'GET', '/api/import/statements').length;
    await act(async () => { vi.advanceTimersByTime(9000); });
    expect(callsTo(fn, 'GET', '/api/import/statements').length).toBe(settled);     // nothing reads: polling stops
  });

  it('says the newest change politely, and not on the first load', async () => {
    let list = { statements: [statementLine({ state: 'reading', pages: null })], gateway: 'answering' };
    stubFetch(routes({ '/api/import/statements': () => reply(list) }));
    mount();
    expect(await screen.findByText('Reading…')).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('');
    list = { statements: [statementLine()], gateway: 'answering' };
    await act(async () => { vi.advanceTimersByTime(3000); });
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Statement_0324.pdf: Summit Card Services · Mar 2026 · 24 rows · adds up ✓'));
  });

  it('says Preview 1 ready while another reads, sends only the ready ones, and keeps the rest', async () => {
    const lines = [statementLine({ id: 1, name: 'a.pdf', account_id: ACCT_CARD.id }), statementLine({ id: 2, name: 'b.pdf', state: 'layout' })];
    const fn = stubFetch(routes({ '/api/import/statements': { statements: lines, gateway: 'answering' },
      'POST /api/import/batch/preview': () => reply({ ...PREVIEW, files: [], look: { ...PREVIEW.look, same: [] }, balances: { n: 1, kept: 0, rows: [] } }) }));
    mount();
    fireEvent.click(await screen.findByRole('button', { name: 'Preview 1 ready' }));
    await waitFor(() => expect(callsTo(fn, 'POST', '/api/import/batch/preview')).toHaveLength(1));
    expect(callsTo(fn, 'POST', '/api/import/batch/preview')[0].body).toEqual(
      { batch: null, files: [], statements: [{ id: 1, account_id: ACCT_CARD.id, include: [] }], simplefin_include: [] });
  });

  it('says once under the list when the gateway needs an update', async () => {
    stubFetch(routes({ '/api/import/statements': { statements: [statementLine({ state: 'waiting' })], gateway: 'old' } }));
    mount();
    expect(await screen.findAllByText('The gateway needs an update to read new layouts.')).toHaveLength(1);
  });

  it('a removed line collapses with Undo and goes for good when the toast does', async () => {
    const gone = () => new Response(null, { status: 204 });
    const fn = stubFetch(routes({ '/api/import/statements': { statements: [statementLine({ id: 7, name: 'a.pdf' })], gateway: 'answering' },
      'DELETE /api/import/statements/7': gone, 'POST /api/import/statements/7/restore': { statement: statementLine({ id: 7 }) } }));
    mount();
    fireEvent.click(await screen.findByRole('button', { name: 'Remove a.pdf' }));
    expect(await screen.findByText('Removed a.pdf')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
    await waitFor(() => expect(callsTo(fn, 'POST', '/api/import/statements/7/restore')).toHaveLength(1));
  });

  it('the toast running out removes the statement for good', async () => {
    const gone = () => new Response(null, { status: 204 });
    const fn = stubFetch(routes({ '/api/import/statements': { statements: [statementLine({ id: 7, name: 'a.pdf' })], gateway: 'answering' },
      'DELETE /api/import/statements/7': gone }));
    mount();
    fireEvent.click(await screen.findByRole('button', { name: 'Remove a.pdf' }));
    await screen.findByText('Removed a.pdf');
    act(() => { vi.advanceTimersByTime(10_000); });
    await waitFor(() => expect(callsTo(fn, 'DELETE', '/api/import/statements/7')).toHaveLength(2));
    expect(callsTo(fn, 'DELETE', '/api/import/statements/7').map((c) => c.url.includes('final=1'))).toEqual([false, true]);
  });

  it('importing settles a pending removal so the one toast left is the import’s Undo', async () => {
    const a = statementLine({ id: 1, name: 'a.pdf', account_id: ACCT_CARD.id });
    let list = [a, statementLine({ id: 7, name: 'b.pdf', account_id: ACCT_BANK.id })];
    const fn = stubFetch(routes({ '/api/import/statements': () => reply({ statements: list, gateway: 'answering' }),
      'DELETE /api/import/statements/7': () => { list = [a]; return new Response(null, { status: 204 }); },
      'POST /api/import/batch/preview': () => reply({ ...PREVIEW, files: [], look: { ...PREVIEW.look, same: [] } }) }));
    mount();
    fireEvent.click(await screen.findByRole('button', { name: 'Remove b.pdf' }));
    await screen.findByText('Removed b.pdf');
    fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Import 2,486' }));
    expect(await screen.findByText('Imported 2,486 into 3 accounts')).toBeInTheDocument();
    const toasts = document.querySelectorAll('.undo-toast');
    expect(toasts).toHaveLength(1);
    expect(toasts[0]).toHaveTextContent('Imported 2,486 into 3 accounts');
    await waitFor(() => expect(callsTo(fn, 'DELETE', '/api/import/statements/7').map((c) => c.url.includes('final=1'))).toEqual([false, true]));
  });
});

describe('Import, focus after a look', () => {
  const FACTS = { kind: 'card', institution: 'Summit Card Services', label: null, last4: '4417', period: ['2026-03-01', '2026-03-31'],
    opening_cents: 120410, closing_cents: 231855, opening_label: 'Previous Balance', closing_label: 'New Balance' };
  const detail = (id: number, state: string) => ({ id, state, gap_cents: state === 'off' ? -1240 : 0, anyway: false, pages: 2, statement: FACTS,
    unreadable: [], sections: [], rows: [{ i: 0, date: '2026-03-15', description: 'SHELL OIL', amount_cents: -4120, page: 1, section: null, state: 'read' }] });
  const a = (over = {}) => statementLine({ id: 1, name: 'a.pdf', account_id: ACCT_CARD.id, ...over });
  const b = (over = {}) => statementLine({ id: 2, name: 'b.pdf', state: 'layout', ...over });

  it('Looks right moves focus to the next line that needs a look', async () => {
    let list = [a({ state: 'layout' }), b()];
    stubFetch(routes({ '/api/import/statements': () => reply({ statements: list, gateway: 'answering' }),
      '/api/import/statements/1': detail(1, 'layout'),
      'POST /api/import/statements/1/layout': () => { list = [a(), b()]; return reply({ statement: a() }); } }));
    mount();
    const [first] = await screen.findAllByRole('button', { name: 'Check the layout · 1 look' });
    fireEvent.click(first);
    fireEvent.click(await screen.findByRole('button', { name: 'Looks right' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Check the layout · 1 look' })).toHaveFocus());
    expect(screen.getByRole('button', { name: 'Check the layout · 1 look' }).closest('li')).toHaveTextContent('b.pdf');
  });

  it('Looks right with nothing else to look at moves focus to Preview', async () => {
    let list = [a({ state: 'layout' })];
    stubFetch(routes({ '/api/import/statements': () => reply({ statements: list, gateway: 'answering' }),
      '/api/import/statements/1': detail(1, 'layout'),
      'POST /api/import/statements/1/layout': () => { list = [a()]; return reply({ statement: a() }); } }));
    mount();
    fireEvent.click(await screen.findByRole('button', { name: 'Check the layout · 1 look' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Looks right' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Preview' })).toHaveFocus());
  });

  it('Done returns focus to the line', async () => {
    let list = [a({ state: 'off', gap_cents: -1240 })];
    stubFetch(routes({ '/api/import/statements': () => reply({ statements: list, gateway: 'answering' }),
      '/api/import/statements/1': detail(1, 'off'),
      'POST /api/import/statements/1/rows': () => { list = [a()]; return reply({ state: 'ready', gap_cents: 0, sections: [], rows: detail(1, 'ready').rows }); } }));
    mount();
    fireEvent.click(await screen.findByRole('button', { name: 'Off by $12.40 · Check rows' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Done' }));
    await waitFor(() => expect(screen.getByLabelText('Account for a.pdf')).toHaveFocus());
  });
});

describe('Import, the statements joy line', () => {
  const two = [statementLine({ id: 1, name: 'a.pdf', account_id: ACCT_CARD.id }), statementLine({ id: 2, name: 'b.pdf', account_id: ACCT_BANK.id })];
  async function commit(over: Record<string, unknown>) {
    stubFetch(routes({ '/api/import/statements': { statements: two, gateway: 'answering' },
      'POST /api/import/batch/preview': () => reply({ ...PREVIEW, files: [], look: { ...PREVIEW.look, same: [] } }),
      'POST /api/import/batch/commit': () => reply({ ...COMMIT, months: 3, ...over }, 201) }));
    mount();
    fireEvent.click(await screen.findByRole('button', { name: 'Preview' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Import 2,486' }));
    await screen.findByText(/^Imported/);
  }

  it('two statements that all add up say so, with a check mark', async () => {
    await commit({ statements: 2, added_up: 2 });
    expect(screen.getByText('2 statements, every one adds up.')).toHaveClass('imp-joy--lively');
  });

  it('one imported anyway shows no such line', async () => {
    await commit({ statements: 2, added_up: 1 });
    expect(screen.queryByText(/every one adds up/)).toBeNull();
  });

  it('Celebrations off shows none, and the months rule applies unchanged', async () => {
    localStorage.setItem('steward-celebrations', 'off');
    await commit({ statements: 2, added_up: 2, months: 14 });
    expect(screen.queryByText(/every one adds up/)).toBeNull();
    expect(screen.getByText("That's 14 months of history in one go.")).toBeInTheDocument();
  });

  it('reduced motion shows the line without the lively class', async () => {
    vi.stubGlobal('matchMedia', (q: string) => ({ matches: q.includes('reduce'), media: q, addEventListener: () => {}, removeEventListener: () => {},
      addListener: () => {}, removeListener: () => {}, onchange: null, dispatchEvent: () => false }));
    await commit({ statements: 2, added_up: 2 });
    expect(screen.getByText('2 statements, every one adds up.')).not.toHaveClass('imp-joy--lively');
  });
});

describe('Import, statement changes settle before Preview', () => {
  it('keeps Preview waiting while an account change is on its way, then lets it through', async () => {
    let release: () => void = () => {};
    const line = statementLine({ id: 1, name: 'a.pdf', account_id: ACCT_CARD.id });
    let list = [line];
    stubFetch(routes({ '/api/import/statements': () => reply({ statements: list, gateway: 'answering' }),
      'PATCH /api/import/statements/1': () => new Promise((res) => {
        release = () => { list = [{ ...line, account_id: ACCT_BANK.id }]; res(reply({ statement: list[0] })); };
      }) }));
    mount();
    const preview = await screen.findByRole('button', { name: 'Preview' });
    expect(preview).toBeEnabled();
    fireEvent.change(screen.getByLabelText('Account for a.pdf'), { target: { value: String(ACCT_BANK.id) } });
    await waitFor(() => expect(screen.getByRole('button', { name: 'Preview' })).toBeDisabled());
    await act(async () => { release(); });
    await waitFor(() => expect(screen.getByRole('button', { name: 'Preview' })).toBeEnabled());
    expect(screen.getByLabelText('Account for a.pdf')).toHaveValue(String(ACCT_BANK.id));      // the refreshed list has landed
  });

  it('reopening a line shows the rows and gap the book holds now, not the cached ones', async () => {
    const FACTS = { kind: 'card', institution: 'Summit Card Services', label: null, last4: '4417', period: ['2026-03-01', '2026-03-31'],
      opening_cents: 120410, closing_cents: 231855, opening_label: 'Previous Balance', closing_label: 'New Balance' };
    let amount = -4120;
    let gap = -1240;
    const rows = () => [{ i: 0, date: '2026-03-15', description: 'SHELL OIL', amount_cents: amount, page: 1, section: null, state: 'read' }];
    stubFetch(routes({
      '/api/import/statements': { statements: [statementLine({ id: 1, name: 'a.pdf', state: 'off', gap_cents: -1240, account_id: ACCT_CARD.id })], gateway: 'answering' },
      '/api/import/statements/1': () => reply({ id: 1, state: 'off', gap_cents: gap, anyway: false, pages: 2, statement: FACTS, unreadable: [], sections: [], rows: rows() }),
      'POST /api/import/statements/1/rows': () => { amount = -7658; gap = 0; return reply({ state: 'off', gap_cents: gap, sections: [], rows: rows() }); },
    }));
    mount();
    const tag = await screen.findByRole('button', { name: 'Off by $12.40 · Check rows' });
    fireEvent.click(tag);
    const field = await screen.findByLabelText('Amount, row 1');
    fireEvent.change(field, { target: { value: '76.58' } });
    fireEvent.blur(field);
    expect(await screen.findByText('Adds up ✓')).toBeInTheDocument();
    fireEvent.click(tag);                                                           // close it
    await waitFor(() => expect(screen.queryByLabelText('Amount, row 1')).toBeNull());
    fireEvent.click(tag);                                                           // and reopen
    await waitFor(() => expect(screen.getByLabelText('Amount, row 1')).toHaveValue('76.58'));
    expect(screen.getByText('Adds up ✓')).toBeInTheDocument();
  });
});

describe('the receipt and the hiccups (phase 17 spec 3.7)', () => {
  it('every commit says how many rows came in and how many wait', async () => {
    stubFetch(routes({ 'POST /api/import/batch/commit': () => reply({ ...COMMIT, waiting: 12 }, 201) }));
    await toPreview();
    fireEvent.click(screen.getByRole('button', { name: 'Import 2,486' }));
    expect(await screen.findByTestId('import-done')).toHaveTextContent('2,486 rows in. 12 to sort in Review.');
    expect(screen.getByTestId('import-done')).not.toHaveAttribute('role');     // said through the polite region (issue #51)
    expect(screen.getByText('Imported 2,486 into 3 accounts')).toBeInTheDocument();       // the receipt and its Undo stay
    expect(screen.getByRole('button', { name: 'Undo' })).toBeInTheDocument();
  });

  it('nothing left to sort says so', async () => {
    stubFetch(routes({ 'POST /api/import/batch/commit': () => reply({ ...COMMIT, waiting: 0 }, 201) }));
    await toPreview();
    fireEvent.click(screen.getByRole('button', { name: 'Import 2,486' }));
    expect(await screen.findByTestId('import-done')).toHaveTextContent('2,486 rows in, and nothing to sort.');
  });

  it('one row, and the one that waits, read in the singular (R3)', async () => {
    stubFetch(routes({ 'POST /api/import/batch/commit': () => reply({ ...COMMIT, imported: 1, waiting: 1 }, 201) }));
    await toPreview();
    fireEvent.click(screen.getByRole('button', { name: 'Import 2,486' }));
    expect(await screen.findByTestId('import-done')).toHaveTextContent('1 row in. 1 to sort in Review.');
  });

  it('says why rows wait: to sort, and possible duplicates', async () => {
    stubFetch(routes({ 'POST /api/import/batch/commit': () => reply({ ...COMMIT, imported: 3, waiting: 1, duplicates: 1 }, 201) }));
    await toPreview();
    fireEvent.click(screen.getByRole('button', { name: 'Import 2,486' }));
    expect(await screen.findByTestId('import-done')).toHaveTextContent('3 rows in. 1 to sort and 1 possible duplicate in Review.');
  });

  it('no line and no coins when nothing was added', async () => {
    stubFetch(routes({ 'POST /api/import/batch/commit': () => reply({ ...COMMIT, imported: 0, waiting: 0 }, 201) }));
    await toPreview();
    fireEvent.click(screen.getByRole('button', { name: 'Import 2,486' }));
    await screen.findByText(/^Imported 0/);
    expect(screen.queryByTestId('import-done')).toBeNull();
    expect(document.querySelector('.cel-moment')).toBeNull();
  });

  it("a file Steward couldn't read shows the oops face beside its message", async () => {
    stubFetch(routes({ 'POST /api/import/files': () => reply({ error: "Steward can't read this file." }, 422) }));
    mount().add(file('odd.csv'));
    const tag = await screen.findByText("Steward can't read this file.");
    expect(tag.closest('.imp-row')!.querySelector('svg.acorn')).toHaveAttribute('data-face', 'oops');
  });

  it('a parent book: the line, but no oops face', async () => {
    stubFetch(routes({ 'POST /api/import/files': () => reply({ error: "Steward can't read this file." }, 422) }));
    mount({ kind: 'parent' }).add(file('odd.csv'));
    await screen.findByText("Steward can't read this file.");
    expect(document.querySelector('svg.acorn')).toBeNull();
  });

  it('a parent book: warm words only, no face, no motion, and the receipt and Undo stay', async () => {
    stubFetch(routes({ 'POST /api/import/batch/commit': () => reply({ ...COMMIT, waiting: 12 }, 201) }));
    const r = mount({ kind: 'parent' });
    r.add(file('chase.csv'));
    await screen.findByText('Chase checking · 1,204 rows');
    fireEvent.click(await screen.findByRole('button', { name: 'Preview' }));
    await screen.findByRole('heading', { name: 'Preview', level: 3 });
    fireEvent.click(screen.getByRole('button', { name: 'Import 2,486' }));
    expect(await screen.findByTestId('import-done')).toHaveTextContent('2,486 rows in. 12 to sort in Review.');
    expect(screen.getByText('Imported 2,486 into 3 accounts')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Undo' })).toBeInTheDocument();
    expect(fx.winMoment).not.toHaveBeenCalled();
    expect(document.querySelector('.cel-moment')).toBeNull();
    expect(document.querySelector('svg.acorn')).toBeNull();
  });

  it('under reduced motion the line, the receipt and Undo stay, and no coins are asked for', async () => {
    vi.stubGlobal('matchMedia', (q: string) => ({ matches: q.includes('reduce'), media: q, addEventListener: () => {}, removeEventListener: () => {} }));
    stubFetch(routes({ 'POST /api/import/batch/commit': () => reply({ ...COMMIT, waiting: 12 }, 201) }));
    await toPreview();
    fireEvent.click(screen.getByRole('button', { name: 'Import 2,486' }));
    expect(await screen.findByTestId('import-done')).toHaveTextContent('2,486 rows in. 12 to sort in Review.');
    expect(screen.getByText('Imported 2,486 into 3 accounts')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Undo' })).toBeInTheDocument();
    expect(fx.winMoment).not.toHaveBeenCalled();
  });

  it('with celebrations off the line, the receipt and Undo stay, and no coins are asked for', async () => {
    localStorage.setItem('steward-celebrations', 'off');
    stubFetch(routes({ 'POST /api/import/batch/commit': () => reply({ ...COMMIT, waiting: 12 }, 201) }));
    await toPreview();
    fireEvent.click(screen.getByRole('button', { name: 'Import 2,486' }));
    expect(await screen.findByTestId('import-done')).toHaveTextContent('2,486 rows in. 12 to sort in Review.');
    expect(screen.getByRole('button', { name: 'Undo' })).toBeInTheDocument();
    expect(fx.winMoment).not.toHaveBeenCalled();
  });
});
