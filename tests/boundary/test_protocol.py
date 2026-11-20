import pytest

from steward_boundary import PROTOCOL
from steward_boundary import protocol as P
from steward_boundary import vocab as V
from steward_boundary.schema import ALIAS, DATE, enum, obj

START = {"type": "start", "protocol": PROTOCOL, "turn_id": "t_0123456789abcdef", "book_id": 1, "user_id": 2,
         "question": "Are we spending more on dining?",
         "context": {"today": "2026-10-20", "book_kind": "household", "asker": "Person 1"},
         "history": [{"message": {"role": "user", "content": "x"}, "mac": "a" * 64}],
         "fingerprints": {"salt": "0" * 32, "hashes": ["b" * 64]}, "issued": ["Person 1", "Account 3", "[name]"]}
# One of each wire shape (Ask about this, The context on the wire).
ABOUTS = [
    {"kind": "account", "account": "Account 3"},
    {"kind": "category", "category": "dining", "month": "2026-10"},
    {"kind": "plan"},
    {"kind": "transaction", "date": "2026-10-16", "amount_cents": -8072, "category": "uncategorized",
     "account": "Account 3"},
    {"kind": "flag", "check": "large", "date": "2026-10-14"},
    {"kind": "care", "scenario": 2, "level": "in_home_help"},
    {"kind": "care"},
]


def with_about(about) -> dict:
    return {**START, "context": {**START["context"], "about": about}}


def test_a_good_start_frame_passes():
    assert P.check_frame(P.START, START) == []


def test_bad_start_frames_fail():
    assert P.check_frame(P.START, {**START, "turn_id": "t_1"})
    assert P.check_frame(P.START, {**START, "extra": 1})
    assert P.check_frame(P.START, {**START, "context": {**START["context"], "asker": "Margaret"}})
    assert P.check_frame(P.START, {k: v for k, v in START.items() if k != "issued"})
    assert P.check_frame(P.START, {**START, "issued": ["Margaret"]})       # only aliases and bracket tokens


def test_issued_tokens_come_from_the_start_frame():
    """The aliases and bracket tokens the NAS put in this turn's text: the only ones later layers protect."""
    assert P.issued(START) == frozenset({"Person 1", "Account 3", "[name]"})


def test_tool_results_frame():
    ok = {"type": "tool_results", "seq": 1, "results": [
        {"id": "toolu_01", "name": "accounts", "content": {"accounts": []}, "error": None},
        {"id": "toolu_02", "name": "plan_result", "content": None, "error": "Not in this book."}]}
    assert P.check_frame(P.TOOL_RESULTS, ok) == []
    assert P.check_frame(P.TOOL_RESULTS, {**ok, "results": [{**ok["results"][1], "error": "Anything"}]})


def test_a_tool_result_may_carry_the_rows_a_manager_ticked_and_nothing_else_beside_its_content():
    """Public issue #53: an approved merchant_detail result says which rows and payees were ticked, beside its content.
    Only "r<row>" and "p<payee>" within a card's limits; protocol 2's frame had no such field, so an older gateway
    refuses it and the protocol number moves to 3."""
    rows = {"rows": [], "payees": [], "more": False}
    result = {"id": "toolu_m1", "name": "merchant_detail", "content": rows, "error": None}
    frame = lambda r: {"type": "tool_results", "seq": 1, "results": [r]}           # noqa: E731
    for ticked in ([], ["r0"], ["r0", "r199", "p0", "p49"]):
        assert P.check_frame(P.TOOL_RESULTS, frame({**result, "ticked": ticked})) == [], ticked
    assert P.check_frame(P.TOOL_RESULTS, frame(result)) == []                       # optional: no ticks
    for bad in (["x1"], ["r01"], ["r1000"], ["p100"], ["r0 "], ["Priya Nair"], [0], "r0", ["r0"] * 251):
        assert P.check_frame(P.TOOL_RESULTS, frame({**result, "ticked": bad})), bad
    assert P.check_frame(P.TOOL_RESULTS, frame({**result, "names": ["r0"]}))        # no other field
    assert PROTOCOL == 3


def test_every_line_is_plain_and_names_a_stop():
    assert set(P.LINES) <= P.STOPS | {"away"}
    for line in P.LINES.values():
        assert "!" not in line and line.endswith(".")
        assert not any(w in line.lower() for w in ("elder", "senior", "patient"))
    assert P.LINES["stopped_check"] == ("Steward stopped this answer because something that looked like personal "
                                        "information was about to be sent. Nothing was sent.")


def test_fingerprints_may_carry_approvable_hashes():
    with_approvable = {**START, "fingerprints": {**START["fingerprints"], "approvable": ["c" * 64]}}
    assert P.check_frame(P.START, with_approvable) == []
    assert P.check_frame(P.START, START) == []          # optional: plan A's frames still pass
    assert P.check_frame(P.START, {**START, "fingerprints": {**START["fingerprints"], "approvable": ["Margaret"]}})


def test_a_turn_can_be_cancelled_for_a_card_left_unanswered_or_a_stop():
    assert P.CANCEL_REASONS == ("not_a_member", "chat_off", "approval_timeout", "stopped")
    for reason in P.CANCEL_REASONS:
        assert P.check_frame(P.CANCEL, {"type": "cancel", "reason": reason}) == []


@pytest.mark.parametrize("about", ABOUTS, ids=lambda a: a["kind"])
def test_a_start_with_each_about_passes(about):
    assert P.check_frame(P.START, with_about(about)) == []


@pytest.mark.parametrize("about", [
    {"kind": "account", "account": "Account 3", "label": "Alvarez Checking"},         # an extra key
    {"kind": "plan", "note": "zorblax for Dr. Alvarez"},
    {"kind": "flag", "check": "large", "date": "2026-10-14", "title": "Two trips to Ralphs"},
    {"kind": "category", "category": "Eating out", "month": "2026-10"},              # a free string, not the enum
    {"kind": "account", "account": "Alvarez Checking"},                              # not an alias
    {"kind": "transaction", "date": "2026-10-16T09:00:00", "amount_cents": -8072, "category": "dining",
     "account": "Account 3"},                                                        # a date with a time
    {"kind": "transaction", "date": "2026-10-16", "amount_cents": "80.72", "category": "dining",
     "account": "Account 3"},
    {"kind": "category", "category": "dining", "month": "2026-1"},
    {"kind": "care", "scenario": 2},                                                 # scenario with no level
    {"kind": "care", "level": "in_home_help"},
    {"kind": "nickname"},
    "It's about Account 3.",
], ids=str)
def test_an_about_with_anything_but_its_typed_fields_fails(about):
    assert P.check_frame(P.START, with_about(about))


def test_an_older_gateway_refuses_an_about():
    """Protocol 1's CONTEXT, as a gateway that wasn't deployed would check it: the about is an extra key."""
    old = obj({"today": DATE, "book_kind": enum(V.BOOK_KINDS), "asker": ALIAS})
    assert P.check_frame(old, START["context"]) == []
    assert P.check_frame(old, with_about(ABOUTS[0])["context"])


def test_issued_adds_the_about_alias():
    """P12: the context names the about alias, so the later layers protect it as they protect the asker's."""
    other = with_about({"kind": "account", "account": "Account 12"})
    assert P.issued(other) == frozenset({"Person 1", "Account 3", "[name]", "Account 12"})
    assert P.issued(with_about(ABOUTS[3])) == frozenset({"Person 1", "Account 3", "[name]"})
    assert P.issued(with_about({"kind": "plan"})) == P.issued(START)
