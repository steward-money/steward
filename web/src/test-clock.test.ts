import { TEST_NOW } from './test-clock';

// vite.config.ts pins the tests to the household's time zone. Without it, a test reads the machine's zone and a date
// near midnight moves to the day before or after.
it('runs in the household time zone on any machine', () => {
  expect(Intl.DateTimeFormat().resolvedOptions().timeZone).toBe('America/Los_Angeles');
  expect(new Date(TEST_NOW).getTimezoneOffset()).toBe(7 * 60);
  // Python's DEFAULT_NOW (tests/conftest.py): noon on 2026-10-30 in Los Angeles.
  expect(TEST_NOW.toISOString()).toBe('2026-10-30T19:00:00.000Z');
});
