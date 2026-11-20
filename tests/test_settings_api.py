from sqlalchemy import select

from steward.auth.cookies import HOST_SESSION
from steward.db.models import AccountType, EventLog
from steward.events import log_event
from tests.factories import make_account, make_user


def login(client, db, username="jr"):
    make_user(db, username, f"{username}@x.com", username.upper(), "pw-one-two-3")
    assert client.post("/api/auth/login", json={"username": username, "password": "pw-one-two-3"}).status_code == 200


def test_change_own_password_keeps_this_device_and_signs_out_others(client, db):
    login(client, db)                                   # session A, now in the client's jar
    cookie_a = client.cookies[HOST_SESSION]
    client.post("/api/auth/login", json={"username": "jr", "password": "pw-one-two-3", "device_label": "laptop"})
    # the jar now holds session B (the laptop); the password change is made from B
    r = client.post("/api/auth/password", json={"current_password": "pw-one-two-3", "new_password": "new-pw-four-5678"})
    assert r.status_code == 204
    assert client.get("/api/auth/me").status_code == 200   # B, the device that made the change, stays signed in
    client.cookies.set(HOST_SESSION, cookie_a, domain="nas.tail1234.ts.net")   # in place of B
    assert client.get("/api/auth/me").status_code == 401   # A was signed out
    client.cookies.clear()
    assert client.post("/api/auth/login", json={"username": "jr", "password": "new-pw-four-5678"}).status_code == 200


def test_change_own_password_wrong_current(client, db):
    login(client, db)
    r = client.post("/api/auth/password", json={"current_password": "wrong-wrong-1", "new_password": "new-pw-four-5678"})
    assert r.status_code == 403 and r.json() == {"error": "That isn't your current password."}
    r = client.post("/api/auth/password", json={"current_password": "pw-one-two-3", "new_password": "short"})
    assert r.status_code == 400 and "12" in r.json()["error"]
    client.cookies.clear()
    assert client.post("/api/auth/login", json={"username": "jr", "password": "pw-one-two-3"}).status_code == 200


def test_users_list(client, db):
    login(client, db)
    make_user(db, "spouse", "s@x.com", "Sam", "pw-one-two-3")
    r = client.get("/api/users")
    assert [u["username"] for u in r.json()["users"]] == ["jr", "spouse"]
    assert set(r.json()["users"][0]) == {"id", "username", "display_name", "email", "can_reset"}


def test_setting_type_resolves_new_account_event(client, db):
    login(client, db)
    a = make_account(db, "Acct 1234", type=AccountType.unknown, source="simplefin", institution_name="Big Bank")
    log_event(db, "info", "system", "New account 'Acct 1234' from Big Bank needs its type and owner set in Settings",
              actionable=True)
    db.commit()
    r = client.patch(f"/api/b/ours/accounts/{a.id}", json={"type": "checking", "name": "Everyday checking"})
    assert r.status_code == 200
    db.commit()  # ends the read snapshot opened by a.id above, so the API session's write is visible
    e = db.scalars(select(EventLog)).one()
    assert e.resolved_at is not None


def test_patching_without_type_change_leaves_event(client, db):
    login(client, db)
    a = make_account(db, "Acct 1234", type=AccountType.unknown, source="simplefin", institution_name="Big Bank")
    log_event(db, "info", "system", "New account 'Acct 1234' from Big Bank needs its type and owner set in Settings",
              actionable=True)
    db.commit()
    client.patch(f"/api/b/ours/accounts/{a.id}", json={"owner": "joint"})
    db.commit()  # ends the read snapshot opened by a.id above, so the API session's write is visible
    assert db.scalars(select(EventLog)).one().resolved_at is None


def test_change_password_is_locked_after_five_wrong_tries(client, db):
    login(client, db)
    for _ in range(5):
        r = client.post("/api/auth/password", json={"current_password": "wrong-wrong-1", "new_password": "new-pw-four-5678"})
        assert r.status_code == 403
    r = client.post("/api/auth/password", json={"current_password": "pw-one-two-3", "new_password": "new-pw-four-5678"})
    assert r.status_code == 423 and "Too many tries" in r.json()["error"] and "locked_until" in r.json()
    r = client.post("/api/auth/login", json={"username": "jr", "password": "new-pw-four-5678"})
    assert r.status_code == 401  # the password was not changed, and this device is locked (issue #7: as a wrong one)
