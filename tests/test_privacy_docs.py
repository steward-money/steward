"""The privacy docs say what the checks do (0.1.0 release gate, fix PR 16d). Each test runs the check a finding was
about, shows what it lets through, and then requires the docs to say so. The phrases the gate found false are kept out
of the docs and the in-app text by tests/test_doc_words.py; this file holds the cases that need the code beside them."""
import re

from steward.advisor.identifiers import Identifier, _generic
from steward.advisor.redact import exact_redact
from steward.advisor import state
from steward_gateway.redact import names_nobody
from tests.public_docs import ROOT, flat, ledger_cells, section

PRIVACY = ROOT / "docs/explanations/privacy.md"
MISSES = "## What the checks can miss"


def misses() -> str:
    return flat(section(PRIVACY, MISSES))


def test_names_the_gateway_drops_on_purpose_are_listed_as_misses():
    """PRIV-3, part 2: a model hit whose every word is a state, a month or a money word is dropped by design, so the
    name goes as typed even when the model finds it. The model also looks only for people, street addresses and dates
    of birth, and it can miss a name outright."""
    for name in ("Virginia Roth", "Dakota Steward", "August"):
        assert names_nobody(name, "[name]"), name
    said = misses()
    assert "virginia roth" in said
    assert "a full name the book doesn't know" in said
    assert "employers, schools, towns" in said


def test_the_exact_pass_matches_what_the_gate_found_it_missed_and_the_docs_say_so():
    """PRIV-4, then public issue #2: the exact pass now matches a name part of 2 letters, keeps a person's name made only
    of generic words, matches an address typed short, and loads every name the book stores (tests/advisor/test_redact.py
    holds the sources). The docs say what it matches, and the misses no longer list these."""
    li = Identifier("Li Wei", "name", "Person 2", name=True)
    assert exact_redact("How much did Li spend on groceries?", [li])[0] == "How much did Person 2 spend on groceries?"
    home = Identifier("1234 North Elm Street Apt 5 Springfield IL 62704", "address", "[address]")
    assert exact_redact("We live at 1234 North Elm Street, can we refinance?", [home])[0] == \
        "We live at [address], can we refinance?"
    assert _generic("August")
    august = Identifier("August", "name", "Child 1", name=True, everyday=True)
    assert exact_redact("Is August's tuition due in August?", [august])[0] == "Is Child 1's tuition due in Child 1?"
    said = misses()
    for gone in ("a two-letter name", "without its unit, city or zip", "a child called august", "400 days",
                 "set-aside"):
        assert gone not in said, gone
    assert "virginia roth" in said and "elm st" in said
    matched = flat(section(PRIVACY, "## What Steward replaces"))
    for want in ("2 letters or more", '"li"', "a child called august", "wherever it appears in your question",
                 '"1234 north elm"', "every payee the book has seen", "set-asides", "nicknames",
                 "added them by hand"):
        assert want in matched, want


def test_the_docs_list_of_what_is_sent_matches_the_disclosure():
    """PRIV-8: the docs said Claude sees amounts, dates, categories and aliases; the tools also send ages, the state
    and tax facts, which the in-app disclosure already listed. Both now draw on one list."""
    sent = state.SENT_TO_CLAUDE
    joined = ", ".join(sent[:-1]) + " and " + sent[-1]
    assert joined in state.DISCLOSURE
    privacy = flat(PRIVACY.read_text(encoding="utf-8"))
    readme = flat(section(ROOT / "README.md", "### Private"))
    for kind in sent:
        assert kind in privacy, kind
        assert kind in readme, kind
    assert "an age is sent where a date of birth isn't" in privacy


def chat_copy(name: str) -> str:
    """One single-line constant of web/src/screens/chat/copy.ts."""
    text = (ROOT / "web/src/screens/chat/copy.ts").read_text(encoding="utf-8")
    m = re.search(rf"^export const {name} = (['\"])(.*)\1;$", text, re.M)
    assert m, name
    return m.group(2)


def test_the_in_app_privacy_lines_name_the_approved_rows_and_the_miss():
    """PRIV-2 and PRIV-3 in the app (TRI-2, the 16d re-check). Store and payee names go in rows a manager approves, and
    the gateway's model can miss a name. The disclosure shows once per person per book, so the Ask lede and the
    Settings › Chat lede, which stay on screen, each say both, as the disclosure does. Public issue #1 narrowed the
    rest: a person paid by transfer goes as [name] unless the manager ticks that row, which the disclosure says and the
    card shows, so the ledes no longer say a payee can be a person."""
    lines = {"LEDE": chat_copy("LEDE"), "SETTINGS_LEDE": chat_copy("SETTINGS_LEDE"), "DISCLOSURE": state.DISCLOSURE}
    for name, line in lines.items():
        said = flat(line)
        assert "a manager approves" in said, name
        assert "can miss one" in said, name
    assert "unless the manager ticks that row" in flat(state.DISCLOSURE)
    assert "a payee can be a person" not in flat(lines["SETTINGS_LEDE"])


RULES = "## Which store rows hide a name"


def rule(label: str, shape: str) -> list[set[str]]:
    """The quoted words in each group of the rule bullet that starts with **label**, in capitals. `shape` is a pattern
    whose groups are the bullet's lists, so a bullet reworded out of its shape fails here rather than passing on
    nothing."""
    lines = [line for line in section(PRIVACY, RULES).splitlines() if line.startswith(f"- **{label}")]
    assert len(lines) == 1, label
    m = re.search(shape, lines[0])
    assert m, (label, lines[0])
    return [{w.upper() for w in re.findall(r'"([^"]+)"', group)} for group in m.groups()]


def test_the_rows_that_hide_a_name_are_exactly_the_ones_the_docs_list():
    """Public issue #1, review round 3: privacy.md lists every word merchant_detail reads to send a row as "[name]".
    Each list in the docs equals the one in the code, every word the docs name masks a row as they say, a payment or
    deposit word without "to" or "from" doesn't, and the rows the miss list says keep a name do keep it."""
    from steward.advisor.tools import merchant as M
    from steward.watch.payees import payee, words
    from steward.watch.vocab import METHOD_TITLES

    def stored(raw: str, merchant: str | None = None, way: str = "to") -> tuple[str, str | None]:
        p = payee(raw, merchant)
        return M._store(M._row_text(p, merchant), p.key, (), raw, way=way)

    def shown(raw: str, merchant: str | None = None, way: str = "to") -> str:
        return stored(raw, merchant, way)[0]

    # Review round 4 (R1): the payee parser's own channels, each with a name after "to", and an app's star.
    to, check, star = rule("Zelle, a wire, bill pay or a numbered check", r"after (.*), such as (.*), or a payment "
                                                                          r"app's name .* such as (.*)\.$")
    assert to == {"TO"} and [shown(c) for c in check] == ["Check to [name]"] and \
        [shown(s) for s in star] == ["PayPal to [name]"]
    for raw, title in (("ZELLE TO PRIYA NAIR", "Zelle"), ("WIRE TO PRIYA NAIR", "Wire"),
                       ("BILL PAY TO PRIYA NAIR", "Bill pay"), ("CHECK 1042 TO PRIYA NAIR", "Check")):
        assert shown(raw) == f"{title} to [name]", raw
    assert set(METHOD_TITLES.values()) == {"Zelle", "Wire", "Bill pay", "Check"}      # the four the bullet names
    for app in words().apps - set(METHOD_TITLES.values()):     # "ZELLE *X" is Zelle's channel, and names no one
        assert shown(f"{app.upper()} *JOHNDOE") == f"{app} to [name]", app
    assert shown("CHECK TO PRIYA NAIR") == shown("MAILED CHECK TO PRIYA NAIR") == "Transfer to [name]"

    runs, whole, stars = rule("A payment app:", r"starts with (.*); the words (.*); or (.*) right before a star\.$")
    assert runs == {w for _, r, _, _ in M.APPS for w in r} and whole == {w for _, _, ws, _ in M.APPS for w in ws}
    assert stars == {w for *_, s in M.APPS for w in s}
    for word in runs:
        assert shown(f"{word}XYZ PRIYA NAIR").endswith(" to [name]"), word
    for word in whole:
        assert shown(f"{word} PRIYA NAIR").endswith(" to [name]"), word
    for word in stars:
        assert shown(f"{word}*PRIYA NAIR").endswith(" to [name]"), word
        assert "[" not in shown(f"{word} PRIYA NAIR"), word         # without the star, a store's word

    ends, example, kept = rule("A wire:", r"ends in (.*), such as (.*), except inside (.*)\.$")
    assert ends == set(M.WIRE_ENDS) and kept == set(M.WIRE_EXCEPT)
    for word in ends | example:
        assert shown(f"FED{word.removeprefix('FED')} PRIYA NAIR") == "Wire to [name]", word
    for word in kept:
        assert "[" not in shown(f"{word} LOUNGE 1234"), word

    inside, starts, alone, unlike = rule("A transfer word:", r"contains (.*); a word that starts with (.*); or the "
                                                            r"words (.*); except inside (.*)\.$")
    assert inside == set(M.TRANSFER_INSIDE) and starts == set(M.TRANSFER_STARTS) and alone == set(M.TRANSFER_WORDS)
    for raw in [f"E{w}ER PRIYA NAIR" for w in inside] + [f"{w}S PRIYA NAIR" for w in starts] + \
               [f"{w} PRIYA NAIR" for w in alone]:
        assert shown(raw) == "Transfer to [name]", raw
    assert unlike == set(M.TRANSFER_EXCEPT)
    for word in unlike:
        assert "[" not in shown(f"{word} BREA CA"), word

    marks, fields, holder, alone = rule("A name field", r"right before (.*): (.*), except inside (.*); or the words "
                                                        r"(.*) before a word\.$")
    assert marks == {":", "=", "/"} and fields == set(M.NAME_FIELDS) and holder == set(M.NAME_EXCEPT)
    assert alone == set(M.NAME_WORDS)
    for word in fields:
        for mark in marks:
            assert shown(f"ACH CREDIT {word}{mark}PRIYA NAIR") == "Transfer to [name]", (word, mark)
        assert "[" not in shown(f"{word} PIZZA 1234"), word          # no mark: a store's word ("No Name Bar")
    for word in holder:
        assert "[" not in shown(f"ACME DES:PAYROLL {word}:PRIYA NAIR"), word
    for word in alone:
        assert shown(f"{word} PRIYA NAIR") == "Transfer to [name]", word
    assert '"indn:"' in flat(section(PRIVACY, RULES)) and "[" not in shown("ACME DES:PAYROLL INDN:PRIYA NAIR")

    pays, beside, unlike, targets = rule("A payment or deposit word", r"after it: (.*); or (.*) right before .*; "
                                                                      r"except inside (.*), or when the word after "
                                                                      r"\"to\" or \"from\" is (.*)\.$")
    assert pays == set(M.PAYMENT_WORDS) and beside == set(M.PAYMENT_BESIDE) and unlike == set(M.PAYMENT_EXCEPT)
    assert targets == set(M.PAYMENT_TARGET_EXCEPT)
    for word in pays | beside:
        assert shown(f"{word} TO PRIYA NAIR") == shown(f"{word} FROM PRIYA NAIR") == "Transfer to [name]", word
        assert "Priya" in shown(f"{word} PRIYA NAIR"), word
        for target in targets:                                         # "CREDIT TO CARD", "WITHDRAWAL FROM SAVINGS"
            assert "[" not in shown(f"{word} TO {target} 1234") + shown(f"{word} FROM {target} 1234"), (word, target)
    for word in pays:
        assert shown(f"{word} 1234 BREA TO PRIYA NAIR").endswith(" to [name]"), word
    for word in beside:                                                # "CR GOODS TO GO": not right before "to"
        assert "[" not in shown(f"{word} GOODS TO GO"), word
    for words_ in unlike:
        assert "[" not in shown(f"{words_} PURCHASE FARM TO TABLE"), words_

    # Money in says "from", and a payee whose rows go both ways says "to or from" (review round 4, R3).
    rules = flat(section(PRIVACY, RULES))
    assert 'a row with money out says "to" and a row with money in says "from"' in rules
    assert '"to or from" when they go both ways' in rules
    assert shown("MOBILE DEPOSIT FROM PRIYA NAIR", way="from") == "Transfer from [name]"
    assert shown("ZELLE TO PRIYA NAIR", way="to or from") == "Zelle to or from [name]"

    # A row reads its payee's other lines only when it goes under a name its own line doesn't hold (the final review).
    assert "when a row goes under a name its bank line doesn't hold, such as one review gave it, steward also reads " \
           "every bank line it holds for that payee" in rules
    assert M._named_elsewhere("POS DEBIT 1234", "Priya Nair", payee("POS DEBIT 1234", "Priya Nair"))
    assert not M._named_elsewhere("COSTCO WHSE #1234", "Costco", payee("COSTCO WHSE #1234", "Costco"))
    assert not M._named_elsewhere("COSTCO WHSE #1234 CREDIT TO CARD", None, payee("COSTCO WHSE #1234 CREDIT TO CARD"))

    # What the miss list says keeps a name does keep it, so the docs promise no more than the code does. Such a row
    # has nothing at home, so the card offers it no tick (public issue #53, review): the docs say so rather than offer one.
    said = misses()
    assert "which store rows hide a name" in said
    assert "these rows have no tick, so a name the model does find in one always goes as [name]" in said
    assert "the name also goes when the manager ticks that row" not in said
    assert "in a row the rules do hide, the name goes when the manager ticks that row" in said
    for raw, merchant in (("PAYWISE PRIYA NAIR", None), ("MOBILE DEPOSIT", "Priya Nair"),
                          ("ATM WITHDRAWAL #1234 BREA CA", "Priya Nair")):
        assert stored(raw, merchant)[1] is None, raw
    for case in ("a store that carries its owner's name", "a person paid through an app steward doesn't know",
                 "a person named in review on a deposit or cash row", "uses none of the words listed there"):
        assert case in said, case
    assert "Priya" in shown("PAYWISE PRIYA NAIR")                      # an app Steward doesn't know
    assert shown("MOBILE DEPOSIT", "Priya Nair") == "Priya Nair"        # named in Review on a deposit row
    assert shown("ATM WITHDRAWAL #1234 BREA CA", "Priya Nair") == "Priya Nair"
    assert shown("PAYPAL *STEAMGAMES") == "PayPal to [name]"           # the store the rules name as hidden too
    assert '"paypal *steamgames"' in rules
    assert "[" not in shown("HP INSTANT INK")                          # the old example no longer matches


def test_the_gateway_masks_a_name_its_model_finds_in_an_unticked_row_as_the_docs_say():
    """Public issue #53: the docs' example is what the gateway does. A person's name its model tags in a row the manager
    didn't tick goes as [name]; the same row ticked goes as the manager saw it."""
    from steward_gateway.checks import mask_store_names
    from tests.gateway.fakes import NameListDetector
    content = {"rows": [{"date": "2026-10-18", "amount_cents": -18_500, "category": "home",
                         "merchant": "John Doe Plumbing", "account": "Account 2"}], "payees": [], "more": False}
    detectors = [NameListDetector(names=("John Doe",))]
    assert mask_store_names(content, frozenset(), detectors)["rows"][0]["merchant"] == "[name] Plumbing"
    assert mask_store_names(content, frozenset({"r0"}), detectors) == content
    rules = flat(section(PRIVACY, RULES))
    assert "replaces each person's name its model finds with [name]. a row such as \"john doe plumbing\" can go as " \
           "\"[name] plumbing\"" in rules
    assert "a store named after someone for a person and replace that name too" in rules
    assert "a ticked row goes as the manager saw it" in rules
    assert "the book's log shows each row as it was sent" in rules
    # Ask's walk-through and the card say it too (public issue #53, review), so neither promises the rows go as shown.
    ask = flat((ROOT / "docs/explanations/ask.md").read_text(encoding="utf-8"))
    assert "in rows the manager didn't tick, your gateway also replaces a person's name its model finds with [name]" \
        in ask
    card = flat(chat_copy("CARD_EVERY_ROW"))
    assert card == "every row as it would go, apart from a person's name your gateway finds", card


def test_the_disclosure_says_which_rows_hide_a_name_as_the_docs_do():
    """Review round 4 (R2): the in-app disclosure, the claims, the README and the docs all say a row "Steward reads as"
    Zelle, a wire, a transfer or a payment app, and point to Privacy's list, rather than promising every bank line
    that "says" one."""
    phrase = "reads as zelle, a wire, a transfer or a payment app"
    texts = {"DISCLOSURE": state.DISCLOSURE, "P4": ledger_cells("P4")[1], "P6": ledger_cells("P6")[1],
             "README": section(ROOT / "README.md", "### Private"),
             "ask.md": (ROOT / "docs/explanations/ask.md").read_text(encoding="utf-8"),
             "privacy.md": PRIVACY.read_text(encoding="utf-8")}
    for name, text in texts.items():
        assert phrase in flat(text), name
        assert "bank line says" not in flat(text), name
    assert "(privacy lists the words)" in flat(state.DISCLOSURE)
    assert "privacy lists the words" in flat(texts["P4"])
    assert "lists the words steward reads" in flat(texts["README"])


def test_the_names_promise_carries_privacys_gaps_wherever_it_is_made():
    """The final review: P4 says Claude never receives the names Steward holds "except in store rows a manager approves
    and in the gaps Privacy lists". The README's bullet and the in-app disclosure carry the same exception, and the
    README's links to the list."""
    p4 = flat(ledger_cells("P4")[1])
    assert "except in store rows a manager approves and in the gaps privacy lists" in p4
    bullet = [line for line in section(ROOT / "README.md", "### Private").splitlines()
              if line.startswith("- Claude never receives the names")]
    assert len(bullet) == 1
    assert "except in store rows a manager approves and in the gaps [privacy](docs/explanations/privacy.md#what-the-" \
           "checks-can-miss) lists" in flat(bullet[0])
    assert "the names, account numbers, addresses, emails and birth dates it holds, apart from the gaps privacy " \
           "lists," in flat(state.DISCLOSURE)
    assert "in the gaps listed under [what the checks can miss](#what-the-checks-can-miss)" in \
        flat(section(PRIVACY, "## What never reaches Claude"))


def test_a_payee_or_set_aside_is_said_to_be_matched_only_as_a_whole():
    """Review round 4 (Argon B1, B4): payees, set-asides and nicknames go to the exact pass whole, not by parts, since
    their parts are often a store's words; a contact's name is matched by parts (tests/advisor/test_redact.py,
    test_a_payee_is_matched_whole_and_a_contact_by_its_parts). The docs say so and name the miss."""
    payee = Identifier("Priya Nair", "payee", "Payee 3")
    assert exact_redact("Did I Zelle Priya Nair?", [payee])[0] == "Did I Zelle Payee 3?"
    assert exact_redact("Did I Zelle Priya?", [payee])[0] == "Did I Zelle Priya?"
    matched = flat(section(PRIVACY, "## What Steward replaces"))
    assert "set-asides, and the nicknames you give rows and rules, each as a whole" in matched
    said = misses()
    assert 'such as "priya" alone for a payee "priya nair"' in said
    assert "save the person as a contact in the inventory and each part of their name is replaced too" in said


def test_claude_codes_own_typing_is_said_to_go_around_steward():
    """PRIV-12: Steward checks what its tools return to Claude Code; what a person types there goes to Anthropic
    without passing through Steward."""
    guide = flat(section(ROOT / "docs/guides/gateway.md", "## Advanced: Claude Code"))
    assert "what you type into claude code doesn't pass through steward" in guide
    assert "claude code" in misses()
    # Settings › Chat's note says the same, and that the gateway's check on a lookup can miss a name (PRIV-3).
    note = flat(chat_copy("CC_NOTE"))
    assert "what you type into claude code doesn't pass through steward" in note
    assert "can miss one" in note


def test_the_fixed_instructions_are_said_to_show_in_full_beside_their_fingerprint():
    """Public issue #3: the gateway answers GET /v1/instructions with the prefix and its hash (tests/gateway/test_app.py),
    Steward checks the hash before showing it (tests/advisor/test_chat_api.py), and a Getting ready row in Settings ›
    Chat opens to it (web/src/screens/chat/SentLog.test.tsx). Privacy says so."""
    said = flat(PRIVACY.read_text(encoding="utf-8"))
    assert "opening that row shows their full text with the fingerprint beside it" in said
    assert "show only as a fingerprint" not in said
    assert '"/v1/instructions"' in (ROOT / "src/steward_gateway/app.py").read_text(encoding="utf-8")
    assert "FixedInstructions" in (ROOT / "web/src/screens/chat/SentLog.tsx").read_text(encoding="utf-8")


def test_p7_promises_every_part_of_your_data_not_everything():
    """PRIV-10: the request also carries the gateway's fixed instructions, its settings and Claude's own earlier
    replies, which aren't household data and aren't logged as text."""
    for text in (ledger_cells("P7")[1], section(ROOT / "README.md", "### Private"),
                 PRIVACY.read_text(encoding="utf-8")):
        assert "every part of your data that was sent" in flat(text)


def test_the_proxys_log_is_said_to_hold_the_installs_address():
    """Issue #5: the proxy's filter drops request>host and request>tls>server_name from its error lines
    (tests/test_proxy_files.py), but Caddy's certificate lines still name the domain. Privacy says so."""
    for path in sorted((ROOT / "deploy/proxy").glob("Caddyfile*")):
        text = path.read_text(encoding="utf-8")
        assert "request>host delete" in text and "request>tls>server_name delete" in text, path.name
    said = flat(PRIVACY.read_text(encoding="utf-8"))
    assert "its certificate lines name your install's address" in said


def test_page_titles_are_said_to_reach_the_browsers_history_and_sync():
    """Issue #29, privacy ruling: a page for one item is titled with its name (web/src/frame/itemTitle.ts strips its
    numbers), and a title goes into the browser's history and, with sync on, to the browser's maker. Privacy says so."""
    item_title = (ROOT / "web/src/frame/itemTitle.ts").read_text(encoding="utf-8")
    assert "export function titleName" in item_title and "titleName(given)" in item_title
    said = flat(section(PRIVACY, "## What stays on your machines"))
    assert "titled with its name, never its numbers" in said
    assert "your browser's history and, if browser sync is on, with your browser's maker" in said
