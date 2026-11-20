import json
import logging
import os
from datetime import datetime, timedelta, timezone

# Typer forces coloured output when GITHUB_ACTIONS, FORCE_COLOR or PY_COLORS is set, and the escape codes split
# option names such as --max-age-hours in the CLI tests' output. It reads this once, when typer.rich_utils is
# first imported, so it's set before anything imports Typer. tests/test_cli.py checks it holds.
os.environ["_TYPER_FORCE_DISABLE_TERMINAL"] = "1"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from steward.app import create_app  # noqa: E402
from steward.db.keys import ensure_key  # noqa: E402
from steward.logs import START_LINES  # noqa: E402
from steward.settings import Settings  # noqa: E402
from steward_gateway.logs import QUIET  # noqa: E402

# Every logger the gateway's setup_logging() touches: the root, the gateway's own, the quiet libraries and uvicorn's,
# and Steward's own (steward.logs.configure).
LOGGERS_SETUP_TOUCHES = ("", "steward", "steward.http", *START_LINES, "steward_gateway", *QUIET, "uvicorn",
                         "uvicorn.error", "uvicorn.access")


@pytest.fixture(autouse=True)
def clean_logging():
    """setup_logging changes process-wide logging (a root handler with a scrub filter, filters on the quiet
    loggers); put every logger it touches back after each test so none of it leaks into the next."""
    saved = {}
    for name in LOGGERS_SETUP_TOUCHES:
        lg = logging.getLogger(name or None)
        saved[name] = (lg.level, list(lg.handlers), list(lg.filters), lg.propagate,
                       [list(h.filters) for h in lg.handlers])
    yield
    for name, (level, handlers, filters, propagate, hfilters) in saved.items():
        lg = logging.getLogger(name or None)
        lg.setLevel(level)
        lg.handlers[:] = handlers
        lg.filters[:] = filters
        lg.propagate = propagate
        for h, f in zip(handlers, hfilters):
            h.filters[:] = f


@pytest.fixture(autouse=True)
def no_real_ollama(monkeypatch):
    """A gateway built in a test gets a stand-in title model and categorizer, and opens no PDFs, and `serve` prints a
    stand-in start line instead of asking Ollama, unless the test passes its own: the gateway
    runs the real Ollama on 127.0.0.1:11434, and a test that reached it waited seconds a turn on a real model (and on a
    machine without it, on a port that hangs). Tests of the Ollama clients themselves talk to their own fake server."""
    from tests.gateway.fakes import FakeCategorizer, FakeOllama
    monkeypatch.setattr("steward_gateway.app.Ollama", lambda *a, **k: FakeOllama())
    monkeypatch.setattr("steward_gateway.app.Categorizer", lambda *a, **k: FakeCategorizer())
    from tests.gateway.fakes import FakeStatementModel
    monkeypatch.setattr("steward_gateway.app.StatementModel", lambda *a, **k: FakeStatementModel(features=()))
    from steward_gateway.pdf import PdfReader as _Real
    monkeypatch.setattr("steward_gateway.app.PdfReader", lambda *a, **k: _Real(check=dict))
    monkeypatch.setattr("steward_gateway.cli.local_model_line", lambda cfg, **k: "Local model: (a test)")


def pytest_xdist_auto_num_workers(config):
    """`-n auto` takes half the cores, never all of them (flaky-test audit S6). The deploy's run shares the machine
    with the gateway, Ollama and agents, and a suite that starves its own workers turns every ceiling into a flake."""
    return max(1, (os.cpu_count() or 2) // 2)


def pytest_addoption(parser):
    parser.addoption("--release", action="store_true", default=False,
                     help="the release gate: tests/test_claims.py also fails on any claim still pending")


def _one_cookie_per_name(request):
    names = [part.split("=", 1)[0].strip() for part in request.headers.get("cookie", "").split(";") if "=" in part]
    twice = sorted({n for n in names if names.count(n) > 1})
    if twice:
        raise AssertionError(f"this request carries two cookies named {twice}; the server reads one of them, and "
                             "which one is the client's choice. Clear the jar first, or set the cookie with "
                             'domain="nas.tail1234.ts.net" so it replaces the one the server set.')


@pytest.fixture(autouse=True)
def one_cookie_per_name(monkeypatch):
    """A test that puts an old session token back with cookies.set() but no domain adds a second cookie beside the one
    the server set, and the request carries both. Which one the server reads then rests on the client's cookie order,
    which httpx2 2.16 reversed, so a test of "the old token is refused" could pass on the new one. Every TestClient
    fails such a request instead."""
    from starlette.testclient import TestClient as StarletteClient
    init = StarletteClient.__init__

    def guarded(self, *args, **kwargs):
        init(self, *args, **kwargs)
        self.event_hooks = {**self.event_hooks, "request": [*self.event_hooks["request"], _one_cookie_per_name]}

    monkeypatch.setattr(StarletteClient, "__init__", guarded)


@pytest.fixture(autouse=True)
def fresh_settings_cache():
    """get_settings() caches the first Settings it builds. A test that set other STEWARD_ variables must not hand
    them to the next test (flaky-test audit S4)."""
    from steward.settings import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def default_gateway_name():
    """Spec 2026-11-05, section 7: create_app and the CLI set the gateway's name for the process; a test that set one
    never hands it to the next. Unset, it's "the gateway"."""
    from steward import gateway_name
    gateway_name.reset()
    yield
    gateway_name.reset()


@pytest.fixture(autouse=True)
def household_tz():
    """Spec 2026-11-05, section 7: tests run in the household's time zone they were written for, America/Los_Angeles
    (the settings fixture says so too, for create_app), and no test hands its zone to the next."""
    from steward import clock
    clock.configure("America/Los_Angeles")
    yield
    clock.reset()


@pytest.fixture
def settings(tmp_path, monkeypatch) -> Settings:
    monkeypatch.setenv("STEWARD_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("STEWARD_BASE_URL", "https://nas.tail1234.ts.net")
    monkeypatch.setenv("STEWARD_TIMEZONE", "America/Los_Angeles")
    monkeypatch.setenv("STEWARD_ENV", "test")
    monkeypatch.setenv("STEWARD_EXPORT_TMP_DIR", str(tmp_path / "export-tmp"))
    monkeypatch.setenv("STEWARD_SIMPLEFIN_HOSTS", "testserver")
    monkeypatch.setenv("STEWARD_WEB_DIR", str(tmp_path / "no-web-build"))  # tests never depend on a local web/dist
    return Settings(_env_file=None)


@pytest.fixture
def books(settings):
    from steward.books.registry import open_books
    ensure_key(settings.key_path)
    reg = open_books(settings)
    yield reg
    reg.dispose()


@pytest.fixture
def ours(books):
    return books.book("ours")


@pytest.fixture
def engine(books, ours):
    return books.engine_for(ours)


@pytest.fixture
def ddb(books):
    with Session(books.directory) as s:
        yield s


@pytest.fixture
def db(engine, books, ours):
    from steward.books.people import attach_people
    from tests.factories import TEST_BOOK_KEY
    with Session(engine) as s:
        attach_people(s, lambda: books.people(ours.id))
        s.info[TEST_BOOK_KEY] = (books, ours.id)
        yield s


def _leaves(v):
    if isinstance(v, dict):
        for x in v.values():
            yield from _leaves(x)
    elif isinstance(v, list):
        for x in v:
            yield from _leaves(x)
    elif isinstance(v, str):
        yield v


LAYOUT_TEXT = ("layout", "statement_layout")   # undo_text's models for a remembered layout's label


@pytest.fixture
def undo_entries_hold_no_text():
    """Polish round P36: undo_entry never holds a note or a name. At every flush of a book session the notes, nicknames
    and rule labels in the book are remembered, so text on a row an action deletes still counts; each time an undo
    entry is written, every string in it is checked against all of them, and the test fails if one is there. Account
    names (an account's, a feed's, a held feed's) count for a merge's or a "They're different"'s entry: the journal
    keeps none. Remembered layouts' labels (a bank's or an account's name) are checked against a layout's own journal
    fields only, since a bank's name is often a merchant's too. Suites that journal list it in pytestmark. It yields a
    set: a test that writes an old-format entry on purpose adds the text it plants."""
    from sqlalchemy import event, inspect, select, union_all
    from steward.db.models import (Account, AccountFeed, Flag, HeldFeed, ImportLayout, MerchantRule, StatementLayout,
                                   Transaction, TransactionNote, UndoEntry, UndoText)
    q = union_all(*(select(c.label("v")) for c in (TransactionNote.body, TransactionNote.author_name,
                                                   Transaction.nickname, MerchantRule.nickname, MerchantRule.note,
                                                   MerchantRule.note_by_name, Flag.note)),
                  select(UndoText.value.label("v")).where(((UndoText.model != "account") | (UndoText.field != "name"))
                                                          & UndoText.model.not_in(LAYOUT_TEXT)))
    labels_q = union_all(select(ImportLayout.label.label("v")), select(StatementLayout.label.label("v")),
                         *(select(c.label("v")).where(UndoText.model.in_(LAYOUT_TEXT))
                           for c in (UndoText.value, UndoText.written)))
    names_q = union_all(*(select(c.label("v")) for c in (Account.name, AccountFeed.name, HeldFeed.name)),
                        select(UndoText.value.label("v")), select(UndoText.written.label("v")))
    is_book: dict = {}
    seen: set[str] = set()
    names: set[str] = set()
    labels: set[str] = set()
    allowed: set[str] = set()
    found: list[tuple[str, str]] = []

    def check(session, _ctx):
        conn = session.connection()
        if conn.engine not in is_book:
            is_book[conn.engine] = inspect(conn).has_table("undo_entry")
        if not is_book[conn.engine]:
            return                                     # the directory
        seen.update(v for v in conn.execute(q).scalars() if v)
        names.update(v for v in conn.execute(names_q).scalars() if v)
        labels.update(v for v in conn.execute(labels_q).scalars() if v)
        for e in (o for o in session.new if isinstance(o, UndoEntry)):
            text = seen | names if e.action in ("merge", "different") else seen
            parts = [json.loads(e.before), json.loads(e.after)]
            found.extend((e.action, s) for s in _leaves(parts) if s in text and s not in allowed)
            layout_rows = [d.get(k) for d in parts for k in ("layouts", "statement_layouts")]
            layout_rows += [d.get(p, {}).get("statement_layout") for d in parts for p in ("gone", "was")]
            found.extend((e.action, s) for s in _leaves(layout_rows) if s in labels and s not in allowed)

    event.listen(Session, "after_flush", check)
    yield allowed
    event.remove(Session, "after_flush", check)
    assert found == []


@pytest.fixture
def app(settings, books):
    return create_app(settings, books)


@pytest.fixture
def client(app):
    with TestClient(app, base_url="https://nas.tail1234.ts.net") as c:
        yield c


DEFAULT_NOW = datetime(2026, 10, 30, 19, 0, tzinfo=timezone.utc)   # noon on 2026-10-30 in the household's time zone
FROZEN_NOW = datetime(2026, 10, 13, 19, 0, tzinfo=timezone.utc)    # noon on 2026-10-13 in the household's time zone


class FakeClock:
    """steward.clock.now for one test. By default it starts at DEFAULT_NOW and moves one microsecond each time it is
    read, so timestamps keep their order as on the real clock but the date never turns mid-run. set() pins it."""

    def __init__(self, at: datetime, tick: timedelta = timedelta(microseconds=1)):
        self.at, self.tick = at, tick

    def now(self) -> datetime:
        t = self.at
        self.at += self.tick
        return t

    def set(self, at: datetime, tick: timedelta = timedelta(0)) -> None:
        self.at, self.tick = at, tick

    def advance(self, by: timedelta) -> None:
        self.at += by


@pytest.fixture(autouse=True)
def fake_clock(request, monkeypatch):
    """Every test runs on a FakeClock unless it is marked real_clock (flaky-test audit S2). Data dated October 2026
    and "this month" logic no longer fail at a month's end, and no module reads a clock the test can't see."""
    if request.node.get_closest_marker("real_clock"):
        yield None
        return
    c = FakeClock(DEFAULT_NOW)
    monkeypatch.setattr("steward.clock.now", c.now)
    yield c


@pytest.fixture
def frozen(fake_clock):
    """Pin the clock (steward.clock.now: today(), and every timestamp Steward writes) to noon on 2026-10-13, without
    ticking. List it before signed_in."""
    fake_clock.set(FROZEN_NOW)
    return FROZEN_NOW


@pytest.fixture
def signed_in(client, db):
    """The client signed in as user jr (display name JR)."""
    from tests.factories import make_user
    make_user(db)
    r = client.post("/api/auth/login", json={"username": "jr", "password": "pw-one-two-3"})
    assert r.status_code == 200
    return client


@pytest.fixture
def margaret(books, ddb, ours):
    """The fictional parent book "Margaret Ellis": jr manages it and ours, vee views it."""
    from steward.books.service import create_book
    from tests.factories import make_person
    book = create_book(books, ddb, name="Margaret Ellis", kind="parent",
                       owner_labels=["Margaret", "Robert (husband)", "Joint"])
    make_person(ddb, "jr", [(ours.id, "manager"), (book.id, "manager")])
    make_person(ddb, "vee", [(book.id, "viewer")])
    return book


@pytest.fixture
def mdb(books, margaret):
    from steward.books.people import attach_people
    with Session(books.engine_for(margaret)) as s:
        attach_people(s, lambda: books.people(margaret.id))
        yield s


@pytest.fixture
def mctx(books, margaret, ddb):
    """jr, as a manager of Margaret Ellis, the way open_book would hand him to a route."""
    from sqlalchemy import select
    from steward.db.directory_models import User
    from steward.deps import BookContext
    jr = ddb.scalar(select(User).where(User.username == "jr"))
    return BookContext(margaret, "manager", jr.id, books.people(margaret.id))
