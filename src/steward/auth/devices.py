"""Issue #7 (gate finding SEC-5): wrong passwords count per device and account, so a stranger's tries lock out only
the stranger. Five wrong in 15 minutes lock password sign-in for that account on that device for 15 minutes
(users.LOCK_AFTER, LOCK_MINUTES). On a device the account doesn't know, the account is the name as typed
(account_key), so a username and its email count apart, as two names nobody has do. A device the account knows counts
by the account. Any lock answers exactly as a wrong password does (api/auth.py), so none shows that a username
exists.

A device is a random id in a cookie the sign-in page sets (cookies.device_name), signed with a key derived from
steward.key, so a made-up id is no device and a restart keeps the ids. Ids cost nothing to get, so only a device that
has signed in to an account is trusted with its own count for it: a known device. A sign-in, by password or passkey,
writes a note for that account into the device's cookie, under the same signature. The note covers the account's
password hash, so a new password (a reset link, a manager's reset, steward reset-password) voids every note written
under the old one. The device that sets a new password, by a password change or a reset link, gets a note for it.
Nothing else writes a note. A signed-in request leaves the cookie alone, so a copied session that
takes a new id is still a stranger's device.

Any other device is charged to its address as well, so a guesser that takes a new id for every few tries still stops
after five from that address. It is also charged to its address's total over every name (ADDRESS_LOCK_AFTER), so one
address can't try enough made-up names to push its own counts out of memory. The account's own lock stays as a
backstop at 50 wrong in a row from devices it doesn't know (users.ACCOUNT_LOCK_AFTER), so a guess spread over many
addresses still stops. A known device is never held by it, so a stranger can't lock the owner's phone out.

The counts live in memory, like the per-address limits (ratelimit.py): one process serves the household, and a
restart forgets them. The store is bounded: a known device's counts are kept apart from every other device's, and a
running lock is never the one a full store drops, so a flood of other tries can't open a lock. An account's key carries a fingerprint of its password hash, so a new password starts every
device's count afresh, whichever process set it. Time is steward.clock's, so tests move the fake clock instead of
sleeping."""
import hashlib
import hmac
import secrets
import threading
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from starlette.requests import Request
from starlette.responses import Response

from steward.auth import cookies
from steward.auth.users import LOCK_AFTER, LOCK_MINUTES
from steward.db.directory_models import User
from steward.db.keys import derive_key

MAX_KEYS = 10_000
MAX_NOTES = 4               # accounts one device stays known to; the oldest note goes first
ADDRESS_LOCK_AFTER = 30     # wrong passwords from one address's unknown devices, whatever the names, in the window
COOKIE_DAYS = 400           # the longest a browser keeps a cookie
WINDOW = timedelta(minutes=LOCK_MINUTES)
KEY_INFO = "steward/device-cookie"


def same(got: str, want: str) -> bool:
    """compare_digest on bytes. A cookie is read as latin-1, and compare_digest raises TypeError on a str that isn't
    ASCII, so a str compare would answer 500. What we sign is ASCII, so a value that isn't can't match."""
    try:
        return hmac.compare_digest(got.encode("ascii"), want.encode("ascii"))
    except UnicodeEncodeError:
        return False


@dataclass(frozen=True)
class Card:
    """A device cookie whose signature checked out: the id and its notes, {user id: note}."""
    did: str
    notes: dict[int, str]


class DeviceKeys:
    """Signs and checks device cookies: "<id>[.<user id>-<note>]...<signature>"."""

    def __init__(self, key: bytes | None = None):
        self._key = key or secrets.token_bytes(32)

    @classmethod
    def from_master(cls, master_hex: str) -> "DeviceKeys":
        """The app's keys, derived from steward.key, so a device keeps its id and notes across a restart."""
        return cls(bytes.fromhex(derive_key(master_hex, KEY_INFO)))

    def _mac(self, text: str, n: int) -> str:
        return hmac.new(self._key, text.encode(), hashlib.sha256).hexdigest()[:n]

    def _value(self, did: str, notes: dict[int, str]) -> str:
        body = ".".join([did, *(f"{uid}-{note}" for uid, note in notes.items())])
        return f"{body}.{self._mac(body, 32)}"

    def new(self) -> tuple[str, str]:
        did = secrets.token_urlsafe(16)
        return did, self._value(did, {})

    def read(self, value: str | None) -> Card | None:
        if not value or len(value) > 300:
            return None
        body, _, sig = value.rpartition(".")
        if not body or not same(sig, self._mac(body, 32)):
            return None
        did, *rest = body.split(".")
        notes = {}
        for part in rest:
            uid, _, note = part.partition("-")
            if not uid.isdigit() or not note:
                return None
            notes[int(uid)] = note
        return Card(did, notes) if did else None

    def check(self, value: str | None) -> str | None:
        card = self.read(value)
        return card.did if card else None

    def note(self, did: str, user: User) -> str:
        """What a sign-in to `user` writes for this device. It stops matching once the password changes."""
        return self._mac(f"note:{did}:{user.id}:{user.password_hash}", 16)

    def knows(self, card: Card, user: User) -> bool:
        note = card.notes.get(user.id)
        return note is not None and same(note, self.note(card.did, user))

    def with_note(self, did: str, notes: dict[int, str], user: User) -> str:
        kept = {uid: n for uid, n in notes.items() if uid != user.id}
        kept[user.id] = self.note(did, user)
        return self._value(did, dict(list(kept.items())[-MAX_NOTES:]))


@dataclass
class Device:
    """Whom a sign-in try counts against. `keys` are checked for a lock and charged a failure. `address`, set for a
    device the account doesn't know, is charged toward that address's total too. `did` is the device's id, `cookie`
    the value to set when the request came without a good one, and `known` says the device has signed in to this
    account."""
    keys: tuple[str, ...]
    did: str
    cookie: str | None = None
    known: bool = False
    address: str | None = None


def _card(request: Request) -> Card | None:
    st = request.app.state
    return st.device_keys.read(request.cookies.get(cookies.device_name(st.settings.base_url)))


def identify(request: Request, user: User | None) -> Device:
    """The request's device, as a try for `user` sees it: a device known to that account counts alone; any other
    is charged to its address too, and one without a good cookie gets a new id for the answer to set."""
    st = request.app.state
    card = _card(request)
    if card and user is not None and st.device_keys.knows(card, user):
        return Device((f"device:{card.did}",), card.did, known=True)
    address = request.client.host if request.client else "unknown"
    if card:
        return Device((f"device:{card.did}", f"address:{address}"), card.did, address=address)
    new_id, value = st.device_keys.new()
    return Device((f"address:{address}", f"device:{new_id}"), new_id, cookie=value, address=address)


def _set(request: Request, response: Response, value: str) -> None:
    response.set_cookie(cookies.device_name(request.app.state.settings.base_url), value,
                        max_age=COOKIE_DAYS * 86400, **cookies.FLAGS)


def give_cookie(request: Request, response: Response, device: Device) -> None:
    if device.cookie:
        _set(request, response, device.cookie)


def mark_page(request: Request, response: Response) -> None:
    """The page's answer gives a device without a good cookie its id, so its first try already counts as its own."""
    if not _card(request):
        _set(request, response, request.app.state.device_keys.new()[1])


def signed_in(request: Request, response: Response, user: User, device: Device | None = None) -> None:
    """This device is now known to the account: the answer writes the account's note into its cookie. `device`, when
    the sign-in identified one, keeps the id its try was charged to. Only a sign-in, a password change that
    re-checked the current password, and a reset link's new password call this."""
    keys = request.app.state.device_keys
    card = _card(request)
    did = device.did if device else card.did if card else keys.new()[0]
    notes = card.notes if card and card.did == did else {}
    _set(request, response, keys.with_note(did, notes, user))


def _fingerprint(user: User) -> str:
    return hashlib.sha256(user.password_hash.encode()).hexdigest()[:16]


def account_key(user: User | None, typed: str, device: Device) -> str:
    """Whose password a try was for. On a device the account doesn't know: the name as typed, as users.find_user
    matches it (case and surrounding spaces aside), whether or not it is an account's. Keyed by the account, a
    username and its email would share one count only when they belong to a real account, so four tries at one and
    one at the other would lock the sixth only then, which would show that the two are one account (issue #50, Argon
    round). On a device the account knows: the account, so it has five tries for the account in all; only a device
    that signed in to the account takes that path, so it shows nothing that device doesn't know. A real account's key
    carries its password hash's fingerprint, so a new password starts every device's count afresh, whichever process
    set it. The account's own 50-in-a-row lock counts by the account (users.ACCOUNT_LOCK_AFTER)."""
    if device.known:
        return f"user:{user.id}:{_fingerprint(user)}"
    name = "name:" + typed.strip().lower()
    return name if user is None else f"{name}:{_fingerprint(user)}"


def account_keys(user: User, device: Device) -> tuple[str, ...]:
    """Every key a try at this account counts under on this device: the account's on a device it knows, else its
    username's and its email's."""
    return tuple(dict.fromkeys(account_key(user, typed, device) for typed in (user.username, user.email) if typed))


@dataclass
class _Count:
    stamps: deque = field(default_factory=deque)
    locked_until: datetime | None = None


class DeviceLockouts:
    def __init__(self, max_keys: int = MAX_KEYS):
        self._max = max_keys
        self._lock = threading.Lock()
        self._counts: OrderedDict[tuple[str, str], _Count] = OrderedDict()   # devices the account doesn't know
        self._known: OrderedDict[tuple[str, str], _Count] = OrderedDict()    # devices that signed in to it
        self._addresses: OrderedDict[str, _Count] = OrderedDict()   # wrong tries at every name, per address

    def __len__(self) -> int:
        return len(self._counts) + len(self._known)

    def _store(self, device: Device) -> OrderedDict:
        """A known device's counts live apart, so tries from devices nobody knows can't push them out: the account's
        own lock doesn't hold a known device, so nothing else would. Only a sign-in makes a device known."""
        return self._known if device.known else self._counts

    def locked(self, device: Device, accounts: str | tuple[str, ...], now: datetime) -> datetime | None:
        """When password sign-in opens again on this device for these keys (account_key), or None when it is open.
        A tuple is held when any of its keys is."""
        with self._lock:
            store = self._store(device)
            found = [store.get((k, a)) for a in _many(accounts) for k in device.keys]
            if device.address is not None:
                found.append(self._addresses.get(device.address))
        return max((c.locked_until for c in found if _running(c, now)), default=None)

    def failed(self, device: Device, accounts: str | tuple[str, ...], now: datetime) -> None:
        """One wrong password, charged to each key and, once, to the address's total."""
        with self._lock:
            for a in _many(accounts):
                for k in device.keys:
                    _charge(self._store(device), (k, a), now, LOCK_AFTER, self._max)
            if device.address is not None:
                _charge(self._addresses, device.address, now, ADDRESS_LOCK_AFTER, self._max)

    def cleared(self, device: Device, accounts: str | tuple[str, ...]) -> None:
        """A sign-in clears this device's counts for the account (account_keys: at both its names). Its
        address's total stays, since the right password on one device says nothing about the other names tried from
        there."""
        with self._lock:
            for a in _many(accounts):
                for k in device.keys:
                    self._store(device).pop((k, a), None)


def _many(accounts: str | tuple[str, ...]) -> tuple[str, ...]:
    return (accounts,) if isinstance(accounts, str) else accounts


def _running(c: _Count | None, now: datetime) -> bool:
    return bool(c and c.locked_until and c.locked_until > now)


def _charge(store: OrderedDict, key, now: datetime, after: int, max_keys: int) -> None:
    c = store.pop(key, None) or _Count()
    while c.stamps and c.stamps[0] <= now - WINDOW:
        c.stamps.popleft()
    c.stamps.append(now)
    if len(c.stamps) >= after:
        c.locked_until, c.stamps = now + WINDOW, deque()
    store[key] = c                                  # the most recently seen is last
    while len(store) > max_keys:
        # The longest unseen goes, but never a lock that is running, so a flood of other tries can't open one. When
        # every other entry is a running lock, the newest goes. One address meets its total long before its tries
        # fill the store, and the account's own lock holds a guess spread over many addresses.
        victim = next((k for k, v in store.items() if not _running(v, now)), key)
        del store[victim]
