import { expect, test } from '@playwright/test';
import { GATEWAY, loginAs, restoreVault, shown, waitForGateway } from './fixtures';

test.describe.serial('Get Steward ready', () => {
  test.beforeAll(async () => { await restoreVault(); });

  test('a manager sees eight rows; settings env owns are read-only; email is set up and tested', async ({ page }) => {
    await loginAs(page, 'alex');
    await page.goto('/b/ours/settings/ready');
    await expect(page.getByRole('heading', { name: 'Get Steward ready', level: 2 })).toBeVisible();
    await expect(page.getByTestId('ready-row')).toHaveCount(8);
    await expect(page.getByText('Set in your settings file (STEWARD_GATEWAY_NAME)')).toBeVisible();
    await page.getByTestId('ready-row').filter({ hasText: 'Email' }).click();
    await page.getByLabel('Host').fill('smtp.example.com');
    await page.getByLabel('User').fill('steward@example.com');
    await page.getByLabel('Password').fill(['e2e', 'mail', 'pass'].join('-'));
    await page.getByLabel('From').fill('steward@example.com');
    await page.getByRole('button', { name: 'Save' }).first().click();
    await expect(shown(page, 'Saved.')).toBeVisible();
    await page.getByRole('button', { name: 'Send a test', exact: true }).click();
    await expect(shown(page, /^Sent to .+\. Check your inbox\.$/)).toBeVisible();
    await expect(page.getByTestId('ready-row').filter({ hasText: 'Email' })).toHaveClass(/is-done/);
  });

  test('someone who manages no household gets nothing', async ({ page }) => {
    await loginAs(page, 'vee-desktop', 'pw-viewer-demo-2026');
    expect((await page.request.get('/api/install/settings')).status()).toBe(404);
  });

  test('pairing again by code against the stand-in gateway', async ({ page }) => {
    await page.request.post(`${GATEWAY}/__e2e/arm`, { data: { code: 'K7M-4QX' } });
    await loginAs(page, 'alex');
    await page.goto('/b/ours/settings/ready');
    await page.getByTestId('ready-row').filter({ hasText: 'Ask' }).click();
    await page.getByRole('button', { name: 'Pair again' }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Pair', exact: true }).click();
    await page.getByLabel('Code').fill('AAA-AAA');
    await page.getByRole('button', { name: 'Pair', exact: true }).click();
    await expect(page.getByText("That code doesn't match. 4 tries left.")).toBeVisible();
    await page.getByLabel('Code').fill('k7m-4qx');
    await page.getByRole('button', { name: 'Pair', exact: true }).click();
    await expect(page.getByText('Paired with Zebra-Box.')).toBeVisible();
    await waitForGateway(page, 'ours', (s) => s.gateway.state === 'ok');
  });
});
