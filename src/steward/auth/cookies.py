"""The names and flags of Steward's two cookies: the session, and the device the sign-in page marks (issue #7).

Issue #14: on an https STEWARD_BASE_URL each name takes the __Host- prefix, so the browser keeps the cookie only when
it is Secure, on path /, and has no Domain: no other host on the domain can set or shadow it. A plain-http install
(the demo on localhost) keeps the plain names, since a __Host- cookie must come over a secure connection. 0.1.0 set
the plain session name everywhere; 0.1.1 still signed in with it and moved it to the new name. From 0.1.2 a session
is read only under the install's own name, so a plain-named cookie another host on the domain plants signs no one in.
Signing in and signing out still clear a left-over plain one. Every cookie here is Secure, HttpOnly and SameSite=Lax,
as the session cookie always was."""
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import Response

SESSION = "steward_session"
HOST_SESSION = "__Host-steward_session"
DEVICE = "steward_device"
HOST_DEVICE = "__Host-steward_device"
FLAGS = {"path": "/", "secure": True, "httponly": True, "samesite": "lax"}


def https(base_url: str) -> bool:
    return urlsplit(base_url.strip()).scheme.lower() == "https"


def session_name(base_url: str) -> str:
    return HOST_SESSION if https(base_url) else SESSION


def device_name(base_url: str) -> str:
    return HOST_DEVICE if https(base_url) else DEVICE


def _base(request: Request) -> str:
    return request.app.state.settings.base_url


def read_session(request: Request) -> tuple[str | None, str | None]:
    """The session token and the name it came under: only the install's own name (0.1.0's plain name no longer
    counts on an https install)."""
    name = session_name(_base(request))
    token = request.cookies.get(name)
    return (token, name) if token else (None, None)


def set_session(request: Request, response: Response, token: str, max_age: int) -> None:
    name = session_name(_base(request))
    response.set_cookie(name, token, max_age=max_age, **FLAGS)
    if name != SESSION and request.cookies.get(SESSION):
        response.delete_cookie(SESSION, **FLAGS)     # a left-over plain name from 0.1.0


def clear_session(request: Request, response: Response) -> None:
    for name in dict.fromkeys((session_name(_base(request)), SESSION)):
        response.delete_cookie(name, **FLAGS)
