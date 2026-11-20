import { test, type Page } from '@playwright/test';
import { loginAs, restoreVault } from './fixtures';
import { DEMO_PORT, EMPTY_PORT } from './ports';

// The demo household as server.sh seeded it, whatever ran before (flaky-test audit E2).
test.beforeAll(async () => { await restoreVault(); });

declare const process: { env: Record<string, string | undefined> };
const TASK = process.env.SHOTS_TASK;
type Shot = { name: string; path: string; vault: 'demo' | 'empty'; ready: string | null; before?: (page: Page) => Promise<void>; prep?: (page: Page) => Promise<void>; act?: (page: Page) => Promise<void> };

/** A kid below 100% and tuition-only, so the Plan's college row shows the Profile line. */
async function halfShareKid(page: Page) {
  const r = await page.request.get('/api/b/ours/children');
  const kid = (await r.json()).children[0];
  await page.request.patch(`/api/b/ours/children/${kid.id}`, { data: { share_pct: 50, covers_living: false } });
}
/** Each kid's college select shows its whole label clear of the chevron (at 390 it ran edge to edge, under it). */
async function collegeFits(page: Page) {
  const over = await page.locator('.college-child select').evaluateAll((els) => els.map((el) => {
    const s = el as HTMLSelectElement;
    const cs = getComputedStyle(s);
    const ctx = document.createElement('canvas').getContext('2d')!;
    ctx.font = cs.font;
    const text = ctx.measureText(s.options[s.selectedIndex].text).width;
    const room = s.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
    return text > room + 0.5 ? `${s.options[s.selectedIndex].text}: ${Math.ceil(text)} > ${Math.floor(room)}` : null;
  }).filter(Boolean));
  if (over.length) throw new Error(`college select text overflows: ${over.join('; ')}`);
}
// Each task's screens. "empty" is the desktop project's empty household (onboarding ruling 15); "demo" the demo one.
/** Finish `section` from its last step without the server saying 5 of 5 (the demo's profile is already complete), so the
 *  page gets the section's own moment, shot mid-animation over the row. */
const finishWithMoment = (section: string, lastStep: string, row: string) => async (page: Page) => {
  await page.route(new RegExp(`/api/b/[^/]+/profile/sections/${section}$`), async (route) => {
    const res = await route.fetch();
    const body = await res.json();
    await route.fulfill({ response: res, json: { ...body, finished: false } });
  });
  await page.goto(`/profile/${section}/${lastStep}`);
  await page.getByRole('button', { name: 'Done' }).click({ timeout: 20_000 });
  await page.getByTestId(`row-${row}`).waitFor({ timeout: 20_000 });
  await page.locator('.cel-moment').waitFor({ timeout: 20_000 });
  await page.waitForTimeout(700);                                   // mid-toss, mid-glow
};
const SHOTS: Record<string, Shot[]> = {
  '8': [
    { name: 'profile', path: '/profile', vault: 'empty', ready: 'Profile' },
    { name: 'you-step', path: '/profile/you/you', vault: 'empty', ready: 'You and your partner' },
    { name: 'you-pay', path: '/profile/you/pay', vault: 'empty', ready: 'You and your partner' },
    { name: 'you-card', path: '/profile/you', vault: 'demo', ready: 'You and your partner' },
  ],
  '9': [
    { name: 'kids-list', path: '/profile/kids/kids', vault: 'demo', ready: 'Kids and college' },
    { name: 'kids-cover', path: '/profile/kids/cover', vault: 'demo', ready: 'Kids and college' },
    { name: 'kids-plans', path: '/profile/kids/plans', vault: 'demo', ready: 'Kids and college' },
    { name: 'kids-card', path: '/profile/kids', vault: 'demo', ready: 'Kids and college' },
    { name: 'kids-card-open', path: '/profile/kids', vault: 'demo', ready: 'Kids and college',
      act: async (page) => { await page.getByRole('button', { name: /^Edit / }).first().click(); await page.getByRole('slider').first().waitFor(); } },
    { name: 'plan-try-college', path: '/plan/try', vault: 'demo', ready: null, prep: halfShareKid,
      act: async (page) => { await page.getByRole('button', { name: /College type/ }).first().click(); await page.getByText('Change in Profile').first().waitFor(); await collegeFits(page); } },
  ],
  '10': [
    { name: 'home-own', path: '/profile/home/own', vault: 'demo', ready: 'Home and cars' },
    { name: 'home-homes', path: '/profile/home/homes', vault: 'demo', ready: 'Home and cars' },
    { name: 'home-mortgages', path: '/profile/home/mortgages', vault: 'demo', ready: 'Home and cars' },
    { name: 'home-cars', path: '/profile/home/cars', vault: 'demo', ready: 'Home and cars' },
    { name: 'home-card', path: '/profile/home', vault: 'demo', ready: 'Home and cars' },
  ],
  '11': [
    { name: 'benefits-ss', path: '/profile/benefits/ss', vault: 'demo', ready: 'Social Security and Medicare' },
    { name: 'benefits-health', path: '/profile/benefits/health', vault: 'demo', ready: 'Social Security and Medicare' },
    { name: 'benefits-card', path: '/profile/benefits', vault: 'demo', ready: 'Social Security and Medicare' },
    { name: 'banks-link', path: '/profile/banks/link', vault: 'empty', ready: 'Banks and accounts' },
    { name: 'banks-card', path: '/profile/banks', vault: 'demo', ready: 'Banks and accounts' },
  ],
  '12': [
    { name: 'moment-kids', path: '/profile', vault: 'demo', ready: 'Profile', before: finishWithMoment('kids', 'plans', 'kids') },
    { name: 'moment-home', path: '/profile', vault: 'demo', ready: 'Profile', before: finishWithMoment('home', 'cars', 'home') },
    // The demo's profile is 5 of 5, so Done on its last step lands on the all-done line and its confetti.
    { name: 'profile-cheer-all', path: '/profile', vault: 'demo', ready: 'Profile', before: async (page) => {
      await page.goto('/profile/kids/plans');
      await page.getByRole('button', { name: 'Done' }).click();
      await page.getByRole('main').getByText('Your profile is complete. The Plan knows your household now.').waitFor();
      await page.waitForTimeout(500);                                 // mid-burst
    } },
  ],
  '13': [
    { name: 'home-line', path: '/', vault: 'empty', ready: null, before: async (page) => {
      await page.request.post('/api/b/ours/profile/seen', { data: {} });   // past the first visit's band
      await page.goto('/');
      await page.getByTestId('profile-line').waitFor();
    } },
    { name: 'plan-setup', path: '/plan', vault: 'empty', ready: null },
    { name: 'card-earlier', path: '/profile/you', vault: 'demo', ready: 'You and your partner' },
  ],
  // The final fix round: the Kids card's Add a kid (and the Home and Social Security cards it sits beside, unchanged for JR).
  'final': [
    { name: 'kids-card', path: '/profile/kids', vault: 'demo', ready: 'Kids and college' },
    { name: 'home-card', path: '/profile/home', vault: 'demo', ready: 'Home and cars' },
  ],
};
const SIZES = [['1280x800', { width: 1280, height: 800 }], ['390x844', { width: 390, height: 844 }]] as const;
// Playwright runs from web/ and creates the folder when it saves.
const OUT = `../.superpowers/sdd/2026-10-29-steward-profile-household/shots/task-${TASK}`;

test.describe('profile screenshots', () => {
  test.skip(!TASK || !SHOTS[TASK], 'set SHOTS_TASK to a task with screens');
  test('each screen at both sizes, light and dark', async ({ browser }, testInfo) => {
    test.skip(testInfo.project.name !== 'desktop', 'one run is enough');
    test.setTimeout(600_000);
    const only = process.env.SHOTS_ONLY?.split(',');
    for (const shot of SHOTS[TASK!].filter((x) => !only || only.includes(x.name))) {
      for (const theme of ['light', 'dark'] as const) {
        for (const [size, viewport] of SIZES) {
          const port = shot.vault === 'demo' ? DEMO_PORT : EMPTY_PORT.desktop;
          const ctx = await browser.newContext({ viewport, baseURL: `http://localhost:${port}` });
          await ctx.addInitScript((t) => { localStorage.setItem('steward-theme', t); localStorage.setItem('steward-celebrations', 'on'); }, theme);
          const page = await ctx.newPage();
          await loginAs(page, 'alex');
          await shot.prep?.(page);
          await (shot.before ? shot.before(page) : page.goto(shot.path));
          if (shot.ready) await page.getByRole('heading', { name: shot.ready, level: 1 }).waitFor();
          else await page.waitForLoadState('networkidle');
          await shot.act?.(page);
          // Fonts, and any transition the act started (a row's chevron turning), settle before the shot.
          await page.evaluate(() => Promise.all([document.fonts.ready, ...document.getAnimations().map((a) => a.finished.catch(() => null))]));
          await page.screenshot({ path: `${OUT}/${shot.name}-${size}-${theme}.png`, fullPage: true });
          await ctx.close();
        }
      }
    }
  });
});
