from datetime import datetime, timedelta

from sqlalchemy import Integer, String, cast, or_, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from steward.auth.passwords import hash_password, verify_password
from steward.db.directory_models import DirectoryMeta, User

LOCK_AFTER = 5             # wrong passwords from one device for one account, inside LOCK_MINUTES (auth/devices.py)
LOCK_MINUTES = 15
ACCOUNT_LOCK_AFTER = 50    # wrong passwords in a row for one account from devices it doesn't know: the backstop (#7)
MIN_PASSWORD_LEN = 12
_DUMMY_HASH = hash_password("dummy-password-not-used")
MISSES = "sign_in_misses"   # directory_meta: the row miss() changes


class UserExists(ValueError):
    pass


class BadCredentials(PermissionError):
    pass


class Locked(PermissionError):
    def __init__(self, until: datetime):
        super().__init__(f"Locked until {until.isoformat()}")
        self.until = until


class WeakPassword(ValueError):
    pass


def _check_strength(password: str) -> None:
    if len(password) < MIN_PASSWORD_LEN:
        raise WeakPassword(f"Use at least {MIN_PASSWORD_LEN} characters.")


def find_user(db: Session, username_or_email: str) -> User | None:
    key = username_or_email.strip().lower()
    return db.scalar(select(User).where(or_(User.username == key, User.email == key)))


def create_user(db: Session, username: str, email: str, display_name: str, password: str) -> User:
    _check_strength(password)
    username, email = username.strip().lower(), email.strip().lower()
    if find_user(db, username) or find_user(db, email):
        raise UserExists(username)
    u = User(username=username, email=email, display_name=display_name,
             password_hash=hash_password(password))
    db.add(u)
    db.commit()
    return u


def set_password(db: Session, user: User, password: str) -> None:
    _check_strength(password)
    user.password_hash = hash_password(password)
    user.failed_logins = 0
    user.locked_until = None
    db.commit()


def check_login(db: Session, username_or_email: str, password: str, now: datetime, backstop: bool = True) -> User:
    """`backstop` False is a device the account knows (auth/devices.py): the account's lock neither holds it nor
    counts its wrong passwords."""
    u = find_user(db, username_or_email)
    if u is None:
        verify_password(_DUMMY_HASH, password)  # keeps timing close to a known user
        miss(db)
        raise BadCredentials()
    try:
        _check_password(db, u, password, now, backstop)
    except Locked:
        verify_password(_DUMMY_HASH, password)  # before the write, inside the write lock, as a wrong password hashes
        miss(db)
        raise
    return u


def miss(db: Session) -> None:
    """Issue #50: the database work of a wrong password at a real name, for a name nobody has and an account its own
    lock holds. A wrong password hashes, counts and commits, which writes a page, so these hash first (the caller)
    and then change one row of directory_meta and commit: the same hash, reads and writes in the same order, inside
    the same write lock, and a guess can't tell by the commit's write or by how long the lock is held whether a name
    exists. The row must change: SQLite writes nothing for an UPDATE that leaves a row as it was."""
    db.execute(insert(DirectoryMeta).values(key=MISSES, value="1").on_conflict_do_update(
        index_elements=[DirectoryMeta.key],
        set_={"value": cast(cast(DirectoryMeta.value, Integer) + 1, String)}))
    db.scalar(select(DirectoryMeta.value).where(DirectoryMeta.key == MISSES))   # as the count's refresh reads
    db.commit()


def check_password(db: Session, user: User, password: str, now: datetime, backstop: bool = True) -> None:
    """Re-check a signed-in user's password; counts failures and locks like a sign-in."""
    _check_password(db, user, password, now, backstop)


def signed_in(u: User) -> None:
    """Any sign-in, by password or passkey, starts the account's count again (the caller commits)."""
    u.failed_logins = 0
    u.locked_until = None


def _check_password(db: Session, u: User, password: str, now: datetime, backstop: bool) -> None:
    if backstop and u.locked_until and u.locked_until > now:
        raise Locked(u.locked_until)
    if not verify_password(u.password_hash, password):
        if not backstop:
            raise BadCredentials()
        db.execute(update(User).where(User.id == u.id)
                   .values(failed_logins=User.failed_logins + 1))
        db.refresh(u)
        if u.failed_logins >= ACCOUNT_LOCK_AFTER:
            # A lockout leaves Claude Code grants alone: anyone who knows a username can cause one, so revoking here
            # would let a stranger cut off a person's grants. A new password revokes them (sessions.revoke_all_for_user).
            u.locked_until = now + timedelta(minutes=LOCK_MINUTES)
            u.failed_logins = 0
        db.commit()
        raise BadCredentials()
    signed_in(u)
    db.commit()
