"""Public issue #53: the gateway may take text out of an unticked approved store name, as [name], and never put any in.
masked_from is the NAS's half of that rule (steward.advisor.outgoing records nothing else); the gateway's half,
mask_store_names, is held to it in tests/gateway/test_checks.py."""
import random

import pytest

from steward_boundary.store_rows import NAME, masked_from, store_names, ticks_of


@pytest.mark.parametrize("sent, approved", [
    ("Tom Becker Plumbing", "Tom Becker Plumbing"),             # unchanged
    ("[name] Plumbing", "Tom Becker Plumbing"),
    ("[name]", "Tom Becker Plumbing"),                          # the whole name, the gateway's fallback
    ("[name] and [name] Law", "Hollis and Crane Law"),
    ("Zelle to [name]", "Zelle to [name]"),                     # the NAS's own token, left alone
    ("Zelle [name]", "Zelle to [name]"),                        # a span that ran over the NAS's token
    ("[name][name]", "Al Bo"),                                  # two spans that touch
])
def test_text_replaced_by_name_is_taken_from_the_approved_name(sent, approved):
    assert masked_from(sent, approved)


@pytest.mark.parametrize("sent, approved", [
    ("Someone Else", "StreamBox"),                              # other text
    ("StreamBox [name]", "StreamBox"),                          # [name] where nothing was taken out
    ("[name]StreamBox", "StreamBox"),
    ("[name] Plumbing!", "Tom Becker Plumbing"),                # text added after the mask
    ("Becker [name]", "Tom Becker Plumbing"),                   # pieces out of order
    ("[name] Plumbing Plumbing", "Tom Plumbing"),
    ("[name]", ""),                                             # nothing to take out
    ("[NAME] Plumbing", "Tom Plumbing"),                        # only the exact token counts
    ("[name] Plumbing", None),
    (None, "Tom Plumbing"),
])
def test_anything_else_is_refused(sent, approved):
    assert not masked_from(sent, approved)


def test_a_long_run_of_name_tokens_is_judged_in_linear_time():
    """A gateway that sends many tokens against a long name can't stall the NAS: there is no backtracking regex."""
    assert not masked_from(NAME * 40 + "x", "a" * 4000)
    assert masked_from(NAME * 40 + "x", "ab" * 40 + "x")


def test_a_random_mask_of_any_name_is_accepted_and_an_added_character_is_not():
    """Property, seeded so it runs the same every time: replacing any runs of an approved name with [name] passes, and
    the same text with one character added at the end does not."""
    rnd = random.Random(53)
    alphabet = "abcAB [].'-to"
    for _ in range(3000):
        approved = "".join(rnd.choice(alphabet) for _ in range(rnd.randint(1, 30)))
        cuts = sorted(rnd.sample(range(len(approved) + 1), k=min(len(approved) + 1, 2 * rnd.randint(1, 4))))
        sent, at = "", 0
        for a, b in zip(cuts[::2], cuts[1::2]):
            if b > a:
                sent += approved[at:a] + NAME
                at = b
        sent += approved[at:]
        assert masked_from(sent, approved), (sent, approved)
        assert not masked_from(sent + "\x00", approved), (sent, approved)


def test_store_names_and_ticks_walk_rows_then_payees():
    content = {"rows": [{"merchant": "A"}, {"merchant": "B"}], "payees": [{"payee": "Payee 1", "name": "C"}],
               "more": False}
    assert [(tick, item[field]) for tick, item, field in store_names(content)] == [("r0", "A"), ("r1", "B"),
                                                                                   ("p0", "C")]
    assert ticks_of({"ticked": ["r1", "p0"]}) == frozenset({"r1", "p0"})
    assert ticks_of({}) == frozenset() and ticks_of({"ticked": None}) == frozenset()
