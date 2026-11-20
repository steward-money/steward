import { expect, test, type Page } from '@playwright/test';
import { daysBack, importFile, loginAs, mdy, restoreVault, serverDay, tab, youMenu } from './fixtures';

// The demo household as server.sh seeded it, whatever ran before (flaky-test audit E2).
test.beforeAll(async () => { await restoreVault(); });

const PHASES = ['night', 'dawn', 'sunrise', 'morning', 'day', 'afternoon', 'evening', 'dusk'];

test.beforeEach(async ({ page }) => { await loginAs(page, 'alex'); });

async function newManualAccount(page: Page, name: string) {
  const r = await page.request.post('/api/b/ours/accounts', { data: { name, type: 'credit', owner: 'joint', balance_cents: 0 } });
  return (await r.json()).id as number;
}

test('a manual balance update shows on the Accounts tab', async ({ page }, testInfo) => {
  await page.goto('/settings/banks');
  const manual = page.getByRole('region', { name: 'Updated manually' });
  const row = manual.getByRole('listitem').filter({ hasText: '529 — kid 1' });
  await row.getByRole('button', { name: 'Update balance' }).click();
  const cents = testInfo.project.name === 'phone' ? 2_500_000 : 2_600_000;
  await page.getByLabel('Balance').fill((cents / 100).toFixed(2));
  await page.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByRole('main').getByText('Saved.')).toBeVisible();
  // A 529 is not day-to-day, so Home leaves it out; the Accounts tab lists it under Updated manually.
  await tab(page, 'Accounts').click();
  await expect(page.getByRole('heading', { name: 'Accounts', level: 1 })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Updated manually' }).getByRole('link', { name: /529 — kid 1/ }))
    .toContainText(cents === 2_500_000 ? '$25,000.00' : '$26,000.00');
});

test('a liability typed as positive is corrected by the server message', async ({ page }) => {
  await page.goto('/');
  await tab(page, 'Accounts').click();
  await page.getByRole('button', { name: 'Add an account' }).click();
  await page.getByRole('dialog', { name: 'Add an account' }).getByRole('button', { name: /^One you update manually/ }).click();
  const sheet = page.getByRole('dialog', { name: 'Add account' });
  await sheet.getByLabel('Name').fill('Car loan');
  await sheet.getByLabel('Type').selectOption('mortgage');
  await sheet.getByLabel('Owner').selectOption('joint');
  await sheet.getByLabel('Balance').fill('12000');
  await sheet.getByRole('button', { name: 'Save' }).click();
  await expect(sheet.getByText('Enter what you owe as a negative number.')).toBeVisible();
});

test("Profile's Home and cars card saves the same fact the flow reads, with Saved · Undo", async ({ page }, testInfo) => {
  const value = testInfo.project.name === 'phone' ? '1310000' : '1320000';
  await page.goto('/settings/setup/home?from=bookmark');            // Setup was Profile; Profile is its own page now
  await expect(page).toHaveURL(/\/b\/ours\/profile\/home\?from=bookmark$/);
  await page.getByRole('button', { name: 'Edit What it would sell for today' }).first().click();
  await page.getByLabel('What it would sell for today').fill(value);
  const saved = page.waitForResponse((r) => r.url().endsWith('/api/b/ours/facts') && r.request().method() === 'POST');
  await page.getByLabel('What it would sell for today').press('Enter');
  await saved;
  await expect(page.getByRole('main').getByText('Saved · Undo')).toBeVisible();
  const facts = (await (await page.request.get('/api/b/ours/facts')).json()).facts;
  expect(facts['home.value_cents'].value).toBe(`${value}00`);
});

test('theme choice persists', async ({ page }) => {
  const bar = () => page.locator('meta[name="theme-color"]').getAttribute('content');
  await page.goto('/settings/appearance');                 // the old address lands on Appearance under You
  await expect(page).toHaveURL(/\/b\/ours\/you\/appearance$/);
  await expect(page.getByRole('heading', { name: 'Appearance', level: 1 })).toBeVisible();
  await page.getByRole('radio', { name: 'Dark' }).check();
  expect(await page.evaluate(() => document.documentElement.dataset.phase)).toBe('night');
  expect(await page.evaluate(() => localStorage.getItem('steward-theme'))).toBe('dark');
  expect(await bar()).toBe('#0E1729');      // phase 17: the browser bar follows the chosen theme
  await page.reload();
  expect(await page.evaluate(() => document.documentElement.dataset.phase)).toBe('night');
  expect(await bar()).toBe('#0E1729');      // and the first paint sets it too
  await page.getByRole('radio', { name: 'Light' }).check();
  expect(await bar()).toBe('#FAF8F3');
  await page.getByRole('radio', { name: 'Follow the sky' }).check();
  expect(await page.evaluate(() => localStorage.getItem('steward-theme'))).toBeNull();
  expect(PHASES).toContain(await page.evaluate(() => document.documentElement.dataset.phase));
});

test('change my password signs other devices out', async ({ page, browser }, testInfo) => {
  const user = `pw-${testInfo.project.name}`;
  await page.request.post('/api/auth/logout');
  await loginAs(page, user, 'pw-change-demo-2026');
  const other = await browser.newContext();
  const otherPage = await other.newPage();
  await loginAs(otherPage, user, 'pw-change-demo-2026');
  await page.goto('/settings/people');
  await expect(page.getByRole('heading', { name: 'Sign-in and devices', level: 1 })).toBeVisible();
  await page.getByLabel('Current password').fill('pw-change-demo-2026');
  await page.getByLabel('New password').fill('pw-changed-demo-2026');
  await page.getByRole('button', { name: 'Change my password' }).click();
  await expect(page.getByRole('main').getByText("Your password is changed. Other devices were signed out. Passkeys still sign in, so remove any you don't recognize.")).toBeVisible();
  expect((await otherPage.request.get('/api/auth/me')).status()).toBe(401);
  expect((await page.request.get('/api/auth/me')).status()).toBe(200);
  await other.close();
});

test('the You menu has the name, Settings for the book, Sign-in and devices and Appearance', async ({ page }) => {
  await page.goto('/');
  await youMenu(page);
  const menu = page.getByRole('dialog', { name: 'Alex' });
  await expect(menu.getByText('alex@example.com')).toBeVisible();
  await menu.getByRole('link', { name: 'Sign-in and devices' }).click();
  await expect(page).toHaveURL(/\/b\/ours\/you\/devices$/);
  await expect(page.getByRole('heading', { name: 'Sign-in and devices', level: 1 })).toBeVisible();
  await youMenu(page);
  await menu.getByRole('link', { name: 'Appearance' }).click();
  await expect(page.getByRole('heading', { name: 'Appearance', level: 1 })).toBeVisible();
  await youMenu(page);
  await menu.getByRole('link', { name: 'Settings for Ours' }).click();
  await expect(page.getByRole('heading', { name: 'Settings for Ours', level: 1 })).toBeVisible();
  for (const g of ['Banks and accounts', 'Book members']) await expect(page.getByRole('link', { name: g, exact: true })).toBeVisible();
});

test('the old Accounts address in Settings opens the Accounts tab, keeping the query', async ({ page }) => {
  await page.goto('/settings/accounts?from=bookmark');
  await expect(page).toHaveURL(/\/b\/ours\/accounts\?from=bookmark$/);
  await expect(page.getByRole('heading', { name: 'Accounts', level: 1 })).toBeVisible();
  await expect(tab(page, 'Accounts')).toHaveAttribute('aria-current', 'page');
});

test('banks page shows unlinked state and the event log lists entries', async ({ page }) => {
  await page.goto('/settings/banks');
  await expect(page.getByLabel('Setup token')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Link' })).toBeVisible();
  await expect(page.getByText('Big Bank', { exact: true })).toBeVisible(); // institutions list
  // Issue #16: Home's Fix opens this page, which names the banks Home's sign-in warnings are about and gives the step.
  const need = page.getByTestId('sign-in-needed');
  await expect(need).toContainText('Big Bank needs you to sign in again at SimpleFIN.');
  await expect(need).toContainText('Sign in again at SimpleFIN Bridge, then link SimpleFIN with a new setup token.');
  await page.goto('/settings/log');
  await expect(page.getByRole('listitem').first()).toBeVisible();
  await page.getByRole('checkbox', { name: 'Unresolved only' }).check();
  await expect(page.getByRole('listitem').filter({ hasText: 'Resolved' })).toHaveCount(0);
});

test('category rules list what a sort taught Steward, and a rule can be removed', async ({ page }, testInfo) => {
  const acct = await newManualAccount(page, `Rules ${testInfo.project.name}`);
  const desc = `UNION SERVICE CENTER ${testInfo.project.name.toUpperCase()}`;
  const day = mdy(daysBack(await serverDay(page), 3));          // Engineering choice 23: dated from the server's day
  await importFile(page, 'ours', acct, 'rule.csv',
    `Transaction Date,Post Date,Description,Category,Type,Amount,Memo\n${day},${day},${desc},Home,Sale,-80.00,\n`);
  const row = (await (await page.request.get(`/api/b/ours/transactions?q=${encodeURIComponent(desc)}&dates=all`)).json()).transactions[0];
  expect((await page.request.patch(`/api/b/ours/transactions/${row.id}`, { data: { category: 'home' } })).ok()).toBeTruthy();
  await page.goto('/settings/rules');
  const item = page.getByRole('listitem').filter({ hasText: desc });
  await expect(item).toBeVisible();
  await expect(item.getByRole('button', { name: /^Category Home and garden/ })).toBeVisible();
  await expect(item.getByText('1 transaction')).toBeVisible();
  await item.getByRole('button', { name: `Remove rule for ${desc}` }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Remove' }).click();
  await expect(item).toHaveCount(0);
  const rules = (await (await page.request.get('/api/b/ours/categorize/rules')).json()).rules;
  expect(rules.some((r: { pattern: string }) => r.pattern.startsWith('UNION SERVICE CENTER'))).toBeFalsy();
});
