import re

import pytest

from steward_boundary.about import about_text
from steward_boundary.fingerprint import hashes_for
from steward_boundary.protocol import issued as issued_of
from steward_gateway.checks import Part, Stopped, check_parts, verify_history
from steward_gateway.prompt import context_text
from steward_gateway.mac import load_or_create_key, mac, verify
from steward_gateway.redact.patterns import PatternDetector
from tests.gateway.fakes import NameListDetector

SALT = "1" * 32
DET = [PatternDetector(), NameListDetector()]
HASHES = hashes_for([("Margaret Ellis", True), ("(714) 555-0102", False)], SALT)


ISSUED = frozenset({"Person 1"})


def run(*parts, issued=ISSUED):
    check_parts(list(parts), detectors=DET, salt=SALT, hashes=HASHES, issued=issued)


def tool(name, content, is_error=False):
    return Part("tool_result", {"tool_use_id": "toolu_01A09q90qw90lq917835lq9", "name": name, "is_error": is_error,
                                "content": content})


DEBTS = {"debts": [{"account": "Account 2", "kind": "mortgage", "balance_cents": 41_250_000, "rate_bp": 625.0,
                    "payment_cents": 300_000, "payoff_month": "2048-06"}]}


def test_clean_parts_pass():
    run(Part("question", {"text": "How much is left on the mortgage for Person 1?"}),
        Part("system", {"text": "Today is Tuesday, October 20, 2026. This is a household book. The person asking is Person 1."}),
        tool("debts", DEBTS), tool("plan_result", {"error": "Not in this book."}, is_error=True))


@pytest.mark.parametrize("part, check, path", [
    (Part("question", {"text": "Is account 123456789 open?"}), "text", "parts[0].text"),
    (Part("question", {"text": "Email lpark@example.com about it"}), "model", "parts[0].text"),
    (Part("question", {"text": "How is margaret doing?"}), "fingerprint", "parts[0].text"),
    (tool("debts", {"debts": [{**DEBTS["debts"][0], "account": "Margaret"}]}), "schema", "parts[0].content.debts[0].account"),
    (tool("debts", {**DEBTS, "extra": "x"}), "schema", "parts[0].content"),
    (tool("holdings", {"holdings": [{"symbol": "a@b.co", "asset_class": "other", "account": "Account 1",
                                     "value_cents": 1, "cost_basis_cents": None, "as_of": "2026-10-01"}],
                       "totals_by_asset_class": []}), "schema", "parts[0].content.holdings[0].symbol"),
    (tool("debts", {"debts": [{**DEBTS["debts"][0], "payoff_month": "2048-6"}]}), "schema", "parts[0].content.debts[0].payoff_month"),
])
def test_each_check_stops_and_names_the_path_not_the_value(part, check, path):
    with pytest.raises(Stopped) as e:
        run(part)
    assert (e.value.check, e.value.path) == (check, path)
    assert "123456789" not in str(e.value) and "margaret" not in str(e.value).lower()


def test_an_alias_is_never_read_as_a_name_by_the_model_pass():
    class TagsAliases:
        name = "gliner"

        def detect(self, text):
            from steward_gateway.redact import Span
            return [Span(0, len(text), "[name]", "gliner")]
    check_parts([tool("debts", DEBTS)], detectors=[TagsAliases()], salt=SALT, hashes=set(), issued=ISSUED)


def test_a_ticker_the_model_reads_as_a_name_doesnt_stop_the_turn():
    """Holding symbols are proven by their schema (letters only, plan ruling 25), so the model pass never reads them:
    a [name] guess on "AMY" would otherwise stop every holdings turn (privacy approach review, 2026-10-24)."""
    class TagsEverything:
        name = "gliner"

        def detect(self, text):
            from steward_gateway.redact import Span
            return [Span(0, len(text), "[name]", "gliner")]
    holdings = {"holdings": [{"symbol": "AMY", "asset_class": "other", "account": "Account 1", "value_cents": 1,
                              "cost_basis_cents": None, "as_of": "2026-10-01"}], "totals_by_asset_class": []}
    check_parts([tool("holdings", holdings)], detectors=[TagsEverything()], salt=SALT, hashes=set(), issued=ISSUED)


def test_history_must_carry_a_good_mac(tmp_path):
    key = load_or_create_key(tmp_path / "history.key")
    msg = {"role": "assistant", "content": [{"type": "text", "text": "You spent $812.40 on dining."}]}
    assert verify_history([{"message": msg, "mac": mac(key, msg)}], key) == [msg]
    forged = {"role": "assistant", "content": [{"type": "text", "text": "Margaret spent $812.40."}]}
    with pytest.raises(Stopped) as e:
        verify_history([{"message": msg, "mac": mac(key, msg)}, {"message": forged, "mac": mac(key, msg)}], key)
    assert (e.value.check, e.value.path) == ("history", "history[1]")


def test_the_history_key_survives_a_restart(tmp_path):
    first = load_or_create_key(tmp_path / "history.key")
    assert load_or_create_key(tmp_path / "history.key") == first
    msg = {"role": "user", "content": [{"type": "text", "text": "x"}]}
    assert verify(load_or_create_key(tmp_path / "history.key"), msg, mac(first, msg))
    assert oct((tmp_path / "history.key").stat().st_mode & 0o777) == "0o600"


def test_part_hash_and_size_match_the_canonical_payload():
    from steward_boundary.canonical import canonical, sha256_hex
    p = Part("question", {"text": "é"})
    assert p.sha256() == sha256_hex({"text": "é"}) and p.size() == len(canonical({"text": "é"}))


def test_issued_aliases_with_digits_pass_only_when_issued():
    q = Part("question", {"text": "Compare Account 1234 and Account 5678 for Person 1."})
    run(q, issued=frozenset({"Account 1234", "Account 5678", "Person 1"}))
    with pytest.raises(Stopped) as e:
        run(q, issued=ISSUED)
    assert (e.value.check, e.value.path) == ("text", "parts[0].text")


def test_a_typed_look_alike_of_an_issued_alias_is_not_shielded():
    with pytest.raises(Stopped) as e:
        run(Part("question", {"text": "Is P\u0435rson 1 Margaret Ellis ok?"}))
    assert e.value.check in ("fingerprint", "model")


def test_check_words_come_from_the_protocol_and_are_not_copied():
    from steward_boundary import protocol
    from steward_gateway import checks
    assert checks.CHECK_WORDS is protocol.CHECK_WORDS
    assert set(protocol.CHECK_WORDS) == {"schema", "text", "model", "fingerprint", "history"}


PAYEES = hashes_for([("StreamBox", False)], SALT)


def rows(merchant="StreamBox", is_error=False):
    return Part("merchant_rows", {"tool_use_id": "toolu_02", "name": "merchant_detail", "is_error": is_error,
                                  "content": {"rows": [{"date": "2026-10-18", "amount_cents": -1599,
                                                        "category": "subscriptions", "merchant": merchant,
                                                        "account": "Account 2"}], "payees": [], "more": False}})


def check(*parts):
    check_parts(list(parts), detectors=DET, salt=SALT, hashes=HASHES, approvable=PAYEES, issued=ISSUED)


def test_approved_store_rows_may_carry_payee_names_but_nothing_else_may():
    check(rows())
    with pytest.raises(Stopped) as e:
        check(Part("question", {"text": "Did StreamBox go up?"}))
    assert e.value.check == "fingerprint"


def test_approved_rows_still_stop_on_a_known_name_or_a_phone():
    for merchant in ("Margaret Ellis", "Call (714) 555-0102"):
        with pytest.raises(Stopped):
            check(rows(merchant))


def test_a_name_guess_in_an_approved_store_name_does_not_stop_the_turn():
    # NameListDetector tags "Tom Becker" as [name]; in an approved row it is the store the manager saw.
    check(rows("Tom Becker Plumbing"))
    with pytest.raises(Stopped) as e:
        check(Part("question", {"text": "What did Tom Becker charge?"}))
    assert e.value.check == "model"


def test_a_ticked_payment_app_payee_can_be_a_persons_name_and_the_docs_say_so():
    """Gate finding PRIV-2, then public issue #1: a Zelle payee is a person the book doesn't know. merchant_detail sends
    "Zelle to [name]" unless the manager ticks that row on the card (tests/advisor/test_approvals.py); a ticked row
    keeps the name, and in an approved store row a [name] guess doesn't stop the turn, so Claude receives it. The
    claims name the tick as the exception."""
    from steward.advisor.tools.merchant import _store, clean_store
    from steward.watch.payees import known_label, payee
    from tests.public_docs import ROOT, flat, ledger_cells, section
    p = payee("ZELLE PAYMENT TO PRIYA NAIR 0412")
    assert _store(known_label(p), p.key, ()) == ("Zelle to [name]", "Zelle to Priya Nair")
    check(rows("Zelle to [name]"))                        # unticked: the token goes, and the checks take it
    ticked = clean_store(known_label(p), ())
    check(rows(ticked))                                   # NameListDetector tags it [name]; the ticked row still goes
    privacy = ROOT / "docs/explanations/privacy.md"
    for heading in ("## What never reaches Claude", "## What the checks can miss"):
        assert "store rows a manager approves" in flat(section(privacy, heading)), heading
    for text in (ledger_cells("P4")[1], section(privacy, "## What the checks can miss"),
                 section(ROOT / "README.md", "### Private")):
        assert "the manager ticks that row" in flat(text)
    assert "store rows a manager approves" in flat(ledger_cells("P4")[1])
    readme = flat((ROOT / "README.md").read_text(encoding="utf-8"))
    assert "except in store rows a manager approves" in readme


# --- Public issue #53: a person's name the model finds in an unticked approved store name goes as [name] -------------


def content_of(*merchants, payees=()):
    return {"rows": [{"date": "2026-10-18", "amount_cents": -1599, "category": "subscriptions", "merchant": m,
                      "account": "Account 2"} for m in merchants],
            "payees": [{"payee": f"Payee {i + 1}", "name": n} for i, n in enumerate(payees)], "more": False}


def masked(content, ticked=(), detectors=DET):
    from steward_gateway.checks import mask_store_names
    return mask_store_names(content, frozenset(ticked), detectors, ISSUED)


def test_an_unticked_approved_row_sends_a_name_the_model_finds_as_name():
    got = masked(content_of("Tom Becker Plumbing", payees=["Dr. Alvarez"]))
    assert [r["merchant"] for r in got["rows"]] == ["[name] Plumbing"]
    assert [p["name"] for p in got["payees"]] == ["[name]"]
    check(rows(got["rows"][0]["merchant"]))                      # what goes passes the checks


def test_the_same_row_ticked_keeps_the_name():
    content = content_of("Tom Becker Plumbing", "Priya Nair", payees=["Dr. Alvarez"])
    got = masked(content, {"r0", "p0"})
    assert [r["merchant"] for r in got["rows"]] == ["Tom Becker Plumbing", "[name]"]
    assert [p["name"] for p in got["payees"]] == ["Dr. Alvarez"]
    assert content == content_of("Tom Becker Plumbing", "Priya Nair", payees=["Dr. Alvarez"])     # not changed in place


def test_a_row_with_only_a_store_name_is_unchanged():
    content = content_of("StreamBox", "Zelle to [name]", payees=["StreamBox"])
    assert masked(content) == content


def test_a_name_the_model_misses_is_left_and_the_docs_say_so():
    """The model is the only reader here: a person's name it doesn't recognize goes as the card showed it."""
    from tests.public_docs import ROOT, flat, section
    content = content_of("Margo Quill Pottery")
    assert masked(content) == content
    said = flat(section(ROOT / "docs/explanations/privacy.md", "## What the checks can miss"))
    assert "when the gateway's model doesn't recognize it" in said
    assert "these rows have no tick, so a name the model does find in one always goes as [name]" in said


def test_only_a_name_is_masked_and_other_findings_still_stop_the_turn():
    got = masked(content_of("Call (714) 555-0102"))
    assert got["rows"][0]["merchant"] == "Call (714) 555-0102"     # a phone isn't masked here: the check stops it
    with pytest.raises(Stopped) as e:
        check(rows(got["rows"][0]["merchant"]))
    assert e.value.check in ("text", "model", "fingerprint")


def test_a_mask_that_would_run_past_the_limit_sends_the_whole_name_as_name():
    from steward_boundary import tools as T
    long = "Tom " + "x" * (T.MERCHANT_STORE_CHARS - 4)
    assert len(long) == T.MERCHANT_STORE_CHARS
    assert masked(content_of(long))["rows"][0]["merchant"] == "[name]"


def test_text_the_plain_view_changes_sends_the_whole_name_as_name():
    """The model reads the plain view; an edit made there can't be laid on other text, so the whole name goes."""
    assert masked(content_of("Tom​ Becker"))["rows"][0]["merchant"] == "[name]"


def test_a_result_of_the_wrong_shape_is_left_for_the_schema_check():
    bad = {"rows": [{"merchant": "Tom Becker"}], "payees": [], "more": False}
    assert masked(bad) is bad
    assert masked({"rows": "Tom Becker"}) == {"rows": "Tom Becker"}


def test_every_mask_the_gateway_makes_is_one_the_nas_records():
    """The two halves of public issue #53 agree: whatever mask_store_names sends, masked_from accepts against the
    approved name, so a masked row is recorded and never refused as changed."""
    from steward_boundary.store_rows import masked_from
    from tests.gateway.fakes import THIRD_PARTY_NAMES
    names = [f"{n} Plumbing" for n in THIRD_PARTY_NAMES]
    names += [f"Paid {n} and {m}" for n, m in zip(THIRD_PARTY_NAMES, THIRD_PARTY_NAMES[::-1])]
    names += ["Tom" * 20, "Tom " * 15, "TomBecker", "tom becker", "StreamBox", "Zelle to [name]", "Tom​ Becker"]
    for n in names:
        approved = n[:60]
        sent = masked(content_of(approved))["rows"][0]["merchant"]
        assert masked_from(sent, approved), (sent, approved)


def test_the_tick_never_reaches_claude():
    """mask_store_names returns content of the same shape: no `ticked`, nothing beside the rows."""
    content = content_of("Tom Becker Plumbing")
    assert set(masked(content, {"r0"})) == set(content) == {"rows", "payees", "more"}


def test_the_only_free_text_in_store_rows_is_the_approved_names():
    """Task 6.5 audit: approved rows are scanned without the payee and institution hashes, so that exemption must
    reach nothing but the store and payee names. Every other string a merchant_detail result can hold is a date, a
    category word or an alias, which no identifier hash can match; a new free-text field would fail here first."""
    from steward_boundary import tools as T
    from steward_boundary.validate import walk_strings
    from steward_gateway.checks import STORE_FIELDS
    content = {**rows().payload["content"], "payees": [{"payee": "Payee 3", "name": "StreamBox"}]}
    strings = list(walk_strings(T.result_schema("merchant_detail", False), content, "c"))
    assert {p for p, _, _ in strings if p.endswith(STORE_FIELDS)} == {"c.rows[0].merchant", "c.payees[0].name"}
    assert {k for p, k, _ in strings if not p.endswith(STORE_FIELDS)} <= {"date", "enum", "alias"}
    assert {k for p, k, _ in strings if p.endswith(STORE_FIELDS)} == {"free_text"}


def test_store_rows_must_be_a_successful_merchant_detail_result():
    for bad in (rows(is_error=True), Part("merchant_rows", {**rows().payload, "name": "accounts"})):
        with pytest.raises(Stopped) as e:
            check(bad)
        assert e.value.check == "schema"


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


def _start(about: dict) -> dict:
    return {"issued": [], "context": {"today": "2026-10-20", "book_kind": "household", "asker": "Person 1",
                                      "about": about}}


@pytest.mark.parametrize("about", ABOUTS, ids=lambda a: a["kind"])
def test_the_context_line_for_each_kind_passes_every_check(about):
    """Ask about this: the line sits in the system part's untyped text, so the text rule, the model pass and the
    fingerprints all read it, with the about alias issued."""
    start = _start(about)
    run(Part("system", {"text": context_text(start["context"])}), issued=issued_of(start))


class TagsAccounts:
    """GLiNER reading an account alias as an account number, as it may."""
    name = "gliner"

    def detect(self, text):
        from steward_gateway.redact import Span
        return [Span(m.start(), m.end(), "[account number]", "gliner") for m in re.finditer(r"Account \d+", text)]


def test_an_about_alias_the_nas_didnt_issue_stops_at_the_model_pass():
    """P12 (Engineering choice 2): the text rule lets a lone "Account 3" through, so it is the model pass that stops a
    line whose alias wasn't issued; issued, the span is cut away around it."""
    start = _start({"kind": "account", "account": "Account 3"})
    part = Part("system", {"text": context_text(start["context"])})
    check_parts([part], detectors=[TagsAccounts()], salt=SALT, hashes=set(), issued=issued_of(start))
    with pytest.raises(Stopped) as e:
        check_parts([part], detectors=[TagsAccounts()], salt=SALT, hashes=set(), issued=frozenset({"Person 1"}))
    assert (e.value.check, e.value.path) == ("model", "parts[0].text")


# Argon follow-up 1: identifiers whose words are also the about line's own vocabulary. A contact "Sunrise Memory Care"
# fingerprints "memory" and "care" (name=True); accounts named "Fun" or "School" fingerprint those words.
ABOUT_CLASH = hashes_for([("Sunrise Memory Care", True), ("Fun", False), ("School", False), ("Eating Out Fund", False),
                          ("Large", False), ("Margaret Ellis", True)], SALT)
CLASHING = [
    {"kind": "care", "scenario": 2, "level": "memory_care"},
    {"kind": "care"},
    {"kind": "category", "category": "entertainment", "month": "2026-10"},
    {"kind": "category", "category": "childcare_education", "month": "2026-10"},
    {"kind": "transaction", "date": "2026-10-16", "amount_cents": -8072, "category": "dining", "account": "Account 3"},
    {"kind": "flag", "check": "large", "date": "2026-10-14"},
]


@pytest.mark.parametrize("about", CLASHING, ids=lambda a: a.get("level") or a.get("category") or a["kind"])
def test_the_about_lines_own_words_never_trip_the_fingerprints(about):
    """The about sentence is built only from protocol.ABOUT's enums, an issued alias, a date and an amount, so it is
    typed vocabulary: a book identifier that shares its words doesn't stop the turn."""
    start = _start(about)
    ctx = context_text(start["context"])
    check_parts([Part("question", {"text": "Is this normal?"}), Part("system", {"text": ctx})], detectors=DET,
                salt=SALT, hashes=ABOUT_CLASH, issued=issued_of(start), about=about_text(about))


def test_the_question_beside_an_about_line_is_still_fingerprinted():
    start = _start({"kind": "care", "scenario": 2, "level": "memory_care"})
    for question in ("What does Sunrise charge?", "Is memory care at Sunrise higher?", "Did Fun go up?"):
        with pytest.raises(Stopped) as e:
            check_parts([Part("question", {"text": question}), Part("system", {"text": context_text(start["context"])})],
                        detectors=DET, salt=SALT, hashes=ABOUT_CLASH, issued=issued_of(start),
                        about=about_text(start["context"]["about"]))
        assert (e.value.check, e.value.path) == ("fingerprint", "parts[0].text")


def test_only_the_about_line_itself_is_exempt_not_the_rest_of_the_system_part():
    """A system part whose ending isn't the about sentence given, or that names someone before it, is scanned whole."""
    line = about_text({"kind": "care", "scenario": 2, "level": "memory_care"})
    for text, given in ((f"Today is Tuesday, October 20, 2026. The person asking is Margaret. {line}", line),
                        (f"Today is Tuesday, October 20, 2026. Sunrise. {line}", line),
                        (f"Today is Tuesday, October 20, 2026. {line}", "It's about the care projection."),
                        (f"Today is Tuesday, October 20, 2026. {line}", None)):
        with pytest.raises(Stopped) as e:
            check_parts([Part("system", {"text": text})], detectors=[], salt=SALT, hashes=ABOUT_CLASH,
                        issued=frozenset({"Person 1"}), about=given)
        assert (e.value.check, e.value.path) == ("fingerprint", "parts[0].text")
    # The about line is exempt only at the end of a system part: the same words in the question still stop.
    with pytest.raises(Stopped) as e:
        check_parts([Part("question", {"text": line})], detectors=[], salt=SALT, hashes=ABOUT_CLASH,
                    issued=frozenset({"Person 1"}), about=line)
    assert (e.value.check, e.value.path) == ("fingerprint", "parts[0].text")


def test_an_about_amount_shaped_like_a_known_number_still_stops():
    """The about sentence's words are exempt, its digits are not: an amount whose digits are a known phone number
    ($71,455,501.02 is 714 555 0102) is scanned as the scan reads any number."""
    about = {"kind": "transaction", "date": "2026-10-16", "amount_cents": -7_145_550_102, "category": "dining",
             "account": "Account 3"}
    start = _start(about)
    part = Part("system", {"text": context_text(start["context"])})
    with pytest.raises(Stopped) as e:
        check_parts([part], detectors=[], salt=SALT, hashes=HASHES, issued=issued_of(start), about=about_text(about))
    assert (e.value.check, e.value.path) == ("fingerprint", "parts[0].text")
    ok = {**about, "amount_cents": -8072}
    check_parts([Part("system", {"text": context_text(_start(ok)["context"])})], detectors=[], salt=SALT,
                hashes=HASHES, issued=issued_of(_start(ok)), about=about_text(ok))


def test_the_text_rule_still_reads_the_about_line():
    """Only the fingerprints leave the about words out: a scenario number too long to be a count stops at the text
    rule, as any long number in the context would."""
    about = {"kind": "care", "scenario": 123_456_789, "level": "memory_care"}
    start = _start(about)
    with pytest.raises(Stopped) as e:
        check_parts([Part("system", {"text": context_text(start["context"])})], detectors=[], salt=SALT,
                    hashes=frozenset(), issued=issued_of(start), about=about_text(about))
    assert (e.value.check, e.value.path) == ("text", "parts[0].text")


CARE_RESULT = {"set_up": True, "age": 84, "spending_counted": True, "medi_cal_limit_cents": 13_000_000,
               "medi_cal_rules_year": 2026,
               "scenarios": [{"n": 1, "level": "memory_care", "care_start_year": 2027, "hours_a_week": None,
                              "adult_day_days": None, "room": None, "home_sale_year": None, "family_monthly_cents": 0,
                              "spending_change_monthly_cents": 0, "care_monthly_cents": 900_000,
                              "care_cost_is_quote": False, "lasts_until_year": 2031, "lasts_until_age": 89,
                              "lasts_past_age": None, "ltc_pays_from_year": None, "ltc_runs_out_year": None,
                              "medi_cal_year": 2027, "medi_cal_status": "over_limit", "medi_cal_over_cents": 100,
                              "medi_cal_first_eligible_year": None}],
               "monthly": {"months": [{"month": "2026-10", "in_cents": 1, "out_cents": 1}], "expected_in_cents": 0,
                           "expected_out_cents": 0, "cash_above_floor_cents": 0, "short_month": None, "short_cents": 0}}
FLAGS_RESULT = {"flags": [{"check": "large", "severity": "normal", "status": "open", "amount_cents": -12_345,
                           "date": "2026-10-14", "account": "Account 1", "payee": "Payee 2"}]}


@pytest.mark.parametrize("name, content", [("care_projection", CARE_RESULT), ("flags", FLAGS_RESULT)],
                         ids=["care_projection", "flags"])
def test_an_enum_in_a_tool_result_never_trips_the_fingerprints(name, content):
    """Review of Argon follow-up 1: an enum is proven against its fixed list by the schema check, so its words
    ("memory_care", "large") are never an identifier. Without this a care About passes the first request and its
    care_projection result stops on the contact "Sunrise Memory Care"."""
    check_parts([tool(name, content)], detectors=DET, salt=SALT, hashes=ABOUT_CLASH, issued=ISSUED)


def test_only_enums_are_left_out_of_a_tool_results_fingerprints():
    """A symbol is proven by its pattern, not a fixed list, so it is still scanned: "LARGE" is the account "Large"."""
    holdings = {"holdings": [{"symbol": "LARGE", "asset_class": "other", "account": "Account 1", "value_cents": 1,
                              "cost_basis_cents": None, "as_of": "2026-10-01"}], "totals_by_asset_class": []}
    with pytest.raises(Stopped) as e:
        check_parts([tool("holdings", holdings)], detectors=DET, salt=SALT, hashes=ABOUT_CLASH, issued=ISSUED)
    assert (e.value.check, e.value.path) == ("fingerprint", "parts[0].content.holdings[0].symbol")


@pytest.mark.parametrize("member, symbol", [("Wei Ma", "MA"), ("Jin Ko", "KO"), ("Mo", "MO")])
def test_a_two_letter_name_part_never_stops_a_holdings_symbol_that_spells_it(member, symbol):
    """Review of public issue #2: the exact pass replaces a 2-letter part in the question, but its fingerprint isn't
    sent, so a household with a member named Wei Ma can still read a Mastercard holding."""
    hashes = hashes_for([(member, True)], SALT)
    holdings = {"holdings": [{"symbol": symbol, "asset_class": "us_stock", "account": "Account 1", "value_cents": 1,
                              "cost_basis_cents": None, "as_of": "2026-10-01"}], "totals_by_asset_class": []}
    check_parts([tool("holdings", holdings)], detectors=DET, salt=SALT, hashes=hashes, issued=ISSUED)
    if " " in member:
        with pytest.raises(Stopped):          # the whole name is still fingerprinted
            check_parts([Part("question", {"text": f"How is {member.lower()} doing?"})], detectors=DET, salt=SALT,
                        hashes=hashes, issued=ISSUED)
