import { expect, test, type Page } from '@playwright/test';
import { dollars } from '../src/lib/format';
import { loginAs, restoreVault } from './fixtures';

// The demo household as server.sh seeded it, whatever ran before (flaky-test audit E2).
test.beforeAll(async () => { await restoreVault(); });

// web/e2e/seed_spending.py: Gifts is clearly over its target, by $50, whatever day this runs; every other Flexible target leaves its category on target. Val is a viewer of the household book.

type Month = { month: string; days_left: number | null; earliest: string | null };
type Targets = { categories: { category: string; kind: string; monthly_cents: number | null }[] };
const paceRow = (page: Page, category: string) => page.locator(`[data-testid="pace-row"][data-category="${category}"]`);
const thisMonth = async (page: Page) => (await (await page.request.get('/api/b/ours/spending')).json()) as Month;
const MONTH_NAME = /^[A-Z][a-z]+ \d{4}$/;
const prevMonth = (ym: string) => {
  const [y, m] = ym.split('-').map(Number);
  return m === 1 ? `${y - 1}-12` : `${y}-${String(m - 1).padStart(2, '0')}`;
};

/** The page fits the screen: nothing pushes it wider than the viewport (an overflowing row scrolls the whole page). */
async function fitsTheScreen(page: Page) {
  const { scroll, inner } = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, inner: window.innerWidth }));
  expect(scroll, 'the page scrolls sideways').toBeLessThanOrEqual(inner);
}

test.describe('a manager', () => {
  test.beforeEach(async ({ page }) => { await loginAs(page, 'alex'); });

  test("Home's For you names Gifts, $50 over, and See › opens it", async ({ page }) => {
    const m = await thisMonth(page);
    await page.goto('/');
    const row = page.getByTestId('for-you').getByRole('listitem').filter({ hasText: 'Gifts is $50 over its target' });
    await expect(row.locator('.foryou__dot')).toHaveClass(/is-amber/);
    await row.getByRole('link', { name: /See ›$/ }).click();
    await expect(page).toHaveURL(new RegExp(`/b/ours/spending/gifts\\?month=${m.month}$`));
    await expect(page.getByTestId('cat-state')).toContainText('Over by $50');
    await page.goto(`/spending?month=${m.month}`);
    await expect(page.locator('#flexible').getByTestId('over-line')).toContainText('Gifts is $50 over its target');
    await expect(paceRow(page, 'gifts').locator('.sp-pace__fill')).toHaveClass(/is-clear/);
    await expect(page.getByTestId('sp-total')).toContainText(/^\$[\d,]+ of \$[\d,]+$/);   // with targets, the head is "of" the plan
  });

  test("the head's out · in line is Home's figure's two sides, and the month before has its own", async ({ page }) => {
    type Flow = { flow: { in_cents: number; out_cents: number } };
    const { flow } = (await (await page.request.get('/api/b/ours/home')).json()) as Flow;
    await page.goto('/');
    await expect(page.locator('.verdict__legend')).toHaveText(`${dollars(flow.in_cents)} in${dollars(flow.out_cents)} out`);
    await page.goto('/spending');
    await expect(page.getByTestId('sp-in-out')).toHaveText(`${dollars(flow.out_cents)} out · ${dollars(flow.in_cents)} in`);
    const m = await thisMonth(page);
    type Head = { head: { money_out_cents: number; money_in_cents: number } };
    const { head } = (await (await page.request.get(`/api/b/ours/spending?month=${prevMonth(m.month)}`)).json()) as Head;
    await page.goto(`/spending?month=${prevMonth(m.month)}`);
    await expect(page.getByTestId('sp-in-out')).toHaveText(`${dollars(head.money_out_cents)} out · ${dollars(head.money_in_cents)} in`);
  });

  test('a bigger target moves its row to the top, and the order holds in the month before', async ({ page }) => {
    const before = (await (await page.request.get('/api/b/ours/spending/targets')).json()) as Targets;
    const gifts = before.categories.find((r) => r.category === 'gifts')!;
    const top = Math.max(...before.categories.filter((r) => r.kind === 'everyday').map((r) => r.monthly_cents ?? 0));
    try {
      await page.goto('/spending');
      await expect(paceRow(page, 'gifts')).toBeVisible();
      await expect(page.getByTestId('pace-row').first()).not.toHaveAttribute('data-category', 'gifts');
      await page.getByRole('navigation', { name: 'Spending views' }).getByRole('link', { name: 'Targets' }).click();
      await page.getByRole('textbox', { name: 'Gifts target' }).fill(String(top / 100 + 100));
      await page.getByRole('button', { name: 'Save targets' }).click();
      await expect(page.getByRole('main').getByText('Targets saved.')).toBeVisible();
      await page.goto('/spending');
      const rows = page.getByTestId('pace-row');
      await expect(rows.first()).toHaveAttribute('data-category', 'gifts');
      const order = await rows.evaluateAll((els) => els.map((e) => e.getAttribute('data-category')));
      await page.getByRole('link', { name: MONTH_NAME }).click();          // the previous month (next is disabled)
      await expect(page).toHaveURL(/month=\d{4}-\d{2}$/);
      await expect(rows.first()).toHaveAttribute('data-category', 'gifts');
      const prev = await rows.evaluateAll((els) => els.map((e) => e.getAttribute('data-category')));
      expect(prev.filter((c) => order.includes(c))).toEqual(order.filter((c) => prev.includes(c)));
    } finally {
      const r = await page.request.put('/api/b/ours/spending/targets', { data: { targets: [{ category: 'gifts', kind: gifts.kind, monthly_cents: gifts.monthly_cents }] } });
      expect(r.ok()).toBeTruthy();
    }
  });

  test("fixed costs wear the server's tone, and a commitment is never amber", async ({ page }) => {
    const m = (await (await page.request.get('/api/b/ours/spending')).json()) as { fixed: { category: string; kind: string; tone: string }[] };
    await page.goto('/spending');
    await expect(page.getByTestId('sp-total')).toBeVisible();
    for (const r of m.fixed) {
      const row = page.locator(`[data-testid="fixed-row"][data-category="${r.category}"]`);
      await expect(row).toHaveClass(new RegExp(`is-${r.tone}`));
      if (r.kind === 'commitment') await expect(row).not.toHaveClass(/is-amber/);
    }
  });

  test('Over time draws each category against its target, for the last 12 or 6 months', async ({ page }) => {
    await page.goto('/spending');
    await page.getByRole('navigation', { name: 'Spending views' }).getByRole('link', { name: 'Over time' }).click();
    await expect(page.getByRole('heading', { name: 'Spending over time', level: 1 })).toBeVisible();
    const card = page.locator('[data-testid="trend-card"][data-category="gifts"]');
    // The chart is hidden from screen readers; the card's link says the category, the count and the target.
    await expect(card).toHaveAccessibleName(/^Gifts\s*(Over in \d+ of the last \d+ months|Over last month|Under target all \d+ months|Under target last month)\s*Target \$[\d,]+/);
    await expect(card.locator('rect.sp-chart__bar')).toHaveCount(12);
    await expect(page.getByRole('button', { name: 'Last 12 months' })).toHaveAttribute('aria-pressed', 'true');
    await page.getByRole('button', { name: 'Last 6 months' }).click();
    await expect(page).toHaveURL(/months=6$/);
    await expect(page.getByRole('button', { name: 'Last 6 months' })).toHaveAttribute('aria-pressed', 'true');
    await expect(card.locator('rect.sp-chart__bar')).toHaveCount(6);
    // The count follows the span chosen: the demo has years of history, so 6 complete months in the 6-month view and
    // 12 in the 12-month one (before this change both counted 6).
    const of = async (n: number) => ((await (await page.request.get(`/api/b/ours/spending/trends?months=${n}`)).json()) as
      { categories: { category: string; over_of: number }[] }).categories.find((c) => c.category === 'gifts')!.over_of;
    expect([await of(6), await of(12)]).toEqual([6, 12]);
  });

  test("a category opens with its pace and this month's transactions, and links to all of them", async ({ page }) => {
    await page.goto('/spending');
    await paceRow(page, 'gifts').getByRole('link', { name: 'Gifts' }).click();
    await expect(page).toHaveURL(/\/spending\/gifts\?month=\d{4}-\d{2}$/);
    await expect(page.getByRole('heading', { level: 1 })).toHaveText(/^Gifts · [A-Z][a-z]+ \d{4}$/);
    await expect(page.getByTestId('cat-state')).toContainText('Over by $50');
    await expect(page.getByTestId('cat-txn').filter({ hasText: 'Petalworks' }).first()).toBeVisible();
    await expect(page.getByRole('link', { name: 'Change the target' })).toHaveAttribute('href', '/b/ours/spending/targets');
    await page.getByRole('link', { name: 'All Gifts transactions' }).click();
    await expect(page).toHaveURL(/\/transactions\?category=gifts$/);
    await page.goBack();
    await expect(page).toHaveURL(/\/spending\/gifts/);
  });

  test('an old Cash flow address lands on Spending with its month', async ({ page }) => {
    const month = prevMonth((await thisMonth(page)).month);
    await page.goto(`/cashflow?month=${month}`);
    await expect(page).toHaveURL(new RegExp(`/b/ours/spending\\?month=${month}$`));
    await expect(page.getByRole('heading', { level: 1 })).toHaveText(MONTH_NAME);
    await expect(page.getByTestId('sp-total')).toBeVisible();
  });

  test('a new account without a type is flagged until it has one', async ({ page }) => {
    const r = await page.request.post('/api/b/ours/accounts', { data: { name: 'Acct 4242', type: 'unknown', owner: 'joint', balance_cents: 0 } });
    expect(r.status()).toBe(201);
    const id = (await r.json()).id as number;
    try {
      await page.goto('/spending');
      await expect(page.getByRole('main').getByText('Accounts without a type')).toBeVisible();
      await expect(page.getByRole('link', { name: /^Set the type of \d+ new accounts?$/ })).toHaveAttribute('href', '/b/ours/settings/banks');
    } finally {
      // Typed and closed, so no later spec sees an extra new account.
      await page.request.patch(`/api/b/ours/accounts/${id}`, { data: { type: 'savings', is_active: false } });
    }
  });

  test('every Spending page fits the screen', async ({ page }) => {
    for (const [path, ready] of [['/spending', 'sp-total'], ['/spending/trends', 'trend-card'], ['/spending/targets', 'sumcard'], ['/spending/gifts', 'cat-txn']] as const) {
      await page.goto(path);
      await expect(page.getByTestId(ready).first()).toBeVisible();
      await fitsTheScreen(page);
    }
  });
});

test.describe('a viewer', () => {
  test.beforeEach(async ({ page }) => { await loginAs(page, 'val-viewer', 'pw-viewer-demo-2026'); });

  test('reads Spending, its targets and a category, with nothing to change', async ({ page }) => {
    await page.goto('/spending');
    await expect(paceRow(page, 'gifts')).toContainText('Over by $50');
    await expect(page.getByRole('link', { name: 'Set one' })).toHaveCount(0);
    await expect(page.getByText('Sort them')).toHaveCount(0);
    await expect(page.locator('a[data-testid="unsorted-row"]')).toHaveCount(0);   // Not sorted yet opens nothing

    await page.getByRole('navigation', { name: 'Spending views' }).getByRole('link', { name: 'Targets' }).click();
    await expect(page.getByRole('heading', { name: 'Set targets', level: 1 })).toBeVisible();
    await expect(page.locator('[data-testid="target-row"][data-category="gifts"]')).toContainText(/\$[\d,]+/);
    const screen = page.locator('.screen.spending');
    await expect(screen.getByRole('textbox')).toHaveCount(0);
    await expect(screen.getByRole('combobox')).toHaveCount(0);
    for (const name of ['Save targets', 'Accept all suggestions', 'Use these targets']) await expect(screen.getByRole('button', { name })).toHaveCount(0);

    await page.goto('/spending/gifts');
    await expect(page.getByRole('heading', { level: 1 })).toHaveText(/^Gifts · /);
    await expect(page.getByTestId('cat-txn').first()).toBeVisible();
    await expect(page.getByRole('link', { name: 'Change the target' })).toHaveCount(0);
  });
});
