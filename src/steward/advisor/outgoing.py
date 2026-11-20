"""The log of what was sent (spec 4.6). Each row is one part of one request, written before the gateway may send it;
payload is the canonical JSON whose SHA-256 the gateway computed, so the row is byte for byte what goes. Settings lists
one line per request (ruling 9); every member can read it, managers can export it, and nothing in the app edits or
deletes a row."""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import Engine, case, func, select, tuple_
from sqlalchemy.orm import Session

from steward import gateway_name
from steward.advisor.aliases import display
from steward.books.history import names_for
from steward.db.models import OUTGOING_PARTS, AdvisorOutgoing, ChatConversation, ChatTurn
from steward import clock
from steward.inventory.present import at_label, day_label, joined, size_label
from steward_boundary import tools as T
from steward_boundary.aliases import ALIAS_TOKEN_RE
from steward_boundary.canonical import canonical
from steward_boundary.store_rows import STORE_NAMES, masked_from, store_names

PAGE = 50
CHANNEL_LABELS = {"chat": "Chat", "mcp": "Claude Code", "prewarm": "Getting ready"}
PART_LABELS = {"question": "Question", "system": "Today's date and who is asking", "tool_result": "Result",
               "merchant_rows": "Store rows", "history_ref": "Earlier messages in this conversation"}
SYSTEM_ABOUT = "Today's date, who is asking and what it's about"      # the context part of a turn with an about (P10)


_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,39}")


def model_name(value: object) -> str | None:
    """The Claude model the gateway said a request goes to, when it looks like a model id (it fits the column);
    otherwise None (gate finding PRIV-15). A Claude Code lookup has none: Claude Code picks its own model."""
    return value if isinstance(value, str) and _MODEL.fullmatch(value) else None


class RecordRefused(ValueError):
    """A part to record wasn't understood or didn't match its hash. Nothing was written."""


@dataclass(frozen=True)
class Approval:
    """A manager's OK on one merchant_detail call's store rows (Task 6.5): who gave it, exactly the content the NAS sent
    with it (their ticks applied), and the rows and payees they ticked ("r0", "p2"; public issue #53)."""
    user_id: int
    content: dict
    ticked: frozenset[str] = frozenset()


def as_approved(content: object, ok: Approval) -> bool:
    """Whether `content` is the approved rows: byte for byte, or with some text of an unticked store or payee name
    replaced by [name] (masked_from), which the gateway does to a person's name its model finds (public issue #53). A
    ticked name, and every other field, must be exactly as approved. Text taken out passes; text put in never does."""
    if not isinstance(content, dict):
        return False
    if canonical(content) == canonical(ok.content):
        return True
    shape = [(k, len(ok.content.get(k) or [])) for k, _, _ in STORE_NAMES]
    if any(not isinstance(content.get(k), list) or len(content[k]) != n for k, n in shape):
        return False
    restored = json.loads(json.dumps(content))
    approved = dict(((t, f), item) for t, item, f in store_names(ok.content))
    for tick, item, field in store_names(restored):
        before = approved[(tick, field)].get(field)
        if tick not in ok.ticked and isinstance(item, dict) and masked_from(item.get(field), before):
            item[field] = before
    return canonical(restored) == canonical(ok.content)


def _row(*, user_id, conversation_id, turn_id, seq, channel, part, raw: bytes, model, now,
         approved_by: int | None = None) -> AdvisorOutgoing:
    return AdvisorOutgoing(at=now, user_id=user_id, conversation_id=conversation_id, turn_id=turn_id, seq=seq,
                           channel=channel, part=part, payload=raw.decode("utf-8"),
                           sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw), model=model,
                           approved_by=approved_by)


def _store_rows(p: dict) -> bool:
    """Whether a part carries store rows, which need a manager's OK (spec 4.4). Keyed on the content as well as the
    label (preflight ruling P1): a successful merchant_detail result with rows or payees needs it whatever its label;
    an empty one, which goes back with no card (plan B ruling 5), doesn't."""
    payload = p["payload"]
    if payload.get("name") == T.MERCHANT and not payload.get("is_error"):
        content = payload.get("content")
        return not isinstance(content, dict) or bool(content.get("rows") or content.get("payees"))
    return p["part"] == "merchant_rows"


def record_parts(db: Session, *, user_id: int, conversation_id: int | None, turn_id: str, seq: int, channel: str,
                 model: str | None, parts: list[dict], now: datetime,
                 approved: Mapping[str, Approval] | None = None) -> list[int]:
    """Writes each part before the gateway may send it. `approved` maps a merchant_detail call id to its Approval
    (Task 6.5): store rows nobody approved, or rows that differ from the approved ones in any byte other than a name
    the gateway masked in an unticked row (as_approved), are refused, so they are never sent."""
    rows = []
    for p in parts:
        if p.get("part") not in OUTGOING_PARTS or not isinstance(p.get("payload"), dict):
            raise RecordRefused("A part wasn't understood.")
        raw = canonical(p["payload"])
        if hashlib.sha256(raw).hexdigest() != p.get("sha256") or len(raw) != p.get("bytes"):
            raise RecordRefused("A part didn't match its hash.")
        approved_by = None
        if _store_rows(p):          # store rows are recorded, and so sent, only with a manager's OK (spec 4.4)
            ok = (approved or {}).get(p["payload"].get("tool_use_id"))
            if ok is None:
                raise RecordRefused("Store rows nobody approved aren't recorded or sent.")
            approved_by = ok.user_id
            if not as_approved(p["payload"].get("content"), ok):
                raise RecordRefused("Store rows that differ from the approved ones aren't recorded or sent.")
        rows.append(_row(user_id=user_id, conversation_id=conversation_id, turn_id=turn_id, seq=seq, channel=channel,
                         part=p["part"], raw=raw, model=model_name(model), now=now, approved_by=approved_by))
    db.add_all(rows)
    db.flush()
    return [r.id for r in rows]


def record_payload(db: Session, *, user_id: int, conversation_id: int | None, turn_id: str, seq: int, channel: str,
                   part: str, payload: dict, now: datetime, model: str | None = None) -> AdvisorOutgoing:
    row = _row(user_id=user_id, conversation_id=conversation_id, turn_id=turn_id, seq=seq, channel=channel, part=part,
               raw=canonical(payload), model=model_name(model), now=now)
    db.add(row)
    db.flush()
    return row


WHAT_FILTERS = ("question", "results", "store_rows", "prewarm")
FILTER_PART = {"question": "question", "results": "tool_result", "store_rows": "merchant_rows"}
CHECKS = ("The shape matches what Steward may send", "No number longer than 4 digits",
          "{gateway}'s privacy check found no names", "Nothing matches a name or number this book knows")
# Approved store rows skip the store and payee names in the last two checks (plan B ruling 3), so the log says so
# rather than ticking checks that didn't run on them (final review, Task 9).
STORE_CHECKS = CHECKS[:2] + (
    "{gateway}'s privacy check found no names other than the store and payee names a manager approved",
    "Nothing matches a person's name or a number this book knows. Store and payee names were skipped because a "
    "manager approved them")
PREWARM_WHAT = "Steward's instructions to Claude, no household data"


def _plural(n: int, one: str) -> str:
    return f"{n} {one}{'' if n == 1 else 's'}"


def _what(info: dict, channel: str) -> tuple[str, str]:
    """What one request sent, in plain words, and its kind (for the row's icon)."""
    if channel == "prewarm":
        return PREWARM_WHAT, "prewarm"
    if info["question"]:
        n = info["replaced"]
        return ("Question" if not n else f"Question, with {_plural(n, 'part')} replaced"), "question"
    if info["store_rows"] is not None:
        rows = info["store_rows"]
        n_rows = "1 store row" if rows == 1 else f"{rows} store rows"
        return (f"{n_rows}, approved by {info['approver']}" if info["approver"] else n_rows), "store_rows"
    if info["results"]:
        titles = list(dict.fromkeys(t[:1].lower() + t[1:] for t in info["titles"]))
        return f"{_plural(info['results'], 'result')}: {joined(titles)}", "results"
    return "Earlier messages", "history"


def requests_page(db: Session, directory: Engine, *, offset: int, today: date, people=(), who: int | None = None,
                  what: str | None = None, titles: dict[str, str] | None = None) -> dict:
    from steward.advisor.saw import TITLES
    titles = titles or TITLES
    q = (select(AdvisorOutgoing.turn_id, AdvisorOutgoing.seq, func.min(AdvisorOutgoing.at),
                func.max(AdvisorOutgoing.id), func.sum(AdvisorOutgoing.bytes), func.min(AdvisorOutgoing.user_id),
                func.min(AdvisorOutgoing.channel), func.min(AdvisorOutgoing.conversation_id))
         .group_by(AdvisorOutgoing.turn_id, AdvisorOutgoing.seq))
    if who is not None:
        q = q.where(AdvisorOutgoing.user_id == who)
    if what == "prewarm":
        q = q.where(AdvisorOutgoing.channel == "prewarm")
    elif what in FILTER_PART:
        q = q.having(func.max(case((AdvisorOutgoing.part == FILTER_PART[what], 1), else_=0)) == 1)
    groups = db.execute(q.order_by(func.max(AdvisorOutgoing.id).desc()).offset(offset).limit(PAGE + 1)).all()
    keys = [(g[0], g[1]) for g in groups[:PAGE]]
    info: dict[tuple, dict] = defaultdict(lambda: {"question": 0, "results": 0, "titles": [], "store_rows": None,
                                                   "approver": None, "replaced": 0})
    approvers: dict[tuple, int] = {}
    if keys:
        for turn_id, seq, part, name, n_rows, approved in db.execute(
                select(AdvisorOutgoing.turn_id, AdvisorOutgoing.seq, AdvisorOutgoing.part,
                       func.json_extract(AdvisorOutgoing.payload, "$.name"),
                       func.json_array_length(AdvisorOutgoing.payload, "$.content.rows"), AdvisorOutgoing.approved_by)
                .where(tuple_(AdvisorOutgoing.turn_id, AdvisorOutgoing.seq).in_(keys))):
            i = info[(turn_id, seq)]
            if part == "question":
                i["question"] += 1
            elif part == "tool_result":
                i["results"] += 1
                i["titles"].append(titles.get(name, str(name)))
            elif part == "merchant_rows":
                i["store_rows"] = (i["store_rows"] or 0) + int(n_rows or 0)
                if approved is not None:
                    approvers[(turn_id, seq)] = approved
        chips = dict(db.execute(select(ChatTurn.turn_id, ChatTurn.chips)
                                .where(ChatTurn.turn_id.in_({k[0] for k in keys}))).all())
        for (turn_id, seq), i in info.items():
            if i["question"]:
                i["replaced"] = len(json.loads(chips.get(turn_id) or "[]"))
    ids = {g[5] for g in groups} | set(approvers.values())
    names = names_for(directory, ids)        # people who have left too, and a restored book's "Someone, before ..."
    for k, uid in approvers.items():
        info[k]["approver"] = names.get(uid, "a manager")
    conv_titles = dict(db.execute(select(ChatConversation.id, ChatConversation.title).where(
        ChatConversation.id.in_({g[7] for g in groups if g[7] is not None}))).all()) if groups else {}
    rows = []
    for turn_id, seq, at, _, size, uid, channel, cid in groups[:PAGE]:
        words, kind = _what(info[(turn_id, seq)], channel)
        local = at.astimezone(clock.household_tz())
        rows.append({"key": f"{turn_id}.{seq}", "at": at.isoformat(), "when": at_label(at, today),
                     "day": "Today" if local.date() == today else f"{local:%a}, {day_label(local.date(), today)}",
                     "who": names.get(uid, "Someone no longer in the book"),
                     "channel": CHANNEL_LABELS.get(channel, channel), "what": words, "kind": kind,
                     "conversation": conv_titles.get(cid), "size": size_label(size)})
    return {"requests": rows, "next_offset": offset + PAGE if len(groups) > PAGE else None,
            "people": [{"id": p.id, "name": p.display_name} for p in people]}


def _strings(value, out: list[str]) -> list[str]:
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for v in value.values():
            _strings(v, out)
    elif isinstance(value, list):
        for v in value:
            _strings(v, out)
    return out


def request_detail(db: Session, key: str, *, names: dict[int, str] | None = None,
                   labels: dict[str, str] | None = None, today: date | None = None) -> dict | None:
    turn_id, _, seq = key.rpartition(".")
    if not turn_id or not seq.isdigit():
        return None
    rows = list(db.scalars(select(AdvisorOutgoing).where(AdvisorOutgoing.turn_id == turn_id,
                                                         AdvisorOutgoing.seq == int(seq)).order_by(AdvisorOutgoing.id)))
    if not rows:
        return None
    names, labels = names or {}, labels or {}
    about = db.scalar(select(ChatTurn.about).where(ChatTurn.turn_id == turn_id)) is not None
    parts = [{"id": r.id, "part": r.part,
              "label": SYSTEM_ABOUT if about and r.part == "system" else PART_LABELS[r.part], "sha256": r.sha256,
              "bytes": r.bytes, "payload": json.loads(r.payload)} for r in rows]
    found = sorted({a for p in parts for s in _strings(p["payload"], []) for a in ALIAS_TOKEN_RE.findall(s)})
    approver = next((r.approved_by for r in rows if r.approved_by is not None), None)
    cid = rows[0].conversation_id
    conv = db.get(ChatConversation, cid) if cid is not None else None
    return {"key": key, "parts": parts,
            "who": names.get(rows[0].user_id, "Someone no longer in the book"),
            "when": at_label(rows[0].at, today) if today else rows[0].at.isoformat(),
            "approved_by": names.get(approver, "a manager") if approver is not None else None,
            "conversation": display(conv.title, labels) if conv is not None else None,
            "size": size_label(sum(r.bytes for r in rows)), "checks": [gateway_name.fill(c) for c in (STORE_CHECKS if any(r.part == "merchant_rows" for r in rows) else CHECKS)],
            "aliases": [{"alias": a, "label": labels[a]} for a in found if a in labels and labels[a] != a]}


def export(db: Session) -> list[dict]:
    return [{"id": r.id, "at": r.at.isoformat(), "user_id": r.user_id, "conversation_id": r.conversation_id,
             "turn_id": r.turn_id, "seq": r.seq, "channel": r.channel, "part": r.part,
             "payload": json.loads(r.payload), "sha256": r.sha256, "bytes": r.bytes, "model": r.model,
             "approved_by": r.approved_by}
            for r in db.scalars(select(AdvisorOutgoing).order_by(AdvisorOutgoing.id))]
