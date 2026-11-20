# Contributing

Thank you for helping. This page covers how to run Steward's code and tests, what a pull request needs, and the parts of the code a change most often touches.

## Before you send a change

- Sign off every commit with `git commit -s`. That adds a `Signed-off-by:` line, which certifies that you wrote the change or have the right to submit it under the project's license (the [Developer Certificate of Origin](https://developercertificate.org/)). Steward is AGPL-3.0-or-later; see `LICENSE`.
- AI help is welcome. Say in the pull request which parts an AI tool wrote, and review and test them as you would your own: your sign-off covers them too. Steward itself is largely written with Claude (see "How Steward is made" in the README).
- Steward is developed in a private repository and exported to this one at each release, so a pull request here isn't merged directly. When a contribution fits, it's carried over with you named on the commit (`Co-authored-by:`) and arrives in the next release. The pull request is then closed with a note saying so.
- Comments and tests in the code sometimes say `spec N` or `ruling N`. Those refer to design notes that are not public. New work cites public issues instead.
- The yearly refresh of the tax tables (`src/steward/engine/data/rules/`) is best effort. If a table is out of date, an issue or a pull request with the new figures and their source is welcome.
- If you run a changed copy for other people, point the Source code link on the About page at your own source. It is `SOURCE_URL` in `web/src/screens/you/aboutCopy.ts`. The AGPL (section 13) requires that people using a changed copy over a network can get its source.

Report a security problem through [SECURITY.md](SECURITY.md), never in a public issue.

## Development

```
uv sync
uv run pytest tests/test_setasides_api.py      # the tests for what you changed: serial is fastest for a few files
uv run pytest -n auto --dist loadfile          # the whole suite on half the cores
```

Test what a change can affect: the changed module's tests and its callers' tests while you work, then the whole suite in parallel once before a pull request, or when shared code changes (Spending, the Plan's inputs, the engine, the database models and migrations). Starting the workers costs a few seconds, so `-n auto` only pays off for a large run. `-n auto` takes half the cores (`tests/conftest.py`), and `--dist loadfile` keeps a file's tests on one worker. Every test fails after 120 s (perf budgets and model evals after 15 minutes) rather than hang. Perf tests (`-m perf`) measure time and run serially, without `-n`. No test reaches a real Ollama: every gateway a test builds gets a stand-in title model and categorizer (`tests/conftest.py`).

### Tests and time

A test waits for the thing to happen, never for time to pass, and stays deterministic on a busy machine:

- Python reads the time through `steward.clock`; every test runs on `fake_clock` (`tests/conftest.py`), from
  2026-10-30 noon and ticking a microsecond per read. `frozen` pins it to 2026-10-13; `@pytest.mark.real_clock` opts out.
- A condition with a ceiling is `tests/waiting.py`: `wait_until`, `wait_for_card`, `when_waiting`. No `time.sleep`.
- Web tests start on `TEST_NOW` (`web/src/test-clock.ts`). Fake timers come from its `useFakeTimers()`, and `findBy`
  and `waitFor` advance them. Lazy screens are `lazyScreen()`, preloaded before a file's tests.
- Web unit tests and e2e run in the household's time zone, America/Los_Angeles, whatever the machine's zone
  (`web/vite.config.ts`, `web/playwright.config.ts`).
- Timing budgets live in `tests/perf` and `web/e2e/perf`, run on purpose on a quiet machine, never in a deploy.
- `tests/test_determinism_ratchet.py` fails on a new sleep, wall-clock budget, `{ timeout: }`, `shouldAdvanceTime`,
  `React.lazy` or `waitForTimeout`; its allowlist only shrinks.

Order checks, by hand, one file or folder at a time, never in a deploy. Each prints its seed, and the second line
replays one:

    uv run --with pytest-randomly pytest -p randomly -q tests/test_setasides_api.py
    uv run --with pytest-randomly pytest -p randomly --randomly-seed=1234 -q tests/test_setasides_api.py
    cd web && npx vitest run --sequence.shuffle src/screens/Review.test.tsx
    cd web && npx vitest run --sequence.shuffle --sequence.seed=1234 src/screens/Review.test.tsx

A repeat run to prove a flake fixed is capped and gentle, and never runs beside another run:
`nice -n 19 timeout 600 <command>`.

Steward needs `sqlcipher3-binary`, which ships wheels for x86_64 Linux only, so the image builds on x86_64 hardware. An ARM machine would need a different build.

## Web app

The front end lives in `web/` (React + Vite). The API serves the built app from `web/dist`; without a build, only the API answers.

    cd web && npm ci
    npm run dev          # Vite on :5173, proxying /api to a `uv run steward serve` on :8420
    npm run build        # type-check and build to web/dist
    npm run test         # Vitest unit tests
    npm run e2e          # build, start a fresh demo vault on :8441, run Playwright at phone and desktop sizes
    npm run e2e:perf     # the browser budgets (Home under 1.5 s, review action, lever, celebrations); on a quiet machine

Appearance follows the sky by default (Settings offers Follow the sky, Light and Dark). Fonts are bundled with the app through fontsource. `npm run shots` regenerates the design mockups in `docs/design/m1c/`.

Playwright needs Chromium once: `npx playwright install chromium`. The e2e server seeds the demo household into a temporary directory and deletes it afterwards; it never touches a real vault. Each spec starts from the seeded demo household: `web/e2e/vault_keeper.py` puts it back before every spec (port 8543 by default, the demo port + 102), and seeds it afresh when the household's day has turned since, so a run across midnight keeps "this month" right. e2e uses ports 8441-8443 and, for the fake gateway, 8541-8543; if another run holds them, `STEWARD_E2E_BASE_PORT=8471 npm run e2e` moves the whole set (8471-8473 and 8571-8573). The servers stop on their own within seconds when the run that started them ends, even when it is killed.

## Import formats

Steward imports four formats: Chase credit card CSV, Chase checking CSV, OFX / QFX (Quicken), and "Other CSV (choose columns)" for anything else. For other banks, try the OFX/QFX export first; if there isn't one, use "Other CSV" and choose the date, description and amount columns.

To add a bank's CSV as a named format: add its exact header list to `csv_formats.py`, a `parse_<name>` function with the right `ColumnMapping`, and a branch in `registry.detect`/`parse`, with a sample file under `tests/samples/` whose values are fake.

## Investment accounts

An investment account's page (brokerage, 401(k), IRAs, HSA and 529) shows:
- what its bank sends;
- this year's change, what was put in, and market growth (the change less what was put in);
- the balance against the running total put in, from the first balance Steward saw;
- what it's invested in, with each fund's cost a year;
- for a retirement account, this year's contributions against the limit, with "on pace".

`GET /api/b/<book>/accounts/{id}/investment` serves it. The routes are household books only.

- **Holdings** come from SimpleFIN when the bank sends them, or from a positions CSV: Import positions on the account page, `POST /api/b/<book>/accounts/{id}/positions/preview`, then `POST …/positions`. The file is read in memory and never kept; its columns are remembered per institution.
- **Fund costs** come from `src/steward/data/expense_ratios.json` (28 common Vanguard, Fidelity, Schwab and iShares index funds and ETFs, each figure checked against the fund's fact sheet, prospectus or page; Steward never looks a fund up), or from the household's own entry (`PUT /api/b/<book>/expense-ratios`).
- **An advisory fee** is set on the account (`PATCH …/accounts/{id}` with `advisory_fee_bp`) and counts in the Plan.
- **Contribution limits** come from `src/steward/engine/data/rules/retirement_<year>.json`, the same table Next dollar reads. When this year's is missing, the page says so and `steward migrate` warns.
- **Accounts opens with net worth split** into Investable, Home and cars, and What you owe (and Business when there is any). `GET /api/b/<book>/summary` carries `split`, and the history carries each day's `investable_cents`.
- **A Vehicle** is an account entered by hand, optionally linked to its loan.

## Plan API

The Plan tab's data comes from these endpoints, all behind sign-in: `GET /api/b/ours/plan` (the saved plan, its levers and a fresh result, or `{"setup": [...]}` listing what is still missing), `POST /api/b/ours/plan/preview` (lever values in, ages and band out), `POST /api/b/ours/plan/impacts` (the "What would change it" list), `POST /api/b/ours/plan` (save; every save is kept, the newest is the plan), `GET /api/b/ours/plan/history`, `GET /api/b/ours/plan/setup`, `GET /api/b/ours/checklist`, `GET/POST/PATCH/DELETE /api/b/ours/children`, `GET/PUT /api/b/ours/asset-classes` and `GET /api/b/ours/cashflow?months=12`. Results are cached in memory by input digest (256 entries).

The web client sends lever changes as a delta: `assumed` holds only the fields someone moved (a lever's slider, or "Try this"), `try` holds every field of each lever whose suggestion someone changed ("Change suggestion", which pins it), and `pinned` is sent only to unpin (the saved pinned list minus that lever). Sending back the whole `levers` object would pin every lever. Requests are debounced 150 ms and a newer one aborts the one in flight.

The earnings record downloaded from the my Social Security website is uploaded as the raw request body, never as a form, so it is never written to disk: `POST /api/b/ours/ss/import?person=<person>` with the downloaded XML file as the body and `content-type: application/xml`. Steward keeps each year's Social Security taxed earnings and nothing else from the file. `GET /api/b/ours/ss` summarizes each person's imported record: how many years, the first and last year, and when it was imported (null if none).

## Performance budgets

Measured against the seeded demo household: the perf fixture seeds three 5-year households alongside each other, at least 15,000 transactions. Each budget has a test in `tests/perf/` that fails on regression. The plan preview and impacts budgets run on those three merged households (9 children and six 529 accounts), which is heavier than a real one, so passing there is conservative. The tests report the 95th percentile of 20 runs; the sync budget uses 3 runs and the CSV commit budget a single run, since each needs a fresh vault or account. The three ingestion budgets run on the local fixture only and skip when `STEWARD_PERF_BASE_URL` is set.

| Budget | Target |
|---|---|
| Home interactive, phone over a private network | < 1.5 s (server share: `/api/b/ours/summary` < 300 ms) |
| Transaction search over 5 years | < 200 ms |
| Review-queue action round trip (PATCH) | < 150 ms |
| Review queue (uncategorized + possible duplicates) | < 300 ms |
| Net worth history, 90 days | < 300 ms |
| SimpleFIN sync, 4 institutions, 3,200 transactions (95th percentile of 3 runs, fresh vault each) | < 60 s |
| CSV import, 24 months (about 2,500 rows) | < 5 s |
| Import preview, same file | < 2 s |
| Home interactive, phone, CPU 4× slower, 30 ms RTT (cold cache, browser median of 5) | < 1.5 s |
| Home layout shift, same phone, until the strip, Since and the fonts have landed (CLS, worst of 5) | < 0.1 |
| Review action, browser round trip (p95 of 10) | < 150 ms |
| Three Review sorts in a row with every celebration on (`e2e/perf/celebrate.spec.ts`, 4× CPU) | no long task over 100 ms; the canvas removed afterwards |
| Celebrate code, loaded on the first celebration (`web/scripts/bundle-size.mjs`) | ≤ 12 KB gzip |
| Initial JS + CSS, gzipped | ≤ 190 KB |
| Plan route code, loaded on demand (`web/scripts/bundle-size.mjs`) | < 40 KB gzip |
| Net worth chart on Accounts, loaded on demand (`web/scripts/bundle-size.mjs`) | < 8 KB gzip |
| Reimbursable payer prompt (`PayerInline`), loaded on first use (`web/scripts/bundle-size.mjs`) | ≤ 8 KB gzip |
| Reimbursements tab (`Reimbursements`) with its imports, loaded on demand | ≤ 20 KB gzip |
| Trip list (`Trip`) with its imports, loaded on demand | ≤ 20 KB gzip |
| Investment account sections (`Investment`) with their imports, loaded on demand | ≤ 20 KB gzip |
| Import positions (`PositionsImport`), loaded on first use | ≤ 8 KB gzip |
| Contribution limits on Accounts (`LimitsTable`), loaded on demand | ≤ 8 KB gzip |
| Net worth split on Accounts (`NetWorthSplit`), loaded on demand | ≤ 6 KB gzip |
| Lever move to updated ages on Try changes (`e2e/perf/perf.spec.ts`, `/plan/try`, phone, 4× CPU, 30 ms RTT, p95 of 20) | < 350 ms |
| Retirement solve, 2,000 histories x all ages (median of 5) | < 400 ms |
| Lever impacts, same histories (median of 3) | < 3 s |
| Lever drag round trip, `POST /api/b/ours/plan/preview` (p95 of 20, every request a cache miss) | < 500 ms |
| Lever impacts, `POST /api/b/ours/plan/impacts` (median of 3) | < 3 s |
| Cash flow, 12 months over 5 years of data, `GET /api/b/ours/cashflow` | < 150 ms |
| Book dependency (membership check and the book's cached engine), on top of sign-in | < 5 ms per request |
| Opening a book whose engine isn't open yet (the first request after a switch) | < 300 ms |
| Start-up with four books (open each, check its Alembic upgrade) | < 3 s |
| Redaction, 1000 characters, GPU p95 (`tests/perf/test_redaction_gpu_budgets.py`) | < 60 ms (< 120 ms with look-alikes) |
| Secret rule stays linear (`tests/boundary/test_text.py`) | 4× the text in < 8× the CPU time |
| Inventory page API with 200 items (`uv run pytest -q -m perf tests/perf/test_inventory_budgets.py`) | < 500 ms |
| Suggester over 10,000 transactions (same test file) | < 2 s |
| All checks over 10,000 transactions, none checked before (`tests/perf/test_watch_budgets.py`) | < 2 s |

Run the three book budgets with `uv run pytest -q -m perf tests/perf/test_books_budgets.py`.

The browser budgets run with `npm run e2e:perf` (the perf project, a phone); the bundle budget runs inside `npm run build`.

Perf tests are skipped by default. Run them on demand:

```
uv run pytest -m perf tests/perf
STEWARD_PERF_FACTOR=2.5 uv run pytest -m perf tests/perf
```

`STEWARD_PERF_FACTOR` multiplies measured times, so a fast desktop can approximate slower hardware. 2.5 approximates a low-power NAS processor, about 2.5 times slower than a fast desktop core. The engine budgets run with `STEWARD_PERF_FACTOR=2.5 uv run pytest -m perf tests/perf/test_engine_budgets.py`.

When `STEWARD_PERF_BASE_URL` is set, the tests skip seeding and sign in as the demo user `alex` on that server. That needs a throwaway, demo-seeded vault, never a real one: the review test edits transactions. Seed it with `STEWARD_ALLOW_DEMO=1 steward seed-demo --years 5`, run with three different `--seed` values and `--force` after the first, to reach 15,000 rows. Network time is included, so this is the phone-facing number. Results are cached in memory, so a remote run must use fresh lever values (the plan tests draw a run-unique offset) or a freshly started server, or it times cache hits.
