"""A fake Anthropic Messages API (spec 11.3). It records every request body and header and answers from a script: as
server-sent events when the request streams, as JSON when it doesn't (a max_tokens 0 prewarm returns no content).
Nothing here reaches Anthropic, and the key the tests use is the dummy "test-key-not-real"."""
from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

USAGE = {"input_tokens": 1200, "output_tokens": 300, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 4500}


@dataclass
class Reply:
    blocks: list[dict]
    stop_reason: str = "end_turn"
    usage: dict = field(default_factory=lambda: dict(USAGE))
    model: str = "claude-opus-5-5"
    status: int = 200
    error_type: str = "api_error"
    error_message: str = "fake error"
    error_body: object = None   # the whole error body when set (a body that isn't Anthropic's shape)
    hold: threading.Event | None = None   # the stream stops after its first text delta until this is set, so a Stop
                                          # lands while it is still going (it used to race a pause between deltas)
    arrived: threading.Event | None = None    # set once the whole request has arrived
    answer: threading.Event | None = None     # the reply waits for this first: a request sent and not yet answered
    sse: list[str | bytes] | None = None  # these raw events instead of the scripted ones (a stream cut short, bad data)
    cut: bool = False                     # after its events the stream breaks the connection mid-body


class Cut(Exception):
    """Raised inside a streaming body: uvicorn drops the connection without the closing chunk."""


def text(t: str) -> dict:
    return {"type": "text", "text": t}


def tool_use(id: str, name: str, input: dict) -> dict:
    return {"type": "tool_use", "id": id, "name": name, "input": input}


def thinking(signature: str) -> dict:
    return {"type": "thinking", "thinking": "", "signature": signature}


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def sse(reply: Reply, msg_id: str = "msg_fake_0") -> list[str]:
    """The raw events a scripted reply streams, to cut short or change for Reply.sse."""
    return list(_events(reply, msg_id))


def _body(reply: Reply, msg_id: str) -> Iterator[str]:
    yield from reply.sse if reply.sse is not None else _events(reply, msg_id)
    if reply.cut:
        raise Cut()


def _events(reply: Reply, msg_id: str) -> Iterator[str]:
    u = reply.usage
    yield _sse("message_start", {"type": "message_start", "message": {
        "id": msg_id, "type": "message", "role": "assistant", "model": reply.model, "content": [], "stop_reason": None,
        "stop_sequence": None, "usage": {"input_tokens": u["input_tokens"], "output_tokens": 1,
                                         "cache_creation_input_tokens": u["cache_creation_input_tokens"],
                                         "cache_read_input_tokens": u["cache_read_input_tokens"]}}})
    for i, b in enumerate(reply.blocks):
        if b["type"] == "text":
            yield _sse("content_block_start", {"type": "content_block_start", "index": i,
                                               "content_block": {"type": "text", "text": ""}})
            for k in range(0, len(b["text"]), 20):
                if k and reply.hold is not None:
                    reply.hold.wait()
                yield _sse("content_block_delta", {"type": "content_block_delta", "index": i,
                                                   "delta": {"type": "text_delta", "text": b["text"][k:k + 20]}})
        elif b["type"] == "tool_use":
            yield _sse("content_block_start", {"type": "content_block_start", "index": i, "content_block": {
                "type": "tool_use", "id": b["id"], "name": b["name"], "input": {}}})
            yield _sse("content_block_delta", {"type": "content_block_delta", "index": i, "delta": {
                "type": "input_json_delta", "partial_json": json.dumps(b["input"])}})
        elif b["type"] == "thinking":
            yield _sse("content_block_start", {"type": "content_block_start", "index": i, "content_block": {
                "type": "thinking", "thinking": "", "signature": ""}})
            yield _sse("content_block_delta", {"type": "content_block_delta", "index": i, "delta": {
                "type": "signature_delta", "signature": b["signature"]}})
        yield _sse("content_block_stop", {"type": "content_block_stop", "index": i})
    yield _sse("message_delta", {"type": "message_delta", "delta": {"stop_reason": reply.stop_reason,
                                                                    "stop_sequence": None},
                                 "usage": {"output_tokens": u["output_tokens"]}})
    yield _sse("message_stop", {"type": "message_stop"})


class FakeAnthropic:
    def __init__(self, script: Callable[[dict, int], Reply]):
        self.script = script
        self.requests: list[dict] = []
        self.headers: list[dict] = []

    def app(self) -> FastAPI:
        app = FastAPI()

        @app.post("/v1/messages")
        async def messages(request: Request):
            body = await request.json()
            self.requests.append(body)
            self.headers.append(dict(request.headers))
            n = len(self.requests)
            reply = self.script(body, n)
            if reply.arrived is not None:
                reply.arrived.set()
            if reply.answer is not None:
                await asyncio.to_thread(reply.answer.wait, 30)
            rid = {"request-id": f"req_fake_{n}"}
            if reply.status != 200:
                err = reply.error_body if reply.error_body is not None else {
                    "type": "error", "error": {"type": reply.error_type, "message": reply.error_message}}
                return JSONResponse(err, status_code=reply.status, headers=rid)
            if body.get("stream") or reply.cut:     # a cut reply breaks mid-body either way
                return StreamingResponse(_body(reply, f"msg_fake_{n}"), media_type="text/event-stream", headers=rid)
            empty = body.get("max_tokens") == 0
            return JSONResponse({"id": f"msg_fake_{n}", "type": "message", "role": "assistant", "model": reply.model,
                                 "content": [] if empty else reply.blocks,
                                 "stop_reason": "max_tokens" if empty else reply.stop_reason, "stop_sequence": None,
                                 "usage": reply.usage}, headers=rid)

        @app.post("/v1/messages/count_tokens")
        async def count_tokens(request: Request):
            return {"input_tokens": len(json.dumps(await request.json())) // 4}

        return app
