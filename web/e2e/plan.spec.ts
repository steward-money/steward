import { expect, test } from '@playwright/test';
import { loginAs, moveFar, tab, restoreVault } from './fixtures';

// The demo household as server.sh seeded it, whatever ran before (flaky-test audit E2).
test.beforeAll(async () => { await restoreVault(); });

type Kid = { id: number; label: string; birth_year: number; cost_level: string; account_id: number | null; monthly_contribution_cents: number };
const LEVEL: Record<string, RegExp> = { in_state_public: /^In-state public/, out_of_state_public: /^Out-of-state public/, private: /^Private/ };

test.beforeEach(async ({ page }) => { await loginAs(page, 'alex'); });

test('the Plan tab opens Plan', async ({ page }) => {
  await page.goto('/');
  await tab(page, 'Plan').click();
  await expect(page).toHaveURL(/\/plan$/);
  await expect(page.getByTestId('age-comfortable')).toBeVisible();
});

test('the five pages: each tab opens its page, and Plan stays lit in the main navigation', async ({ page }) => {
  await page.goto('/plan');
  const pages = page.getByRole('navigation', { name: 'Plan pages' });
  await expect(pages.getByRole('link')).toHaveText(['Overview', 'Try changes', 'Next dollar', 'How it works', 'Taxes']);
  const visits: [string, RegExp, () => Promise<void>][] = [
    ['Try changes', /\/plan\/try$/, () => expect(page.getByTestId('lever-save_more')).toBeVisible()],
    ['Next dollar', /\/plan\/next$/, () => expect(page.locator('.nd__step').first()).toContainText(/Cash for \d+ months of spending/)],
    ['How it works', /\/plan\/how$/, () => expect(page.getByRole('heading', { name: 'What moves your plan most' })).toBeVisible()],
    ['Taxes', /\/taxes$/, () => expect(page.getByRole('heading', { name: 'Taxes', level: 1 })).toBeVisible()],
    ['Overview', /\/plan$/, () => expect(page.getByTestId('age-comfortable')).toBeVisible()],
  ];
  for (const [name, url, shows] of visits) {
    await pages.getByRole('link', { name, exact: true }).click();
    await expect(page).toHaveURL(url);
    await shows();
    await expect(pages.getByRole('link', { name, exact: true })).toHaveAttribute('aria-current', 'page');
    await expect(page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Plan', exact: true })).toHaveAttribute('aria-current', 'page');
  }
});

test('setup: each checklist row opens its setup step, which returns to the Plan; finishing shows the ages', async ({ page }) => {
  const kids: Kid[] = (await (await page.request.get('/api/b/ours/children')).json()).children;
  const kid = kids.filter((k) => k.account_id !== null).pop()!;
  expect((await page.request.delete(`/api/b/ours/children/${kid.id}`)).status()).toBe(200);

  await page.goto('/plan');
  await expect(page.getByRole('heading', { name: 'Required' })).toBeVisible();
  await expect(page.getByText(/^\d of \d done$/)).toBeVisible();
  await expect(page.getByRole('navigation', { name: 'Plan pages' }).getByRole('link')).toHaveText(['Overview', 'Taxes']);
  await expect(page.getByTestId('setup-row').filter({ hasText: 'About you' })).toHaveAttribute('href', '/b/ours/profile/you?from=plan');

  await page.getByTestId('setup-row').filter({ hasText: "Sam's earnings record" }).click();
  await expect(page).toHaveURL(/\/profile\/benefits(\/ss)?\?from=plan$/);
  await page.getByTestId('earnings-file-spouse').setInputFiles('e2e/fixtures/earnings-wrong.xml');
  await expect(page.getByRole('alert')).toHaveText("That file doesn't look like a Social Security earnings record. Nothing was saved.");
  await page.getByTestId('earnings-file-spouse').setInputFiles('e2e/fixtures/earnings-sam.xml');
  await expect(page.getByRole('main').getByText('10 years, 2016 to 2025.')).toBeVisible();
  await page.getByRole('main').getByRole('link', { name: 'Plan', exact: true }).click();
  await expect(page).toHaveURL(/\/plan$/);

  await page.getByTestId('setup-row').filter({ hasText: 'Children' }).click();
  await expect(page).toHaveURL(/\/profile\/kids(\/\w+)?\?from=plan$/);
  await page.getByRole('button', { name: 'Add a kid' }).click();
  const added = page.waitForResponse((r) => r.url().endsWith('/api/b/ours/children') && r.request().method() === 'POST');
  await page.getByLabel('Name').last().fill(kid.label);
  await page.getByLabel('Birth year').last().fill(String(kid.birth_year));
  await added;
  await page.getByRole('button', { name: 'Next' }).click();
  await page.getByRole('radio', { name: LEVEL[kid.cost_level] }).first().check();
  await page.getByRole('button', { name: 'Next' }).click();
  await page.getByRole('region', { name: kid.label }).getByRole('combobox').first().selectOption(String(kid.account_id));
  await page.getByRole('button', { name: 'Done' }).click();
  await expect(page).toHaveURL(/\/plan$/);
  await expect(page.getByTestId('age-comfortable')).toBeVisible();
});

test('every tooltip stays on screen and opens downward', async ({ page }) => {
  await page.goto('/plan');
  await expect(page.getByTestId('age-comfortable')).toBeVisible();
  const vw = page.viewportSize()!.width;
  const tips = page.locator('.tip__btn');
  const n = await tips.count();
  expect(n).toBeGreaterThanOrEqual(2);
  for (let i = 0; i < n; i++) {
    const btn = tips.nth(i);
    await btn.scrollIntoViewIfNeeded();
    await btn.click();
    const body = page.locator('.tip.is-open .tip__body');
    await expect(body).toBeVisible();
    const b = (await body.boundingBox())!;
    const t = (await btn.boundingBox())!;
    expect(b.x).toBeGreaterThanOrEqual(15.5);
    expect(b.x + b.width).toBeLessThanOrEqual(vw - 15.5);
    expect(b.y).toBeGreaterThanOrEqual(t.y + t.height - 1);
    await page.keyboard.press('Escape');
    await expect(body).toBeHidden();
  }
});

test('moving a lever recomputes the ages live and marks the row', async ({ page }) => {
  await page.goto('/plan/try');
  await expect(page.getByTestId('age-comfortable')).toBeVisible();
  const row = page.getByTestId('lever-save_more');
  await expect(row.locator('.lever__effect')).not.toHaveText('Working it out…');
  await row.locator('.lever__head').click();
  await expect(row.getByRole('slider')).toHaveCount(1);
  await moveFar(page, page.getByTestId('slider-save_more'));
  await expect(row.getByText('Changed')).toBeVisible();
  await expect(page.getByTestId('trybar').locator('.trybar__was')).toBeVisible();
  await expect(row.locator('.lever__effect')).not.toHaveClass(/is-stale/);
});

test('Try this moves the slider to the suggestion', async ({ page }) => {
  await page.goto('/plan/try');
  const row = page.getByTestId('lever-spending');
  await expect(row.locator('.lever__effect')).not.toHaveText('Working it out…');
  await row.locator('.lever__head').click();
  await row.getByRole('button', { name: 'Try this' }).click();
  await expect(row.getByText('Changed')).toBeVisible();
  await expect(row.locator('.lever__effect')).toHaveText('In use');
  await expect(row.getByRole('button', { name: 'Try this' })).toBeDisabled();
});

test('Reset undoes a change; Update the plan saves it, the Overview compares with the earlier save, and the confirmation stays', async ({ page }) => {
  expect((await page.request.post('/api/b/ours/plan', { data: {} })).status()).toBe(201);
  await page.goto('/plan/try');
  const row = page.getByTestId('lever-save_more');
  await row.locator('.lever__head').click();
  await moveFar(page, page.getByTestId('slider-save_more'));
  const bar = page.getByTestId('savebar');
  await expect(bar).toContainText('Plan not updated');
  await expect(bar).not.toContainText('You changed');
  await bar.getByRole('button', { name: 'Reset' }).click();
  await expect(bar).toBeHidden();
  await expect(row.getByText('Changed')).toBeHidden();

  await moveFar(page, page.getByTestId('slider-save_more'));
  await page.getByTestId('savebar').getByRole('button', { name: 'Update the plan' }).click();
  const done = page.getByTestId('savebar-done');
  await expect(done).toContainText('Plan updated.');
  await expect(done).toContainText(/Saved by Alex today at \d{1,2}:\d{2} [ap]m\./);

  // The Overview compares today with the newest plan saved before today (Engineering choice 8; the seed has one from
  // 3 days ago), never with the save just made, and keeps Earlier versions.
  const pages = page.getByRole('navigation', { name: 'Plan pages' });
  await pages.getByRole('link', { name: 'Overview', exact: true }).click();
  const line = page.getByTestId('change-line');
  await expect(line).toHaveText(/^(▲ |▼ )?(.+ earlier than on|.+ later than on|No change since|Comfortably at \d+ now, from|Not by 70 now, from) [A-Z][a-z]{2} \d{1,2}/);
  const today = await page.evaluate(() => new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric' }).format(new Date()));
  expect(await line.textContent()).not.toContain(today);
  await page.getByRole('button', { name: 'Earlier versions' }).click();
  const sheet = page.getByRole('dialog', { name: 'Earlier versions' });
  await expect(sheet).toContainText('Current');
  await sheet.getByRole('button', { name: 'Close' }).click();
  await pages.getByRole('link', { name: 'Try changes', exact: true }).click();
  await expect(page.getByTestId('savebar-done')).toBeVisible();   // persistent until the next change
});

test('on a phone the Plan not updated bar stays in reach while you work further down', async ({ page, isMobile }) => {
  test.skip(!isMobile, 'the pinned bar is the phone layout');
  expect((await page.request.post('/api/b/ours/plan', { data: {} })).status()).toBe(201);
  await page.goto('/plan/try');
  const row = page.getByTestId('lever-full_ss');
  await row.locator('.lever__head').click();
  await row.getByRole('radiogroup', { name: 'Full Social Security benefits' }).getByRole('radio', { checked: false }).click();
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  const bar = page.getByTestId('savebar');
  await expect(bar).toHaveClass(/savebar--pinned/);
  await expect(bar).toBeInViewport();
  await bar.getByRole('button', { name: 'Reset' }).click();
  await expect(bar).toBeHidden();
});

test('the pinned Plan not updated bar never covers the last lever', async ({ page }) => {
  expect((await page.request.post('/api/b/ours/plan', { data: {} })).status()).toBe(201);
  await page.goto('/plan/try');
  const row = page.getByTestId('lever-full_ss');
  await row.locator('.lever__head').click();
  await row.getByRole('radiogroup', { name: 'Full Social Security benefits' }).getByRole('radio', { checked: false }).click();
  const last = page.locator('.lever').last().locator('.lever__head');
  await last.waitFor();
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await expect(page.getByTestId('savebar')).toHaveClass(/savebar--pinned/);
  // Pinning adds the page's foot room, so scroll to the end again to see the page as left with the bar pinned.
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await expect(last).toBeInViewport();
  const hit = await last.evaluate((el) => {
    const r = el.getBoundingClientRect();
    const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    return !!top && el.contains(top);
  });
  expect(hit).toBe(true);
});

test('the Overview, Next dollar and How it works pages, and the Try changes bar that stays in view', async ({ page, isMobile }) => {
  await page.goto('/plan');
  await expect(page.getByRole('heading', { name: 'Money for retirement, year by year' })).toBeVisible();
  await expect(page.locator('.band__words')).toContainText(/Retiring at \d+|Not by 70/);
  await expect(page.locator('svg.chart')).toHaveCount(1);
  await expect(page.getByTestId('help-card').getByRole('link')).toHaveAttribute('href', '/b/ours/plan/try');
  await expect(page.getByTestId('next-card').getByRole('link')).toHaveAttribute('href', '/b/ours/plan/next');

  await page.goto('/plan/next');
  await expect(page.getByRole('heading', { name: 'Next dollar' })).toBeVisible();
  await expect(page.locator('.nd__step').first()).toContainText(/Cash for \d+ months of spending/);
  await expect(page.getByText('Steward suggests; it never moves money.')).toBeVisible();
  if (isMobile) {
    const small = await page.locator('.nd button, .nd input').evaluateAll((els) =>
      els.filter((el) => el.getBoundingClientRect().height < 44).map((el) => el.outerHTML.slice(0, 80)));
    expect(small).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  }

  await page.goto('/plan/how');
  await expect(page.getByRole('heading', { name: 'Where your numbers come from' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Pay', exact: true })).toHaveAttribute('href', '/b/ours/profile/you?from=plan');

  await page.goto('/plan/try');
  const bar = page.getByTestId('trybar');
  await expect(bar).toBeVisible();
  expect(await bar.evaluate((el) => getComputedStyle(el).position)).toBe('sticky');
  await expect(page.locator('svg.chart')).toHaveCount(0);
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await expect(bar).toBeInViewport();
  // In the viewport is not enough: nothing (the phone's top bar) may cover the pinned bar's centre.
  const uncovered = await bar.evaluate((el) => {
    const r = el.getBoundingClientRect();
    const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    return !!top && el.contains(top);
  });
  expect(uncovered).toBe(true);
});

test('How it works opens a fold to its exact lines', async ({ page }) => {
  await page.goto('/plan/how');
  await expect(page.getByRole('heading', { name: 'What moves your plan most' })).toBeVisible();
  await expect(page.locator('.facts.moves .facts__row').first()).toContainText('Social Security at 67');
  const markets = page.locator('details.how-fold', { has: page.locator('summary', { hasText: 'Markets' }) });
  await expect(markets).not.toHaveAttribute('open', '');
  await markets.locator('summary').click();
  await expect(markets).toContainText(/^MarketsSteward runs your money through [\d,]+ simulated market histories/);
});
