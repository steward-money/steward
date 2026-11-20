"""The outgoing checks (spec 4.5) on the parts a request adds: the question, the per-turn context and tool results.
1. Schema: each part's shape, and each tool result against its schema with no extra fields.
2. Text: every string passes the outgoing text rule; dates and months must be exact ISO.
3. Model: GLiNER-PII and Presidio again over the strings no schema type proves (the question, the context, free_text
   fields; ruling 3). A name, email, phone, address, number or date-of-birth hit stops the turn.
4. Fingerprints: every string's token windows against the book's salted identifier hashes, except an enum's and the about
   sentence's words (its digits are still read; _fingerprinted).
Recording first (check 5) is the loop's job. History is checked by MAC instead (ruling 2). A Stopped names the check
and the path, never the value. Before approved store rows become a part, mask_store_names replaces a person's name the
model finds in each unticked store or payee name with [name] (public issue #53)."""
from __future__ import annotations

import json
from collections.abc import Collection, Sequence
from dataclasses import dataclass

from steward_boundary import tools as T
from steward_boundary.canonical import canonical, sha256_hex
from steward_boundary.fingerprint import digit_runs, scan, tokens
from steward_boundary.normal import view
from steward_boundary.protocol import CHECK_WORDS  # noqa: F401 - re-exported; defined once in the boundary
from steward_boundary.schema import BOOL, enum, obj
from steward_boundary.spans import Edit, apply
from steward_boundary.store_rows import NAME, STORE_NAMES, store_names
from steward_boundary.text import typed_ok
from steward_boundary.validate import validate, walk_strings
from steward_gateway.mac import verify
from steward_gateway.redact import STOP_LABELS, Detector, detect_all

_TEXT = obj({"text": {"type": "string", "maxLength": 8000}})
_TOOL = obj({"tool_use_id": {"type": "string", "pattern": r"^[A-Za-z0-9_-]{1,100}$"},
             "name": enum([t.name for t in T.TOOLS]), "is_error": BOOL, "content": {"type": "object"}})
_ROWS = obj({"tool_use_id": {"type": "string", "pattern": r"^[A-Za-z0-9_-]{1,100}$"},
             "name": enum([T.MERCHANT]), "is_error": BOOL, "content": {"type": "object"}})
SHAPES = {"question": _TEXT, "system": _TEXT, "tool_result": _TOOL, "merchant_rows": _ROWS}
# In approved store rows a [name] guess on the store's own name doesn't stop the turn (plan B ruling 3).
STORE_FIELDS = (".merchant", ".name")
STORE_STOP_LABELS = STOP_LABELS - {"[name]"}


class Stopped(Exception):
    def __init__(self, check: str, path: str):
        super().__init__(f"the {check} check stopped the turn at {path}")
        self.check, self.path = check, path


@dataclass(frozen=True)
class Part:
    part: str
    payload: dict

    def sha256(self) -> str:
        return sha256_hex(self.payload)

    def size(self) -> int:
        return len(canonical(self.payload))


def _first(errs: list[str]) -> str:
    return errs[0].split(":")[0]


def _strings(base: str, p: Part) -> list[tuple[str, str | None, str]]:
    if p.part in ("question", "system"):
        return [(f"{base}.text", None, p.payload["text"])]
    schema = T.result_schema(p.payload["name"], p.payload["is_error"])
    return list(walk_strings(schema, p.payload["content"], f"{base}.content"))


def _fingerprinted(p: Part, path: str, kind: str | None, value: str, about: str | None) -> list[str]:
    """What of a string the fingerprints read (Argon follow-up 1 and its review).
    - An enum is proven against its fixed list by the schema check above, so its words ("memory_care", "large") are
      never an identifier: it isn't scanned. Every other kind is, a symbol or a date as much as free text.
    - The about sentence (steward_boundary.about) is built by this gateway from the checked START's protocol.ABOUT
      object: enums, an issued alias, a date and whole numbers. Its words ("memory care", "Fun", "school") are cut off
      the end of the system part, but its runs of digits (an amount, a date) are still scanned, as the scan reads any
      number, so an amount shaped like a known phone or account number stops. The rest of the part, and every other
      part, is scanned whole."""
    if kind == "enum":
        return []
    if about and p.part == "system" and path.endswith(".text") and value.endswith(" " + about):
        toks = tokens(about)
        return [value[:-len(about) - 1], *(" ".join(toks[i:j]) for i, j in digit_runs(toks))]
    return [value]


def check_parts(parts: Sequence[Part], *, detectors: Sequence[Detector], salt: str, hashes,
                approvable=frozenset(), issued: Collection[str] = frozenset(), about: str | None = None) -> None:
    """`issued` is protocol.issued(start) plus any bracket labels this gateway's own redaction inserted. Without it
    alias digits count toward the digit limit and the turn stops (fail closed). `approvable` holds the payee and
    institution hashes: every part is scanned against them too, except approved store rows (plan B ruling 3).
    `about` is about_text of the START's about object, when it has one: the text rule and the model pass still read
    it, and the fingerprints read only its digits (_fingerprinted)."""
    every = frozenset(hashes) | frozenset(approvable)
    for i, p in enumerate(parts):
        base = f"parts[{i}]"
        shape = SHAPES.get(p.part)
        if shape is None:
            raise Stopped("schema", base)
        errs = validate(shape, p.payload, base)
        if not errs and p.part == "merchant_rows" and p.payload["is_error"] is not False:
            errs = [f"{base}.is_error: store rows are a successful result"]
        if not errs and p.part in ("tool_result", "merchant_rows"):
            errs = validate(T.result_schema(p.payload["name"], p.payload["is_error"]), p.payload["content"],
                            f"{base}.content")
        if errs:
            raise Stopped("schema", _first(errs))
        strings = _strings(base, p)
        for path, kind, value in strings:
            if kind not in ("enum", "alias") and not typed_ok(kind, value, issued=issued):
                raise Stopped("text", path)
        store = p.part == "merchant_rows"
        for path, kind, value in strings:
            if kind not in (None, "free_text"):
                continue
            labels = STORE_STOP_LABELS if store and path.endswith(STORE_FIELDS) else STOP_LABELS
            if any(s.label in labels for s in detect_all(value, detectors, issued)):
                raise Stopped("model", path)
        hit = scan([(path, s) for path, kind, value in strings for s in _fingerprinted(p, path, kind, value, about)],
                   salt, frozenset(hashes) if store else every)
        if hit is not None:
            raise Stopped("fingerprint", hit)


_RESULT = T.result_schema(T.MERCHANT, False)["properties"]
# The longest each store name may be, from the result schema: a mask that runs past it sends the whole name as [name].
_LIMITS = {field: _RESULT[key]["items"]["properties"][field]["maxLength"] for key, field, _ in STORE_NAMES}


def _mask_name(value: str, detectors: Sequence[Detector], issued: Collection[str]) -> str:
    """`value` with each span the model tags as a person replaced by [name]. The spans are on the plain view, so when
    that view isn't `value` itself (an invisible character, a full-width letter) no edit can be laid on `value` and the
    whole name goes as [name]. Either way it is text taken out, never put in."""
    spans = [Edit(s.start, s.end, NAME, s.layer) for s in detect_all(value, detectors, issued) if s.label == NAME]
    if not spans:
        return value
    return apply(value, spans)[0] if view(value).plain == value else NAME


def mask_store_names(content: dict, ticked: Collection[str], detectors: Sequence[Detector],
                     issued: Collection[str] = frozenset()) -> dict:
    """Public issue #53: an approved merchant_detail result with each person's name the model finds in an unticked store
    or payee name replaced by [name]; a ticked one goes as the manager saw it. `ticked` is the result's own `ticked`
    from the tool_results frame (steward_boundary.store_rows), read here and never sent. Content of the wrong shape is
    returned as it came, for the schema check in check_parts to stop. Runs before the part is recorded, so the book's
    log, the conversation and Claude all get the masked rows; the NAS records them only when masked_from holds."""
    if not isinstance(content, dict) or validate(T.result_schema(T.MERCHANT, False), content):
        return content
    out = json.loads(json.dumps(content))
    seen: dict[str, str] = {}               # a store's rows share a name, so the model reads each name once
    for tick, item, field in store_names(out):
        if tick in ticked:
            continue
        value = item[field]
        if value not in seen:
            seen[value] = _mask_name(value, detectors, issued)
        masked = seen[value]
        item[field] = masked if len(masked) <= _LIMITS[field] else NAME
    return out


def verify_history(history: Sequence[dict], key: bytes) -> list[dict]:
    out = []
    for i, item in enumerate(history):
        if not verify(key, item["message"], item["mac"]):
            raise Stopped("history", f"history[{i}]")
        out.append(item["message"])
    return out
