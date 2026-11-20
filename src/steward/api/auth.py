from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from steward import clock, gateway_name
from steward.api.body import bad, json_object, text
from steward.auth import cookies, devices, reset, sessions, users
from steward.auth.passwords import verify_password
from steward.auth.ratelimit import FORGOT, limit, try_again
from steward.db.directory_models import Membership, Session as DbSession, User
from steward.deps import current_session, current_user, directory_db, now

router = APIRouter(prefix="/api/auth")


class LoginIn(BaseModel):
    username: str
    password: str
    device_label: str = ""


def _user_out(u: User) -> dict:
    return {"id": u.id, "username": u.username, "display_name": u.display_name}


PREFS = ("celebrations", "sound")
ON_OFF = "Choose on or off."


def _prefs(u: User) -> dict:
    """Phase 17 spec 3.6: the person's own Celebrations and Sound; None is never chosen."""
    return {"celebrations": u.celebrations, "sound": u.sound}


WRONG = "That username or password isn't right."


def _wrong(request: Request, device: devices.Device) -> JSONResponse:
    r = JSONResponse({"error": WRONG}, status_code=401)
    devices.give_cookie(request, r, device)
    return r


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(directory_db)):
    """Issue #7: wrong passwords count per device and account (auth/devices.py), and a device the account doesn't
    know yet counts against its address too. There the counts go by the name as typed, so a username and its email
    count apart whether or not they are an account's. A wrong password, a name nobody has, this device's lock and the
    account's lock all answer the same 401 after one password hash, so none shows that a username exists. A wrong
    password, a name nobody has and the account's lock also hash, write and commit in the same order, so each holds
    the write lock as long (issue #50, users.miss)."""
    t = now()
    lockouts = request.app.state.device_lockouts
    found = users.find_user(db, body.username)
    device = devices.identify(request, found)
    account = devices.account_key(found, body.username, device)
    if lockouts.locked(device, account, t):
        verify_password(users._DUMMY_HASH, body.password)   # one hash, as a wrong password takes
        return _wrong(request, device)
    try:
        u = users.check_login(db, body.username, body.password, t, backstop=not device.known)
    except users.Locked:
        # check_login has hashed once. Charged like a wrong password: an uncharged try here, against a charged one at
        # a name nobody has, would show by the address's lock whether the name exists.
        lockouts.failed(device, account, t)
        return _wrong(request, device)
    except users.BadCredentials:
        lockouts.failed(device, account, t)
        return _wrong(request, device)
    lockouts.cleared(device, devices.account_keys(u, device))   # at the username and the email
    label = body.device_label or request.headers.get("user-agent", "")[:120]
    token, _ = sessions.create_session(db, u, label, t, request.app.state.settings.session_days)
    cookies.set_session(request, response, token, request.app.state.settings.session_days * 86400)
    devices.signed_in(request, response, u, device)
    return {"user": _user_out(u)}


@router.post("/logout", status_code=204)
def logout(request: Request, db: Session = Depends(directory_db)):
    token, _ = cookies.read_session(request)
    if token:
        sessions.revoke_token(db, token, now())
    resp = Response(status_code=204)
    cookies.clear_session(request, resp)
    return resp


@router.get("/me")
def me(request: Request, found=Depends(current_session), db: Session = Depends(directory_db)):
    from steward.api.books import books_for
    u, s = found
    return {"user": {**_user_out(u), "email": u.email, "prefs": _prefs(u)},
            "session": {"id": s.id, "device_label": s.device_label, "created_at": s.created_at.isoformat()},
            "books": books_for(request, db, u.id),
            "gateway_name": gateway_name.current(),   # spec 2026-11-05, section 7
            "timezone": clock.household_tz().key}      # issue #26: the web's today is the household's


NAME = "Enter a name of up to 80 characters."


@router.patch("/me")
def change_me(me: User = Depends(current_user), body: dict = Depends(json_object), db: Session = Depends(directory_db)):
    """Each person changes only their own display name (onboarding: About you) and their own Celebrations and Sound
    (phase 17 spec 3.6). An empty body still asks for the name."""
    unknown = sorted(set(body) - {"display_name", *PREFS})
    if unknown:
        raise bad(f"Unknown field: {unknown[0]}.")
    name = text(body.get("display_name"), NAME, 80) if not body or "display_name" in body else None
    for k in PREFS:
        if k in body and not isinstance(body[k], bool):
            raise bad(ON_OFF)
    u = db.get(User, me.id)
    if name is not None:
        u.display_name = name
    for k in PREFS:
        if k in body:
            setattr(u, k, body[k])
    db.commit()
    return {"user": {**_user_out(u), "prefs": _prefs(u)}}


@router.get("/sessions")
def list_sessions(u: User = Depends(current_user), db: Session = Depends(directory_db)):
    rows = db.scalars(select(DbSession).where(DbSession.user_id == u.id, DbSession.revoked_at.is_(None),
                                                  DbSession.expires_at > now())
                      .order_by(DbSession.last_seen_at.desc()))
    return {"sessions": [{"id": s.id, "device_label": s.device_label,
                          "created_at": s.created_at.isoformat(),
                          "last_seen_at": s.last_seen_at.isoformat()} for s in rows]}


@router.delete("/sessions/{sid}", status_code=204)
def revoke_session(sid: int, request: Request, found=Depends(current_session), db: Session = Depends(directory_db)):
    u, this_session = found
    if not sessions.revoke(db, sid, u.id, now()):
        raise HTTPException(404, detail={"error": "That device isn't signed in."})
    resp = Response(status_code=204)
    if sid == this_session.id:
        cookies.clear_session(request, resp)     # this device signed itself out
    return resp


def _locked(until, t) -> HTTPException:
    return HTTPException(423, detail={"error": try_again(until, t), "locked_until": until.isoformat()})


def require_current_password(request: Request, db: Session, me: User, password: str, t) -> None:
    """Re-check the signed-in person's own password before a sensitive change. Failures count as they do at sign-in,
    toward this device's lock and, for a device the account doesn't know, its address's and the account's. A session
    doesn't make its device known (only a sign-in does), so a copied session that takes new device ids is held like
    any stranger. The person is signed in, so a lock may say so (423)."""
    lockouts = request.app.state.device_lockouts
    device = devices.identify(request, me)
    account = devices.account_keys(me, device)   # no name is typed: the username's and the email's counts apply
    until = lockouts.locked(device, account, t)
    if until:
        raise _locked(until, t)
    try:
        users.check_password(db, me, password, t, backstop=not device.known)
    except users.Locked as e:
        raise _locked(e.until, t)
    except users.BadCredentials:
        lockouts.failed(device, account, t)
        raise HTTPException(403, detail={"error": "That isn't your current password."})
    lockouts.cleared(device, account)


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str


@router.post("/password", status_code=204)
def change_password(body: ChangePasswordIn, request: Request, found=Depends(current_session),
                    db: Session = Depends(directory_db)):
    me, this_session = found
    t = now()
    require_current_password(request, db, me, body.current_password, t)
    try:
        users.set_password(db, me, body.new_password)
    except users.WeakPassword as e:
        raise HTTPException(400, detail={"error": str(e)})
    reset.close_open_resets(db, me.id, t)
    sessions.revoke_all_for_user(db, me.id, t, keep=this_session.id)  # commits
    resp = Response(status_code=204)
    devices.signed_in(request, resp, me)    # issue #7: the note for the old password is void; this device proved the new one
    return resp


class ForgotIn(BaseModel):
    username: str


class ResetIn(BaseModel):
    token: str
    password: str


class PasswordIn(BaseModel):
    password: str


class ResetOtherIn(BaseModel):
    current_password: str
    password: str


@router.post("/forgot", status_code=202, dependencies=[Depends(limit("forgot", FORGOT))])
def forgot(body: ForgotIn, request: Request, background: BackgroundTasks, db: Session = Depends(directory_db)):
    mailer = request.app.state.mailer
    # Send after the response so timing and SMTP failures don't reveal whether the account exists.
    reset.request_reset(db, mailer, request.app.state.settings.base_url, body.username, now(),
                        send=lambda *a: background.add_task(reset.safe_send, mailer, *a))
    return {"message": "If that account exists, we sent a link. It works for 15 minutes."}


@router.post("/reset")
def do_reset(body: ResetIn, request: Request, response: Response, db: Session = Depends(directory_db)):
    try:
        u = reset.consume(db, body.token, body.password, now())
    except reset.BadToken:
        raise HTTPException(400, detail={"error": reset.LINK_GONE, "gone": True})   # the page drops its form (#19)
    except users.WeakPassword as e:
        raise HTTPException(400, detail={"error": str(e)})
    # Issue #7: the new password voids every device's note. This device chose it, from the emailed link, so it gets a
    # note for it, as a password change does, and isn't held by its address's count when it signs in next.
    devices.signed_in(request, response, u)
    return {"user": _user_out(u)}


users_router = APIRouter(prefix="/api/users")


NOT_YOURS_TO_RESET = "You can reset a password only for people in books you manage."


def _shared_ids(db: Session, me_id: int):
    mine = select(Membership.book_id).where(Membership.user_id == me_id)
    return select(Membership.user_id).where(Membership.book_id.in_(mine))


def _can_reset(db: Session, me_id: int, target_id: int) -> bool:
    if me_id == target_id:
        return False
    theirs = set(db.scalars(select(Membership.book_id).where(Membership.user_id == target_id)))
    managed = set(db.scalars(select(Membership.book_id).where(Membership.user_id == me_id,
                                                              Membership.role == "manager")))
    return bool(theirs) and theirs <= managed


def _managed_ids(me_id: int):
    """Everyone in a book I manage, whose email I may see (a viewer sees only their own)."""
    mine = select(Membership.book_id).where(Membership.user_id == me_id, Membership.role == "manager")
    return select(Membership.user_id).where(Membership.book_id.in_(mine))


@users_router.get("")
def list_users(me: User = Depends(current_user), db: Session = Depends(directory_db)):
    rows = db.scalars(select(User).where(or_(User.id.in_(_shared_ids(db, me.id)), User.id == me.id)).order_by(User.id))
    managed = set(db.scalars(_managed_ids(me.id))) | {me.id}
    return {"users": [{"id": u.id, "username": u.username, "display_name": u.display_name,
                       "can_reset": _can_reset(db, me.id, u.id)} | ({"email": u.email} if u.id in managed else {})
                      for u in rows]}


@users_router.post("/{uid}/reset-password", status_code=204)
def reset_other(uid: int, body: ResetOtherIn, request: Request, me: User = Depends(current_user),
                db: Session = Depends(directory_db)):
    if uid == me.id:
        raise HTTPException(403, detail={"error": "Change your own password from Settings instead."})
    require_current_password(request, db, me, body.current_password, now())
    target = db.scalar(select(User).where(User.id == uid, User.id.in_(_shared_ids(db, me.id))))
    if target is None:
        raise HTTPException(404, detail={"error": "No such person."})
    if not _can_reset(db, me.id, uid):
        raise HTTPException(403, detail={"error": NOT_YOURS_TO_RESET})
    try:
        users.set_password(db, target, body.password)
    except users.WeakPassword as e:
        raise HTTPException(400, detail={"error": str(e)})
    reset.start_clean(db, target.id, now())   # recovery starts clean; they re-add devices
    return Response(status_code=204)
