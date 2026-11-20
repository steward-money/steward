import { expect, test, type Page } from '@playwright/test';
import { loginAs, restoreVault } from './fixtures';

// The demo household as server.sh seeded it, whatever ran before (flaky-test audit E2).
test.beforeAll(async () => { await restoreVault(); });

// One seeded question per project (web/e2e/seed_same_account.py).
const PAIRS = {
  phone: { name: 'Joint Taxable', bank: 'Betterment', other: 'Betterment Brokerage' },
  desktop: { name: 'Joint Brokerage', bank: 'Wealthfront', other: 'Wealthfront Brokerage' },
} as const;
const pairOf = (project: string) => PAIRS[project as keyof typeof PAIRS];
const card = (page: Page, bank: string) =>
  page.getByRole('region', { name: 'Same account?' }).filter({ hasText: `from ${bank} through another login` });

/** Review screenshots (gitignored): 390 × 844 on the phone project, 1280 × 800 on the desktop. */
async function shot(page: Page, project: string, name: string) {
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: `e2e/screenshots-same/${name}-${project === 'phone' ? '390' : '1280'}.png` });
}

async function setUp(page: Page, theme: 'light' | 'dark', project: string) {
  if (project === 'phone') await page.setViewportSize({ width: 390, height: 844 });
  await page.addInitScript((t) => {
    localStorage.setItem('steward-theme', t);
    localStorage.setItem('steward-celebrations', 'off');
  }, theme);
  await loginAs(page, 'alex');
}

test.describe('Same account? (the screenshot gate)', () => {
  for (const theme of ['light', 'dark'] as const) {
    test(`the card on Accounts, and Merge into…, ${theme}`, async ({ page }, info) => {
      const p = pairOf(info.project.name);
      await setUp(page, theme, info.project.name);
      await page.goto('/b/ours/accounts');
      await expect(card(page, p.bank)).toBeVisible();
      await shot(page, info.project.name, `accounts-${theme}`);
      await page.getByRole('link', { name: new RegExp(`^${p.name}`) }).first().click();
      await page.getByRole('button', { name: 'Merge into…' }).click();
      const sheet = page.getByRole('dialog', { name: `Merge ${p.name} into…` });
      await sheet.getByRole('radio', { name: new RegExp(`^${p.other}`) }).check();
      await expect(sheet.getByTestId('merge-preview')).toHaveText('No transactions match; 5 will move over.');
      await shot(page, info.project.name, `merge-into-${theme}`);
    });

    // The answers are stubbed (pre-flight ruling F6): a real answer would consume the question Task 10 merges for real.
    test(`the card after an answer, and the kept account's note, ${theme}`, async ({ page }, info) => {
      const p = pairOf(info.project.name);
      await setUp(page, theme, info.project.name);
      const kept = { id: 3, name: p.name };
      await page.route('**/same-accounts/*/merge', (r) => r.fulfill({
        json: { merged: { kept, dropped: 23, kept_new: 2 }, undo: 'shot-token' } }));
      await page.route('**/same-accounts/*/different', (r) => r.fulfill({ json: { undo: 'shot-token' } }));
      await page.goto('/b/ours/accounts');
      const found = card(page, p.bank);
      await found.getByRole('button', { name: 'Merge them' }).click();
      const merged = page.getByRole('region', { name: 'Same account?' }).filter({ hasText: 'One account, as it should be.' });
      await expect(merged).toBeVisible();
      await expect(merged.getByRole('button', { name: 'Undo' })).toBeVisible();
      await merged.evaluate((el) => el.scrollIntoView({ block: 'center' }));
      await shot(page, info.project.name, `merged-${theme}`);

      await page.reload();
      await card(page, p.bank).getByRole('button', { name: "They're different" }).click();
      const apart = page.getByRole('region', { name: 'Same account?' }).filter({ hasText: 'Kept apart.' });
      await expect(apart).toBeVisible();
      await expect(apart.getByRole('button', { name: 'Undo' })).toBeVisible();
      await apart.evaluate((el) => el.scrollIntoView({ block: 'center' }));
      await shot(page, info.project.name, `kept-apart-${theme}`);

      // The kept account's page: learn the other account's id, then answer Merge into… with a stubbed reply naming it.
      await page.goto('/b/ours/accounts');
      const href = await page.getByRole('link', { name: new RegExp(`^${p.other}`) }).first().getAttribute('href');
      const otherId = Number(href?.split('/').pop());
      await page.route('**/accounts/*/merge-into', (r) => r.fulfill({
        json: { merged: { kept: { id: otherId, name: p.other }, dropped: 0, kept_new: 5 }, undo: 'shot-token' } }));
      await page.getByRole('link', { name: new RegExp(`^${p.name}`) }).first().click();
      await page.getByRole('button', { name: 'Merge into…' }).click();
      const sheet = page.getByRole('dialog', { name: `Merge ${p.name} into…` });
      await sheet.getByRole('radio', { name: new RegExp(`^${p.other}`) }).check();
      await expect(sheet.getByTestId('merge-preview')).toBeVisible();
      await sheet.getByRole('button', { name: `Merge into ${p.other}` }).click();
      await expect(page.getByRole('main').getByText('One account, as it should be.')).toBeVisible();
      await expect(page.getByRole('heading', { level: 1, name: p.other })).toBeVisible();
      await shot(page, info.project.name, `merged-note-${theme}`);
    });
  }
});

test('merging the second login counts the account once and leaves no copies', async ({ page }, info) => {
  const p = pairOf(info.project.name);
  await page.addInitScript(() => localStorage.setItem('steward-celebrations', 'off'));
  await loginAs(page, 'alex');
  const get = async (path: string) => (await page.request.get(`/api/b/ours${path}`)).json();
  type Acct = { id: number; name: string };
  const named = (list: Acct[]) => list.filter((a) => a.name === p.name).map((a) => a.id);
  const before = { summary: await get('/summary'), review: await get('/review'), ids: named((await get('/accounts')).accounts) };
  expect(before.ids).toHaveLength(1);                         // held, the second login's copy isn't an account
  await page.goto('/b/ours/accounts');
  await card(page, p.bank).getByRole('button', { name: 'Merge them' }).click();
  await expect(page.getByRole('main').getByText(`Merged. ${p.name} now gets updates from both logins; 5 copies were dropped and 1 new transaction kept.`)).toBeVisible();
  await expect(page.getByRole('main').getByText('One account, as it should be.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Undo' })).toBeVisible();
  const after = { summary: await get('/summary'), review: await get('/review'), ids: named((await get('/accounts')).accounts) };
  expect(after.summary.net_worth_cents).toBe(before.summary.net_worth_cents);    // counted once, before and after
  expect(after.ids).toEqual(before.ids);
  expect(after.review.count).toBe(before.review.count);                          // no copies reached Review
  const rows = (await get(`/transactions?account_id=${before.ids[0]}&limit=50`)).transactions as { raw_description: string }[];
  expect(rows.map((t) => t.raw_description).sort()).toEqual(['ADVISORY FEE', 'COFFEE', 'COFFEE', 'DEPOSIT', 'DIVIDEND', 'WIRE FEE']);
});
