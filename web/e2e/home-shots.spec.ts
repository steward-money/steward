import { test, type Page } from '@playwright/test';
import { loginAs, restoreVault } from './fixtures';
import { DEMO_PORT } from './ports';

// The demo household as server.sh seeded it, whatever ran before (flaky-test audit E2).
test.beforeAll(async () => { await restoreVault(); });

declare const process: { env: Record<string, string | undefined> };
const TASK = process.env.SHOTS_TASK;
type Home = Record<string, unknown> & { flow: Record<string, unknown>; signs: Record<string, unknown> };
const verdict = (opener: string, problems: unknown[], clauses: unknown[], extra: Record<string, unknown> = {}) =>
  ({ opener, problems, clauses, stale: null, best_month: false, ...extra });
const DINING = { category: 'dining', spent_cents: 64_000, target_cents: 50_000, over_cents: 14_000, over_level: 'clear' };
const SHOPPING = { category: 'shopping', spent_cents: 51_200, target_cents: 40_000, over_cents: 11_200, over_level: 'clear' };
const LINES = [
  { kind: 'pay', category: 'income', name: 'Northwind payroll', n: 1, places: 1, cents: 421_200 },
  { kind: 'bill', category: 'utilities', name: 'City Power', n: 1, places: 1, cents: -18_600 },
  { kind: 'large', category: 'taxes', name: 'County tax collector', n: 1, places: 1, cents: -190_000 },
  { kind: 'group', category: 'dining', name: null, n: 3, places: 3, cents: -18_000 },
];
const daysBack = (iso: string, n: number) => {
  const d = new Date(`${iso}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() - n);
  return d.toISOString().slice(0, 10);
};
/** A plan saved ten days before the server's day (#17: Home names the saved plan by its date), as the household's day
 *  the server sends (#52). */
const saved = (h: Home) => daysBack(h.as_of as string, 10);
/** A last look three days before the server's day, so Since shows in every state (Task 9 renders it). */
const since = (h: Home, lines: unknown[], more = 0, nw: number | null = null) => {
  const day = daysBack(h.as_of as string, 3);
  return { from: `${day}T19:00:00+00:00`, from_day: day, over_month: false, lines, more, net_worth_cents: nw };
};

/** The gate's three states: the demo's own answer, with the verdict, the figure, the signs and Since set for each. */
const STATES: Record<string, (h: Home) => Home> = {
  steady: (h) => ({ ...h,
    verdict: verdict('steady', [], [{ kind: 'spending_on_track' }, { kind: 'bills_paid' }, { kind: 'plan', age: 54, still: true, saved_day: saved(h) }], { best_month: true }),
    flow: { ...h.flow, in_cents: 638_000, out_cents: 514_000, net_cents: 124_000, payday: null },
    signs: { ...h.signs, flexible: { spent_cents: 314_000, target_cents: 420_000, rows: [] }, bills: { paid: 5, of: 5, late: [] } },
    since: since(h, LINES, 2, 210_000) }),
  payday: (h) => ({ ...h,
    verdict: verdict('mostly', [], [{ kind: 'behind_until_payday', little: true }, { kind: 'over_one', category: 'dining' }]),
    flow: { ...h.flow, in_cents: 210_000, out_cents: 251_000, net_cents: -41_000, payday: { day: 15 } },
    signs: { ...h.signs, flexible: { spent_cents: 251_000, target_cents: 420_000, rows: [DINING] }, bills: { paid: 2, of: 5, late: [] } },
    since: since(h, LINES.slice(0, 2)) }),
  over: (h) => ({ ...h,
    verdict: verdict('problem', [{ kind: 'late_bill', category: 'utilities', usual_day: 12 }], [{ kind: 'plan', age: 54, still: false, saved_day: saved(h) }], { stale: { n: 6, oldest_days: 29, newest_days: 29, account: null } }),
    flow: { ...h.flow, in_cents: 512_000, out_cents: 498_000, net_cents: 14_000, payday: null },
    signs: { ...h.signs, flexible: { spent_cents: 468_000, target_cents: 420_000, rows: [DINING, SHOPPING] },
      bills: { paid: 4, of: 5, late: [{ category: 'utilities', usual_day: 12 }] } },
    since: since(h, []) }),
};
const SIZES = [['1280x800', { width: 1280, height: 800 }], ['390x844', { width: 390, height: 844 }]] as const;
// Playwright runs from web/ and creates the folder when it saves.
const OUT = `../.superpowers/sdd/2026-10-30-steward-home/shots/task-${TASK}`;

async function patchHome(page: Page, state: (h: Home) => Home) {
  await page.route(/\/api\/b\/ours\/home(\?.*)?$/, async (route) => {
    const res = await route.fetch();
    await route.fulfill({ response: res, json: state((await res.json()) as Home) });
  });
}

test.describe('Home screenshots (the gate for Tasks 7-9)', () => {
  test.skip(!TASK, 'set SHOTS_TASK to the task number');
  test('Accounts: the net worth card with its 90-day line, both sizes, light and dark', async ({ browser }, testInfo) => {
    test.skip(testInfo.project.name !== 'desktop' || !process.env.SHOTS_ACCOUNTS, 'set SHOTS_ACCOUNTS=1 (Task 9)');
    for (const theme of ['light', 'dark'] as const) {
      for (const [size, viewport] of SIZES) {
        const ctx = await browser.newContext({ viewport, baseURL: `http://localhost:${DEMO_PORT}` });
        await ctx.addInitScript((t) => { localStorage.setItem('steward-theme', t); }, theme);
        const page = await ctx.newPage();
        await loginAs(page, 'alex');
        await page.goto('/accounts');
        await page.getByTestId('nw-recent').waitFor({ timeout: 30_000 });
        await page.evaluate(() => document.fonts.ready);
        await page.screenshot({ path: `${OUT}/accounts-${size}-${theme}.png`, fullPage: true });
        await ctx.close();
      }
    }
  });
  test('Home in three states at both sizes, light and dark', async ({ browser }, testInfo) => {
    test.skip(testInfo.project.name !== 'desktop', 'one run is enough');
    test.setTimeout(600_000);
    // SHOTS_ONLY=steady|payday|over narrows a re-shoot to one state.
    for (const [name, state] of Object.entries(STATES).filter(([n]) => !process.env.SHOTS_ONLY || n === process.env.SHOTS_ONLY)) {
      for (const theme of ['light', 'dark'] as const) {
        for (const [size, viewport] of SIZES) {
          const ctx = await browser.newContext({ viewport, baseURL: `http://localhost:${DEMO_PORT}` });
          await ctx.addInitScript((t) => { localStorage.setItem('steward-theme', t); }, theme);
          const page = await ctx.newPage();
          await loginAs(page, 'alex');
          await patchHome(page, state);
          await page.goto('/');
          await page.getByTestId('verdict').and(page.locator('[data-ready=true]')).waitFor({ timeout: 30_000 });
          await page.getByTestId('flow-strip').waitFor({ timeout: 30_000 });
          // Fonts, and the joy line's fade, settle before the shot.
          await page.evaluate(() => Promise.all([document.fonts.ready, ...document.getAnimations().map((a) => a.finished.catch(() => null))]));
          await page.screenshot({ path: `${OUT}/${name}-${size}-${theme}.png`, fullPage: true });
          await ctx.close();
        }
      }
    }
  });
});
