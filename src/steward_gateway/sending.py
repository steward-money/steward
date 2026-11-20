"""Whether a request to Anthropic may have gone out, for the budget rule: a reservation is released only when certainly
nothing was sent (an error status back counts as nothing to bill); otherwise it is settled.

make_client's HTTP client reports on each request made inside watching(attempt). httpx2's request hook runs before
DNS, the connection pool, TCP and TLS, and marks the attempt "waiting"; httpcore2's trace marks it "sent" as it starts
writing the request headers, so a request cut off partway through counts as sent, and so does one a response came back
for. An attempt nothing reported on ("unknown": another client, or cancelled before the hook ran) may have gone out.

It fails closed: "waiting" is trusted only once this process has seen the trace mark a request sent, so an httpx2 or
httpcore2 whose trace no longer fires (an event renamed, a transport without traces) settles every request, as before
this module, instead of releasing ones that went out. The SDK's error says nothing was sent when it is a connection that
was never made or never handed out."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

import anthropic
import httpx2

# Causes of an APIConnectionError (APITimeoutError included) raised before any connection carried the request. Only
# sound because the client never retries (make_client): a retry could follow a sent attempt.
NEVER_CONNECTED = (httpx2.ConnectError, httpx2.ConnectTimeout, httpx2.PoolTimeout)
SENDING = ".send_request_headers.started"       # httpcore2's trace event as it starts writing a request (HTTP/1.1, /2)

_traced = False         # the trace has marked a request sent in this process, so "waiting" can be believed


class Attempt:
    """One request's progress: "unknown", "waiting" (nothing written yet) or "sent". It only moves forward."""
    __slots__ = ("state",)

    def __init__(self) -> None:
        self.state = "unknown"


_attempt: ContextVar[Attempt | None] = ContextVar("steward_gateway_attempt", default=None)


@contextmanager
def watching(attempt: Attempt) -> Iterator[Attempt]:
    """Requests made in this block (in this task) report to `attempt`."""
    token = _attempt.set(attempt)
    try:
        yield attempt
    finally:
        _attempt.reset(token)


def nothing_sent(attempt: Attempt, error: BaseException | None = None) -> bool:
    """Certainly nothing reached Anthropic: the client never began writing the request (and its trace is known to
    work), or the SDK says no connection was made or handed out."""
    if attempt.state == "waiting" and _traced:
        return True
    return isinstance(error, anthropic.APIConnectionError) and isinstance(error.__cause__, NEVER_CONNECTED)


async def _on_request(request: httpx2.Request) -> None:
    attempt = _attempt.get()
    if attempt is None:
        return
    if attempt.state == "unknown":
        attempt.state = "waiting"
    earlier = request.extensions.get("trace")

    async def trace(name: str, info: dict) -> None:
        global _traced
        if name.endswith(SENDING):
            attempt.state = "sent"
            _traced = True
        if earlier is not None:
            await earlier(name, info)
    request.extensions["trace"] = trace


async def _on_response(response: httpx2.Response) -> None:
    attempt = _attempt.get()
    if attempt is not None:
        attempt.state = "sent"      # an answer came back, whatever the trace said


def http_client() -> httpx2.AsyncClient:
    """The SDK's own default HTTP client (its timeouts, limits, keep-alive and proxy handling), plus the hooks."""
    return anthropic.DefaultAsyncHttpxClient(event_hooks={"request": [_on_request], "response": [_on_response]})
