import { expect, test as base } from '@playwright/test';
import { loginAs, youMenu } from './fixtures';
import { EMPTY_PORT } from './ports';

// Each project has its own empty household (onboarding ruling 15), and these tests build on each other in order.
const test = base.extend({
  baseURL: async ({}, use, testInfo) => {
    await use(`http://localhost:${testInfo.project.name === 'phone' ? EMPTY_PORT.phone : EMPTY_PORT.desktop}`);
  },
});
test.describe.configure({ mode: 'serial' });

test('a first visit lands on Profile with the band; You and your partner saves as it goes', async ({ page }) => {
  await loginAs(page, 'alex');
  await page.goto('/');
  await expect(page).toHaveURL(/\/b\/ours\/profile$/);
  await expect(page.getByText('Everything you enter stays on your home server.')).toBeVisible();
  await expect(page.getByText("Steward opened your profile in place of Home because it isn't finished.")).toBeVisible();
  await page.getByRole('button', { name: 'Start' }).click();
  await expect(page).toHaveURL(/\/profile\/you\/you$/);
  await expect(page.getByRole('img', { name: 'Step 1 of 4' })).toBeVisible();
  const saved = page.waitForResponse((r) => r.url().endsWith('/api/b/ours/facts') && r.request().method() === 'POST');
  await page.getByLabel('Your birthday').fill('1984-03-12');
  await saved;
  await page.getByRole('button', { name: 'Next' }).click();
  await page.getByLabel("Kit's birthday").fill('1986-07-30');
  await page.getByRole('button', { name: 'Next' }).click();
  await page.getByLabel('State').selectOption('CA');
  await page.getByLabel('Married filing jointly').check();
  await page.getByRole('button', { name: 'Next' }).click();
  await page.getByLabel("Alex's yearly salary, before deductions").fill('165000');
  await page.getByLabel('401(k) contribution').fill('8');
  await page.getByLabel('Match rate').fill('50');
  await page.getByLabel('Match cap').fill('6');
  await expect(page.getByText('The employer adds $4,950 a year.')).toBeVisible();
  await page.getByRole('button', { name: 'Done' }).click();
  await expect(page).toHaveURL(/\/profile$/);
  await expect(page.getByText('1 of 5 done')).toBeVisible();
});

test('Kids and college: two kids, one 529 suggested by name and made a 529 plan, and the Plan says how it counts college', async ({ page }) => {
  await loginAs(page, 'alex');
  const made = await page.request.post('/api/b/ours/accounts', { data: { name: "Ava's college fund", type: 'savings', owner: 'joint', balance_cents: 1_200_000 } });
  expect(made.ok()).toBeTruthy();
  await page.goto('/profile/kids/kids');
  for (const [name, year] of [['Ava', '2015'], ['Ben', '2018']]) {
    await page.getByRole('button', { name: 'Add a kid' }).click();
    const added = page.waitForResponse((r) => r.url().endsWith('/api/b/ours/children') && r.request().method() === 'POST');
    await page.getByLabel('Name').last().fill(name);
    await page.getByLabel('Birth year').last().fill(year);
    await added;
  }
  await page.getByRole('button', { name: 'Next' }).click();
  await expect(page.getByRole('heading', { name: 'Ava · college from 2033' })).toBeVisible();
  await page.getByRole('region', { name: 'Ava · college from 2033' }).getByRole('radio', { name: 'Private (4 years)' }).check();
  await expect(page.getByText(/^About \$[\d,]+ in today's dollars for 4 years\.$/).first()).toBeVisible();
  await page.getByRole('button', { name: 'Next' }).click();
  await expect(page.getByText("Ava's college fund looks like Ava's. It's set up as Savings.")).toBeVisible();
  await page.getByRole('button', { name: 'Link it and make it a 529 plan' }).click();
  await expect(page.getByText(/Ava's 529 is on track to cover/)).toBeVisible();
  await page.getByRole('button', { name: 'Done' }).click();
  await expect(page).toHaveURL(/\/profile$/);
  await expect(page.getByRole('main').getByText('College, planned.')).toBeVisible();
  const plan = await (await page.request.get('/api/b/ours/plan/setup')).json();
  expect(plan.setup.find((i: { key: string }) => i.key === 'children').done).toBe(true);
});

test('leaving Home and cars after Homes and coming back resumes at Mortgages', async ({ page }) => {
  await loginAs(page, 'alex');
  // A manual mortgage with no rate, payment or payoff keeps the section unfinished (pre-flight F10).
  const made = await page.request.post('/api/b/ours/accounts', { data: { name: 'Mortgage', type: 'mortgage', owner: 'joint', balance_cents: -40000000 } });
  expect(made.ok()).toBeTruthy();
  await page.goto('/profile/home/own');
  await page.getByRole('radio', { name: 'Yes' }).check();
  await page.getByRole('button', { name: 'Next' }).click();
  const saved = page.waitForResponse((r) => r.url().endsWith('/api/b/ours/facts') && r.request().method() === 'POST');
  await page.getByLabel('What it would sell for today').fill('1250000');
  await saved;
  await page.getByRole('button', { name: 'Next' }).click();
  await expect(page).toHaveURL(/\/profile\/home\/mortgages$/);
  await page.goto('/');
  await page.goto('/profile');
  await expect(page.getByTestId('row-home')).toContainText('Continue');
  await page.getByTestId('row-home').click();
  await expect(page).toHaveURL(/\/profile\/home\/mortgages$/);
});

test('the You menu opens Profile, and old settings addresses land there', async ({ page }) => {
  await loginAs(page, 'alex');
  await page.goto('/transactions');
  await youMenu(page);
  await page.getByRole('link', { name: 'Profile' }).click();
  await expect(page).toHaveURL(/\/b\/ours\/profile$/);
  await page.goto('/setup/pay?from=plan');
  await expect(page).toHaveURL(/\/b\/ours$/);
  await page.goto('/settings/profile/children');
  await expect(page).toHaveURL(/\/b\/ours\/profile\/kids/);
});
