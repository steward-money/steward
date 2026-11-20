"""The model loop against the fake Anthropic server, with a scripted NAS on the other side of the frames."""
import asyncio
import json
import threading
from dataclasses import replace
from datetime import datetime, timezone

import anthropic
import httpcore2
import httpx2
import pytest

from steward_boundary import PROTOCOL
from steward_boundary.canonical import canonical
from steward_boundary.tools import DECLINED
from steward_gateway.app import make_client
from steward_gateway.config import MAX_TOKENS
from steward_gateway.prices import estimate_nd, reserve_price
from steward_gateway.prompt import request_body
from steward_gateway.config import FALLBACK_BETA
from steward_gateway.ledger import Ledger
from steward_gateway.loop import TurnDeps, run_turn
from steward_gateway.mac import load_or_create_key, mac
from steward_gateway import sending
from steward_gateway.redact.patterns import PatternDetector
from tests.gateway.fake_anthropic import FakeAnthropic, Reply, sse, text, thinking, tool_use
from tests.gateway.fakes import NameListDetector
from tests.servers import serve_in_thread
from tests.waiting import CEILING

NOW = datetime(2026, 10, 20, 17, 0, tzinfo=timezone.utc)
ACCOUNTS = {"accounts": [{"account": "Account 1", "account_type": "checking", "tax_treatment": "none", "owner": "Joint",
                          "balance_cents": 1_450_000, "as_of": "2026-10-12", "arrives": "feed", "sync_behind": False,
                          "active": True}]}


def start_frame(question="Are we spending more on dining than last year?", history=(), book_id=1, kind="household",
                asker="Person 1", today="2026-10-20"):
    return {"type": "start", "protocol": PROTOCOL, "turn_id": "t_00000000000000a1", "book_id": book_id, "user_id": 2,
            "question": question, "context": {"today": today, "book_kind": kind, "asker": asker},
            "history": list(history), "fingerprints": {"salt": "2" * 32, "hashes": []}, "issued": []}


class FakeNas:
    def __init__(self, results=None, record_reply="recorded", cancel=False):
        self.frames, self.queue = [], None
        self.results = results or (lambda calls: [{"id": c["id"], "name": c["name"], "content": ACCOUNTS, "error": None}
                                                  for c in calls])
        self.record_reply, self.cancel = record_reply, cancel

    async def send(self, frame):
        self.queue = self.queue or asyncio.Queue()
        self.frames.append(frame)
        if frame["type"] == "record":
            await self.queue.put({"type": self.record_reply, "seq": frame["seq"]})
        elif frame["type"] == "tool_calls":
            await self.queue.put({"type": "cancel", "reason": "not_a_member"} if self.cancel else
                                 {"type": "tool_results", "seq": frame["seq"], "results": self.results(frame["calls"])})

    async def receive(self, kinds, timeout):
        return await asyncio.wait_for(self.queue.get(), timeout)

    def kinds(self):
        return [f["type"] for f in self.frames]

    def of(self, kind):
        return [f for f in self.frames if f["type"] == kind]


def turn(cfg, script, start=None, nas=None, cap_cents=1000, stopped=False, with_client=True, detectors=None):
    fake, nas = FakeAnthropic(script), nas or FakeNas()
    ledger = SpyLedger(cfg.ledger_path, cfg.timezone, cap_cents, cfg.max_cap_cents, cfg.default_model)
    key = load_or_create_key(cfg.history_key_path)
    with serve_in_thread(fake.app()) as url:
        async def go():
            # The gateway's own client, so the tests send what the gateway sends
            client = make_client(replace(cfg, anthropic_base_url=url), "test-key-not-real") if with_client else None
            deps = TurnDeps(config=cfg, ledger=ledger, client=client, mac_key=key, stopped=lambda: stopped, now=lambda: NOW,
                            detectors=[PatternDetector(), NameListDetector()] if detectors is None else detectors)
            try:
                return await run_turn(start or start_frame(), nas, deps)
            finally:
                if client is not None:
                    await client.close()
        frame = asyncio.run(go())
    return frame, fake, nas, ledger, key


def test_a_plain_answer_is_recorded_first_then_streamed(gw_cfg):
    done, fake, nas, ledger, _ = turn(gw_cfg, lambda b, n: Reply([text("You spent $812.40 on dining in September.")]))
    assert done["stop"] == "end_turn" and done["line"] is None and done["requests"] == 1 and done["cost_nd"] > 0
    kinds = nas.kinds()
    assert kinds[:3] == ["redacted", "record", "sent"] and kinds[-2:] == ["assistant", "done"]
    assert "".join(f["delta"] for f in nas.of("text")) == "You spent $812.40 on dining in September."
    assert [p["part"] for p in nas.of("record")[0]["parts"]] == ["question", "system"]
    body = fake.requests[0]
    assert nas.of("record")[0]["model"] == body["model"]               # the sent log names the model (PRIV-15)
    assert body["model"] == "claude-opus-5-5" and body["max_tokens"] == 16_000 and body["stream"] is True
    assert body["thinking"] == {"type": "adaptive"} and body["output_config"] == {"effort": "medium"}
    assert body["fallbacks"] == "default" and body["tool_choice"] == {"type": "auto"}
    assert body["cache_control"] == {"type": "ephemeral"}
    assert fake.headers[0]["anthropic-beta"] == FALLBACK_BETA
    assert body["messages"][-2] == {"role": "user", "content": [{"type": "text",
                                                                 "text": "Are we spending more on dining than last year?"}]}
    assert body["messages"][-1] == {"role": "system", "content": "Today is Tuesday, October 20, 2026. This is a "
                                                                 "household book. The person asking is Person 1."}
    assert ledger.state(NOW, gw_cfg)["spent_cents"] >= 0 and ledger.state(NOW, gw_cfg)["reserved_cents"] == 0


# The headers a request to Anthropic may carry, besides the SDK's own x-stainless-* ones (OS, runtime, retry count).
SENT_HEADERS = {"host", "accept", "accept-encoding", "connection", "content-type", "content-length", "user-agent",
                "authorization", "anthropic-version", "anthropic-beta"}


def assert_sent_headers(h: dict, beta: str | None) -> None:
    """One request's headers: the key once, as a bearer token; the API version the gateway was tested on; exactly the
    beta named, or none; and no header outside SENT_HEADERS."""
    assert h["authorization"] == "Bearer test-key-not-real" and "x-api-key" not in h
    assert h["anthropic-version"] == "2026-10-21"
    assert h.get("anthropic-beta") == beta
    assert {k for k in h if k not in SENT_HEADERS and not k.startswith("x-stainless-")} == set()


def test_each_request_carries_the_key_once_and_no_header_outside_the_list(gw_cfg):
    """anthropic 2.x (API version 2026-10-21) sends the key as Authorization: Bearer, where 1.x sent X-Api-Key. Every
    request carries the key once, the API version the gateway was tested on, the fallback beta and no other, and no
    header outside SENT_HEADERS: no trace context from the SDK's OpenTelemetry support (traceparent, baggage), and
    nothing a later SDK adds unreviewed."""
    def script(body, n):
        if n == 1:
            return Reply([tool_use("toolu_01", "accounts", {"include_closed": False})], stop_reason="tool_use")
        return Reply([text("done")])
    done, fake, *_ = turn(gw_cfg, script)
    assert done["stop"] == "end_turn" and len(fake.headers) == 2
    for h in fake.headers:
        assert_sent_headers(h, beta=FALLBACK_BETA)


def test_the_question_is_redacted_before_it_is_checked_and_sent(gw_cfg):
    q = "Did Tom Becker get the check? Write to tom@example.com"
    done, fake, nas, _, _ = turn(gw_cfg, lambda b, n: Reply([text("I can't see that.")]), start_frame(question=q))
    assert nas.of("redacted")[0]["text"] == "Did [name] get the check? Write to [email]"
    assert fake.requests[0]["messages"][-2]["content"][0]["text"] == "Did [name] get the check? Write to [email]"


def test_a_tool_round_keeps_thinking_and_records_the_result(gw_cfg):
    def script(body, n):
        if n == 1:
            return Reply([thinking("sig-1"), tool_use("toolu_01", "accounts", {"include_closed": False})],
                         stop_reason="tool_use")
        return Reply([text("Account 1 holds $14,500.00.")])
    done, fake, nas, _, _ = turn(gw_cfg, script)
    assert done["stop"] == "end_turn" and done["requests"] == 2
    assert nas.of("status")[0]["tool"] == "accounts"
    assert nas.of("tool_calls")[0]["calls"] == [{"id": "toolu_01", "name": "accounts", "input": {"include_closed": False}}]
    second = nas.of("record")[1]["parts"]
    assert second == [{"part": "tool_result", "payload": {"tool_use_id": "toolu_01", "name": "accounts",
                                                          "is_error": False, "content": ACCOUNTS},
                       "sha256": second[0]["sha256"], "bytes": second[0]["bytes"]}]
    msgs = fake.requests[1]["messages"]
    assert msgs[-2]["role"] == "assistant"
    assert msgs[-2]["content"][0] == {"type": "thinking", "thinking": "", "signature": "sig-1"}
    assert msgs[-1] == {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_01",
                                                     "content": canonical(ACCOUNTS).decode(), "is_error": False}]}


def test_the_prefix_is_identical_across_books_people_and_days(gw_cfg):
    reply = lambda b, n: Reply([text("ok")])
    _, a, _, _, _ = turn(gw_cfg, reply, start_frame())
    _, b, _, _, _ = turn(gw_cfg, reply, start_frame(book_id=3, kind="parent", asker="Person 3", today="2026-11-02"))
    for field in ("tools", "system"):
        assert canonical(a.requests[0][field]) == canonical(b.requests[0][field])
    assert a.requests[0]["messages"][-1] != b.requests[0]["messages"][-1]


def test_eight_steps_then_a_plain_stop(gw_cfg):
    script = lambda b, n: Reply([tool_use(f"toolu_{n:02d}", "accounts", {"include_closed": False})], stop_reason="tool_use")
    done, fake, nas, _, _ = turn(gw_cfg, script)
    assert done["stop"] == "steps" and done["line"] == "I stopped after 8 steps. Try asking about one part of this at a time."
    assert len(fake.requests) == 8 and len(nas.of("tool_calls")) == 7


def test_a_check_stops_the_turn_and_nothing_more_is_sent(gw_cfg):
    holdings = {"holdings": [{"symbol": "a@b.co", "asset_class": "other", "account": "Account 1", "value_cents": 1,
                              "cost_basis_cents": None, "as_of": "2026-10-01"}], "totals_by_asset_class": []}
    nas = FakeNas(results=lambda calls: [{"id": c["id"], "name": "holdings", "content": holdings, "error": None}
                                         for c in calls])
    script = lambda b, n: Reply([tool_use("toolu_01", "holdings", {})], stop_reason="tool_use")
    done, fake, nas, _, _ = turn(gw_cfg, script, nas=nas)
    # a symbol is proven by its schema (letters only), so the schema check is the one that fires
    assert (done["stop"], done["check"], done["path"]) == ("stopped_check", "schema", "parts[0].content.holdings[0].symbol")
    assert len(fake.requests) == 1 and len(nas.of("record")) == 1


def test_an_approvable_hash_still_stops_an_ordinary_result(gw_cfg):
    """Plan B Task 1: payee hashes travel apart in `approvable`, but until an approved merchant_rows part exists every
    part is scanned against both sets, so splitting them lets nothing new through."""
    from steward_boundary.fingerprint import hashes_for
    holdings = {"holdings": [{"symbol": "SBOX", "asset_class": "other", "account": "Account 1", "value_cents": 1,
                              "cost_basis_cents": None, "as_of": "2026-10-01"}], "totals_by_asset_class": []}
    nas = FakeNas(results=lambda calls: [{"id": c["id"], "name": "holdings", "content": holdings, "error": None}
                                         for c in calls])
    def script(b, n):
        return Reply([tool_use("toolu_01", "holdings", {})], stop_reason="tool_use")
    start = start_frame()
    start["fingerprints"] = {"salt": "2" * 32, "hashes": [],
                             "approvable": sorted(hashes_for([("SBox", False)], "2" * 32))}
    done, fake, nas, _, _ = turn(gw_cfg, script, start=start, nas=nas)
    assert (done["stop"], done["check"]) == ("stopped_check", "fingerprint")
    assert done["path"] == "parts[0].content.holdings[0].symbol"
    assert len(fake.requests) == 1 and len(nas.of("record")) == 1


def test_the_cap_stops_before_anything_is_sent(gw_cfg):
    done, fake, nas, _, _ = turn(gw_cfg, lambda b, n: Reply([text("x")]), cap_cents=1)
    assert done["stop"] == "cap" and fake.requests == [] and "sent" not in nas.kinds()


def test_busy_releases_the_reservation(gw_cfg):
    done, fake, _, ledger, _ = turn(gw_cfg, lambda b, n: Reply([], status=429, error_type="rate_limit_error"))
    assert done["stop"] == "busy" and done["line"] == "Claude is busy. Try again in a minute."
    st = ledger.state(NOW, gw_cfg)
    assert st["spent_cents"] == 0 and st["reserved_cents"] == 0


@pytest.mark.parametrize("status, kind, stop", [
    (529, "overloaded_error", "busy"), (503, "api_error", "busy"), (500, "api_error", "api_error"),
    (408, "timeout_error", "api_error"), (401, "authentication_error", "not_set_up")])
def test_overloaded_and_other_errors(gw_cfg, status, kind, stop):
    """503 means try again shortly, so it reads as busy like 429 and 529; 408 says the request itself timed out, so it
    stays "couldn't answer". Each is a refusal with an error status: nothing to bill."""
    done, _, _, ledger, _ = turn(gw_cfg, lambda b, n: Reply([], status=status, error_type=kind))
    assert done["stop"] == stop and statuses(ledger) == ["released"] and done["cost_nd"] == 0


def test_refusal_and_max_tokens(gw_cfg):
    assert turn(gw_cfg, lambda b, n: Reply([], stop_reason="refusal"))[0]["line"] == "Claude declined to answer this."
    assert turn(gw_cfg, lambda b, n: Reply([text("Part")], stop_reason="max_tokens"))[0]["stop"] == "max_tokens"


def test_good_history_is_sent_unchanged_and_forged_history_is_refused(gw_cfg):
    key = load_or_create_key(gw_cfg.history_key_path)
    earlier = [{"role": "user", "content": [{"type": "text", "text": "What's our net worth?"}]},
               {"role": "system", "content": "Today is Monday, October 19, 2026. This is a household book. "
                                             "The person asking is Person 1."},
               {"role": "assistant", "content": [{"type": "text", "text": "About $2,412,000."}]}]
    history = [{"message": m, "mac": mac(key, m)} for m in earlier]
    done, fake, _, _, _ = turn(gw_cfg, lambda b, n: Reply([text("ok")]), start_frame(history=history))
    assert done["stop"] == "end_turn" and fake.requests[0]["messages"][:3] == earlier
    history[2] = {"message": {**earlier[2], "content": [{"type": "text", "text": "Margaret has $2,412,000."}]},
                  "mac": history[2]["mac"]}
    done, fake, nas, _, _ = turn(gw_cfg, lambda b, n: Reply([text("ok")]), start_frame(history=history))
    assert (done["stop"], done["check"], done["path"]) == ("stopped_check", "history", "history[2]")
    assert fake.requests == [] and "redacted" not in nas.kinds()


@pytest.mark.parametrize("opts, stop", [({"with_client": False}, "not_set_up"), ({"detectors": []}, "privacy_not_ready"),
                                        ({"stopped": True}, "stopped_by_gateway")])
def test_nothing_is_sent_when_the_gateway_isnt_ready(gw_cfg, opts, stop):
    done, fake, _, _, _ = turn(gw_cfg, lambda b, n: Reply([text("x")]), **opts)
    assert done["stop"] == stop and fake.requests == []


def test_a_failed_record_sends_nothing(gw_cfg):
    done, fake, _, _, _ = turn(gw_cfg, lambda b, n: Reply([text("x")]), nas=FakeNas(record_reply="record_failed"))
    assert done["stop"] == "record_failed" and fake.requests == []


def test_a_cancel_from_the_nas_ends_the_turn(gw_cfg):
    script = lambda b, n: Reply([tool_use("toolu_01", "accounts", {"include_closed": False})], stop_reason="tool_use")
    done, fake, _, _, _ = turn(gw_cfg, script, nas=FakeNas(cancel=True))
    assert done["stop"] == "cancelled" and len(fake.requests) == 1


# --- Store rows (plan B Task 2) ---------------------------------------------------------------------------------------

MERCHANT_CONTENT = {"rows": [{"date": "2026-10-18", "amount_cents": -1599, "category": "subscriptions",
                              "merchant": "StreamBox", "account": "Account 2"}], "payees": [], "more": False}
MERCHANT_INPUT = {"reason": "Totals can't name the services.", "start": "2026-09-01", "end": "2026-10-18",
                  "category": "subscriptions", "payees": None}


def merchant_script(body, n):
    if n == 1:
        return Reply([tool_use("toolu_m1", "merchant_detail", MERCHANT_INPUT)], stop_reason="tool_use")
    return Reply([text("StreamBox costs $15.99 a month.")])


def answering(content=MERCHANT_CONTENT, error=None):
    return lambda calls: [{"id": c["id"], "name": c["name"], "content": content, "error": error} for c in calls]


def test_approved_store_rows_are_recorded_as_merchant_rows(gw_cfg):
    done, fake, nas, _, _ = turn(gw_cfg, merchant_script, nas=FakeNas(results=answering()))
    assert done["stop"] == "end_turn"
    second = nas.of("record")[1]
    assert [p["part"] for p in second["parts"]] == ["merchant_rows"]
    assert second["parts"][0]["payload"]["content"] == MERCHANT_CONTENT


def test_approved_store_rows_go_with_the_conversations_later_questions(gw_cfg):
    """Gate finding PRIV-9: the approved rows are part of the conversation's history, so a follow-up question resends
    them with no new card; nothing new is disclosed, but the docs must not say the names go for one question only."""
    from tests.public_docs import ROOT, flat, ledger_cells
    done, _, nas, _, _ = turn(gw_cfg, merchant_script, nas=FakeNas(results=answering()))
    assert done["stop"] == "end_turn"
    # What the NAS stores as the conversation: each "sent" frame's messages and each assistant message, in order.
    history = [m for f in nas.frames if f["type"] in ("sent", "assistant")
               for m in (f["messages"] if f["type"] == "sent" else [{"message": f["message"], "mac": f["mac"]}])]
    follow_up = start_frame(question="And how does that compare with our savings rate?", history=history)
    done, fake, nas, _, _ = turn(gw_cfg, lambda b, n: Reply([text("About the same.")]), start=follow_up)
    assert done["stop"] == "end_turn"
    assert "StreamBox" in canonical(fake.requests[0]["messages"]).decode()
    assert [p["part"] for f in nas.of("record") for p in f["parts"]] == ["question", "system"]   # no card, no new rows
    for text_ in (ledger_cells("P6")[1], (ROOT / "docs/explanations/privacy.md").read_text(encoding="utf-8")):
        assert "approved rows stay in that conversation" in flat(text_)


def test_a_declined_store_name_call_goes_as_an_ordinary_error_result(gw_cfg):
    done, fake, nas, _, _ = turn(gw_cfg, merchant_script, nas=FakeNas(results=answering(None, DECLINED)))
    assert done["stop"] == "end_turn"
    assert [p["part"] for p in nas.of("record")[1]["parts"]] == ["tool_result"]


def test_an_empty_store_name_result_goes_as_an_ordinary_tool_result(gw_cfg):
    """Preflight ruling P1: a merchant_detail call that finds no rows and no payees has no card (plan B ruling 5),
    so it isn't approved store rows and goes as a plain tool_result."""
    empty = {"rows": [], "payees": [], "more": False}
    done, fake, nas, _, _ = turn(gw_cfg, merchant_script, nas=FakeNas(results=answering(empty)))
    assert done["stop"] == "end_turn"
    second = nas.of("record")[1]
    assert [p["part"] for p in second["parts"]] == ["tool_result"]
    assert second["parts"][0]["payload"]["content"] == empty


PLUMBER = {**MERCHANT_CONTENT, "rows": [{**MERCHANT_CONTENT["rows"][0], "merchant": "Tom Becker Plumbing"},
                                        MERCHANT_CONTENT["rows"][0]]}


def ticking(content, ticked=None):
    def results(calls):
        out = [{"id": c["id"], "name": c["name"], "content": content, "error": None} for c in calls]
        return [{**r, "ticked": ticked} for r in out] if ticked is not None else out
    return results


def sent_rows(fake):
    """The store rows in the tool_result Claude got, decoded from the block the gateway sent."""
    block = next(b for m in fake.requests[1]["messages"] if m["role"] == "user" and isinstance(m["content"], list)
                 for b in m["content"] if b.get("type") == "tool_result")
    return json.loads(block["content"])


def test_an_unticked_approved_row_goes_with_a_name_the_model_finds_as_name(gw_cfg):
    """Public issue #53: the gateway replaces the name before the row is recorded, so the log, the conversation and
    Claude all get "[name] Plumbing"; the store's own row is unchanged."""
    done, fake, nas, _, _ = turn(gw_cfg, merchant_script, nas=FakeNas(results=ticking(PLUMBER, [])))
    assert done["stop"] == "end_turn"
    recorded = nas.of("record")[1]["parts"][0]["payload"]["content"]
    assert [r["merchant"] for r in recorded["rows"]] == ["[name] Plumbing", "StreamBox"]
    assert sent_rows(fake) == recorded
    assert "Tom Becker" not in canonical(fake.requests).decode()
    assert "Tom Becker" not in canonical([f for f in nas.frames if f["type"] in ("sent", "assistant")]).decode()


def test_a_result_with_no_ticks_counts_as_nothing_ticked(gw_cfg):
    done, fake, nas, _, _ = turn(gw_cfg, merchant_script, nas=FakeNas(results=ticking(PLUMBER)))
    assert done["stop"] == "end_turn"
    assert sent_rows(fake)["rows"][0]["merchant"] == "[name] Plumbing"


def test_a_ticked_row_keeps_the_name_and_the_tick_never_reaches_claude(gw_cfg):
    done, fake, nas, _, _ = turn(gw_cfg, merchant_script, nas=FakeNas(results=ticking(PLUMBER, ["r0"])))
    assert done["stop"] == "end_turn"
    assert sent_rows(fake) == PLUMBER == nas.of("record")[1]["parts"][0]["payload"]["content"]
    sent = canonical(fake.requests).decode()
    assert "ticked" not in sent and '"r0"' not in sent
    assert "ticked" not in canonical(nas.of("record")).decode()


def test_ticks_on_another_tools_result_change_nothing_and_a_bad_tick_is_refused(gw_cfg):
    def script(body, n):
        if n == 1:
            return Reply([tool_use("toolu_a", "accounts", {"include_closed": False})], stop_reason="tool_use")
        return Reply([text("Done.")])
    done, fake, nas, _, _ = turn(gw_cfg, script, nas=FakeNas(results=ticking(ACCOUNTS, ["r0"])))
    assert done["stop"] == "end_turn" and "ticked" not in canonical(fake.requests).decode()
    done, fake, nas, _, _ = turn(gw_cfg, merchant_script, nas=FakeNas(results=ticking(PLUMBER, ["Tom Becker"])))
    assert done["stop"] == "bad_request" and len(fake.requests) == 1


def test_a_result_under_another_tools_name_is_refused(gw_cfg):
    """Task 6.5: a result must carry its call's name, so rows can't be relabelled into the looser store-rows checks
    (or a store result hidden under another tool's name)."""
    relabel = lambda calls: [{"id": c["id"], "name": "merchant_detail", "content": MERCHANT_CONTENT, "error": None}
                             for c in calls]
    script = lambda b, n: Reply([tool_use("toolu_a", "accounts", {"include_closed": False})], stop_reason="tool_use") \
        if n == 1 else Reply([text("Done.")])
    done, fake, nas, _, _ = turn(gw_cfg, script, nas=FakeNas(results=relabel))
    assert done["stop"] == "bad_request" and len(fake.requests) == 1
    assert len(nas.of("record")) == 1


def test_a_cancel_while_a_card_waits_ends_the_turn_with_nothing_reserved(gw_cfg):
    """Task 6.5: when the NAS cancels during a card's wait (Stop, the browser gone, the timeout), the turn ends at once
    and no reservation stays open; the only request sent was the one already settled."""
    class CancellingNas(FakeNas):
        async def send(self, frame):
            if frame["type"] == "tool_calls":
                self.frames.append(frame)
                self.queue = self.queue or asyncio.Queue()
                await self.queue.put({"type": "cancel", "reason": "stopped"})
                return
            await super().send(frame)

    done, fake, nas, ledger, _ = turn(gw_cfg, merchant_script, nas=CancellingNas())
    assert done["stop"] == "cancelled" and len(fake.requests) == 1
    assert statuses(ledger) == ["settled"] and nothing_open(ledger, gw_cfg)


def test_the_gateway_waits_longer_for_a_card_than_for_other_results(gw_cfg):
    waits = []

    class TimingNas(FakeNas):
        async def receive(self, kinds, timeout):
            if "tool_results" in kinds:
                waits.append(timeout)
            return await super().receive(kinds, timeout)

    turn(gw_cfg, merchant_script, nas=TimingNas(results=answering()))
    turn(gw_cfg, lambda b, n: Reply([tool_use("toolu_a", "accounts", {"include_closed": False})], stop_reason="tool_use")
         if n == 1 else Reply([text("Done.")]), nas=TimingNas())
    assert waits == [660.0, 180.0]


# --- Reservations: sized on the full request body, released on every failure path (preflight ruling 4), except a
# request that was sent and then cancelled or dropped with no usage back, which counts in full (chunk 2 ruling) ------


def statuses(ledger):
    with ledger._conn() as c:
        return [r[0] for r in c.execute("SELECT status FROM spend ORDER BY id")]


def nothing_open(ledger, cfg):
    st = ledger.state(NOW, cfg)
    return st["reserved_cents"] == 0 and "reserved" not in statuses(ledger)


class SpyLedger(Ledger):
    reserved: list = None

    def reserve(self, **kw):
        self.reserved = (self.reserved or []) + [kw["estimate_nd"]]
        return super().reserve(**kw)


class Stream:
    """A stand-in for client.beta.messages.stream(): what the SDK would do, scripted per test."""

    def __init__(self, enter=None, events=()):
        self.enter, self.events = enter, events

    async def __aenter__(self):
        if self.enter:
            await self.enter()
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        async def gen():
            for e in self.events:
                yield e()
        return gen()


class StubClient:
    def __init__(self, make):
        self.beta = type("B", (), {"messages": type("M", (), {"stream": staticmethod(make)})})()


def run_stub(cfg, make, nas=None, reply_timeout=180.0):
    ledger = SpyLedger(cfg.ledger_path, cfg.timezone, 1000, cfg.max_cap_cents, cfg.default_model)
    deps = TurnDeps(config=cfg, ledger=ledger, client=StubClient(make), mac_key=load_or_create_key(cfg.history_key_path),
                    stopped=lambda: False, now=lambda: NOW, detectors=[PatternDetector(), NameListDetector()],
                    reply_timeout=reply_timeout)
    return ledger, deps, nas or FakeNas()


def test_the_reservation_is_the_estimate_over_the_full_request_body(gw_cfg):
    def script(body, n):
        if n == 1:
            return Reply([tool_use("toolu_01", "accounts", {"include_closed": False})], stop_reason="tool_use")
        return Reply([text("done")])
    ledger = SpyLedger(gw_cfg.ledger_path, gw_cfg.timezone, 1000, gw_cfg.max_cap_cents, gw_cfg.default_model)
    fake = FakeAnthropic(script)
    deps_kw = dict(config=gw_cfg, ledger=ledger, mac_key=load_or_create_key(gw_cfg.history_key_path),
                   stopped=lambda: False, now=lambda: NOW, detectors=[PatternDetector(), NameListDetector()])
    with serve_in_thread(fake.app()) as url:
        async def go():
            client = anthropic.AsyncAnthropic(api_key="test-key-not-real", base_url=url, max_retries=0)
            try:
                return await run_turn(start_frame(), FakeNas(), TurnDeps(client=client, **deps_kw))
            finally:
                await client.close()
        asyncio.run(go())
    price = reserve_price(gw_cfg, gw_cfg.default_model)
    for body, reserved in zip(fake.requests, ledger.reserved):
        sent = {k: v for k, v in body.items() if k not in ("stream", "fallbacks")}
        assert reserved == estimate_nd(len(canonical(sent)), MAX_TOKENS, price)
        assert sent == request_body(sent["model"], sent["messages"])
    assert len(ledger.reserved) == 2 and ledger.reserved[1] > ledger.reserved[0]


def test_a_record_that_never_comes_back_releases_the_reservation(gw_cfg):
    class Silent(FakeNas):
        async def send(self, frame):
            self.queue = self.queue or asyncio.Queue()
            self.frames.append(frame)
    ledger, deps, nas = run_stub(gw_cfg, lambda **kw: pytest.fail("nothing may be sent"), Silent(), reply_timeout=0.05)
    done = asyncio.run(run_turn(start_frame(), nas, deps))
    assert done["stop"] == "dropped" and "sent" not in nas.kinds()
    assert statuses(ledger) == ["released"] and nothing_open(ledger, gw_cfg)


def test_a_failed_record_releases_the_reservation(gw_cfg):
    ledger, deps, nas = run_stub(gw_cfg, lambda **kw: pytest.fail("nothing may be sent"),
                                 FakeNas(record_reply="record_failed"))
    assert asyncio.run(run_turn(start_frame(), nas, deps))["stop"] == "record_failed"
    assert statuses(ledger) == ["released"] and nothing_open(ledger, gw_cfg)


@pytest.mark.parametrize("status, kind", [(429, "rate_limit_error"), (500, "api_error"), (401, "authentication_error")])
def test_api_errors_release_the_reservation(gw_cfg, status, kind):
    ledger = turn(gw_cfg, lambda b, n: Reply([], status=status, error_type=kind))[3]
    assert statuses(ledger) == ["released"] and nothing_open(ledger, gw_cfg)


def test_a_dead_connection_releases_the_reservation(gw_cfg):
    def refuse(**kw):
        raise anthropic.APIConnectionError(request=None)
    ledger, deps, nas = run_stub(gw_cfg, refuse)
    assert asyncio.run(run_turn(start_frame(), nas, deps))["stop"] == "api_error"
    assert statuses(ledger) == ["released"] and nothing_open(ledger, gw_cfg)


def test_a_turn_cancelled_after_sending_counts_in_full(gw_cfg):
    entered = asyncio.Event()

    async def hang():
        entered.set()                           # the request is in flight (sent and recorded just before it)
        await asyncio.Event().wait()
    ledger, deps, nas = run_stub(gw_cfg, lambda **kw: Stream(enter=hang))

    async def go():
        task = asyncio.create_task(run_turn(start_frame(), nas, deps))
        await asyncio.wait_for(entered.wait(), CEILING)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(go())
    assert statuses(ledger) == ["settled"] and nothing_open(ledger, gw_cfg)
    # The NAS is told what the cancelled request was charged, so the turn shows its real cost (final review, 1).
    (done,) = nas.of("done")
    assert (done["stop"], done["requests"], done["cost_nd"]) == ("cancelled", 1, ledger.reserved[0])


def test_an_unexpected_exception_while_sending_counts_in_full(gw_cfg):
    async def boom():
        raise RuntimeError("the SDK broke")
    ledger, deps, nas = run_stub(gw_cfg, lambda **kw: Stream(enter=boom))
    with pytest.raises(RuntimeError):
        asyncio.run(run_turn(start_frame(), nas, deps))
    assert statuses(ledger) == ["settled"] and nothing_open(ledger, gw_cfg)


def test_a_socket_that_dies_before_the_sent_frame_releases_the_reservation(gw_cfg):
    class Dies(FakeNas):
        async def send(self, frame):
            await super().send(frame)
            if frame["type"] == "sent":
                raise ConnectionError("the NAS closed the socket")
    ledger, deps, nas = run_stub(gw_cfg, lambda **kw: pytest.fail("nothing may be sent"), Dies())
    with pytest.raises(ConnectionError):
        asyncio.run(run_turn(start_frame(), nas, deps))
    assert statuses(ledger) == ["released"] and nothing_open(ledger, gw_cfg)


def test_a_stream_that_drops_before_its_first_event_counts_in_full(gw_cfg):
    def die():
        raise anthropic.APIConnectionError(request=None)
    ledger, deps, nas = run_stub(gw_cfg, lambda **kw: Stream(events=[die]))
    assert asyncio.run(run_turn(start_frame(), nas, deps))["stop"] == "api_error"
    assert statuses(ledger) == ["settled"] and nothing_open(ledger, gw_cfg)


def test_a_stream_that_fails_midway_is_charged_in_full_and_nothing_stays_reserved(gw_cfg):
    def die():
        raise anthropic.APIConnectionError(request=None)
    ledger, deps, nas = run_stub(gw_cfg, lambda **kw: Stream(events=[lambda: type("E", (), {"type": "ping"})(), die]))
    done = asyncio.run(run_turn(start_frame(), nas, deps))
    assert done["stop"] == "api_error"
    assert statuses(ledger) == ["settled"] and nothing_open(ledger, gw_cfg)
    assert ledger.state(NOW, gw_cfg)["spent_cents"] > 0
    assert (done["requests"], done["cost_nd"]) == (1, ledger.reserved[0])      # the done frame carries the charge


# --- Turn logs carry counts and ids through the scrub filter (final review, finding 8) -------------------------------
def _gateway_log(caplog):
    import logging

    from steward_gateway.logs import ScrubFilter
    caplog.handler.addFilter(ScrubFilter())
    caplog.set_level(logging.INFO, logger="steward_gateway")
    return caplog


def test_a_turns_logs_carry_its_counts_and_ids(gw_cfg, caplog):
    import re
    _gateway_log(caplog)
    done = turn(gw_cfg, lambda b, n: Reply([text("You spent $812.40 on dining.")]))[0]
    out = caplog.text
    assert "[values not logged]" not in out
    assert re.search(r"turn\.request turn_id=t_00000000000000a1 step=1 model=claude-opus-5-5 input_tokens=\d+ "
                     r"cache_write_tokens=\d+ cache_read_tokens=\d+ output_tokens=\d+ cost_nd=\d+", out), out
    assert re.search(rf"turn\.done turn_id=t_00000000000000a1 stop=end_turn requests=1 cost_nd={done['cost_nd']} "
                     r"ms=\d+", out), out
    assert "812" not in out and "dining" not in out


def test_a_failed_request_logs_its_status_and_request_id(gw_cfg, caplog):
    _gateway_log(caplog)
    turn(gw_cfg, lambda b, n: Reply([], status=500))
    assert ("turn.request_failed turn_id=t_00000000000000a1 step=1 status=500 request_id=req_fake_1 "
            "error_type=InternalServerError") in caplog.text, caplog.text


def test_a_refused_request_logs_anthropics_reason(gw_cfg, caplog):
    _gateway_log(caplog)
    turn(gw_cfg, lambda b, n: Reply([], status=400, error_type="invalid_request_error",
                                    error_message="messages.0.content:  Input should be a valid\nlist"))
    assert ("error_type=BadRequestError error_kind=invalid_request_error "
            "error_message=messages.0.content: Input should be a valid list") in caplog.text, caplog.text


def test_a_refusal_with_another_body_logs_no_reason(gw_cfg, caplog):
    _gateway_log(caplog)
    turn(gw_cfg, lambda b, n: Reply([], status=400, error_body=["not", "an", "error"]))
    assert "turn.request_failed" in caplog.text and "error_type=BadRequestError" in caplog.text
    assert "error_message" not in caplog.text and "error_kind" not in caplog.text, caplog.text


def test_a_dead_connection_logs_its_error_type(gw_cfg, caplog):
    def refuse(**kw):
        raise anthropic.APIConnectionError(request=None)
    _gateway_log(caplog)
    ledger, deps, nas = run_stub(gw_cfg, refuse)
    asyncio.run(run_turn(start_frame(), nas, deps))
    assert "turn.request_failed turn_id=t_00000000000000a1 step=1 error_type=APIConnectionError" in caplog.text


def test_models_that_dont_load_log_the_error_type(caplog, monkeypatch):
    import sys
    from types import ModuleType
    _gateway_log(caplog)
    broken = ModuleType("gliner")                      # importing GLiNER from it raises ImportError
    monkeypatch.setitem(sys.modules, "gliner", broken)
    monkeypatch.setitem(sys.modules, "torch", ModuleType("torch"))
    from steward_gateway.redact import load_real_detectors
    assert load_real_detectors()[0] == []
    assert "redaction.load_failed error_type=ImportError" in caplog.text, caplog.text


def test_a_start_on_another_protocol_is_refused_and_nothing_is_recorded(gw_cfg):
    """Ask about this P11: an older NAS's frame ends bad_request before redaction, recording or any request."""
    frame, fake, nas, _, _ = turn(gw_cfg, lambda body, n: Reply([text("never sent")]),
                                  start={**start_frame(), "protocol": PROTOCOL - 1})
    assert (frame["stop"], frame["line"]) == ("bad_request", "Claude couldn't answer just now.")
    assert nas.kinds() == ["done"] and fake.requests == []


def test_the_about_line_is_in_the_context_part_and_recorded_first(gw_cfg):
    start = start_frame()
    start["context"] = {**start["context"], "about": {"kind": "category", "category": "dining", "month": "2026-10"}}
    frame, fake, nas, _, _ = turn(gw_cfg, lambda body, n: Reply([text("You spent $812.40.")]), start=start)
    assert frame["stop"] == "end_turn"
    system = next(p for p in nas.of("record")[0]["parts"] if p["part"] == "system")
    assert system["payload"]["text"].endswith(" It's about Eating out (category dining) in October 2026.")
    assert fake.requests[0]["messages"][1] == {"role": "system", "content": system["payload"]["text"]}


@pytest.mark.parametrize("about, identifier", [
    ({"kind": "care", "scenario": 2, "level": "memory_care"}, ("Sunrise Memory Care", True)),
    ({"kind": "care"}, ("Sunrise Memory Care", True)),
    ({"kind": "category", "category": "entertainment", "month": "2026-10"}, ("Fun", False)),
], ids=["care-scenario", "care", "category"])
def test_an_identifier_that_shares_the_about_lines_words_doesnt_stop_the_first_request(gw_cfg, about, identifier):
    """Argon follow-up 1: a parent book's contact "Sunrise Memory Care", or an account named "Fun", fingerprints words
    the about sentence is made of. The sentence is typed vocabulary (protocol.ABOUT), so its words aren't
    fingerprinted; the question still is."""
    from steward_boundary.fingerprint import hashes_for
    start = start_frame(question="Is this normal?", kind="parent" if about["kind"] == "care" else "household")
    start["context"] = {**start["context"], "about": about}
    start["fingerprints"] = {"salt": "2" * 32, "hashes": sorted(hashes_for([identifier], "2" * 32))}
    frame, fake, nas, _, _ = turn(gw_cfg, lambda body, n: Reply([text("Yes.")]), start=start)
    assert (frame["stop"], frame["check"], frame["path"]) == ("end_turn", None, None)
    start["question"] = f"Is {identifier[0].split()[0]} higher than usual?"
    frame, fake, nas, _, _ = turn(gw_cfg, lambda body, n: Reply([text("never sent")]), start=start)
    assert (frame["stop"], frame["check"], frame["path"]) == ("stopped_check", "fingerprint", "parts[0].text")
    assert fake.requests == []


def test_a_care_about_turn_that_calls_care_projection_completes(gw_cfg):
    """Review of Argon follow-up 1: the care_projection result carries the same words as enums (level "memory_care"),
    so with the contact "Sunrise Memory Care" the turn must get past the tool result too, not just the first request."""
    from steward_boundary.fingerprint import hashes_for
    from tests.gateway.test_checks import CARE_RESULT
    start = start_frame(question="How long does the money last?", kind="parent")
    start["context"] = {**start["context"], "about": {"kind": "care", "scenario": 1, "level": "memory_care"}}
    start["fingerprints"] = {"salt": "2" * 32, "hashes": sorted(hashes_for([("Sunrise Memory Care", True)], "2" * 32))}
    nas = FakeNas(results=lambda calls: [{"id": c["id"], "name": c["name"], "content": CARE_RESULT, "error": None}
                                         for c in calls])

    def script(body, n):
        if n == 1:
            return Reply([tool_use("toolu_c1", "care_projection", {})], stop_reason="tool_use")
        return Reply([text("It lasts until 2031.")])
    frame, fake, nas, _, _ = turn(gw_cfg, script, start=start, nas=nas)
    assert (frame["stop"], frame["check"], frame["path"]) == ("end_turn", None, None)
    assert len(fake.requests) == 2
    assert [p["part"] for p in nas.of("record")[1]["parts"]] == ["tool_result"]


# --- When a request may have gone out (Argon, on the anthropic 2.x bump): released only when certainly nothing was
# sent, settled at the reservation otherwise. These run the real SDK, httpx2 and httpcore2; only a network wait or
# failure is stood in for.

def cancel_turn(cfg, script, ready, release=None):
    """A turn on the gateway's own client, cancelled (Stop, or the NAS gone) once `ready()` returns. `release` frees a
    fake answer the script is holding; it runs before the fake server stops, because the server waits for every
    handler to finish and refuses to leave one still running (tests/servers.serve_in_thread)."""
    fake, nas = FakeAnthropic(script), FakeNas()
    ledger = SpyLedger(cfg.ledger_path, cfg.timezone, 1000, cfg.max_cap_cents, cfg.default_model)
    with serve_in_thread(fake.app()) as url:
        async def go():
            client = make_client(replace(cfg, anthropic_base_url=url), "test-key-not-real")
            deps = TurnDeps(config=cfg, ledger=ledger, client=client, mac_key=load_or_create_key(cfg.history_key_path),
                            stopped=lambda: False, now=lambda: NOW, detectors=[PatternDetector(), NameListDetector()])
            try:
                task = asyncio.create_task(run_turn(start_frame(), nas, deps))
                await asyncio.wait_for(ready(), CEILING)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            finally:
                await client.close()
        try:
            asyncio.run(go())
        finally:
            if release:
                release()
    return fake, nas, ledger


def network_wait(monkeypatch, where="connect"):
    """The request's connect (DNS and TCP) or its wait for a pooled connection hangs. Returns an event set once it
    does."""
    entered = asyncio.Event()

    async def hang(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()
    if where == "connect":
        monkeypatch.setattr(httpcore2._backends.auto.AutoBackend, "connect_tcp", hang)
    else:
        monkeypatch.setattr(httpcore2._async.connection_pool.AsyncPoolRequest, "wait_for_connection", hang)
    return entered


def rename_the_trace(monkeypatch):
    """httpcore2's trace as a later version might send it: the event steward_gateway.sending waits for never fires."""
    real = httpcore2._trace.Trace.atrace

    async def renamed(self, name, info):
        await real(self, name.replace("send_request_headers", "write_request_headers"), info)
    monkeypatch.setattr(httpcore2._trace.Trace, "atrace", renamed)


@pytest.mark.parametrize("where", ["connect", "pool"])
def test_a_cancel_before_the_request_goes_out_releases_the_reservation(gw_cfg, monkeypatch, where):
    """Argon 2.1: a Stop while the client is still resolving and connecting (connect_tcp does both) or waiting for a
    pooled connection has written nothing, so it releases and the done frame carries no cost. That is believed once
    this process has seen the client's trace work, as it has after its first request."""
    monkeypatch.setattr(sending, "_traced", True)
    entered = network_wait(monkeypatch, where)
    fake, nas, ledger = cancel_turn(gw_cfg, lambda b, n: Reply([text("never sent")]), entered.wait)
    assert fake.requests == [] and statuses(ledger) == ["released"] and nothing_open(ledger, gw_cfg)
    (done,) = nas.of("done")
    assert (done["stop"], done["requests"], done["cost_nd"]) == ("cancelled", 1, 0)


def test_a_request_that_goes_out_shows_the_trace_works(gw_cfg, monkeypatch):
    monkeypatch.setattr(sending, "_traced", False)
    assert turn(gw_cfg, lambda b, n: Reply([text("ok")]))[0]["stop"] == "end_turn"
    assert sending._traced is True


def test_until_the_trace_is_seen_working_a_cancel_before_sending_counts_in_full(gw_cfg, monkeypatch):
    """Review finding 1(b): before any request has shown the trace works, "nothing written yet" isn't believed."""
    monkeypatch.setattr(sending, "_traced", False)
    entered = network_wait(monkeypatch)
    fake, nas, ledger = cancel_turn(gw_cfg, lambda b, n: Reply([text("never sent")]), entered.wait)
    assert fake.requests == [] and statuses(ledger) == ["settled"] and nothing_open(ledger, gw_cfg)


def test_a_trace_that_never_fires_fails_closed(gw_cfg, monkeypatch):
    """Review finding 1, the reviewer's probe: with the trace event renamed, a Stop after Anthropic has the request
    settles, as it would have before the client said anything."""
    rename_the_trace(monkeypatch)
    monkeypatch.setattr(sending, "_traced", False)
    arrived, answer = threading.Event(), threading.Event()
    try:
        fake, nas, ledger = cancel_turn(gw_cfg, lambda b, n: Reply([text("late")], arrived=arrived, answer=answer),
                                        lambda: asyncio.to_thread(arrived.wait, CEILING), release=answer.set)
    finally:
        answer.set()
    assert len(fake.requests) == 1 and statuses(ledger) == ["settled"] and nothing_open(ledger, gw_cfg)


def test_an_answered_request_counts_in_full_whatever_the_client_says(gw_cfg, monkeypatch):
    """Review finding 1(a): once Anthropic has answered, a broken stream is billable even when the client's trace and
    response hook both failed to report (the attempt still reads "waiting", and the trace was once seen working)."""
    rename_the_trace(monkeypatch)
    monkeypatch.setattr(sending, "_traced", True)

    async def silent(response):
        pass
    monkeypatch.setattr(sending, "_on_response", silent)
    done, fake, nas, ledger, _ = turn(gw_cfg, lambda b, n: Reply([], sse=HALF[:3], cut=True))
    assert (done["stop"], done["cost_nd"]) == ("dropped", ledger.reserved[0]) and statuses(ledger) == ["settled"]


def test_a_cancel_after_the_request_went_out_counts_in_full(gw_cfg):
    """The other side of 2.1: once the request has reached Anthropic and no answer has come, a Stop may still be
    billed, so it settles at the reservation. This also fails if the client stops saying when it starts writing."""
    arrived, answer = threading.Event(), threading.Event()
    try:
        fake, nas, ledger = cancel_turn(gw_cfg, lambda b, n: Reply([text("late")], arrived=arrived, answer=answer),
                                        lambda: asyncio.to_thread(arrived.wait, CEILING), release=answer.set)
    finally:
        answer.set()
    assert len(fake.requests) == 1 and statuses(ledger) == ["settled"] and nothing_open(ledger, gw_cfg)
    (done,) = nas.of("done")
    assert (done["stop"], done["requests"], done["cost_nd"]) == ("cancelled", 1, ledger.reserved[0])


def test_no_pooled_connection_in_time_releases_the_reservation(gw_cfg, monkeypatch):
    """Argon 2.2: httpx2.PoolTimeout (no connection acquired) arrives as APITimeoutError, and nothing was sent."""
    async def full(*args, **kwargs):
        raise httpcore2.PoolTimeout("no connection in time")
    monkeypatch.setattr(httpcore2._async.connection_pool.AsyncPoolRequest, "wait_for_connection", full)
    done, fake, _, ledger, _ = turn(gw_cfg, lambda b, n: Reply([text("never sent")]))
    assert done["stop"] == "api_error" and fake.requests == []
    assert statuses(ledger) == ["released"] and done["cost_nd"] == 0


@pytest.mark.parametrize("cause, status", [(httpx2.PoolTimeout("pool"), "released"),
                                           (httpx2.ConnectTimeout("connect"), "released"),
                                           (httpx2.ReadTimeout("read"), "settled")], ids=["pool", "connect", "read"])
def test_a_timeout_releases_only_when_it_never_connected(gw_cfg, cause, status):
    """The same rule from the SDK's error alone, for a client that can't say when it started writing."""
    async def enter():
        raise anthropic.APITimeoutError(request=httpx2.Request("POST", "https://x")) from cause
    ledger, deps, nas = run_stub(gw_cfg, lambda **kw: Stream(enter=enter))
    assert asyncio.run(run_turn(start_frame(), nas, deps))["stop"] == "api_error"
    assert statuses(ledger) == [status] and nothing_open(ledger, gw_cfg)


HALF = sse(Reply([text("Half an answer, then more of it")]))     # message_start, block start, deltas, ..., message_stop


def sse_error(kind):
    return f'event: error\ndata: {{"type": "error", "error": {{"type": "{kind}", "message": "fake"}}}}\n\n'


def event(kind, data):
    return f"event: {kind}\ndata: {json.dumps({'type': kind, **data})}\n\n"


BAD_TOOL_INPUT = [event("content_block_start", {"index": 0, "content_block": {
                      "type": "tool_use", "id": "toolu_01", "name": "accounts", "input": {}}}),
                  event("content_block_delta", {"index": 0, "delta": {"type": "input_json_delta",
                                                                      "partial_json": "]]"}}),
                  event("content_block_stop", {"index": 0})] + HALF[-2:]
NO_SUCH_BLOCK = [event("content_block_delta", {"index": 3, "delta": {"type": "text_delta", "text": "x"}})]


@pytest.mark.parametrize("reply, stop", [
    (Reply([], sse=HALF[:3], cut=True), "dropped"),
    (Reply([], sse=HALF[:2], cut=True), "api_error"),
    (Reply([], sse=HALF[:3]), "dropped"),
    (Reply([], sse=HALF[:-1]), "dropped"),
    (Reply([], sse=HALF[1:]), "dropped"),
    (Reply([], sse=HALF[1:2] + HALF[-1:]), "api_error"),
    (Reply([], sse=[]), "api_error"),
    (Reply([], sse=HALF[:1] + ["event: content_block_start\ndata: {not json\n\n"]), "api_error"),
    (Reply([], sse=HALF[:1] + [b'event: content_block_start\ndata: {"type": "\xff"}\n\n']), "api_error"),
    (Reply([], sse=HALF[:1] + BAD_TOOL_INPUT), "api_error"),
    (Reply([], sse=HALF[:1] + NO_SUCH_BLOCK), "api_error"),
    (Reply([], sse=HALF[:3] + [sse_error("api_error")]), "dropped"),
    (Reply([], sse=HALF[:2] + [sse_error("api_error")]), "api_error"),
    (Reply([], sse=HALF[:2] + [sse_error("overloaded_error")]), "busy"),
    (Reply([], sse=HALF[:3] + [sse_error("overloaded_error")]), "dropped"),
    (Reply([], sse=HALF[:2] + [sse_error("rate_limit_error")]), "busy"),
    (Reply([], sse=HALF[:-2] + HALF[-1:]), "dropped"),
    (Reply([], sse=HALF[:1] + HALF[-1:]), "api_error"),
], ids=["dropped-mid-answer", "dropped-before-text", "no-message-stop", "only-message-stop-missing",
        "no-message-start", "no-message-start-no-text", "empty", "bad-json", "bad-utf8", "bad-tool-input",
        "no-such-block", "error-event-mid-answer", "error-event-before-text", "overloaded-before-text",
        "overloaded-mid-answer", "rate-limited-before-text", "no-message-delta", "no-message-delta-no-text"])
def test_a_stream_that_breaks_after_the_answer_began_ends_with_a_done_and_counts_in_full(gw_cfg, reply, stop):
    """Argon 3.1: whatever breaks the stream once Anthropic has answered 200, the turn ends with a done frame the NAS
    can show (not a socket closed without one, which reads as the gateway gone), and the request is charged its full
    reservation: it was sent, and no final usage came back. With some of the answer already on the NAS it ends
    "dropped" (the answer is incomplete), with none "api_error"; a mid-stream overloaded_error is busy, like a 529,
    until answer text has shown. A message_stop with no message_delta has no stop_reason and no final usage."""
    done, fake, nas, ledger, _ = turn(gw_cfg, lambda b, n: reply)
    assert (done["stop"], done["requests"], done["cost_nd"]) == (stop, 1, ledger.reserved[0])
    assert statuses(ledger) == ["settled"] and nothing_open(ledger, gw_cfg)
    assert nas.kinds()[-1] == "done" and "assistant" not in nas.kinds()


@pytest.mark.parametrize("second, stop", [
    (Reply([], sse=HALF[:2], cut=True), "dropped"),
    (Reply([], sse=HALF[:2] + [sse_error("overloaded_error")]), "dropped"),
    (Reply([], status=529, error_type="overloaded_error"), "dropped"),
], ids=["cut", "overloaded-event", "overloaded-status"])
def test_a_break_in_a_later_step_after_earlier_text_ends_dropped(gw_cfg, second, stop):
    """The NAS shows every step's text, so once step 1 has put some answer on screen, a break in step 2 before any of
    its own text leaves an incomplete answer: "dropped", not "api_error" or "busy"."""
    def script(body, n):
        if n == 1:
            return Reply([text("Let me look at your accounts."), tool_use("toolu_01", "accounts", {})],
                         stop_reason="tool_use")
        return second
    done, fake, nas, ledger, _ = turn(gw_cfg, script)
    assert (done["stop"], done["requests"]) == (stop, 2) and len(nas.of("text")) >= 1
    assert nothing_open(ledger, gw_cfg)


def test_a_turn_with_no_answer_text_before_a_later_break_keeps_its_own_outcome(gw_cfg):
    def script(body, n):
        if n == 1:
            return Reply([tool_use("toolu_01", "accounts", {})], stop_reason="tool_use")
        return Reply([], sse=HALF[:2] + [sse_error("overloaded_error")])
    done, _, nas, _, _ = turn(gw_cfg, script)
    assert (done["stop"], done["requests"]) == ("busy", 2) and nas.of("text") == []


@pytest.mark.parametrize("error", [
    httpx2.ReadTimeout("read"), httpx2.ReadError("read"), httpx2.RemoteProtocolError("peer closed"),
    anthropic.APIResponseValidationError(httpx2.Response(200, request=httpx2.Request("POST", "https://x")), None),
    anthropic.APIError("an error with no status", httpx2.Request("POST", "https://x"), body=None),
], ids=lambda e: type(e).__name__)
def test_a_mid_stream_error_the_fake_server_cant_raise_ends_with_a_done(gw_cfg, error):
    def die():
        raise error
    ledger, deps, nas = run_stub(gw_cfg, lambda **kw: Stream(events=[lambda: type("E", (), {"type": "ping"})(), die]))
    done = asyncio.run(run_turn(start_frame(), nas, deps))
    assert (done["stop"], done["requests"], done["cost_nd"]) == ("api_error", 1, ledger.reserved[0])
    assert statuses(ledger) == ["settled"] and nothing_open(ledger, gw_cfg)


def test_a_connection_cut_after_message_stop_keeps_the_whole_answer(gw_cfg):
    """Review finding 4: everything up to message_stop arrived, so the answer and its usage are whole. It ends as
    usual and is charged what it used, not the reservation."""
    done, fake, nas, ledger, _ = turn(gw_cfg, lambda b, n: Reply([text("A whole answer.")], cut=True))
    assert done["stop"] == "end_turn" and 0 < done["cost_nd"] < ledger.reserved[0]
    assert statuses(ledger) == ["settled"] and nothing_open(ledger, gw_cfg)
    (assistant,) = nas.of("assistant")
    assert assistant["message"]["content"] == [{"type": "text", "text": "A whole answer."}]
