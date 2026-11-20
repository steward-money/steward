import { expect, test as base } from '@playwright/test';
import { bankFiles, loginAs, serverDay, upload } from './fixtures';
import { EMPTY_PORT } from './ports';

// The first run on the empty household (guided setup spec 2): each project has its own, and these tests build on each
// other in order, as profile.spec does. kit and robin have not seen the welcome (web/e2e/seed_empty.py).
const test = base.extend({
  baseURL: async ({}, use, testInfo) => {
    await use(`http://localhost:${testInfo.project.name === 'phone' ? EMPTY_PORT.phone : EMPTY_PORT.desktop}`);
  },
});
test.describe.configure({ mode: 'serial' });

const current = (page: import('@playwright/test').Page) => page.locator('[data-testid=focus-card][data-role=current]');

test('the first run from Welcome through an import, sorting and an extra to Planted, then Home; it resumes on the right step', async ({ page }) => {
  await loginAs(page, 'kit', 'pw-demo-kit-2026');
  // Three accounts first (the page loads them fresh), then the three bank files, as import.spec's first test does.
  const make = async (name: string, type: string) =>
    (await (await page.request.post('/api/b/ours/accounts', { data: { name, type, owner: 'joint', balance_cents: 0 } })).json()).id as number;
  const card = await make('Amex welcome', 'credit');
  const checking = await make('First CU welcome', 'checking');
  const savings = await make('Savings welcome', 'savings');
  await page.goto('/');
  await expect(page).toHaveURL(/\/b\/ours\/welcome$/);
  await expect(page.getByRole('heading', { name: 'Steward', level: 1 })).toBeVisible();
  await page.getByRole('button', { name: 'Set up my household' }).click();
  await expect(page.getByRole('heading', { name: 'Bring in your money', level: 1 })).toBeVisible();
  await expect(page.getByRole('img', { name: 'Step 1 of 3' })).toBeVisible();

  const built = bankFiles(await serverDay(page));
  await page.getByRole('link', { name: 'Import', exact: true }).click();
  await expect(page).toHaveURL(/\/settings\/import\?from=welcome$/);
  await page.locator('input[type=file]').setInputFiles((['amex-basic.csv', 'bank.ofx', 'first-cu.csv'] as const).map((n) => upload(n, built[n])));
  await expect(page.getByText('OFX · 3 rows')).toBeVisible();
  await page.getByLabel('Account for amex-basic.csv').selectOption(String(card));
  await page.getByLabel('Account for first-cu.csv').selectOption(String(checking));
  await page.getByLabel('Account for bank.ofx').selectOption(String(savings));
  const use = page.getByRole('button', { name: 'Use this layout' });
  await use.first().click();
  await expect(use).toHaveCount(1);                       // collapse() animates; wait for the fold before the next
  await use.first().click();
  await page.getByRole('button', { name: 'Preview' }).click();
  await page.getByRole('button', { name: 'Import 16' }).click();
  await expect(page.getByText('Imported 16 into 3 accounts')).toBeVisible();
  await page.getByRole('link', { name: 'Back to setup' }).click();
  await expect(page.getByRole('heading', { name: 'Bring in your money', level: 1 })).toBeVisible();
  await expect(page.getByRole('main').getByText(/^16 rows in\./)).toBeVisible();

  // Leaving and coming back resumes on the next step.
  await page.goto('/b/ours/transactions');
  await page.goto('/');
  await expect(page).toHaveURL(/\/b\/ours\/welcome$/);
  await expect(page.getByRole('heading', { name: 'Sort a few', level: 1 })).toBeVisible();
  await expect(page.getByRole('img', { name: 'Step 2 of 3' })).toBeVisible();

  // Five sorts in focus, as review.spec sorts.
  await page.getByRole('link', { name: 'Start sorting' }).click();
  await expect(page).toHaveURL(/\/review\?from=welcome$/);
  for (let i = 0; i < 5; i++) {
    const id = await current(page).getAttribute('data-id');
    const also = current(page).getByRole('checkbox', { name: /^Also the / });
    if (await also.count()) await also.uncheck();
    const take = page.getByTestId('focus-bar').getByRole('button').nth(1);
    const answered = page.waitForResponse((r) => r.request().method() === 'PATCH' && r.url().endsWith(`/transactions/${id}`));
    if ((await take.textContent())?.startsWith('All categories')) {
      await take.click();
      await page.getByRole('dialog', { name: 'Category' }).getByRole('option').first().click();
    } else {
      await take.click();
    }
    expect((await answered).ok()).toBeTruthy();
    await expect(current(page)).not.toHaveAttribute('data-id', id!);
  }
  await page.getByRole('link', { name: 'Back to setup' }).click();
  await expect(page.getByRole('heading', { name: 'A few extras', level: 1 })).toBeVisible();

  // An extra: invite a partner.
  await page.getByRole('button', { name: /Your partner/ }).click();
  await page.getByLabel('Email', { exact: true }).fill('partner-welcome@example.com');
  await page.getByLabel('Name', { exact: true }).fill('Partner Welcome');
  await page.getByRole('button', { name: 'Invite', exact: true }).click();
  await expect(page.getByRole('main').getByText('Invite sent to partner-welcome@example.com.')).toBeVisible();
  await expect(page.getByTestId('extras-row').filter({ hasText: 'Your partner' }).getByText('Done', { exact: true })).toBeVisible();

  await page.getByRole('button', { name: 'Finish' }).click();
  await expect(page.getByRole('heading', { name: 'Planted.', level: 1 })).toBeVisible();
  await page.getByRole('button', { name: 'Go to Home' }).click();
  await expect(page).toHaveURL(/\/b\/ours$/);
});

test('Later marks it and goes Home', async ({ page }) => {
  await loginAs(page, 'robin', 'pw-demo-robin-2026');
  await page.goto('/');
  await expect(page).toHaveURL(/\/b\/ours\/welcome$/);
  await page.getByRole('link', { name: 'Later', exact: true }).click();
  await expect(page).toHaveURL(/\/b\/ours$/);
  await page.reload();
  await expect(page).toHaveURL(/\/b\/ours$/);
});
