"""Serving the gateway (spec 3.2, 7, ruling 6): one uvicorn server over two sockets, so the models load once. The
gateway's own routes answer only on host:port (the Tailscale address); /mcp/… answers only on 127.0.0.1:mcp_port, and only when gateway.toml says mcp = true (off, that socket isn't bound).
serve_in_thread is for tests and the eval rig."""
from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

from starlette.responses import JSONResponse

LOOPBACK = ("127.0.0.1", "::1")


class PortSplit:
    def __init__(self, app, mcp_port: int):
        self.app, self.mcp_port = app, mcp_port

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            host, port = (scope.get("server") or (None, None))[:2]
            on_mcp_socket = port == self.mcp_port and host in LOOPBACK
            if on_mcp_socket != scope["path"].startswith("/mcp/"):
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                    return
                await JSONResponse({"error": "Not here."}, status_code=404)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def _listen(host: str, port: int) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((host, port))
    s.set_inheritable(True)
    return s


def bind_sockets(cfg) -> list[socket.socket]:
    """The gateway's own address, and 127.0.0.1:mcp_port only when Claude Code is on (mcp = true in gateway.toml).
    Off, nothing listens there at all."""
    sockets = [_listen(cfg.host, cfg.port)]
    if cfg.mcp:
        sockets.append(_listen("127.0.0.1", cfg.mcp_port))
    return sockets


def run(cfg, app) -> None:
    import uvicorn
    config = uvicorn.Config(PortSplit(app, cfg.mcp_port), log_level="warning", log_config=None, lifespan="on")
    uvicorn.Server(config).run(sockets=bind_sockets(cfg))


@contextmanager
def serve_in_thread(app, *, sockets: list[socket.socket] | None = None) -> Iterator[str]:
    """A real uvicorn server on a free loopback port (or on the given sockets), in a daemon thread. Yields the first
    socket's address. On a clean exit it returns only once the server has stopped, which uvicorn does after every
    connection's handler has returned, so a test can read what the handlers left behind without polling for it."""
    import uvicorn
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="on")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, kwargs={"sockets": sockets} if sockets else {}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError("The server didn't start.")
        time.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(10)
    if thread.is_alive():
        raise RuntimeError("The server didn't stop.")
