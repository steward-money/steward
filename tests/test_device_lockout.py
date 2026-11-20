"""Issue #7 (gate finding SEC-5): wrong passwords count per device and account, so a stranger's tries lock out only
the stranger. Device ids cost nothing, so a device that hasn't signed in to the account is charged to its address
too, and the account's own lock stays as a backstop at 50 for such devices; a device that has signed in is held by
neither. Every lock answers as a wrong password does, after the same one password hash. Claim S5."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from steward import clock
from steward.app import create_app
from steward.auth import devices
from steward.auth.cookies import HOST_DEVICE, HOST_SESSION
from steward.auth.users import ACCOUNT_LOCK_AFTER, LOCK_AFTER, create_user, set_password
from steward.db.directory_models import User
from steward.settings import Settings

BASE = "https://nas.tail1234.ts.net"
PW = "pw-one-two-3"
WRONG = {"error": "That username or password isn't right."}


def _try(c, password, name="jr"):
    return c.post("/api/auth/login", json={"username": name, "password": password})


def _new_device(app, c) -> None:
    """The client becomes another device: a good device cookie of its own, as the sign-in page gives."""
    c.cookies.clear()
    c.cookies.set(HOST_DEVICE, app.state.device_keys.new()[1], domain="nas.tail1234.ts.net")


def _elsewhere(app, address: str) -> TestClient:
    """A browser at another address."""
    return TestClient(app, base_url=BASE, client=(address, 50000))


def _the_owners_phone(client) -> None:
    """The owner signs in once on `client`, which keeps its device cookie, and signs out again."""
    assert _try(client, PW).status_code == 200
    assert client.post("/api/auth/logout").status_code == 204


def test_someone_elses_wrong_tries_lock_out_only_their_device_and_the_threat_model_says_so(app, client, ddb):
    from tests.public_docs import ROOT, flat
    create_user(ddb, "jr", "jr@x.com", "JR", PW)
    assert _try(client, PW).status_code == 200                  # the owner's phone, which has its device cookie
    client.post("/api/auth/logout")
    with TestClient(app, base_url=BASE) as stranger:
        for _ in range(LOCK_AFTER):
            assert _try(stranger, "guess").json() == WRONG
        assert _try(stranger, PW).json() == WRONG              # locked here, even with the right password
    assert _try(client, PW).status_code == 200                  # never on the owner's phone
    rows = (ROOT / "docs/explanations/threat-model.md").read_text(encoding="utf-8").splitlines()
    s5 = flat(next(r for r in rows if "(../CLAIMS.md#s5)" in r and r.startswith("|")))
    assert "on that device" in s5 and "15 minutes" in s5 and "50" in s5 and "same answer as a wrong password" in s5
    assert "also counts against its address" in s5 and "never lock out a device that has signed in" in s5
    assert "device cookie away" not in s5                      # such a device gets a new id, which counts by address
    assert "only a sign-in, or setting a new password on that device, writes that note" in s5
    assert "copied session" in s5 and "30 wrong passwords" in s5
    assert "last four accounts" in s5 and "signed in before 0.1.1" in s5   # the notes' limits (Argon review)
    assert "counts as signed in again" not in s5               # a signed-in request no longer makes a device known
    assert "a username and its email count apart" in s5 and "five more at its email" in s5   # issue #50
    assert "per device and name, or per device and account once the device has signed in" in s5
    assert "five wrong at its username and email together lock it there" in s5   # a known device


def test_a_guesser_that_drops_the_cookie_is_counted_by_its_address(app, client, ddb):
    create_user(ddb, "jr", "jr@x.com", "JR", PW)
    for _ in range(LOCK_AFTER):
        client.cookies.clear()
        assert _try(client, "guess").json() == WRONG
    client.cookies.clear()
    assert _try(client, PW).json() == WRONG
    with _elsewhere(app, "10.0.0.2") as other:                 # another address isn't held
        assert _try(other, PW).status_code == 200


def test_a_guesser_that_takes_a_new_device_id_every_few_tries_is_counted_by_its_address(app, client, ddb):
    """Issue #7 review: an id costs one page load, so a device the account doesn't know counts against its address
    too. A new id before every four guesses gets nowhere past the fifth guess, while the owner's phone, which signed
    in before from the same address, still signs in."""
    create_user(ddb, "jr", "jr@x.com", "JR", PW)
    _the_owners_phone(client)
    with TestClient(app, base_url=BASE) as guesser:
        for _ in range(3):
            _new_device(app, guesser)                           # what GET /login hands a browser without a cookie
            for _ in range(LOCK_AFTER - 1):
                assert _try(guesser, "guess").json() == WRONG
        _new_device(app, guesser)
        assert _try(guesser, PW).json() == WRONG               # held since the fifth guess
    assert _try(client, PW).status_code == 200


def test_loading_the_sign_in_page_between_guesses_doesnt_start_a_new_count(books, ddb, monkeypatch, tmp_path):
    """The same loop through the real page: GET /login, then four wrong passwords, round after round."""
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<!doctype html><title>Steward</title>", encoding="utf-8")
    monkeypatch.setenv("STEWARD_WEB_DIR", str(web))
    create_user(ddb, "jr", "jr@x.com", "JR", PW)
    app = create_app(Settings(_env_file=None), books)
    with TestClient(app, base_url=BASE) as guesser:
        for _ in range(3):
            guesser.cookies.clear()
            assert HOST_DEVICE in guesser.get("/login").cookies
            for _ in range(LOCK_AFTER - 1):
                assert _try(guesser, "guess").json() == WRONG
        guesser.cookies.clear()
        guesser.get("/login")
        assert _try(guesser, PW).json() == WRONG
    with _elsewhere(app, "10.0.0.2") as other:
        assert _try(other, PW).status_code == 200


def test_fifty_wrong_from_many_addresses_lock_the_account_for_new_devices_only(app, client, ddb, fake_clock):
    create_user(ddb, "jr", "jr@x.com", "JR", PW)
    _the_owners_phone(client)
    for i in range(ACCOUNT_LOCK_AFTER):
        with _elsewhere(app, f"10.0.1.{i // (LOCK_AFTER - 1)}") as stranger:
            assert _try(stranger, "guess").json() == WRONG
    with _elsewhere(app, "10.0.2.1") as fresh:
        assert _try(fresh, PW).json() == WRONG                 # a device the account doesn't know: the backstop
        assert _try(client, PW).status_code == 200              # the owner's phone, which signed in before, isn't held
        fake_clock.set(fake_clock.now() + timedelta(minutes=16))
        assert _try(fresh, PW).status_code == 200


def test_a_known_devices_wrong_passwords_dont_count_toward_the_account_lock(app, client, ddb):
    u = create_user(ddb, "jr", "jr@x.com", "JR", PW)
    _the_owners_phone(client)
    for _ in range(LOCK_AFTER - 1):
        assert _try(client, "typo").json() == WRONG
    ddb.refresh(u)
    assert u.failed_logins == 0
    ddb.commit()                                                # end the read, to see the next write
    with _elsewhere(app, "10.0.0.2") as other:
        _try(other, "guess")
    ddb.refresh(u)
    assert u.failed_logins == 1


def test_a_known_device_still_has_its_own_five(client, ddb):
    create_user(ddb, "jr", "jr@x.com", "JR", PW)
    _the_owners_phone(client)
    for _ in range(LOCK_AFTER):
        assert _try(client, "guess").json() == WRONG
    assert _try(client, PW).json() == WRONG


def test_a_device_that_signed_in_stays_known_after_a_restart(books, client, ddb):
    """The device cookie's key comes from steward.key and the note rides in the cookie, so a restart, which forgets
    every count, doesn't make the owner's phone a stranger: the backstop still doesn't hold it."""
    create_user(ddb, "jr", "jr@x.com", "JR", PW)
    _the_owners_phone(client)
    restarted = create_app(Settings(_env_file=None), books)
    for i in range(ACCOUNT_LOCK_AFTER):
        with _elsewhere(restarted, f"10.0.1.{i // (LOCK_AFTER - 1)}") as stranger:
            _try(stranger, "guess")
    with _elsewhere(restarted, "10.0.2.1") as fresh:
        assert _try(fresh, PW).json() == WRONG
    with TestClient(restarted, base_url=BASE, cookies=client.cookies) as phone:
        assert _try(phone, PW).status_code == 200


def test_a_passkey_sign_in_starts_the_account_count_again_and_makes_the_device_known(app, client, ddb, monkeypatch):
    """A member who signs in only with a passkey: stray wrong passwords don't pile up toward the account lock."""
    from steward.auth import passkeys as pk
    from steward.db.directory_models import Passkey
    u = create_user(ddb, "jr", "jr@x.com", "JR", PW)
    ddb.add(Passkey(user_id=u.id, credential_id=b"cred-1", public_key=b"key", sign_count=0, label="phone"))
    u.failed_logins = ACCOUNT_LOCK_AFTER - 1
    ddb.commit()
    monkeypatch.setattr(pk, "verify_authentication", lambda rp, credential, challenge, passkey: 1)
    raw_id = pk.bytes_to_base64url(b"cred-1")
    cid = client.post("/api/auth/passkeys/login/options").json()["challenge_id"]
    r = client.post("/api/auth/passkeys/login/verify",
                    json={"challenge_id": cid, "credential": {"id": raw_id, "rawId": raw_id}})
    assert r.status_code == 200
    ddb.refresh(u)
    assert u.failed_logins == 0
    card = app.state.device_keys.read(client.cookies.get(HOST_DEVICE))
    assert card and app.state.device_keys.knows(card, u)


def test_a_lock_and_a_wrong_password_take_one_password_hash_each(app, client, ddb, monkeypatch):
    """The same timing path: every 401 costs exactly one hash check, whether the name exists, is locked on this
    device, or is locked everywhere."""
    from steward.auth import passwords
    calls = []
    real = passwords.verify_password

    def counted(h, p):
        calls.append(1)
        return real(h, p)
    monkeypatch.setattr("steward.api.auth.verify_password", counted)
    monkeypatch.setattr("steward.auth.users.verify_password", counted)
    u = create_user(ddb, "jr", "jr@x.com", "JR", PW)

    def hashes(name="jr", password="guess") -> int:
        calls.clear()
        assert _try(client, password, name).json() == WRONG
        return len(calls)
    assert hashes() == 1                                        # a wrong password
    assert hashes("nobody-here") == 1                           # a name nobody has
    for _ in range(LOCK_AFTER - 1):
        hashes()
    assert hashes(password=PW) == 1                             # locked on this device
    u.locked_until = clock.now() + timedelta(minutes=10)
    ddb.commit()
    client = _elsewhere(app, "10.0.0.2")
    assert hashes(password=PW) == 1                             # the account locked for devices it doesn't know


def test_a_new_password_opens_every_device_again(client, ddb):
    """A reset link, a manager's reset or steward reset-password, in this process or another."""
    u = create_user(ddb, "jr", "jr@x.com", "JR", PW)
    for typed in ("jr", "jr@x.com"):                            # the username's count and the email's
        for _ in range(LOCK_AFTER):
            _try(client, "guess", typed)
        assert _try(client, PW, typed).json() == WRONG
    set_password(ddb, u, "pw-four-five-6")
    assert _try(client, "pw-four-five-6", "jr@x.com").status_code == 200
    assert client.post("/api/auth/logout").status_code == 204
    client.cookies.clear()                                      # a new device: the address's count at the username
    assert _try(client, "pw-four-five-6").status_code == 200


def _reaches_the_password_check(monkeypatch):
    """Counts the tries that get past the device and address locks to users.check_login: a lock answers before it."""
    from steward.auth import users
    real, calls = users.check_login, []

    def counted(*args, **kwargs):
        calls.append(args[1])
        return real(*args, **kwargs)
    monkeypatch.setattr(users, "check_login", counted)
    return calls


@pytest.mark.parametrize("exists", [True, False])
def test_a_username_and_its_email_count_apart_whether_or_not_the_account_exists(app, ddb, monkeypatch, exists):
    """Argon round (issue #50): keyed by the account, four wrong at the email and one at the username locked the
    sixth only for a real account, which showed that the two were one account. The counts go by the name as typed
    (as find_user matches it, case and spaces aside), so neither locks here, real or not."""
    if exists:
        create_user(ddb, "alice", "alice@example.com", "Alice", PW)
    reached = _reaches_the_password_check(monkeypatch)
    with _elsewhere(app, "10.0.8.1") as guesser:
        for typed in ["alice@example.com", " Alice@Example.com ", "ALICE@example.com", "alice@example.com", "alice"]:
            assert _try(guesser, "guess", typed).json() == WRONG
        sixth = _try(guesser, PW if exists else "guess", "alice@example.com")
    assert len(reached) == 6                                    # the sixth was checked, not answered by a lock
    assert sixth.status_code == (200 if exists else 401)


@pytest.mark.parametrize("exists", [True, False])
@pytest.mark.parametrize("typed", ["alice", "alice@example.com"])
def test_five_wrong_at_one_name_lock_that_name_on_this_device_whether_or_not_it_exists(app, ddb, monkeypatch,
                                                                                         exists, typed):
    if exists:
        create_user(ddb, "alice", "alice@example.com", "Alice", PW)
    reached = _reaches_the_password_check(monkeypatch)
    with _elsewhere(app, "10.0.8.2") as guesser:
        for _ in range(LOCK_AFTER):
            assert _try(guesser, "guess", f" {typed.upper()} ").json() == WRONG
        assert _try(guesser, PW, typed).json() == WRONG         # locked here, even with the right password
    assert len(reached) == LOCK_AFTER


def test_signing_in_by_username_clears_the_emails_count_too(app, ddb):
    """A sign-in clears the device's and the address's counts at both of the account's names."""
    create_user(ddb, "jr", "jr@x.com", "JR", PW)
    with _elsewhere(app, "10.0.8.3") as c:
        for _ in range(LOCK_AFTER - 1):
            c.cookies.clear()                                   # a new device each try: the address counts
            assert _try(c, "guess", "jr@x.com").json() == WRONG
        c.cookies.clear()
        assert _try(c, PW, "jr").status_code == 200
        for _ in range(LOCK_AFTER - 1):
            c.cookies.clear()
            assert _try(c, "guess", "jr@x.com").json() == WRONG
        c.cookies.clear()
        assert _try(c, PW, "jr@x.com").status_code == 200       # eight wrong at the email, but four since the sign-in


def test_wrong_current_passwords_count_at_the_username_and_the_email(app, client, ddb):
    """A password check has no typed name, so it is held by, and charged to, both of the account's names: a copied
    session can't take five at the check and five more at sign-in by email."""
    create_user(ddb, "jr", "jr@x.com", "JR", PW)
    assert _try(client, PW).status_code == 200
    with _copied_session(app, client, "10.0.8.4") as thief:
        _load_a_page(app, thief)
        for _ in range(LOCK_AFTER):
            assert thief.post("/api/auth/password", json=CHANGE).status_code == 403
        for typed in ("jr", "jr@x.com"):
            assert _try(thief, PW, typed).json() == WRONG       # this device, held at both names


def test_five_wrong_sign_ins_at_the_email_hold_the_password_check(app, client, ddb):
    """The other way round: the check is held by the email's count as well as the username's."""
    create_user(ddb, "jr", "jr@x.com", "JR", PW)
    assert _try(client, PW).status_code == 200
    with _copied_session(app, client, "10.0.8.5") as thief:
        _load_a_page(app, thief)
        for _ in range(LOCK_AFTER):
            assert _try(thief, "guess", "jr@x.com").json() == WRONG
        assert thief.post("/api/auth/password", json=CHANGE).status_code == 423


def test_a_right_current_password_clears_the_emails_count_too(app, client, ddb):
    """Checked by a caller that leaves the password as it is (a new password would start every count afresh anyway)."""
    create_user(ddb, "jr", "jr@x.com", "JR", PW)
    assert _try(client, PW).status_code == 200
    with _copied_session(app, client, "10.0.8.6") as thief:
        _load_a_page(app, thief)
        for _ in range(LOCK_AFTER - 1):
            assert _try(thief, "guess", "jr@x.com").json() == WRONG
        r = thief.post("/api/auth/passkeys/register/options", json={"current_password": PW})
        assert r.status_code == 200
        assert _try(thief, "guess", "jr@x.com").json() == WRONG
        assert _try(thief, PW, "jr@x.com").status_code == 200   # five wrong at the email, but one since the check


def test_a_password_check_charges_the_address_total_once_for_both_names(app, ddb):
    """A wrong current password counts under the username and the email, but is one wrong try for the address's
    total: fifteen from three copied sessions, each held at five by its own names, leave the address open."""
    address = "10.0.8.7"
    for name in ("jr", "vee", "sam"):
        create_user(ddb, name, f"{name}@x.com", name.title(), PW)
        with _elsewhere(app, "10.0.9.1") as phone:
            assert _try(phone, PW, name).status_code == 200
            with _copied_session(app, phone, address) as thief:
                for _ in range(LOCK_AFTER):
                    _load_a_page(app, thief)                    # a new device each try
                    assert thief.post("/api/auth/password", json=CHANGE).status_code == 403
    assert len(app.state.device_lockouts._addresses[address].stamps) == 3 * LOCK_AFTER
    create_user(ddb, "kit", "kit@x.com", "Kit", PW)
    with _elsewhere(app, address) as other:
        assert _try(other, PW, "kit").status_code == 200        # 15 of the address's 30


def test_a_known_device_has_five_for_the_account_at_both_names(client, ddb):
    """A device that signed in to the account already knows that its username and email go together, so it counts by
    the account: five in all, not five at each name."""
    create_user(ddb, "jr", "jr@x.com", "JR", PW)
    _the_owners_phone(client)
    for typed in ["jr"] * 3 + ["jr@x.com"] * 2:
        assert _try(client, "guess", typed).json() == WRONG
    assert _try(client, PW, "jr").json() == WRONG
    assert _try(client, PW, "jr@x.com").json() == WRONG


def test_signing_in_clears_this_devices_count(client, ddb):
    create_user(ddb, "jr", "jr@x.com", "JR", PW)
    for _ in range(LOCK_AFTER - 1):
        _try(client, "guess")
    assert _try(client, PW).status_code == 200
    client.post("/api/auth/logout")
    for _ in range(LOCK_AFTER - 1):
        _try(client, "guess")
    assert _try(client, PW).status_code == 200


def test_the_sign_in_page_and_the_sign_in_answer_give_a_device_its_cookie(books, settings, monkeypatch, tmp_path):
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<!doctype html><title>Steward</title>", encoding="utf-8")
    monkeypatch.setenv("STEWARD_WEB_DIR", str(web))
    with TestClient(create_app(Settings(_env_file=None), books), base_url=BASE) as c:
        page = c.get("/login")
        cookie, = (v for k, v in page.headers.multi_items() if k == "set-cookie")
        assert cookie.startswith(f"{HOST_DEVICE}=") and "domain" not in cookie.lower()
        assert {"secure", "httponly", "path=/", "samesite=lax"} <= {p.strip().lower() for p in cookie.split(";")}
        assert "set-cookie" not in c.get("/login").headers           # it has one now
        c.cookies.clear()
        r = _try(c, "guess")
        assert any(v.startswith(f"{HOST_DEVICE}=") for k, v in r.headers.multi_items() if k == "set-cookie")


def test_a_made_up_or_tampered_device_id_is_no_device():
    keys = devices.DeviceKeys()
    did, value = keys.new()
    assert keys.check(value) == did
    for bad in (None, "", did, f"{did}.", f"{did}.{'0' * 32}", f"x{value}", value + "0", "a." * 60):
        assert keys.check(bad) is None, bad
    noted = keys.with_note(did, {}, User(id=7, password_hash="h"))
    forged = noted.replace(".7-", ".8-")                        # the note moved to another account
    assert keys.read(noted).notes.keys() == {7} and keys.read(forged) is None
    assert devices.DeviceKeys().check(value) is None             # another install's key


def test_the_device_key_comes_from_steward_key_so_a_restart_keeps_it():
    master = "ab" * 32
    did, value = devices.DeviceKeys.from_master(master).new()
    assert devices.DeviceKeys.from_master(master).check(value) == did
    assert devices.DeviceKeys.from_master("cd" * 32).check(value) is None


def test_a_device_keeps_notes_for_its_last_four_accounts():
    keys = devices.DeviceKeys()
    did, value = keys.new()
    people = [User(id=i, password_hash=f"h{i}") for i in range(1, 6)]
    for u in people:
        value = keys.with_note(did, keys.read(value).notes, u)
    card = keys.read(value)
    assert [keys.knows(card, u) for u in people] == [False, True, True, True, True]
    assert not keys.knows(card, User(id=5, password_hash="a new one"))   # a new password voids the note
    did, value = keys.new()                                     # the last four to sign in, not the first four
    for u in people[:4] + [people[0], people[4]]:
        value = keys.with_note(did, keys.read(value).notes, u)
    card = keys.read(value)
    assert [keys.knows(card, u) for u in people] == [True, False, True, True, True]


def test_the_counts_stay_bounded(fake_clock):
    lockouts = devices.DeviceLockouts(max_keys=10)
    t = fake_clock.now()
    for i in range(50):
        lockouts.failed(devices.Device((f"device:{i}",), str(i)), "name:jr", t)
    assert len(lockouts) == 10


@pytest.mark.parametrize("path", ["/api/auth/password", "/api/auth/passkeys/register/options"])
def test_wrong_current_passwords_lock_this_device_too(signed_in, path):
    body = {"current_password": "wrong-wrong-1", "new_password": "new-pw-four-5678"}
    for _ in range(LOCK_AFTER):
        assert signed_in.post(path, json=body).status_code == 403
    r = signed_in.post(path, json={**body, "current_password": PW})
    assert r.status_code == 423 and r.json()["error"].startswith("Too many tries.")   # signed in: it may say so


def _copied_session(app, client, address: str) -> TestClient:
    """Someone at `address` with a copy of the session cookie on `client`, but not of its device cookie."""
    c = _elsewhere(app, address)
    c.cookies.set(HOST_SESSION, client.cookies.get(HOST_SESSION), domain="nas.tail1234.ts.net")
    return c


def _load_a_page(app, c) -> None:
    """What any page load hands a browser without a good device cookie, then a signed-in request."""
    c.cookies.delete(HOST_DEVICE)
    c.cookies.set(HOST_DEVICE, app.state.device_keys.new()[1], domain="nas.tail1234.ts.net")
    assert c.get("/api/auth/me").status_code == 200


CHANGE = {"current_password": "guess-guess-1", "new_password": "new-pw-four-5678"}


def test_a_copied_session_that_takes_new_device_ids_is_held_at_the_password_check(app, client, ddb):
    """Issue #7 review, round 2: a signed-in request used to make whatever device cookie it carried known, so a
    copied session could take a new id before every four wrong current passwords and never stop. Only a sign-in
    makes a device known now: the new ids count against the address, and the right password is refused while held."""
    u = create_user(ddb, "jr", "jr@x.com", "JR", PW)
    assert _try(client, PW).status_code == 200
    before = u.password_hash
    codes = []
    with _copied_session(app, client, "10.0.3.1") as thief:
        for _ in range(5):
            _load_a_page(app, thief)
            codes += [thief.post("/api/auth/password", json=CHANGE).status_code for _ in range(LOCK_AFTER - 1)]
        assert codes == [403] * LOCK_AFTER + [423] * (len(codes) - LOCK_AFTER)
        _load_a_page(app, thief)
        assert thief.post("/api/auth/password", json={**CHANGE, "current_password": PW}).status_code == 423
    ddb.refresh(u)
    assert u.password_hash == before


def test_a_copied_session_spread_over_many_addresses_meets_the_account_lock(app, client, ddb):
    u = create_user(ddb, "jr", "jr@x.com", "JR", PW)
    assert _try(client, PW).status_code == 200
    before = u.password_hash
    codes = []
    for i in range(ACCOUNT_LOCK_AFTER // (LOCK_AFTER - 1) + 1):
        with _copied_session(app, client, f"10.0.4.{i}") as thief:
            _load_a_page(app, thief)
            codes += [thief.post("/api/auth/password", json=CHANGE).status_code for _ in range(LOCK_AFTER - 1)]
    assert codes.index(423) == ACCOUNT_LOCK_AFTER and set(codes[ACCOUNT_LOCK_AFTER:]) == {423}
    with _copied_session(app, client, "10.0.5.1") as thief:
        _load_a_page(app, thief)
        assert thief.post("/api/auth/password", json={**CHANGE, "current_password": PW}).status_code == 423
    ddb.refresh(u)
    assert u.password_hash == before
    ddb.commit()
    assert client.post("/api/auth/password", json={**CHANGE, "current_password": PW}).status_code == 204  # the phone


def test_a_copied_session_that_takes_new_device_ids_is_held_at_sign_in(app, client, ddb):
    create_user(ddb, "jr", "jr@x.com", "JR", PW)
    assert _try(client, PW).status_code == 200
    with _copied_session(app, client, "10.0.3.1") as thief:
        for _ in range(5):
            _load_a_page(app, thief)
            for _ in range(LOCK_AFTER - 1):
                assert _try(thief, "guess").json() == WRONG
        _load_a_page(app, thief)
        assert _try(thief, PW).json() == WRONG
    for i in range(ACCOUNT_LOCK_AFTER // (LOCK_AFTER - 1) + 1):
        with _copied_session(app, client, f"10.0.4.{i}") as thief:
            _load_a_page(app, thief)
            for _ in range(LOCK_AFTER - 1):
                assert _try(thief, "guess").json() == WRONG
    with _copied_session(app, client, "10.0.5.1") as thief:
        _load_a_page(app, thief)
        assert _try(thief, PW).json() == WRONG                 # the account's lock
    assert _try(client, PW).status_code == 200                  # not on the phone that signed in


def test_a_signed_in_request_leaves_the_device_cookie_alone(app, client, ddb):
    u = create_user(ddb, "jr", "jr@x.com", "JR", PW)
    assert _try(client, PW).status_code == 200
    with _copied_session(app, client, "10.0.3.1") as thief:
        _load_a_page(app, thief)
        r = thief.get("/api/auth/me")
        assert r.status_code == 200 and "set-cookie" not in r.headers
        assert not app.state.device_keys.read(thief.cookies.get(HOST_DEVICE)).notes
    assert app.state.device_keys.knows(app.state.device_keys.read(client.cookies.get(HOST_DEVICE)), u)


def test_a_new_password_makes_the_devices_that_signed_in_strangers_again(app, client, ddb):
    """A reset link, a manager's reset or steward reset-password voids the notes: a device that knew the old password
    counts against its address again until it signs in with the new one."""
    u = create_user(ddb, "jr", "jr@x.com", "JR", PW)
    _the_owners_phone(client)
    set_password(ddb, u, "pw-four-five-6")
    with TestClient(app, base_url=BASE) as stranger:           # the phone's address
        for _ in range(LOCK_AFTER):
            _try(stranger, "guess")
    assert _try(client, "pw-four-five-6").json() == WRONG


def test_changing_your_password_keeps_this_device_known(app, client, ddb):
    create_user(ddb, "jr", "jr@x.com", "JR", PW)
    assert _try(client, PW).status_code == 200
    assert client.post("/api/auth/password", json={**CHANGE, "current_password": PW}).status_code == 204
    client.post("/api/auth/logout")
    with TestClient(app, base_url=BASE) as stranger:           # the phone's address
        for _ in range(LOCK_AFTER):
            _try(stranger, "guess")
    assert _try(client, CHANGE["new_password"]).status_code == 200


def test_the_device_that_uses_a_reset_link_knows_the_new_password(app, client, ddb):
    """Argon review: a reset link voids the phone's note, so if 30 wrong tries from its address had held that
    address, the member who had just proved their email still couldn't sign in there for 15 minutes. The device that
    uses the link chose the new password, so it gets a note for it, as a password change does."""
    import re

    from steward.auth import reset
    create_user(ddb, "jr", "jr@x.com", "JR", PW)
    _the_owners_phone(client)
    with TestClient(app, base_url=BASE) as guesser:            # the phone's address
        for i in range(devices.ADDRESS_LOCK_AFTER):
            guesser.cookies.clear()
            _try(guesser, "guess", f"made-up-{i}")
    reset.request_reset(ddb, app.state.mailer, BASE, "jr", clock.now())
    token = re.search(r"token=(\S+)", app.state.mailer.sent[0][2]).group(1)
    weak = client.post("/api/auth/reset", json={"token": token, "password": "short"})
    assert weak.status_code == 400 and "set-cookie" not in weak.headers     # no note for a reset that didn't happen
    assert client.post("/api/auth/reset", json={"token": token, "password": "pw-four-five-6"}).status_code == 200
    assert _try(client, "pw-four-five-6").status_code == 200
    with TestClient(app, base_url=BASE) as other:              # any other device there is still held
        assert _try(other, "pw-four-five-6").json() == WRONG


def test_thirty_wrong_from_one_address_at_any_names_hold_its_new_devices(app, client, ddb):
    create_user(ddb, "jr", "jr@x.com", "JR", PW)
    _the_owners_phone(client)
    with TestClient(app, base_url=BASE) as guesser:            # the phone's address
        for i in range(devices.ADDRESS_LOCK_AFTER):
            guesser.cookies.clear()
            assert _try(guesser, "guess", f"made-up-{i}").json() == WRONG
        guesser.cookies.clear()
        assert _try(guesser, PW).json() == WRONG
    assert _try(client, PW).status_code == 200                  # the phone that signed in isn't held
    with _elsewhere(app, "10.0.0.2") as other:
        assert _try(other, PW).status_code == 200


def test_trying_other_names_doesnt_push_the_address_count_out(app, ddb):
    """Issue #7 review, round 2: every try at a made-up name from a new id added two counts, so enough of them pushed
    the address's count for the real account out of memory. One address now meets its total first."""
    create_user(ddb, "jr", "jr@x.com", "JR", PW)
    app.state.device_lockouts = devices.DeviceLockouts(max_keys=40)
    with TestClient(app, base_url=BASE) as guesser:
        for name in ["jr"] * (LOCK_AFTER - 1) + [f"made-up-{i}" for i in range(200)] + ["jr"] * (LOCK_AFTER - 1):
            guesser.cookies.clear()
            assert _try(guesser, "guess", name).json() == WRONG
        guesser.cookies.clear()
        assert _try(guesser, PW).json() == WRONG


@pytest.mark.parametrize("name", ["jr", "nobody-here"])
def test_tries_at_a_locked_account_count_against_the_address_as_tries_at_a_made_up_name_do(app, ddb, name):
    """Argon review: a try at an account under its own lock wasn't charged to the device or the address, while a try
    at a name nobody has was. 30 such tries then held the address only when the name didn't exist, which showed
    whether it did. Both are charged now, so both hold the address. (Each name's own count at the address stops
    charging at five, so the probe spreads the rest over made-up names.)"""
    u = create_user(ddb, "jr", "jr@x.com", "JR", PW)
    create_user(ddb, "vee", "vee@x.com", "Vee", "pw-vee-vee-77")
    u.locked_until = clock.now() + timedelta(minutes=15)
    ddb.commit()
    others = [f"made-up-{i // LOCK_AFTER}" for i in range(devices.ADDRESS_LOCK_AFTER - LOCK_AFTER)]
    with _elsewhere(app, "10.0.6.1") as probe:
        for typed in [name] * LOCK_AFTER + others:
            probe.cookies.clear()
            assert _try(probe, "guess", typed).json() == WRONG
        probe.cookies.clear()
        assert _try(probe, "pw-vee-vee-77", "vee").json() == WRONG     # the address is held, at every name
    with _elsewhere(app, "10.0.6.2") as other:
        assert _try(other, "pw-vee-vee-77", "vee").status_code == 200


def test_a_device_trying_a_locked_account_meets_its_own_five(app, ddb, fake_clock):
    """The device count too: five tries while the account is locked hold this device after the account's lock ends."""
    u = create_user(ddb, "jr", "jr@x.com", "JR", PW)
    u.locked_until = clock.now() + timedelta(minutes=1)
    ddb.commit()
    with _elsewhere(app, "10.0.6.3") as guesser:
        _new_device(app, guesser)
        for _ in range(LOCK_AFTER):
            assert _try(guesser, "guess").json() == WRONG
        fake_clock.set(fake_clock.now() + timedelta(minutes=2))
        assert _try(guesser, PW).json() == WRONG


def _flood(app, n: int, prefix: str = "10.0.7") -> None:
    """n wrong tries at made-up names from new devices, spread over addresses so none meets its own limits."""
    for i in range(n):
        with _elsewhere(app, f"{prefix}.{i // (LOCK_AFTER - 1)}") as c:
            _try(c, "guess", f"made-up-{i}")


def test_a_flood_of_other_tries_cant_push_a_known_devices_lock_out(app, client, ddb):
    """Argon review: the counts are a bounded store that drops the longest unseen, and the account's own lock doesn't
    hold a device that signed in. Tries from enough other addresses pushed out a known device's lock, which gave
    whoever holds that device five more guesses, again and again. A known device's counts now live apart from other
    devices' and a lock that is running is never the one dropped."""
    create_user(ddb, "jr", "jr@x.com", "JR", PW)
    app.state.device_lockouts = devices.DeviceLockouts(max_keys=40)
    _the_owners_phone(client)
    for _ in range(LOCK_AFTER - 1):
        assert _try(client, "guess").json() == WRONG
    _flood(app, 100)
    assert _try(client, "guess").json() == WRONG                # the fifth: the count wasn't pushed out
    _flood(app, 100, "10.0.8")
    assert _try(client, PW).json() == WRONG                     # nor the lock


def test_a_flood_of_other_tries_cant_push_a_running_lock_out(fake_clock):
    lockouts = devices.DeviceLockouts(max_keys=10)
    t = fake_clock.now()
    held = devices.Device(("address:10.0.0.9", "device:held"), "held", address="10.0.0.9")
    for _ in range(LOCK_AFTER):
        lockouts.failed(held, "name:jr", t)
    for i in range(50):
        lockouts.failed(devices.Device((f"device:{i}",), str(i), address=f"10.1.0.{i}"), "name:jr", t)
    assert lockouts.locked(held, "name:jr", t)
    assert len(lockouts) <= 10


OUTSIDE_ASCII = [b"x.\xe9", b"x.\x80" + b"0" * 31, b"\xe9.\xe9", b"x.7-\xe9.\xe9"]


def test_a_cookie_with_a_byte_outside_ascii_is_no_device_unit():
    """Argon review: compare_digest raises TypeError on a str that isn't ASCII. Starlette reads a cookie as latin-1,
    so one byte from 0x80 up in the signature was a 500 on every route that reads the device cookie."""
    keys = devices.DeviceKeys()
    for value in OUTSIDE_ASCII:
        assert keys.read(value.decode("latin-1")) is None
    assert not keys.knows(devices.Card("x", {7: "\xe9" * 16}), User(id=7, password_hash="h"))


def test_every_compare_digest_in_steward_compares_bytes():
    """The class behind the finding above: every compare_digest call in src/steward encodes what it compares."""
    from tests.public_docs import ROOT
    calls = [(f, n, line) for f in sorted((ROOT / "src/steward").rglob("*.py"))
             for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1) if "compare_digest(" in line]
    assert len(calls) >= 4
    for f, n, line in calls:
        assert ".encode(" in line, f"{f.relative_to(ROOT)}:{n} compares str; encode both sides (devices.same)"


@pytest.mark.parametrize("value", OUTSIDE_ASCII)
def test_a_device_cookie_with_a_byte_outside_ascii_is_no_device(app, ddb, value):
    create_user(ddb, "jr", "jr@x.com", "JR", PW)
    with TestClient(app, base_url=BASE) as c:
        bad = {"cookie": HOST_DEVICE.encode() + b"=" + value}
        assert c.post("/api/auth/login", json={"username": "jr", "password": "guess"}, headers=bad).json() == WRONG
        r = c.post("/api/auth/login", json={"username": "jr", "password": PW}, headers=bad)
        assert r.status_code == 200 and app.state.device_keys.read(r.cookies.get(HOST_DEVICE))   # a good one now
        token = r.cookies.get(HOST_SESSION)
        both = {"cookie": f"{HOST_SESSION}={token}; ".encode() + bad["cookie"]}
        c.cookies.clear()
        assert c.post("/api/auth/password", json=CHANGE, headers=both).status_code == 403


@pytest.mark.parametrize("value", OUTSIDE_ASCII)
def test_a_session_cookie_with_a_byte_outside_ascii_is_signed_out(client, value):
    assert client.get("/api/auth/me", headers={"cookie": HOST_SESSION.encode() + b"=" + value}).status_code == 401
