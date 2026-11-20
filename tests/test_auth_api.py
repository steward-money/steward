from steward import clock
from datetime import timedelta

from steward.auth import sessions
from steward.auth.cookies import HOST_SESSION, SESSION
from steward.db.directory_models import Session as DbSession
from steward.auth.users import create_user


def _mk(ddb):
    return create_user(ddb, "jr", "jr@x.com", "JR", "pw-one-two-3")


def test_login_sets_cookie_and_me(client, ddb):
    _mk(ddb)
    r = client.post("/api/auth/login", json={"username": "JR", "password": "pw-one-two-3",
                                             "device_label": "JR's phone"})
    assert r.status_code == 200 and r.json()["user"]["username"] == "jr"
    c = r.headers["set-cookie"].lower()
    assert "httponly" in c and "secure" in c and "samesite=lax" in c
    me = client.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["session"]["device_label"] == "JR's phone"


def test_me_includes_the_signed_in_persons_own_email(client, ddb):
    _mk(ddb)
    client.post("/api/auth/login", json={"username": "jr", "password": "pw-one-two-3"})
    assert client.get("/api/auth/me").json()["user"]["email"] == "jr@x.com"


def test_bad_password_and_lockout_messages(client, ddb):
    """Issue #7: five wrong tries lock this device's password sign-in for the account, and the lock answers exactly
    as a wrong password and a name nobody has do, so no answer shows that a username exists."""
    _mk(ddb)
    wrong = {"error": "That username or password isn't right."}
    for _ in range(5):
        r = client.post("/api/auth/login", json={"username": "jr", "password": "no"})
        assert r.status_code == 401 and r.json() == wrong
    r = client.post("/api/auth/login", json={"username": "jr", "password": "pw-one-two-3"})
    assert r.status_code == 401 and r.json() == wrong
    r = client.post("/api/auth/login", json={"username": "nobody-here", "password": "no"})
    assert r.status_code == 401 and r.json() == wrong


def test_logout_invalidates_server_side(client, ddb):
    _mk(ddb)
    client.post("/api/auth/login", json={"username": "jr", "password": "pw-one-two-3"})
    token = client.cookies[HOST_SESSION]
    assert client.post("/api/auth/logout").status_code == 204
    client.cookies.set(HOST_SESSION, token)  # reuse the old token
    assert client.get("/api/auth/me").status_code == 401


def test_revoke_other_device(client, ddb):
    _mk(ddb)
    client.post("/api/auth/login", json={"username": "jr", "password": "pw-one-two-3", "device_label": "A"})
    tok_a = client.cookies[HOST_SESSION]
    client.cookies.clear()
    client.post("/api/auth/login", json={"username": "jr", "password": "pw-one-two-3", "device_label": "B"})
    sessions = client.get("/api/auth/sessions").json()["sessions"]
    sid_a = next(s["id"] for s in sessions if s["device_label"] == "A")
    assert client.delete(f"/api/auth/sessions/{sid_a}").status_code == 204
    client.cookies.set(HOST_SESSION, tok_a, domain="nas.tail1234.ts.net")    # in place of B
    assert client.get("/api/auth/me").status_code == 401


def test_unauthenticated(client):
    r = client.get("/api/auth/me")
    assert r.status_code == 401 and r.json() == {"error": "Please sign in."}


def test_logout_clears_cookie(client, ddb):
    _mk(ddb)
    client.post("/api/auth/login", json={"username": "jr", "password": "pw-one-two-3"})
    r = client.post("/api/auth/logout")
    assert "steward_session=" in r.headers["set-cookie"] and "max-age=0" in r.headers["set-cookie"].lower()


def _login(client, name="jr", label=""):
    return client.post("/api/auth/login", json={"username": name, "password": "pw-one-two-3",
                                                "device_label": label})


def test_cannot_revoke_another_users_session(client, ddb):
    _mk(ddb)
    create_user(ddb, "bo", "bo@x.com", "Bo", "pw-one-two-3")
    _login(client, "jr", "A")
    tok_a = client.cookies[HOST_SESSION]
    sid_a = client.get("/api/auth/me").json()["session"]["id"]
    client.cookies.clear()
    _login(client, "bo", "B")
    assert client.delete(f"/api/auth/sessions/{sid_a}").status_code == 404
    client.cookies.set(HOST_SESSION, tok_a, domain="nas.tail1234.ts.net")    # in place of bo's
    assert client.get("/api/auth/me").status_code == 200


def test_expired_sessions(client, ddb):
    u = _mk(ddb)
    t = clock.now()
    token, _ = sessions.create_session(ddb, u, "x", t, 30)
    assert sessions.resolve(ddb, token, t + timedelta(days=31)) is None
    _login(client, "jr", "web")
    sid = client.get("/api/auth/me").json()["session"]["id"]
    ddb.rollback()  # end the read snapshot so we see the request's commit
    row = ddb.get(DbSession, sid)
    row.expires_at = t - timedelta(days=1)
    ddb.commit()
    assert client.get("/api/auth/me").status_code == 401
    assert not sessions.revoke(ddb, sid, u.id, t)
    token2, s2 = sessions.create_session(ddb, u, "y", t, 30)
    client.cookies.set(HOST_SESSION, token2, domain="nas.tail1234.ts.net")
    s2.expires_at = t - timedelta(seconds=1)
    ddb.commit()
    assert client.get("/api/auth/sessions").status_code == 401


def test_expired_session_not_listed_or_revocable(client, ddb):
    u = _mk(ddb)
    _login(client, "jr", "live")
    t = clock.now()
    _, old = sessions.create_session(ddb, u, "old", t, 30)
    old.expires_at = t - timedelta(days=1)
    ddb.commit()
    labels = [s["device_label"] for s in client.get("/api/auth/sessions").json()["sessions"]]
    assert labels == ["live"]
    assert client.delete(f"/api/auth/sessions/{old.id}").status_code == 404


def test_resolve_updates_last_seen(ddb):
    u = _mk(ddb)
    t = clock.now()
    token, s = sessions.create_session(ddb, u, "x", t, 30)
    later = t + timedelta(hours=2)
    sessions.resolve(ddb, token, later)
    ddb.refresh(s)
    assert s.last_seen_at == later


def test_login_cookie_lifetime_and_path(client, ddb):
    _mk(ddb)
    c = _login(client).headers["set-cookie"].lower()
    assert "max-age=2592000" in c and "path=/" in c


def test_the_sessions_last_seen_is_stamped_at_most_every_five_minutes(signed_in, ddb, fake_clock):
    """Phase 18: the stamp was a committed write on every signed-in request. Inside five minutes a request writes
    nothing; after, the next one stamps, through begin_write, since a GET's directory session is read-only in tests."""
    from sqlalchemy import select

    def seen():
        ddb.rollback()                                  # a fresh snapshot
        return ddb.scalar(select(DbSession.last_seen_at))
    first = seen()
    fake_clock.advance(timedelta(minutes=4))
    assert signed_in.get("/api/auth/me").status_code == 200
    assert seen() == first
    fake_clock.advance(timedelta(minutes=2))
    assert signed_in.get("/api/auth/me").status_code == 200
    assert seen() > first + timedelta(minutes=5)


def test_a_get_reads_the_directory_and_cannot_write_it_in_tests(app):
    """A GET's directory session is a plain read (BEGIN: no write lock). Tests make a write in it an error, so a GET
    that writes the directory without begin_write fails here, as a book GET does."""
    from types import SimpleNamespace

    import pytest
    from sqlalchemy import text, update
    from sqlalchemy.exc import DBAPIError

    from steward.db.directory_models import User
    from steward.deps import _directory_session
    with _directory_session(SimpleNamespace(method="GET", app=app)) as s:
        assert s.execute(text("SELECT 1")).scalar() == 1
        with pytest.raises(DBAPIError, match="readonly"):
            s.execute(update(User).values(display_name="x"))


def _set_cookies(r) -> list[str]:
    return [v for k, v in r.headers.multi_items() if k == "set-cookie"]


def test_on_https_the_session_cookie_takes_the_host_prefix(client, ddb):
    """Issue #14: __Host- means Secure, path / and no Domain, so no other host on the domain can set or shadow it."""
    _mk(ddb)
    c, = (v for v in _set_cookies(_login(client)) if "steward_session" in v)
    assert c.startswith(f"{HOST_SESSION}=") and "domain" not in c.lower()
    assert {"secure", "httponly", "path=/", "samesite=lax"} <= {p.strip().lower() for p in c.split(";")}
    assert client.get("/api/auth/me").status_code == 200


def test_on_a_plain_http_install_the_session_cookie_keeps_its_name(settings, books, ddb, monkeypatch):
    """A __Host- cookie must come over https, so the demo on http://localhost keeps the plain name."""
    from fastapi.testclient import TestClient
    from steward.app import create_app
    from steward.settings import Settings
    _mk(ddb)
    monkeypatch.setenv("STEWARD_BASE_URL", "http://localhost:8420")
    with TestClient(create_app(Settings(_env_file=None), books), base_url="http://localhost:8420") as c:
        cookie, = (v for v in _set_cookies(_login(c)) if "steward_session" in v)
        # A browser on localhost sends a Secure cookie back over http; httpx doesn't, so it goes in by hand here.
        c.cookies.clear()
        c.cookies.set(SESSION, cookie.split(";")[0].split("=", 1)[1])
        assert c.get("/api/auth/me").status_code == 200      # read back under the plain name (0.1.2 review)
    assert cookie.startswith(f"{SESSION}=")


def test_a_0_1_0_session_cookie_no_longer_signs_in_on_an_https_install(client, ddb):
    """0.1.1 still signed in with 0.1.0's plain cookie name and moved it; from 0.1.2 it signs no one in, so a
    plain-named cookie another host on the domain plants is worth nothing (issue #14 review)."""
    u = _mk(ddb)
    token, _ = sessions.create_session(ddb, u, "old phone", clock.now(), 30)
    client.cookies.set(SESSION, token, domain="nas.tail1234.ts.net")    # as the browser kept it
    r = client.get("/api/auth/me")
    assert r.status_code == 401
    assert not any(v.startswith(f"{HOST_SESSION}={token};") for v in _set_cookies(r))


def test_signing_out_this_device_clears_a_left_over_plain_cookie_too(client, ddb):
    _mk(ddb)
    _login(client)
    client.cookies.set(SESSION, "left-over", domain="nas.tail1234.ts.net")
    mine, = client.get("/api/auth/sessions").json()["sessions"]     # the one sign-in above
    cleared = _set_cookies(client.delete(f"/api/auth/sessions/{mine['id']}"))
    for name in (HOST_SESSION, SESSION):
        assert any(v.startswith(f"{name}=") and "max-age=0" in v.lower() for v in cleared), name
    assert client.get("/api/auth/me").status_code == 401


def test_a_plain_cookie_beside_a_host_one_is_ignored(client, ddb):
    """A plain-named cookie another host on the domain planted can't replace a __Host- session that is already there,
    good or not."""
    u = _mk(ddb)
    planted, _ = sessions.create_session(ddb, u, "planted", clock.now(), 30)
    client.cookies.set(HOST_SESSION, "ended-or-made-up", domain="nas.tail1234.ts.net")
    client.cookies.set(SESSION, planted, domain="nas.tail1234.ts.net")
    r = client.get("/api/auth/me")
    assert r.status_code == 401 and not _set_cookies(r)


def test_signing_out_clears_both_names(client, ddb):
    _mk(ddb)
    _login(client)
    client.cookies.set(SESSION, "left-over")
    gone = _set_cookies(client.post("/api/auth/logout"))
    for name in (HOST_SESSION, SESSION):
        assert any(v.startswith(f"{name}=") and "max-age=0" in v.lower() for v in gone), name
