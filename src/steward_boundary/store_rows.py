"""Approved store rows on both sides of the boundary (public issue #53). With an approved merchant_detail result the NAS
sends the rows and payees the manager ticked ("r0", "p2") in the result's `ticked`, beside its content and never
inside it, so nothing new reaches Claude; it rides in the tool_results frame on the paired socket, as the rows do. The
gateway replaces a person's name its model finds in every unticked store or payee name with [name], and leaves ticked
ones as they are. The NAS records the rows, and so lets them be sent, only when they are the approved ones, or the
approved ones with some of an unticked name's text replaced by [name] (masked_from): the gateway can take text out of a
name but never put any in."""
from __future__ import annotations

from collections.abc import Iterator

from steward_boundary import tools as T
from steward_boundary.schema import arr

NAME = "[name]"
# Each store name in a merchant_detail result: the list it is in, its field, and the prefix of its tick.
STORE_NAMES = (("rows", "merchant", "r"), ("payees", "name", "p"))
TICK = {"type": "string", "pattern": r"^(?:r(?:0|[1-9][0-9]{0,2})|p(?:0|[1-9][0-9]?))$"}
TICKS = arr(TICK, T.MERCHANT_ROWS + 50)


def store_names(content: dict) -> Iterator[tuple[str, dict, str]]:
    """(tick, the row or payee, its name field) for every store name in a result whose shape is already checked."""
    for key, field, prefix in STORE_NAMES:
        for i, item in enumerate(content[key]):
            yield f"{prefix}{i}", item, field


def ticks_of(result: dict) -> frozenset[str]:
    """The ticks a checked tool_results entry carries; none when it carries no `ticked`, so every name is unticked."""
    return frozenset(result.get("ticked") or ())


def masked_from(sent: object, approved: object) -> bool:
    """Whether `sent` is `approved`, or `approved` with some runs of its text (each at least one character) replaced by
    [name]. Each piece of `sent` between its [name] tokens must be found in `approved` in order, the first at its start,
    the last at its end and at least one character between neighbours. The leftmost place for each piece is the best
    one, as it leaves the most room for the rest, so one pass decides it, with no backtracking."""
    if not isinstance(sent, str) or not isinstance(approved, str):
        return False
    if sent == approved:
        return True
    pieces = sent.split(NAME)
    if len(pieces) < 2 or not approved.startswith(pieces[0]):
        return False
    at = len(pieces[0])
    for piece in pieces[1:-1]:
        found = approved.find(piece, at + 1)
        if found < 0:
            return False
        at = found + len(piece)
    last = pieces[-1]
    return approved.endswith(last) and len(approved) - len(last) >= at + 1
