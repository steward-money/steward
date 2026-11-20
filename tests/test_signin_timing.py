"""Issue #50: a wrong password takes the same work for a name that exists, a name nobody has, and an account its own
lock holds. A wrong password at a real name counts and commits, which writes a page to the database; the other two
write one too (users.miss), so the commit's write doesn't show whether a username exists."""
import re
import threading
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select

from steward import clock
from steward.api import auth as auth_api
from steward.auth import users
from steward.db.directory_models import DirectoryMeta, User
from steward.auth.passwords import verify_password
from steward.auth.users import BadCredentials, Locked, check_login, create_user

BASE = "https://nas.tail1234.ts.net"
PW = "pw-one-two-3"
KINDS = {"SELECT": "read", "INSERT": "write", "UPDATE": "write", "DELETE": "write"}


class Statements:
    """What one engine runs, as kinds: a read, a write, BEGIN, COMMIT, ROLLBACK, and where the password hash falls
    among them. The tables differ by design (a real name's count against users.miss's row), so the kinds and their
    order are what must match. The hash is in the list because a path that commits before it gives up the write lock
    one hash sooner, which a concurrent sign-in can time. PRAGMA query_only is left out: the pooled connection sets it
    when its last use was a GET, whatever the request does."""

    def __init__(self, engine):
        self.engine, self.seen = engine, []

    def count_hashes(self, monkeypatch):
        def hashed(*args, **kwargs):
            self.seen.append("hash")
            return verify_password(*args, **kwargs)
        for module in (users, auth_api):
            monkeypatch.setattr(module, "verify_password", hashed)

    def _statement(self, conn, cursor, statement, *_):
        word = statement.split(None, 1)[0].upper()
        if word != "PRAGMA":
            self.seen.append(KINDS.get(word, word))

    def _commit(self, conn):
        self.seen.append("COMMIT")

    def _rollback(self, conn):
        self.seen.append("ROLLBACK")

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._statement)
        event.listen(self.engine, "commit", self._commit)
        event.listen(self.engine, "rollback", self._rollback)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine, "before_cursor_execute", self._statement)
        event.remove(self.engine, "commit", self._commit)
        event.remove(self.engine, "rollback", self._rollback)


def _answer(app, monkeypatch, address: str, name: str, password: str = "guess"):
    """One try from a browser nobody has seen, at its own address, so no device or address lock is in play."""
    with TestClient(app, base_url=BASE, client=(address, 50000)) as c, Statements(app.state.books.directory) as s:
        s.count_hashes(monkeypatch)
        r = c.post("/api/auth/login", json={"username": name, "password": password})
    cookie = re.sub(r"=[^;]*;", "=<id>;", r.headers["set-cookie"], count=1)   # each new device gets its own id
    return (r.status_code, r.json(), cookie), s.seen


def test_a_name_nobody_has_and_a_locked_account_make_the_same_statements_as_a_wrong_password(app, ddb, monkeypatch):
    u = create_user(ddb, "jr", "jr@x.com", "JR", PW)
    _answer(app, monkeypatch, "10.0.0.9", "warm-up")   # the first miss ever adds users.miss's row; later ones update it
    real, real_seen = _answer(app, monkeypatch, "10.0.0.1", "jr")
    nobody, nobody_seen = _answer(app, monkeypatch, "10.0.0.2", "nobody-here")
    u.locked_until = clock.now() + timedelta(minutes=10)
    ddb.commit()
    held, held_seen = _answer(app, monkeypatch, "10.0.0.3", "jr", PW)
    # login and check_login each find the name; the hash comes before the write, inside the write lock, on all three
    assert real_seen == ["BEGIN", "read", "read", "hash", "write", "read", "COMMIT"]
    assert nobody_seen == real_seen and held_seen == real_seen
    assert [seen.count("hash") for seen in (real_seen, nobody_seen, held_seen)] == [1, 1, 1]
    assert real[0] == 401 and nobody == real and held == real


def _frames(engine, attempt) -> int:
    """WAL frames (pages) one attempt's commit writes: the log is emptied first, then counted."""
    with engine.connect() as c:
        assert c.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] == 0
    attempt()
    with engine.connect() as c:
        busy, log, _ = c.exec_driver_sql("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
    assert busy == 0
    return log


def test_each_wrong_try_writes_the_same_pages(books, ddb):
    """SQLite writes nothing for an UPDATE that leaves a row as it was, so a no-op UPDATE wouldn't do: the miss
    changes its row every time, and its commit writes as many pages as a real name's count."""
    now = clock.now()
    u = create_user(ddb, "jr", "jr@x.com", "JR", PW)

    def wrong(name, password="guess", error=BadCredentials):
        def attempt():
            with pytest.raises(error):
                check_login(ddb, name, password, now)
            ddb.rollback()                          # ends the read a raise leaves open, so the checkpoint can run
        return attempt
    wrong("warm-up")()
    real = _frames(books.directory, wrong("jr"))
    assert real >= 1
    assert _frames(books.directory, wrong("nobody-here")) == real
    u.locked_until = now + timedelta(minutes=10)
    ddb.commit()
    assert _frames(books.directory, wrong("jr", PW, Locked)) == real


def _misses(ddb) -> int:
    n = int(ddb.scalar(select(DirectoryMeta.value).where(DirectoryMeta.key == users.MISSES)))
    ddb.rollback()
    return n


@pytest.mark.parametrize("second", ["jr", "nobody-here"])
def test_two_wrong_tries_at_once_queue_for_the_write_lock_and_both_count(app, ddb, monkeypatch, second):
    """Argon round: a SELECT in a plain BEGIN would hash outside the write lock, and the second of two tries to
    upgrade its read to a write after the first committed would fail at once with "database is locked" (500). A
    sign-in is a POST, so its directory session begins with BEGIN IMMEDIATE (deps._directory_session): the second try
    waits at BEGIN while the first hashes, and hashes only after the first commits. Both answer 401 and both count."""
    u = create_user(ddb, "jr", "jr@x.com", "JR", PW)
    TestClient(app, base_url=BASE, client=("10.0.0.9", 50000)).post(   # the first miss ever adds users.miss's row
        "/api/auth/login", json={"username": "warm-up", "password": "guess"})
    misses = _misses(ddb)
    engine = app.state.books.directory
    seen, first_hashing, second_begun = [], threading.Event(), threading.Event()

    def hashed(stored, password):
        seen.append(f"hash {password}")
        if password == "guess-a":
            first_hashing.set()
            # A guard against a hang, not a wait for timing: the second try asks for the lock while this one hashes.
            assert second_begun.wait(10), "the second try never asked for the write lock"
        return verify_password(stored, password)

    def statement(conn, cursor, text, *_):
        if text.startswith("BEGIN") and first_hashing.is_set():
            seen.append(text)
            second_begun.set()

    def committed(conn):
        seen.append("COMMIT")

    for module in (users, auth_api):
        monkeypatch.setattr(module, "verify_password", hashed)
    answers = {}

    def attempt(address, name, password):
        r = TestClient(app, base_url=BASE, client=(address, 50000)).post(
            "/api/auth/login", json={"username": name, "password": password})
        answers[password] = r.status_code

    event.listen(engine, "before_cursor_execute", statement)
    event.listen(engine, "commit", committed)
    try:
        first = threading.Thread(target=attempt, args=("10.0.0.1", "jr", "guess-a"))
        first.start()
        assert first_hashing.wait(10)
        other = threading.Thread(target=attempt, args=("10.0.0.2", second, "guess-b"))
        other.start()
        first.join(30)
        other.join(30)
    finally:
        event.remove(engine, "before_cursor_execute", statement)
        event.remove(engine, "commit", committed)
    assert answers == {"guess-a": 401, "guess-b": 401}
    # The second BEGIN IMMEDIATE is sent while the first try hashes; it returns only once the first commits.
    assert seen == ["hash guess-a", "BEGIN IMMEDIATE", "COMMIT", "hash guess-b", "COMMIT"]
    ddb.expire_all()
    assert ddb.get(User, u.id).failed_logins == (2 if second == "jr" else 1)
    assert _misses(ddb) == misses + (0 if second == "jr" else 1)
