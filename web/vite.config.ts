/// <reference types="vitest/config" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { preloadFonts } from './scripts/preload-fonts';

export default defineConfig({
  plugins: [react(), preloadFonts()],
  server: { port: 5173, proxy: { '/api': 'http://127.0.0.1:8420' } },
  build: {
    outDir: 'dist', sourcemap: false, target: 'es2022',
    // One initial chunk, as Rollup built it before Vite 8. Rolldown's default split what the entry imports into 19 shared
    // chunks (20 JS and 4 CSS files on a first open, 3.5 KB more gzip, and Welcome over its budget). `$initial` is every
    // module the entry imports statically, so this group is exactly the first download; lazy chunks import from it.
    rolldownOptions: { output: { codeSplitting: { groups: [{ name: 'index', tags: ['$initial'] }] } } },
  },
  test: {
    environment: 'jsdom', globals: true, setupFiles: ['./src/test-setup.ts'], include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
    // Half the cores (flaky-test audit S6): the suite shares the machine with the gateway and agents, and a suite that
    // starves its own workers turns its ceilings into flakes.
    pool: 'forks', maxWorkers: '50%',
    // The ceilings, written down (Vitest's defaults; S5): a test or hook that hangs fails here.
    testTimeout: 5_000, hookTimeout: 10_000,
    // The household's time zone, as in the Python tests (tests/conftest.py), so TEST_NOW is the same instant on any
    // machine. Each worker sets it before the setup file runs; src/test-clock.test.ts fails if it goes.
    env: { TZ: 'America/Los_Angeles' },
  },
});
