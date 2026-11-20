import logging
from contextlib import asynccontextmanager

import httpx2
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from steward import clock, gateway_name
from steward.advisor.gateway import client_from_settings
from steward.advisor.monitor import AdvisorMonitor, AdvisorRuntime
from steward.api import advisor_tools, grants as grants_api, books as books_api, accounts, asset_classes, auth, business, care, categorize, cashflow, changes, chat, children, csp_report, events, facts, flags, growth, health, home, exports, imports, install, inventory, inventory_files, investments, labels, notes, passkeys, plan, profile, ready, reimburse, review, same_accounts, setasides, setup as setup_api, simplefin, spending, statements as statements_api, ss, suggestions, summary, tax_forms, taxes, transactions, undo, watching, welcome
from steward.api.exports import Downloads
from steward.api.compress import CompressReads
from steward.api.first_run import FirstRun, gate_on
from steward.api.headers import SecurityHeaders, content_security_policy
from steward.api.hosts import HostCheck
from steward.api.limits import BodyLimit
from steward.api.origin import SameOrigin
from steward.auth import setup_link
from steward.app_settings import AppSettings, apply_effective
from steward.auth.passkeys import ChallengeStore
from steward.auth.devices import DeviceKeys, DeviceLockouts
from steward.auth.ratelimit import RateLimiter
from steward.books import sample
from steward.books.jobs import sync_all_books
from steward.books.registry import BookRegistry
from steward.books.sample_guard import SAMPLE_REFUSED, SampleRefused
from steward.deps import now
from steward.logs import RequestLog
from steward.mail import FakeMailer, Mailer, SmtpMailer
from steward.scheduler import Scheduler
from steward.services.catchup import maybe_catch_up
from steward.services import book_export, scheduled_backup
from steward.services.import_staging import ImportStaging
from steward.services.plan_warm import PlanWarmer
from steward.services.statements import where
from steward.services.statements.client import statement_client_for
from steward.services.statements.reader import StatementReader
from steward.services.categorizer_client import HttpModelClient, NullModelClient, client_for
from steward.settings import Settings, get_settings
from steward.taxes.reminders import reminders_tick
from steward.watch.tick import watch_tick
from steward.web import inline_script_hashes, mount_web

log = logging.getLogger(__name__)

BOOK_ROUTERS = (accounts.router, facts.router, transactions.router, review.router, imports.router, imports.member_router, statements_api.router, exports.router, events.router,
                simplefin.router, summary.router, home.router, growth.router, children.router, ss.router, asset_classes.router, cashflow.router,
                plan.router, profile.router, changes.router, suggestions.router, tax_forms.router, flags.router, watching.router, inventory.router, inventory_files.router, chat.router, advisor_tools.router, grants_api.router, business.router, care.router, taxes.router, spending.router, setasides.router, categorize.router, notes.router, labels.router, undo.router, reimburse.router, investments.router, same_accounts.router, ready.router, welcome.router)


def create_app(settings: Settings | None = None, books: BookRegistry | None = None,
               mailer: Mailer | None = None, http=None, gateway=None, first_run_gate: bool | None = None) -> FastAPI:
    settings = settings or get_settings()
    gateway_name.configure(settings.gateway_name)
    clock.configure(settings.timezone)
    books = books or BookRegistry(settings)
    app_settings = AppSettings(settings, books.directory, books.master)

    model: HttpModelClient | NullModelClient = client_for(settings)

    own_http = http is None
    http = http if http is not None else httpx2.Client(timeout=60)

    plan_warm = PlanWarmer(books)

    def sync_job() -> None:
        try:
            sync_all_books(books, http, app.state.model, now(), settings.simplefin_allowed_hosts)
        except Exception:  # noqa: BLE001 - never kill the scheduler thread
            log.exception("Scheduled SimpleFIN sync failed")
        if settings.env != "test":
            plan_warm.kick()    # new figures: the next Plan open finds them worked out (phase 18)

    scheduler = (Scheduler(settings.sync_hours * 3600, sync_job)
                 if settings.sync_hours > 0 and settings.env != "test" else None)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        books.prepare()  # DirectoryUnavailable propagates and stops startup; a broken book is only marked
        app_settings.reload()
        apply_effective(app_settings)    # the zone and name chosen in the app (guided setup spec 4.3)
        book_export.sweep(book_export.tmp_root(settings), 0, everything=True)   # spec P29: every leftover export folder
        app.state.settings = settings
        app.state.statements.place = where.resolve(settings.statement_reading,
                                                   probe=settings.env != "test" or settings.statement_reader)
        app.state.statements.recover()
        if settings.env != "test":
            plan_warm.kick()    # phase 18: Plan's first open after a restart finds its answer
        if settings.env != "test" or settings.statement_reader:
            app.state.statements.start()
        if scheduler:
            scheduler.start()
        if app.state.watcher:
            app.state.watcher.start()
        if app.state.advisor_scheduler:
            app.state.advisor_scheduler.start()
        try:
            yield
        finally:
            plan_warm.stop()
            try:
                app.state.statements.stop()
            finally:
                statement_client.close()     # the clients the app made (a pairing swaps them), never a stand-in
            try:
                if app.state.advisor_scheduler:
                    app.state.advisor_scheduler.stop()
            finally:
                try:
                    if scheduler:
                        scheduler.stop()
                finally:
                    try:
                        if app.state.watcher:
                            app.state.watcher.stop()
                    finally:
                        try:
                            model.close()
                        finally:
                            try:
                                if own_http:
                                    http.close()
                            finally:
                                try:
                                    if gateway is None and app.state.advisor.client is not None:
                                        app.state.advisor.client.close()
                                finally:
                                    books.dispose()

    app = FastAPI(title="Steward", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(BodyLimit)
    app.add_middleware(CompressReads)   # phase 18: API reads gzipped when asked
    if gate_on(settings, first_run_gate):       # release spec 2026-11-05, section 6: 503 until someone exists
        app.add_middleware(FirstRun, has_people=lambda: setup_link.has_people_in(books.directory))
    app.add_middleware(SameOrigin, base_url=settings.base_url)   # outside the body cap: a refused write never streams
    app.add_middleware(HostCheck, names=settings.api_hosts)       # issue #8: /api/ answers only at Steward's own names
    # Outermost (the last added): every answer gets the headers, refusals included; the page's CSP allows its inline
    # first-paint script by the hash of the built index.html.
    app.add_middleware(SecurityHeaders, csp=content_security_policy(inline_script_hashes(settings.web_dir)))
    app.add_middleware(RequestLog)   # outermost of all: one line per answer, its route's template, never its address
    app.state.model = model
    app.state.http = http
    app.state.settings = settings
    app.state.books = books
    app.state.app_settings = app_settings
    advisor_client = gateway if gateway is not None else client_from_settings(settings)
    app.state.advisor = AdvisorRuntime(advisor_client, AdvisorMonitor(advisor_client, books))
    app.state.sync_job = sync_job
    app.state.plan_warm = plan_warm
    app.state.scheduler = scheduler
    app.state.mailer = mailer or (FakeMailer() if settings.env == "test" else SmtpMailer(settings, app_settings))

    backup_since = now()   # the built-in backup's "since" with no record: a fresh install waits for the next scheduled time

    def watch_job() -> None:
        try:
            watch_tick(books, app.state.mailer, settings, now())
        except Exception:  # noqa: BLE001 - never kill the scheduler thread
            log.exception("The watch tick failed")
        try:
            reminders_tick(books, app.state.mailer, settings, now())
        except Exception:  # noqa: BLE001 - never kill the scheduler thread
            log.exception("The quarterly taxes email failed")
        try:
            app.state.statements.sweep_all()
        except Exception:  # noqa: BLE001 - never kill the scheduler thread
            log.exception("The statement sweep failed")
        try:
            scheduled_backup.backup_tick(books, http, settings, now(), backup_since, app_settings)
        except Exception:  # noqa: BLE001 - never kill the scheduler thread
            log.exception("The scheduled backup failed")
        try:
            sample.sweep_removed(books, now())
        except Exception:  # noqa: BLE001 - never kill the scheduler thread
            log.exception("The Sample sweep failed")

    # Ruling 17: every minute, outside tests: the 6:30 AM run, the 7:00 AM summary and the outbox; then the quarterly taxes email (tax decisions ruling 11); then Steward's own daily backup (release spec 2026-11-05, section 7).
    watcher = Scheduler(60, watch_job, name="steward-watch") if settings.env != "test" else None
    app.state.watch_job = watch_job
    app.state.watcher = watcher

    def advisor_job() -> None:
        try:
            app.state.advisor.monitor.tick()
            maybe_catch_up(app.state.advisor.monitor.health, app.state.model, books, now())
            app.state.statements.wake()   # P31: each tick wakes waiting files
        except Exception:  # noqa: BLE001 - never kill the scheduler thread
            log.exception("The advisor tick failed")

    # Spec 3.2: health every 60 seconds and the 3:30 AM cross-check, outside tests and only once paired.
    app.state.advisor_job = advisor_job
    app.state.advisor_scheduler = (Scheduler(60, advisor_job, name="steward-advisor")
                                   if settings.env != "test" and advisor_client is not None else None)

    def use_gateway(client) -> None:
        """Pairing by code (guided setup spec 6.2): the new token takes hold at once, no restart. The advisor, the
        categorizer and the PDF reader all hold the token, so all three are swapped. The app closes only the clients
        it made: whatever stands on app.state now is not its to close."""
        nonlocal model, statement_client
        old = app.state.advisor.use(client)
        if gateway is None and old is not None:
            old.close()
        if not settings.categorizer_url:
            old_model, model = model, client_for(settings)
            app.state.model = model
            old_model.close()
        old_reader, statement_client = statement_client, statement_client_for(settings)
        app.state.statements.client = statement_client
        old_reader.close()
        if app.state.advisor_scheduler is None and settings.env != "test":
            app.state.advisor_scheduler = Scheduler(60, advisor_job, name="steward-advisor")
            app.state.advisor_scheduler.start()

    app.state.use_gateway = use_gateway

    app.state.challenges = ChallengeStore()
    app.state.rate_limits = RateLimiter()           # hardening plan: per-address limits, in memory like the challenges
    app.state.device_keys = DeviceKeys.from_master(books.master)   # issue #7: the device cookie, signed with steward.key
    app.state.device_lockouts = DeviceLockouts()    # issue #7: wrong passwords per device and account
    app.state.export_downloads = Downloads()
    app.state.import_staging = ImportStaging()     # import/export spec P3: staged files live here, in memory only
    statement_client = statement_client_for(settings)
    # PDF statements spec P6: one reader thread; PDF bytes live in its memory only (P7).
    app.state.statements = StatementReader(books, statement_client, health=lambda: app.state.advisor.monitor.fresh(60),
                                           peek=lambda: app.state.advisor.monitor.health)

    @app.exception_handler(SampleRefused)
    async def sample_refused_handler(request: Request, exc: SampleRefused):
        return JSONResponse({"error": SAMPLE_REFUSED}, status_code=403)

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        body = exc.detail if isinstance(exc.detail, dict) else {"error": str(exc.detail)}
        return JSONResponse(body, status_code=exc.status_code, headers=exc.headers)

    app.include_router(health.router)
    app.include_router(csp_report.router)
    app.include_router(auth.router)
    app.include_router(auth.users_router)
    app.include_router(passkeys.router)
    app.include_router(setup_api.router)
    app.include_router(install.router)
    app.include_router(books_api.router)
    app.include_router(books_api.invites_router)
    app.include_router(books_api.book_router)
    for r in BOOK_ROUTERS:
        app.include_router(r, prefix="/api/b/{slug}")
    mount_web(app, settings.web_dir)  # last: the catch-all must not shadow any /api route
    return app
