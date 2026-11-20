"""The WebSocket frames between the NAS and the gateway for one turn (spec 3.2), their schemas, the stop codes a turn
can end with and the plain line for each (spec 10). The NAS opens the socket; the gateway only answers inside it."""
from __future__ import annotations

from steward_boundary import vocab as V
from steward_boundary.aliases import BRACKETS
from steward_boundary.schema import ALIAS, DATE, INT, MONTH, arr, enum, nullable, obj
from steward_boundary.store_rows import TICKS
from steward_boundary.tools import ERRORS
from steward_boundary.validate import validate

HEX32 = {"type": "string", "pattern": r"^[0-9a-f]{32}$"}
HEX64 = {"type": "string", "pattern": r"^[0-9a-f]{64}$"}
TURN_ID = {"type": "string", "pattern": r"^t_[0-9a-f]{16}$"}
# What a question was asked about (Ask about this, P5): one of these strict objects, each field an alias, an enum, an
# ISO date or month, or a whole number, and no extra keys, so no free text can ride along. Care is a scenario (its
# position and level, never its wording) or the projection as a whole.
ABOUT = {"anyOf": [
    obj({"kind": enum(("account",)), "account": ALIAS}),
    obj({"kind": enum(("category",)), "category": enum(V.CATEGORIES), "month": MONTH}),
    obj({"kind": enum(("plan",))}),
    obj({"kind": enum(("transaction",)), "date": DATE, "amount_cents": INT, "category": enum(V.CATEGORIES),
         "account": ALIAS}),
    obj({"kind": enum(("flag",)), "check": enum(V.FLAG_CHECKS), "date": DATE}),
    obj({"kind": enum(("care",)), "scenario": INT, "level": enum(V.CARE_LEVELS)}),
    obj({"kind": enum(("care",))}),
]}
CONTEXT = obj({"today": DATE, "book_kind": enum(V.BOOK_KINDS), "asker": ALIAS, "about": ABOUT}, optional=("about",))
STORED = obj({"message": {"type": "object"}, "mac": HEX64})
# The aliases and bracket tokens the NAS put into this turn's question (Task 2.5): the only ones the gateway protects.
ISSUED_TOKEN = {"anyOf": [ALIAS, enum(BRACKETS)]}
# The hashes of the book's identifiers (spec 4.5, check 4). `approvable` holds the payee and institution names, kept
# apart so an approved merchant_rows part can carry them (plan B ruling 3); every other part is checked against both.
FINGERPRINTS = {"type": "object",
                "properties": {"salt": HEX32, "hashes": arr(HEX64, 200_000), "approvable": arr(HEX64, 200_000)},
                "required": ["salt", "hashes"], "additionalProperties": False}
START = obj({"type": enum(("start",)), "protocol": INT, "turn_id": TURN_ID, "book_id": INT, "user_id": INT,
             "question": {"type": "string", "maxLength": 4000}, "context": CONTEXT, "history": arr(STORED, 4000),
             "fingerprints": FINGERPRINTS, "issued": arr(ISSUED_TOKEN, 2000)})
RECORDED = obj({"type": enum(("recorded",)), "seq": INT})
RECORD_FAILED = obj({"type": enum(("record_failed",)), "seq": INT})
# `ticked`: the rows and payees a manager ticked on an approved merchant_detail card, read by the gateway beside the
# content and never sent on (steward_boundary.store_rows, public issue #53).
TOOL_RESULT = obj({"id": {"type": "string", "pattern": r"^[A-Za-z0-9_-]{1,100}$"},
                   "name": {"type": "string", "maxLength": 40},
                   "content": nullable({"type": "object"}), "error": nullable(enum(ERRORS)), "ticked": TICKS},
                  optional=("ticked",))
TOOL_RESULTS = obj({"type": enum(("tool_results",)), "seq": INT, "results": arr(TOOL_RESULT, 64)})
CANCEL_REASONS = ("not_a_member", "chat_off", "approval_timeout", "stopped")
CANCEL = obj({"type": enum(("cancel",)), "reason": enum(CANCEL_REASONS)})

STOPS = frozenset({"end_turn", "max_tokens", "refusal", "steps", "cap", "cap_mid", "stopped_check", "record_failed",
                   "busy", "api_error", "not_set_up", "privacy_not_ready", "stopped_by_gateway", "cancelled",
                   "dropped", "bad_request"})
# Plain names for the check that fired, used in book events (shared by the gateway and the NAS).
CHECK_WORDS = {"schema": "shape", "text": "number", "model": "name", "fingerprint": "known-identifier",
               "history": "history"}
# {gateway}: the NAS fills it (steward_boundary/naming.py).
LINES = {
    "max_tokens": "This answer was cut short.",
    "refusal": "Claude declined to answer this.",
    "steps": "I stopped after 8 steps. Try asking about one part of this at a time.",
    "cap_mid": "Stopped: this month's budget is used up.",
    "stopped_check": ("Steward stopped this answer because something that looked like personal information was "
                      "about to be sent. Nothing was sent."),
    "record_failed": "Steward couldn't record what would be sent, so it sent nothing.",
    "busy": "Claude is busy. Try again in a minute.",
    "api_error": "Claude couldn't answer just now.",
    "not_set_up": "Chat isn't set up on {gateway} yet.",
    "privacy_not_ready": "Chat is paused because {gateway}'s privacy check isn't ready.",
    "stopped_by_gateway": "Chat is turned off on {gateway}.",
    "dropped": "{gateway} stopped answering, so this answer is incomplete.",
    "away": "Chat needs {gateway}, which isn't answering. It may be asleep or turned off.",
    "bad_request": "Claude couldn't answer just now.",
}


def check_frame(schema: dict, frame: object) -> list[str]:
    return validate(schema, frame)


def issued(start: dict) -> frozenset[str]:
    """The tokens a checked START frame says the NAS issued, with the asker's alias and, when the question was asked
    about an account or a transaction, its account alias (the per-turn context names both; Ask about this P12).
    Pass them as `issued` to the text rule and to aliases.protected_spans."""
    ctx = start["context"]
    about = ctx.get("about") or {}
    return frozenset(start["issued"]) | {ctx["asker"]} | ({about["account"]} if "account" in about else set())
