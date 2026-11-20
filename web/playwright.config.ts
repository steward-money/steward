import { defineConfig, devices } from '@playwright/test';
import { DEMO_PORT, EMPTY_PORT } from './e2e/ports';

// Read in Node; the web tsconfig has no Node types.
declare const process: { pid: number; env: Record<string, string | undefined> };

// The household's time zone, the one e2e/server.sh gives the server (STEWARD_TIMEZONE). The browser runs in it
// (timezoneId below), and so do the specs: a spec that counts days with new Date() gets the server's day on any machine.
// Workers start after the config loads, so they inherit it.
const HOUSEHOLD_ZONE = 'America/Los_Angeles';
process.env.TZ = HOUSEHOLD_ZONE;

const server = (port: number, vault: 'demo' | 'empty') => ({
  command: 'bash e2e/server.sh',
  // The runner's pid: e2e/server.sh stops itself when this run is gone, even after a SIGKILL that skips Playwright's
  // own shutdown of its servers.
  env: { STEWARD_E2E_PORT: String(port), STEWARD_E2E_VAULT: vault, STEWARD_E2E_OWNER: String(process.pid) },
  // Ready when uvicorn says it is listening (after app startup), not when a probe of `url` answers. Before starting
  // a server, Playwright probes its `url` to refuse a port already in use, with no timeout. On the dev machine a connection
  // to a closed loopback port is dropped, not refused, so each probe hung for the kernel's 127 s of SYN retries
  // before its server even started: over six idle minutes a run, which pushed the suite past its command timeout.
  // A taken port still fails the run at once: uvicorn exits when it cannot bind, and so does server.sh.
  wait: { stderr: new RegExp(`Uvicorn running on http://127\\.0\\.0\\.1:${port} `) },
  timeout: 180_000,
  gracefulShutdown: { signal: 'SIGTERM' as const, timeout: 10_000 },
});

export default defineConfig({
  testDir: 'e2e',
  testMatch: /.*\.spec\.ts/,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 30_000,
  reporter: [['list']],
  use: { baseURL: `http://localhost:${DEMO_PORT}`, timezoneId: HOUSEHOLD_ZONE, trace: 'retain-on-failure', screenshot: 'only-on-failure' },
  // The demo household, and one empty household per project for Set up Steward (onboarding ruling 15). Not 8431:
  // on the dev machine the gateway's MCP endpoint listens there. STEWARD_E2E_BASE_PORT moves them all (e2e/ports.ts).
  webServer: [server(DEMO_PORT, 'demo'), server(EMPTY_PORT.phone, 'empty'), server(EMPTY_PORT.desktop, 'empty')],
  projects: [
    { name: 'phone', testIgnore: /perf\//, use: { ...devices['iPhone 13'], browserName: 'chromium' } },
    { name: 'desktop', testIgnore: /perf\//, use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 800 } } },
    // The timing budgets (README, Performance budgets): wall-clock numbers, so only on purpose, on a quiet machine,
    // with `npm run e2e:perf`. Without STEWARD_E2E_PERF=1 the project doesn't exist, so `npx playwright test` (and
    // deploy/deploy.sh --e2e) can never run it (flaky-test audit E1).
    ...(process.env.STEWARD_E2E_PERF === '1'
      ? [{ name: 'perf', testDir: 'e2e/perf', use: { ...devices['iPhone 13'], browserName: 'chromium' as const } }]
      : []),
  ],
});
