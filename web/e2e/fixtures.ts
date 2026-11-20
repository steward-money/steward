import { expect, test, type Locator, type Page } from '@playwright/test';
import { GATEWAY_PORT, VAULT_PORT } from './ports';
declare const Buffer: { from(s: string): never }; // the e2e tsconfig carries no node types

export const DEMO = { alex: 'pw-demo-alex-2026', spouse: 'pw-demo-spouse-2026' } as const;

export async function loginAs(page: Page, username: string, password?: string) {
  const pw = password ?? (DEMO as Record<string, string>)[username] ?? 'pw-locky-demo-2026';
  const r = await page.request.post('/api/auth/login', { data: { username, password: pw, device_label: 'e2e' } });
  expect(r.ok()).toBeTruthy();
}

export async function logout(page: Page) {
  await page.request.post('/api/auth/logout');
}

/** A tab in the main navigation: the bottom bar on a phone, the rail on a laptop. */
export const tab = (page: Page, name: string) => page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name, exact: true });

/** Opens the You menu from the initials (the phone's top bar, or the foot of the laptop rail). */
export async function youMenu(page: Page) { await page.getByRole('button', { name: /^You: / }).click(); }


/** A line as the page shows it, not the copy of its words in a polite region (issues #23, #51: a line that appears is
 *  said through the page's or a sheet's announcer, which holds the same words). For a line that may be in a sheet,
 *  where looking inside main doesn't reach. */
export const shown = (scope: Page | Locator, text: string | RegExp, options?: { exact?: boolean }) => {
  const page = 'page' in scope ? scope.page() : scope;
  return scope.getByText(text, options).and(page.locator(':not([aria-live])'));
};

/** Move a range slider to whichever end it is not at, so the move always changes the value. */
export async function moveFar(page: Page, slider: Locator) {
  await slider.focus();
  const now = Number(await slider.inputValue());
  const max = Number(await slider.getAttribute('max'));
  await page.keyboard.press(now >= max ? 'Home' : 'End');
}

export const CHAT_PW = 'pw-chat-demo-2026';
/** The fake gateway beside the demo server (web/e2e/server.sh: the NAS port + 100). */
export const GATEWAY = `http://127.0.0.1:${GATEWAY_PORT}`;

export async function chatOn(page: Page, slug: string, on = true) {
  const r = await page.request.post(`/api/b/${slug}/chat/on`, { data: { on } });
  expect(r.ok()).toBeTruthy();
}

/** The NAS asks the gateway's health at most every 10 seconds; wait until it has seen the change. */
export async function waitForGateway(page: Page, slug: string, test: (s: { gateway: { state: string }; budget: { cap_line: string | null } | null }) => boolean) {
  await expect.poll(async () => test(await (await page.request.get(`/api/b/${slug}/chat`)).json()), { timeout: 20_000, intervals: [1_000] }).toBe(true);
}

/** Review screenshots of the chat screens (gitignored): light theme, taken after the fonts are ready. */
export async function chatShot(page: Page, project: string, name: string) {
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: `e2e/screenshots-chat/${name}-${project === 'phone' ? '390' : '1280'}.png` });
}

/** What a restore may take on top of the hook or test that calls it: the keeper restarts the server (its health ceiling
 *  is 60 s) and, the first time after the household's day turns, seeds the vault again (about 15 s). A ceiling sized to
 *  the work, not a wait: a quick restore still ends at once. */
const RESTORE_ROOM_MS = 90_000;

/** Puts the demo household back as server.sh seeded it (web/e2e/vault_keeper.py). Every spec on the demo vault calls
 *  it in test.beforeAll, so no spec sees what another spec, or the other project, did (flaky-test audit E2). It
 *  widens the calling hook's or test's own ceiling by RESTORE_ROOM_MS, so a reseed never eats a spec's 30 s. */
export async function restoreVault(): Promise<void> {
  const info = test.info();
  if (info.timeout > 0) info.setTimeout(info.timeout + RESTORE_ROOM_MS);
  const r = await fetch(`http://127.0.0.1:${VAULT_PORT}/restore`, { method: 'POST' });
  if (!r.ok) throw new Error(`the vault keeper answered ${r.status}: ${await r.text()}`);
}

/** Until every finite CSS animation and transition on the page has finished (a sheet sliding up), so a screenshot
 *  shows where things end, not a frame on the way. A condition with the test's ceiling, not a delay. */
export async function animationsSettled(page: Page): Promise<void> {
  await page.waitForFunction(() => document.getAnimations().every((a) =>
    a.playState !== 'running' || a.effect?.getComputedTiming().iterations === Infinity));
}

/** Review focus: switch Review to the list, for the specs that sort from it. The choice is kept per person on this
 *  device, so a reload in the same context stays in the list. */
export async function listView(page: Page): Promise<void> {
  const list = page.getByRole('group', { name: 'View' }).getByRole('button', { name: 'List' });
  await list.click();
  await expect(list).toHaveAttribute('aria-pressed', 'true');
}

/** The e2e server's own day (YYYY-MM-DD), as setasides.spec reads it: main's e2e has no pinned clock, so every date a
 *  spec writes is counted back from the day the server says it is. */
export async function serverDay(page: Page): Promise<string> {
  return ((await (await page.request.get('/api/b/ours/spending')).json()) as { as_of: string }).as_of;
}
/** The calendar day `n` days before an ISO date, as YYYY-MM-DD. */
export const daysBack = (iso: string, n: number) => {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d - n)).toISOString().slice(0, 10);
};
/** YYYY-MM-DD as a US bank writes it, MM/DD/YYYY. */
export const mdy = (iso: string) => `${iso.slice(5, 7)}/${iso.slice(8, 10)}/${iso.slice(0, 4)}`;

/** The import e2e's three bank files (synthetic, as tests/fixtures/banks/ has them), every date 1-70 days before the
 *  server's day: never in the future, and spaced so each file has a day over 12 (month/day is never asked). */
export function bankFiles(day: string): Record<'amex-basic.csv' | 'first-cu.csv' | 'bank.ofx', string> {
  const at = (n: number) => mdy(daysBack(day, n));
  const amex = ([[70, 'BLUE BOTTLE COFFEE', '6.50'], [68, 'AUTOPAY PAYMENT - THANK YOU', '-820.00'], [66, 'HARBOR GROCERY', '84.12'],
    [63, 'STREAMFLIX.COM', '15.49'], [40, 'AUTOPAY PAYMENT - THANK YOU', '-310.00'], [38, 'PINE STREET PHARMACY', '22.80'],
    [35, 'HARBOR GROCERY', '61.07'], [10, 'AUTOPAY PAYMENT - THANK YOU', '-245.00'], [7, 'CITY PARKING', '12.00'],
    [2, 'BLUE BOTTLE COFFEE', '7.25']] as const).map(([n, d, a]) => `${at(n)},${d},${a}`);
  const cu = ([[60, 'DEPOSIT NORTHWIND PAYROLL', '', '2100.00', 'Posted', '4100.00'], [45, 'POS HARBOR GROCERY', '-84.12', '', 'Posted', '4015.88'],
    [30, 'CHECK 1043', '-250.00', '', 'Posted', '3765.88'], [15, 'ATM WITHDRAWAL', '-60.00', '', 'Pending', '3705.88']] as const)
    .map(([n, d, out, inn, s, b]) => `000000001,${at(n)},,${d},${out},${inn},${s},${b}`);
  const ofxDay = (n: number) => daysBack(day, n).replaceAll('-', '');
  const trn = ([[50, '-54.12', '0001', 'GREENLEAF MARKET'], [35, '2100.00', '0002', 'NORTHWIND PAYROLL'], [20, '-118.40', '0003', 'CITY POWER']] as const)
    .map(([n, a, id, name]) => `<STMTTRN><TRNTYPE>${a.startsWith('-') ? 'DEBIT' : 'CREDIT'}<DTPOSTED>${ofxDay(n)}<TRNAMT>${a}<FITID>E2E${ofxDay(n)}${id}<NAME>${name}</STMTTRN>`);
  return {
    'amex-basic.csv': ['Date,Description,Amount', ...amex].join('\n') + '\n',
    'first-cu.csv': ['Account Number,Post Date,Check,Description,Debit,Credit,Status,Balance', ...cu].join('\n') + '\n',
    'bank.ofx': ['OFXHEADER:100', 'DATA:OFXSGML', 'VERSION:102', '', '<OFX>',
      '<BANKMSGSRSV1><STMTTRNRS><TRNUID>1<STATUS><CODE>0<SEVERITY>INFO</STATUS>',
      `<STMTRS><CURDEF>USD<BANKACCTFROM><BANKID>121000000<ACCTID>0000456789<ACCTTYPE>CHECKING</BANKACCTFROM><BANKTRANLIST>`,
      ...trn, '</BANKTRANLIST></STMTRS></STMTTRNRS></BANKMSGSRSV1>', '</OFX>'].join('\n') + '\n',
  };
}
/** One built file for setInputFiles. */
export const upload = (name: string, body: string) =>
  ({ name, mimeType: name.endsWith('.ofx') ? 'application/x-ofx' : 'text/csv', buffer: Buffer.from(body) });

/** The last day of the month `back` months before the server's day: a statement period that is past and recent. */
export function monthEnd(day: string, back: number): string {
  const [y, m] = day.split('-').map(Number);
  const end = new Date(Date.UTC(y, m - 1 - back + 1, 0));
  return end.toISOString().slice(0, 10);
}
/** A synthetic statement PDF from the fake gateway, ready for setInputFiles. */
export async function statementPdf(page: Page, name: string, sid: string, end: string, variant?: string) {
  const q = `sid=${sid}&end=${end}${variant ? `&variant=${variant}` : ''}`;
  const r = await page.request.get(`http://127.0.0.1:${GATEWAY_PORT}/__e2e/statement?${q}`);
  return { name, mimeType: 'application/pdf', buffer: await r.body() };
}

/** Imports one known-format file into one account through the batch API (stage, preview, commit), for specs that need
 *  imported rows. The file is the request body itself (plan Engineering choice 22). */
export async function importFile(page: Page, slug: string, accountId: number, name: string, body: string) {
  const staged = await (await page.request.post(`/api/b/${slug}/import/files`, {
    data: body, headers: { 'Content-Type': 'text/csv', 'X-Filename': encodeURIComponent(name) } })).json();
  const files = staged.files.map((f: { token: string }) => ({ token: f.token, account_id: accountId, layout: null, include: [] }));
  const req = { batch: staged.batch, files, simplefin_include: [] };
  const preview = await (await page.request.post(`/api/b/${slug}/import/batch/preview`, { data: req })).json();
  const done = await page.request.post(`/api/b/${slug}/import/batch/commit`, { data: { ...req, digest: preview.digest } });
  if (!done.ok()) throw new Error(`import answered ${done.status()}`);
  return (await done.json()) as { set_id: number; imported: number };
}
