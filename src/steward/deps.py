import hmac
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from steward import clock, gateway_name
from steward.auth import cookies, sessions
from steward.books.owners import OwnerName, owner_options
from steward.books.people import Person, attach_people, sample_people
from steward.books.sample_guard import SAMPLE_REFUSED
from steward.books.registry import CANT_OPEN, BookInfo, BookUnavailable, household_book_id
from steward.db import generation
from steward.db.directory_models import Book, Membership, User
from steward.db.engine import WRITE
from steward.services.categorizer_client import ModelClient, NullModelClient

SAFE_METHODS = frozenset({"GET", "HEAD"})


def now() -> datetime:
    return clock.now()


def today() -> date:
    return clock.today()


def _directory_session(request: Request) -> Session:
    """Like _book_session: a GET or HEAD reads (a plain BEGIN), so signing a request in never queues behind the
    directory's write lock (phase 18: six signed-in GETs ran one at a time, ~90 ms apart). Every other method may
    write, so it takes the lock up front (BEGIN IMMEDIATE). A GET that must write calls begin_write first; the
    session's stamp does (sessions.resolve). Tests make a GET's directory writes an error, as for books."""
    engine = request.app.state.books.directory
    if request.method in SAFE_METHODS:
        return Session(engine.execution_options(read=True, query_only=request.app.state.settings.env == "test"))
    return Session(engine.execution_options(**WRITE))


def directory_db(request: Request) -> Iterator[Session]:
    with _directory_session(request) as s:
        yield s


def directory_write_db(request: Request) -> Iterator[Session]:
    """BEGIN IMMEDIATE whatever the method: Claude Code's grant door stamps a grant's use on its GETs too."""
    with Session(request.app.state.books.directory.execution_options(**WRITE)) as s:
        yield s


def current_session(request: Request, db: Session = Depends(directory_db)):
    token, _ = cookies.read_session(request)
    t = now()
    found = sessions.resolve(db, token, t) if token else None
    if found is None:
        raise HTTPException(401, detail={"error": "Please sign in."})
    # End the transaction resolve left open, so nothing is held while a handler waits for its request body
    # (json_object). Expired attributes reload later, inside the handler.
    db.commit()
    return found


def current_user(found=Depends(current_session)) -> User:
    return found[0]


VIEWER_COPY = "You can see this book but not change it."
NO_SUCH_BOOK = "No such book."
NOT_IN_THIS_BOOK = "This book doesn't have that."


def sample_ok(fn):
    """Marks a route that a Sample book may answer though real_book_only guards its router (the status reads Home and
    Ask need)."""
    fn.sample_ok = True
    return fn


def viewer_ok(fn):
    """Marks a route that changes nothing though it isn't a GET (plan ruling 10): viewers may call it."""
    fn.viewer_ok = True
    return fn


@dataclass(frozen=True)
class BookContext:
    book: BookInfo
    role: str
    user_id: int
    people: tuple[Person, ...]

    @property
    def is_manager(self) -> bool:
        return self.role == "manager"

    @property
    def owners(self) -> tuple[OwnerName, ...]:
        return owner_options(self.book.kind, self.book.owner_names, self.people)

    def owner_labels(self) -> dict[str, str]:
        """Every owner key in words, as the owner picker shows it: a member's name or a role (issue #31). A key the
        picker leaves out (a household of one's second person) keeps the book's own label."""
        return {o.key: o.label for o in self.book.owner_names} | {o.key: o.label for o in self.owners}


def _cant_open(reason: str, role: str) -> dict:
    return {"error": CANT_OPEN, "reason": reason if role == "manager" else None}


def _people(ddb: Session, book: BookInfo) -> tuple[Person, ...]:
    real = tuple(Person(*r) for r in ddb.execute(
        select(User.id, User.username, User.display_name, Membership.role).join(Membership, Membership.user_id == User.id)
        .where(Membership.book_id == book.id).order_by(User.id)))
    return sample_people(real) if book.sample else real


def open_book(slug: str, request: Request, me: User = Depends(current_user),
              ddb: Session = Depends(directory_db)) -> BookContext:
    """Every route about one book goes through here (spec 2.4). A person who isn't a member gets 404 whether or not
    the book exists; a viewer gets 403 on anything but GET or HEAD unless the route is marked viewer_ok
    (ruling 11). Ends the directory transaction before the handler runs."""
    row = ddb.execute(select(Book, Membership.role).join(Membership, Membership.book_id == Book.id)
                      .where(Book.slug == slug, Book.archived_at.is_(None), Membership.user_id == me.id)).first()
    if row is None:
        ddb.commit()
        raise HTTPException(404, detail={"error": NO_SUCH_BOOK})
    book, role = BookInfo.of(row[0]), row[1]
    endpoint = request.scope.get("endpoint")
    if request.method not in SAFE_METHODS and role != "manager" and not getattr(endpoint, "viewer_ok", False):
        ddb.commit()
        raise HTTPException(403, detail={"error": VIEWER_COPY})
    people = _people(ddb, book)
    ctx = BookContext(book, role, me.id, people)
    ddb.commit()
    unavailable = request.app.state.books.unavailable
    if book.id in unavailable:
        raise HTTPException(503, detail=_cant_open(unavailable[book.id], role))
    request.state.book = ctx
    return ctx


def _book_session(request: Request, engine, ctx: BookContext) -> Session:
    """A GET or HEAD reads (a plain BEGIN): WAL lets it answer from its snapshot while another request holds the
    book's write lock, where BEGIN IMMEDIATE would queue it for up to the 30 s busy timeout. Every other method may
    write, so it takes the lock up front (BEGIN IMMEDIATE). A GET that must write calls begin_write (steward.db.engine)
    first. Tests make a GET's writes an error, so one that writes without it fails there, not now and then in use."""
    if request.method in SAFE_METHODS:
        opts = {"read": True, "query_only": request.app.state.settings.env == "test"}
    else:
        opts = WRITE
    s = Session(engine.execution_options(**opts))
    attach_people(s, lambda: ctx.people)
    s.info[generation.BOOK] = ctx.book.id           # its writes move only this book's plan inputs (phase 18)
    return s


def book_db(request: Request, ctx: BookContext = Depends(open_book)) -> Iterator[Session]:
    """A session on the book's cached engine, the only way a route reaches a book's database."""
    try:
        engine = request.app.state.books.engine_for(ctx.book)
    except BookUnavailable as e:
        raise HTTPException(503, detail=_cant_open(str(e), ctx.role)) from None
    with _book_session(request, engine, ctx) as s:
        yield s


GRANT_REFUSED = "That grant isn't valid. A manager can issue a new one in Settings → Chat → Claude Code."
GATEWAY_ONLY = "Only {gateway} can use a grant."


def _from_gateway(request: Request) -> bool:
    """The gateway's pairing token rides with every grant (ruling I1a), so the grant's text alone opens nothing: Claude
    Code reading mcp-grants.json and calling the NAS itself gets a 401 before the grant is even looked up."""
    from steward.advisor.gateway import read_token
    expected = read_token(request.app.state.settings)
    got = request.headers.get("x-steward-gateway") or ""
    return expected is not None and hmac.compare_digest(got.encode("utf-8"), expected.encode("utf-8"))


def open_book_by_grant(slug: str, request: Request, ddb: Session = Depends(directory_write_db)) -> BookContext:
    """Claude Code's door into one book (spec 7): the BookContext open_book gives, from a grant in the Authorization
    header instead of a session. Unknown, revoked or ended grants get 401; another book's address, a person no longer
    a member, or an archived book get 404, as open_book answers a non-member; a viewer's grant reaches only GET and
    viewer_ok routes. A session cookie is never read here."""
    from steward.advisor import grants
    if not _from_gateway(request):
        raise HTTPException(401, detail={"error": gateway_name.fill(GATEWAY_ONLY)})
    header = request.headers.get("authorization") or ""
    t = now()
    g = grants.resolve(ddb, header[7:] if header.startswith("Bearer ") else None, t)
    if g is None:
        ddb.commit()
        raise HTTPException(401, detail={"error": GRANT_REFUSED})
    row = ddb.execute(select(Book, Membership.role).join(Membership, Membership.book_id == Book.id)
                      .where(Book.id == g.book_id, Book.slug == slug, Book.archived_at.is_(None),
                             Membership.user_id == g.user_id)).first()
    if row is None:
        ddb.commit()
        raise HTTPException(404, detail={"error": NO_SUCH_BOOK})
    book, role = BookInfo.of(row[0]), row[1]
    if book.sample:
        ddb.commit()
        raise HTTPException(403, detail={"error": SAMPLE_REFUSED})
    endpoint = request.scope.get("endpoint")
    if request.method not in SAFE_METHODS and role != "manager" and not getattr(endpoint, "viewer_ok", False):
        ddb.commit()
        raise HTTPException(403, detail={"error": VIEWER_COPY})
    ctx = BookContext(book, role, g.user_id, _people(ddb, book))
    grants.touch(g, t)
    request.state.grant_expires = g.expires_at
    ddb.commit()
    unavailable = request.app.state.books.unavailable
    if book.id in unavailable:
        raise HTTPException(503, detail=_cant_open(unavailable[book.id], role))
    request.state.book = ctx
    return ctx


def grant_book_db(request: Request, ctx: BookContext = Depends(open_book_by_grant)) -> Iterator[Session]:
    """book_db for Claude Code's routes: a session on the book's cached engine."""
    try:
        engine = request.app.state.books.engine_for(ctx.book)
    except BookUnavailable as e:
        raise HTTPException(503, detail=_cant_open(str(e), ctx.role)) from None
    with _book_session(request, engine, ctx) as s:
        yield s


def real_book_only(request: Request, ctx: BookContext = Depends(open_book)) -> None:
    """Routes that reach a bank, Claude, a file or a person are refused in a Sample book (guided setup spec 8.3,
    layer 2), unless the endpoint is marked sample_ok. tests/test_sample_book.py sweeps the routes that need it."""
    if ctx.book.sample and not getattr(request.scope.get("endpoint"), "sample_ok", False):
        raise HTTPException(403, detail={"error": SAMPLE_REFUSED})


def household_only(ctx: BookContext = Depends(open_book)) -> None:
    """Plan, setup, children, Social Security, facts and asset classes exist only in household books (ruling 5)."""
    if ctx.book.kind != "household":
        raise HTTPException(404, detail={"error": NOT_IN_THIS_BOOK})


def install_household(ctx: BookContext = Depends(open_book), ddb: Session = Depends(directory_db)) -> None:
    """For book routes about the whole install (the chat budget on the gateway, Get Steward ready's email, backups,
    gateway and key rows; tests/test_isolation_sweep.py lists them in INSTALL_WIDE): the open book must be THE
    household book (household_book_id), not just any household-kind book, so a manager of an older install's second
    household book gets no install authority."""
    ok = ctx.book.id == household_book_id(ddb)
    ddb.commit()
    if not ok:
        raise HTTPException(404, detail={"error": NOT_IN_THIS_BOOK})


def parent_only(ctx: BookContext = Depends(open_book)) -> None:
    """The inventory, suggestions, tax forms and the change record are in parent books only for now (spec 2.8,
    ruling 32)."""
    if ctx.book.kind != "parent":
        raise HTTPException(404, detail={"error": NOT_IN_THIS_BOOK})


def managers_only(ctx: BookContext = Depends(open_book)) -> None:
    """Reads a viewer doesn't get: the suggestions (design pass decision 9, ruling 29)."""
    if not ctx.is_manager:
        raise HTTPException(404, detail={"error": NOT_IN_THIS_BOOK})


INSTALL_ONLY = "Only a manager of the household can see this."


def install_manager(me: User = Depends(current_user), ddb: Session = Depends(directory_db)) -> User:
    """Settings that belong to the whole install (guided setup spec 4.7): a manager of THE household book
    (household_book_id), never of just any book. Anyone else, signed in or not a manager, gets 404 (the same words
    either way)."""
    household = household_book_id(ddb)
    found = None if household is None else ddb.execute(
        select(Membership.user_id).where(Membership.user_id == me.id, Membership.book_id == household,
                                         Membership.role == "manager")).first()
    if found is None:
        ddb.commit()
        raise HTTPException(404, detail={"error": INSTALL_ONLY})
    # current_session's commit expired `me`. Load it now and let it go, so the handler reading its id and name never
    # reopens this session's write transaction while the handler's own save waits for that lock.
    me.display_name, me.id  # noqa: B018
    ddb.expunge(me)
    ddb.commit()
    return me


def model(request: Request) -> ModelClient:
    ctx = getattr(request.state, "book", None)
    if ctx is not None and ctx.book.sample:
        return NullModelClient()
    return request.app.state.model


def recorded(fn):
    """Marks a write route that writes its change record in parent books (spec 5). The change record sweep requires
    every write route to carry this mark or not_recorded."""
    fn.change_record = True
    return fn


def not_recorded(reason: str):
    """Marks a write route that a parent book doesn't record, with the reason in plain words (ruling 15)."""
    def mark(fn):
        fn.change_record = reason
        return fn
    return mark
