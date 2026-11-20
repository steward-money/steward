"""merchant_detail (spec 4.4): rows with store names, cleaned before anyone sees them, at most 92 days and 200 rows."""
import re
from datetime import date, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from steward.db.models import Account, AccountType, AdvisorAlias, CategorySource, Transaction
from steward.demo import seed, seed_people
from steward_boundary import tools as T
from tests.advisor.helpers import call

TODAY = date(2026, 10, 20)


@pytest.fixture
def household(books, ours, engine):
    with Session(engine) as db:
        seed(db, years=1, seed=7)
    with Session(books.directory) as ddb:
        seed_people(ddb, ours.id)
    return next(p.id for p in books.people(ours.id) if p.username == "alex")


def checking(engine) -> int:
    with Session(engine) as db:
        return db.scalar(select(Account.id).where(Account.type == AccountType.checking).order_by(Account.id))


def add_rows(engine, rows):
    with Session(engine) as db:
        db.add_all([Transaction(account_id=a, posted_at=d, amount_cents=c, raw_description=raw, merchant=m,
                                category=cat, category_source=CategorySource.none) for a, d, c, raw, m, cat in rows])
        db.commit()


def ask(books, ours, uid, engine, **inp):
    base = {"reason": "Totals can't name them.", "start": "2026-10-01", "end": "2026-10-20", "category": None,
            "payees": None}
    return call(engine, ours, books.people(ours.id), uid, T.MERCHANT, {**base, **inp}, TODAY)


def test_rows_carry_date_amount_category_store_and_account_alias(books, ours, engine, household):
    add_rows(engine, [(checking(engine), date(2026, 10, 18), -1599, "STREAMBOX 800-555-0100", "StreamBox",
                       "subscriptions")])
    r = ask(books, ours, household, engine, category="subscriptions")
    assert r["error"] is None
    row = next(x for x in r["content"]["rows"] if x["merchant"] == "StreamBox")
    assert row == {"date": "2026-10-18", "amount_cents": -1599, "category": "subscriptions", "merchant": "StreamBox",
                   "account": row["account"]}
    assert re.fullmatch(r"Account [0-9]+", row["account"])
    assert all(x["category"] == "subscriptions" for x in r["content"]["rows"])


def test_names_and_long_numbers_in_store_names_never_reach_the_rows(books, ours, engine, household):
    add_rows(engine, [(checking(engine), date(2026, 10, 19), -2500, "ZELLE TO SAM 4417002918 CONF", None, "gifts")])
    r = ask(books, ours, household, engine, category="gifts")
    stores = [x["merchant"] for x in r["content"]["rows"]]
    assert stores, r
    for s in stores:
        assert not re.search(r"(?i)\bsam\b", s), s
        assert "4417002918" not in s and not re.search(r"\d{5,}", s.replace(" ", "")), s
    assert any("[name]" in s for s in stores)


def test_store_names_go_through_the_canonical_normaliser_and_the_strict_stored_text_path():
    """Controller addition: look-alike letters, zero-width characters and full-width digits can't hide a name or a
    number from the cleaning, and what comes out passes the strict stored-text rule."""
    from steward.advisor.identifiers import Identifier
    from steward.advisor.tools.merchant import clean_store
    from steward_boundary.text import is_stored_safe

    idents = (Identifier("Sam", "name", "[name]", name=True),)
    cases = {
        "Venmo S​am": "[name]",                                   # zero-width inside the name
        "Venmo Ｓａｍ": "[name]",                          # full-width letters
        "Venmo Sаm": "[name]",                                    # Cyrillic a
        "Shop ４４１７００２９１８": None,   # full-width digits
        "Café​ Rouge": None,
    }
    for raw, wants in cases.items():
        out = clean_store(raw, idents)
        assert is_stored_safe(out), (raw, out)
        assert "​" not in out and not re.search(r"\d{5,}", out), (raw, out)
        assert not re.search(r"(?i)\bs\W*a\W*m\b", out), (raw, out)
        if wants:
            assert wants in out, (raw, out)
    assert not re.search(r"\d{5}", re.sub(r"\D", "", clean_store(list(cases)[3], idents)))
    assert clean_store("", ()) == "[payee]"
    assert clean_store("​", ()) == "[payee]"


def _idents():
    from steward.advisor.identifiers import Identifier
    return (Identifier("Sam", "name", "[name]", name=True), Identifier("Margaret Ellis", "name", "[name]", name=True),
            Identifier("Samantha Becker", "name", "[name]", name=True),
            Identifier("1234 Elm Street", "address", "[address]"), Identifier("4417002918", "number", "[account number]"),
            Identifier("sam@example.com", "email", "[email]"))


def test_a_store_name_holding_an_address_an_account_number_a_name_or_an_email_is_masked():
    """Task 6.5: mask_numbers, the canonical normaliser, the exact pass and the survivors check, in that order."""
    from steward.advisor.tools.merchant import clean_store
    from steward_boundary.text import is_stored_safe
    for raw, wants, gone in (("ACME 1234 ELM STREET", "[address]", "elm"),
                             ("ZELLE TO SAM 4417002918 CONF", "[name]", "4417002918"),
                             ("TRANSFER 4417 0029 18", None, "44170029"),
                             ("PAYPAL sam@example.com", "[email]", "example"),
                             ("Venmo Margaret Ellis", "[name]", "ellis")):
        out = clean_store(raw, _idents())
        assert is_stored_safe(out) and not re.search(r"\d{5,}", out.replace(" ", "")), (raw, out)
        assert gone not in out.casefold().replace(" ", ""), (raw, out)
        if wants:
            assert wants in out, (raw, out)


def test_cutting_a_long_store_name_never_leaves_part_of_a_name_or_a_broken_token():
    """Task 6.5: the 60-character cut comes after the exact pass and never splits a word or a bracket token, so a
    known name cut at the boundary ("Sama" of "Samantha", "Elli" of "Ellis") can't slip through."""
    from steward.advisor.tools.merchant import clean_store
    from steward_boundary.text import is_stored_safe
    for raw in ("z" * 55 + " Samantha Becker", "x" * 55 + " Ellis", "a" * 45 + " Mar garet Ellis",
                "w" * 50 + " Margaret Ellis", "q" * 58 + " Sam"):
        out = clean_store(raw, _idents())
        assert len(out) <= 60 and is_stored_safe(out), (raw, out)
        for part in ("sama", "elli", "beck", "sam"):
            assert not re.search(rf"(?i)\b{part}", out), (raw, out)
        assert out.count("[") == out.count("]"), (raw, out)


def test_a_period_longer_than_92_days_is_refused(books, ours, engine, household):
    assert ask(books, ours, household, engine, start="2026-07-01", end="2026-10-20")["error"] == "That period is too long."
    assert ask(books, ours, household, engine, start="2026-07-21", end="2026-10-20")["error"] is None   # 92 days
    assert ask(books, ours, household, engine, start="2026-10-20", end="2026-10-01")["error"] == \
        "That input wasn't understood."


def test_at_most_200_rows_and_more_says_so(books, ours, engine, household):
    a = checking(engine)
    add_rows(engine, [(a, date(2026, 10, 1) + timedelta(days=i % 3), -100 - i, f"CAFE {i % 7}", None, "dining")
                      for i in range(205)])
    r = ask(books, ours, household, engine, start="2026-10-01", end="2026-10-03", category="dining")
    assert len(r["content"]["rows"]) == T.MERCHANT_ROWS and r["content"]["more"] is True


def test_payee_aliases_name_their_payees_and_unknown_ones_find_nothing(books, ours, engine, household):
    add_rows(engine, [(checking(engine), date(2026, 10, 18), -1599, "STREAMBOX", "StreamBox", "subscriptions")])
    call(engine, ours, books.people(ours.id), household, "recurring", {}, TODAY)      # gives payees their aliases
    with Session(engine) as db:
        alias = db.scalar(select(AdvisorAlias.alias).where(AdvisorAlias.kind == "payee").order_by(AdvisorAlias.id))
    r = ask(books, ours, household, engine, payees=[alias])
    assert [p["payee"] for p in r["content"]["payees"]] == [alias] and r["content"]["payees"][0]["name"]
    assert ask(books, ours, household, engine, payees=["Payee 999"])["content"] == {"rows": [], "payees": [],
                                                                                    "more": False}
    assert ask(books, ours, household, engine, payees=["Sam"])["error"] == "That input wasn't understood."


# --- Public issue #1: a payment-app or transfer recipient goes as [name] unless the manager ticks that row ------------

def test_a_transfer_to_a_person_the_book_doesnt_know_goes_as_name_and_the_card_holds_the_name(books, ours, engine,
                                                                                               household):
    """The rows the tool returns, which are what goes unless a row is ticked, carry "Zelle to [name]"; the name the
    manager can choose to send stays on the NAS beside them (`home`), keyed by row, for the card."""
    a = checking(engine)
    add_rows(engine, [(a, date(2026, 10, 19), -2500, "ZELLE PAYMENT TO PRIYA NAIR 0412", None, "gifts"),
                      (a, date(2026, 10, 18), -4000, "VENMO *JOHNDOE 4029357733 CA", None, "gifts"),
                      (a, date(2026, 10, 17), -1200, "WIRE TO ACME HOLDINGS REF 77", None, "gifts"),
                      (a, date(2026, 10, 16), -1599, "STREAMBOX 800-555-0100", "StreamBox", "gifts"),
                      (a, date(2026, 10, 15), -9000, "ZELLE PAYMENT 0412 CONF 99", None, "gifts"),
                      (a, date(2026, 10, 15), -5000, "CHECK 1043", None, "gifts")])
    r = ask(books, ours, household, engine, category="gifts", start="2026-10-15", end="2026-10-19")
    assert r["error"] is None
    stores = [x["merchant"] for x in r["content"]["rows"]]
    assert stores == ["Zelle to [name]", "Venmo to [name]", "Wire to [name]", "StreamBox", "Check", "Zelle"]
    assert "Priya" not in str(r["content"]) and "Johndoe" not in str(r["content"])
    assert r["home"] == {"r0": "Zelle to Priya Nair", "r1": "Venmo to Johndoe", "r2": "Wire to Acme Holdings"}


def test_a_transfer_to_someone_the_book_knows_offers_nothing_to_tick(books, ours, engine, household):
    """A member's name is replaced whether or not the row is ticked, so there is nothing to send and no tick."""
    add_rows(engine, [(checking(engine), date(2026, 10, 19), -2500, "ZELLE TO SAM 4417002918 CONF", None, "gifts")])
    r = ask(books, ours, household, engine, category="gifts", start="2026-10-19", end="2026-10-19")
    assert [x["merchant"] for x in r["content"]["rows"]] == ["Zelle to [name]"]
    assert r["home"] == {}


def test_a_payee_asked_for_by_alias_that_is_a_transfer_recipient_goes_as_name_too(books, ours, engine, household):
    from steward.watch.payees import payee
    add_rows(engine, [(checking(engine), date(2026, 10, 18), -2500, "ZELLE PAYMENT TO PRIYA NAIR 0412", None, "gifts")])
    with Session(engine) as db:
        db.add(AdvisorAlias(kind="payee", target=payee("ZELLE PAYMENT TO PRIYA NAIR 0412").key, alias="Payee 77"))
        db.commit()
    r = ask(books, ours, household, engine, payees=["Payee 77"])
    assert r["content"]["payees"] == [{"payee": "Payee 77", "name": "Zelle to [name]"}]
    assert [x["merchant"] for x in r["content"]["rows"]] == ["Zelle to [name]"]
    assert r["home"] == {"r0": "Zelle to Priya Nair", "p0": "Zelle to Priya Nair"}


def test_every_other_tool_result_has_nothing_kept_home():
    """`home` is the merchant tool's alone; a result sent to the gateway never carries it (turn.py strips it)."""
    from steward.advisor.tools import HomeOnly, REGISTRY
    assert {name for name, fn in REGISTRY.items() if "HomeOnly" in fn.__code__.co_names} == {T.MERCHANT}
    assert HomeOnly({"a": 1}, {}).content == {"a": 1}


def test_a_transfer_the_payee_parser_reads_as_a_store_goes_as_name_too(books, ours, engine, household):
    """Review round 1: Cash App's card descriptor, an app written without its star and a bank's own transfer lines
    have no channel in their payee key, so they're read from the row's text. A store stays as it is."""
    a = checking(engine)
    add_rows(engine, [(a, date(2026, 10, 19), -2500, "SQC*PRIYA NAIR 8774174551 CA", None, "gifts"),
                      (a, date(2026, 10, 18), -2600, "ONLINE TRANSFER TO PRIYA NAIR REF #IB0123", None, "gifts"),
                      (a, date(2026, 10, 17), -2700, "REAL TIME TRANSFER TO PRIYA NAIR", None, "gifts"),
                      (a, date(2026, 10, 16), -2800, "VENMO PRIYA NAIR", None, "gifts"),
                      (a, date(2026, 10, 15), -2900, "APPLE CASH SENT MONEY PRIYA NAIR", None, "gifts"),
                      (a, date(2026, 10, 14), -1599, "SQ *BLUE BOTTLE OAKLAND CA", None, "gifts"),
                      (a, date(2026, 10, 14), -1499, "TOTAL WINE 1234 BREA CA", None, "gifts")])
    r = ask(books, ours, household, engine, category="gifts", start="2026-10-14", end="2026-10-19")
    assert r["error"] is None
    stores = [x["merchant"] for x in r["content"]["rows"]]
    assert stores[:5] == ["Cash App to [name]", "Transfer to [name]", "Transfer to [name]", "Venmo to [name]",
                          "Apple Cash to [name]"]
    assert "Total Wine" in stores[5:] and not any("[name]" in s for s in stores[5:]), stores
    assert "priya" not in str(r["content"]).lower()
    assert all("priya nair" in r["home"][f"r{i}"].lower() for i in range(5)), r["home"]


def test_a_set_aside_named_for_a_store_leaves_the_stores_rows_named(books, ours, engine, household):
    """Review round 1 of public issue #2: a set-aside called "Costco" is not a person's name to take out of Costco's
    store rows."""
    from steward.db.models import SetAside
    with Session(engine) as db:
        db.add(SetAside(name="Quokka Mart", kind="one_off", amount_cents=100_00, due_year=2027, due_month=3,
                        first_month=date(2026, 11, 1)))
        db.commit()
    add_rows(engine, [(checking(engine), date(2026, 10, 18), -8000, "QUOKKA MART #0412 BREA CA", "Quokka Mart",
                       "gifts")])
    r = ask(books, ours, household, engine, category="gifts", start="2026-10-18", end="2026-10-18")
    assert [x["merchant"] for x in r["content"]["rows"]] == ["Quokka Mart"]


def test_a_payee_asked_by_alias_whose_key_reads_as_a_store_but_is_a_transfer_goes_as_name(books, ours, engine,
                                                                                          household):
    from steward.watch.payees import payee
    raw = "SQC*PRIYA NAIR 8774174551 CA"
    add_rows(engine, [(checking(engine), date(2026, 10, 18), -2500, raw, None, "gifts")])
    with Session(engine) as db:
        db.add(AdvisorAlias(kind="payee", target=payee(raw).key, alias="Payee 78"))
        db.commit()
    r = ask(books, ours, household, engine, payees=["Payee 78"])
    assert r["content"]["payees"] == [{"payee": "Payee 78", "name": "Cash App to [name]"}]
    assert "priya" not in str(r["content"]).lower()


# --- Review round 2 -------------------------------------------------------------------------------------------------

def test_a_payee_named_in_review_and_asked_by_alias_goes_as_name_from_its_rows(books, ours, engine, household):
    """A person sorted an Apple Cash row in Review and named who was paid, so the row's merchant is "Priya Nair" and its
    payee key is just the name. Asked for by alias, it is read on the key's own rows, inside the period or not, and
    goes as "Apple Cash to [name]" with the name kept home. A store asked for by alias keeps its name."""
    from steward.watch.payees import payee
    a = checking(engine)
    add_rows(engine, [(a, date(2026, 8, 3), -2500, "APPLE CASH SENT MONEY", "Priya Nair", "gifts"),
                      (a, date(2026, 10, 18), -1599, "STREAMBOX 800-555-0100", "StreamBox", "subscriptions")])
    with Session(engine) as db:
        db.add(AdvisorAlias(kind="payee", target=payee("APPLE CASH SENT MONEY", "Priya Nair").key, alias="Payee 81"))
        db.add(AdvisorAlias(kind="payee", target=payee("STREAMBOX 800-555-0100", "StreamBox").key, alias="Payee 82"))
        db.commit()
    r = ask(books, ours, household, engine, payees=["Payee 81", "Payee 82"])
    assert r["error"] is None
    assert r["content"]["payees"] == [{"payee": "Payee 81", "name": "Apple Cash to [name]"},
                                      {"payee": "Payee 82", "name": "Streambox"}]
    assert "priya" not in str(r["content"]).lower()
    assert r["home"] == {"p0": "Priya Nair"}
    r = ask(books, ours, household, engine, payees=["Payee 81"], start="2026-08-01", end="2026-08-31")
    assert [x["merchant"] for x in r["content"]["rows"]] == ["Apple Cash to [name]"]
    assert r["content"]["payees"] == [{"payee": "Payee 81", "name": "Apple Cash to [name]"}]
    assert r["home"] == {"r0": "Priya Nair", "p0": "Priya Nair"}


def test_a_payee_alias_with_no_row_left_goes_masked(books, ours, engine, household):
    """Nothing says what a payee with no rows was, so it goes as [payee] and the manager can tick it."""
    with Session(engine) as db:
        db.add(AdvisorAlias(kind="payee", target="PRIYA NAIR", alias="Payee 83"))
        db.commit()
    r = ask(books, ours, household, engine, payees=["Payee 83"])
    assert r["content"]["payees"] == [{"payee": "Payee 83", "name": "[payee]"}]
    assert "priya" not in str(r["content"]).lower()


# --- Review round 3: the class, not its examples --------------------------------------------------------------------
# Bank lines that carry a person's name, as banks really spell them: (raw text, merchant field, the title it goes
# under). Every one must reach Claude as "<title> to [name]", both as a row and as a payee asked for by alias, with the
# name kept home for the tick. A spelling that leaks fails here; add new ones as they turn up.
MASKED = [
    # wires and payment apps written as one word or with a short name (review round 3, finding 1)
    ("FEDWIRE DEBIT VIA BANK OF X BNF PRIYA NAIR", None, "Wire"), ("FEDWIRE CREDIT PRIYA NAIR", None, "Wire"),
    ("WIRED FUNDS PRIYA NAIR", None, "Wire"), ("INTLWIRE PRIYA NAIR", None, "Wire"),
    ("WIRES OUT PRIYA NAIR", None, "Wire"), ("PYPL*PRIYA NAIR", None, "PayPal"),
    ("PAYPALTRANSFER PRIYA NAIR", None, "PayPal"), ("ZELLEPAY PRIYA NAIR", None, "Zelle"),
    ("ZELLEPMT PRIYA NAIR", None, "Zelle"), ("VENMOPAY PRIYA NAIR", None, "Venmo"),
    ("VENMO PRIYA NAIR", None, "Venmo"), ("CASHAPP*PRIYA NAIR", None, "Cash App"),
    ("CASH APP*PRIYA NAIR", None, "Cash App"), ("SQC*PRIYA NAIR 8774174551 CA", None, "Cash App"),
    ("APPLECASHSENT PRIYA NAIR", None, "Apple Cash"), ("APPLECASH PRIYA NAIR", None, "Apple Cash"),
    ("APPLE CASH SENT MONEY PRIYA NAIR", None, "Apple Cash"), ("APL CASH PRIYA NAIR", None, "Apple Cash"),
    ("ZEL*PRIYA NAIR", None, "Zelle"),
    # bank transfer words and their abbreviations (finding 2)
    ("ONLINE TFR TO PRIYA NAIR", None, "Transfer"), ("TRNSFR TO PRIYA NAIR", None, "Transfer"),
    ("MONEY TRANSFERRED TO PRIYA NAIR", None, "Transfer"), ("ETRANSFER PRIYA NAIR", None, "Transfer"),
    ("INTERAC E-TRANSFER PRIYA NAIR", None, "Transfer"), ("TRANSFR FROM PRIYA NAIR", None, "Transfer"),
    ("XFERS PRIYA NAIR", None, "Transfer"), ("XFR PRIYA NAIR", None, "Transfer"),
    ("TRANSFER PRIYA NAIR", None, "Transfer"), ("ONLINE TRANSFER TO PRIYA NAIR REF #IB0123", None, "Transfer"),
    ("MOBILE TRANSFER FROM PRIYA NAIR", None, "Transfer"), ("REAL TIME TRANSFER TO PRIYA NAIR", None, "Transfer"),
    ("MONEY SENT PRIYA NAIR", None, "Transfer"), ("SEND PRIYA NAIR", None, "Transfer"),
    ("INSTANT PAYMENT PRIYA NAIR", None, "Transfer"), ("RTP PRIYA NAIR", None, "Transfer"),
    ("P2P PRIYA NAIR", None, "Transfer"),
    # a payment or deposit word with "to" or "from" and a word after it, money out or in
    ("PYMT TO PRIYA NAIR", None, "Transfer"), ("PMNT TO PRIYA NAIR", None, "Transfer"),
    ("PYT TO PRIYA NAIR", None, "Transfer"), ("PAYMNT TO PRIYA NAIR", None, "Transfer"),
    ("PAYMENT TO PRIYA NAIR", None, "Transfer"), ("PMT FROM PRIYA NAIR", None, "Transfer"),
    ("PAID TO PRIYA NAIR", None, "Transfer"), ("RECEIVED FROM PRIYA NAIR", None, "Transfer"),
    ("RCVD FROM PRIYA NAIR", None, "Transfer"), ("DEPOSIT FROM PRIYA NAIR", None, "Transfer"),
    ("MOBILE DEP FROM PRIYA NAIR", None, "Transfer"), ("ACH FROM PRIYA NAIR", None, "Transfer"),
    # an ACH or wire line with a person's name field, its ":", "=" or "/" read before punctuation becomes spaces
    ("ORIG:PRIYA NAIR ACH CREDIT", None, "Transfer"), ("BNF:PRIYA NAIR", None, "Transfer"),
    ("BENEFICIARY PRIYA NAIR", None, "Transfer"), ("ONLINE BANKING NAME:PRIYA NAIR", None, "Transfer"),
    ("BNF/0012345678 PRIYA NAIR", "Priya Nair", "Transfer"), ("BNF=PRIYA NAIR", None, "Transfer"),
    ("ORIG = PRIYA NAIR", None, "Transfer"), ("ACH CREDIT RCVR: PRIYA NAIR", None, "Transfer"),
    ("PAYEE:PRIYA NAIR", None, "Transfer"), ("ORIGINATOR:PRIYA NAIR", None, "Transfer"),
    # a person named in Review: the merchant field is the name, and the bank line says what it was
    ("APPLE CASH SENT MONEY", "Priya Shah", "Apple Cash"), ("ONLINE TFR", "Priya Rao", "Transfer"),
    # review round 4: checks with a name after "to" (R1)
    ("CHECK TO PRIYA NAIR", None, "Transfer"), ("MAILED CHECK TO PRIYA NAIR", None, "Transfer"),
    ("CHK TO PRIYA NAIR", None, "Transfer"), ("CHEQUE TO PRIYA NAIR", None, "Transfer"),
    ("CHECK 1042 TO PRIYA NAIR", None, "Check"),
    # payment and remittance apps (R2, Argon)
    ("REMITLY*PRIYA NAIR", None, "Remitly"), ("WESTERN UNION PRIYA NAIR", None, "Western Union"),
    ("WU*PRIYA NAIR", None, "Western Union"), ("MONEYGRAM PRIYA NAIR", None, "MoneyGram"),
    ("XOOM PRIYA NAIR", None, "Xoom"), ("WISE US INC PRIYA NAIR", None, "Wise"), ("WISE*PRIYA NAIR", None, "Wise"),
    ("WORLDREMIT PRIYA NAIR", None, "WorldRemit"), ("GOOGLE PAY PRIYA NAIR", None, "Google Pay"),
    ("GPAY PRIYA NAIR", None, "Google Pay"), ("CHIME*PRIYA NAIR", None, "Chime"),
    ("CHIME PAY ANYONE PRIYA NAIR", None, "Chime"), ("CHASE QUICKPAY PRIYA NAIR", None, "QuickPay"),
    ("POPMONEY PRIYA NAIR", None, "Popmoney"), ("SQUARE CASH PRIYA NAIR", None, "Cash App"),
    # transfer abbreviations and rails (R2, Argon)
    ("ONLINE TRF TO PRIYA NAIR", None, "Transfer"), ("TRSF PRIYA NAIR", None, "Transfer"),
    ("BANK TFRS TO PRIYA NAIR", None, "Transfer"), ("TRFS PRIYA NAIR", None, "Transfer"),
    ("INTERAC E-TRF PRIYA NAIR", None, "Transfer"), ("INTERAC ETFR PRIYA NAIR", None, "Transfer"),
    ("INTERAC EMT PRIYA NAIR", None, "Transfer"), ("FEDNOW PRIYA NAIR", None, "Transfer"),
    ("INSTANT PMT PRIYA NAIR", None, "Transfer"), ("REAL TIME PAYMENT PRIYA NAIR", None, "Transfer"),
    ("WIREOUT PRIYA NAIR", None, "Wire"), ("WIRETRN PRIYA NAIR", None, "Wire"),
    # more payment words with "to" or "from", and a number before the name (R6, Argon)
    ("EFT DEBIT TO PRIYA NAIR", None, "Transfer"), ("WITHDRAWAL TO PRIYA NAIR", None, "Transfer"),
    ("ACH PMT TO #4829 PRIYA NAIR", "Priya Nair", "Transfer"),
    # Wells Fargo's card lines, read from after "AUTHORIZED ON <date>" (public issue #49)
    ("MONEY TRANSFER AUTHORIZED ON 03/15 PRIYA NAIR S382074123456789 CARD 1234", None, "Transfer"),
    ("PURCHASE AUTHORIZED ON 03/15 SQC*PRIYA NAIR 8774174551 CA S382074123456789 CARD 1234", None, "Cash App"),
    ("PURCHASE AUTHORIZED ON 03/15 CASH APP*PRIYA NAIR 8774174551 CA S382074123456789 CARD 1234", None, "Cash App"),
    ("MONEY TRANSFER AUTHORIZED ON 03/15 APPLE CASH SENT MONEY 877-233-8552 CA S382074123456789 CARD 1234",
     "Priya Shah", "Apple Cash"),
    ("RECURRING PAYMENT AUTHORIZED ON 03/01 VENMO PRIYA NAIR VISA DIRECT NY S462061234567890 CARD 1234", None,
     "Venmo"),
]
# Money in goes as "<title> from [name]" (review round 4, R3).
MASKED_IN = [
    ("MOBILE DEPOSIT FROM PRIYA NAIR", None, "Transfer"), ("FEDWIRE CREDIT VIA BANK OF X B/O PRIYA NAIR", None, "Wire"),
    ("WIREIN PRIYA NAIR", None, "Wire"), ("DIRECT CREDIT FROM PRIYA NAIR", None, "Transfer"),
    ("CR FROM PRIYA NAIR", None, "Transfer"), ("FEDNOW CREDIT PRIYA NAIR", None, "Transfer"),
    ("CHIPS CREDIT BNF/0012345678 PRIYA NAIR", "Priya Nair", "Transfer"),
    ("ZELLE PAYMENT TO PRIYA NAIR", None, "Zelle"),
    ("VENMO CASHOUT PRIYA NAIR", None, "Venmo"), ("RECEIVED FROM PRIYA NAIR", None, "Transfer"),
    ("APPLE CASH TRANSFER", "Priya Shah", "Apple Cash"),
]
# Store lines near those words that keep their names: a stem must not swallow these. The Bank of America lines carry
# the account holder after INDN: on every ACH bill and paycheck (review round 4, R4).
KEPT = ["VERIZON WIRELESS 800-922-0204", "WIRELESS ZONE", "WIREGRASS ELECTRIC", "HOTWIRE*HOTEL 1234",
        "SENTRY INSURANCE 1234", "SENDIK'S FOOD MARKET MEQUON WI", "SENTARA HEALTHCARE", "SENDGRID",
        "TRANSFORM FITNESS BREA CA", "TRANSAMERICA LIFE", "TRANSIT AUTHORITY", "XFINITY", "ORIGINAL PANCAKE HOUSE",
        "ORIGIN PC", "ZELDA'S PIZZA", "ZELENKA'S HARDWARE", "BENEFIT COSMETICS", "NAMECHEAP.COM", "HOME DEPOT #1234",
        "PAYLESS SHOESOURCE", "PAYCHEX", "INSTACART", "BACK TO SCHOOL STORE", "EDISON PAYMENT 1234",
        "AUTOPAY PAYMENT THANK YOU", "PAYMENT THANK YOU", "T-MOBILE PAYMENT", "TOTAL WINE 1234 BREA CA",
        "SQ *BLUE BOTTLE OAKLAND CA", "CASH WISE FOODS",
        # review round 4: account-holder fields (R4)
        "COMCAST DES:CABLE ID:8475 INDN:JOHN DOE CO ID:0000 WEB",
        "ACME CORP DES:PAYROLL ID:XXXXX123 INDN:DOE,JOHN CO ID:1234 PPD",
        "GEICO DES:PAYMENT ID:4471 INDN:JOHN DOE CO ID:9923 PPD",
        "WELLS FARGO HOME DES:MTG PYMT ID:7720 INDN:JOHN DOE CO ID:4410 PPD",
        "SO CAL EDISON DES:BILL ID:5521 INDN:DOE,JOHN CO ID:7731 WEB",
        # names, wires and transfers by chance (R5)
        "NO NAME BAR", "BIG NAME PIZZA", "BENE PIZZA", "ORIG PANCAKE HOUSE", "TRANSFIGURATION CHURCH",
        "CITY TRANSFER STATION", "BLOOD TRANSFUSION SVCS", "WIRED MAGAZINE", "LIVEWIRE LOUNGE",
        "BARBWIRE BBQ", "HP INSTANT INK", "INSTANT IMPRINTS", "REAL TIME FITNESS",
        # store words beside the new apps and payment words (R2, R6)
        "WISE GUYS PIZZA", "PENNY WISE THRIFT", "WISE USED CARS", "WIND CHIME GIFTS", "WU'S KITCHEN",
        "GOOGLE *YOUTUBE", "GOOGLE STORAGE", "CHECK CARD PURCHASE FARM TO TABLE CAFE",
        "DEBIT CARD PURCHASE BACK TO SCHOOL STORE", "POS DEBIT FARM TO TABLE", "CHECK INTO CASH",
        # the final review's minors: a store and its refund line share a payee key, and "credit", "cr" and
        # "withdrawal" with a target that is no one, a check card written short, and cafes called Wired
        "COSTCO WHSE #1234 BREA CA", "COSTCO WHSE #1234 CREDIT TO CARD", "TARGET T-1234 BREA CA",
        "TARGET T-1234 CR FROM RETURN", "ATM WITHDRAWAL FROM SAVINGS", "STATEMENT CREDIT FROM AMEX OFFER",
        "CR GOODS TO GO", "CHK CARD PURCHASE FARM TO TABLE", "CHECKCARD PURCHASE FARM TO TABLE", "WIRED COFFEE",
        "THE WIRED CAFE",
        # Wells Fargo's card lines (public issue #49)
        "PURCHASE AUTHORIZED ON 03/15 STARBUCKS STORE 05812 SEATTLE WA S382074123456789 CARD 1234",
        "RECURRING PAYMENT AUTHORIZED ON 03/01 NETFLIX.COM NETFLIX.COM CA S462061234567890 CARD 1234",
        "PURCHASE RETURN AUTHORIZED ON 03/16 TARGET T-1234 BREA CA S303075012345678 CARD 1234",
        "PURCHASE AUTHORIZED ON 03/14 SHELL OIL 57444212400 HOUSTON TX P000000381234567 CARD 1234"]


def _by_alias(books, ours, engine, household, lines, sign=-1):
    """Adds each (raw, merchant) row with its own amount, money out unless sign=1, and an alias for each payee key,
    and returns, per line, its row as sent, the alias's name as sent, and what `home` holds for each."""
    from steward.watch.payees import payee
    a = checking(engine)
    add_rows(engine, [(a, date(2026, 10, 18), sign * (2500 + i), raw, merchant, "gifts")
                      for i, (raw, merchant) in enumerate(lines)])
    aliases = {}
    with Session(engine) as db:
        for raw, merchant in lines:
            key = payee(raw, merchant).key
            if key not in aliases:
                aliases[key] = f"Payee {900 + len(aliases)}"
                db.add(AdvisorAlias(kind="payee", target=key, alias=aliases[key]))
        db.commit()
    out = []
    for i, (raw, merchant) in enumerate(lines):
        r = ask(books, ours, household, engine, payees=[aliases[payee(raw, merchant).key]])
        assert r["error"] is None, raw
        row = [(n, x["merchant"]) for n, x in enumerate(r["content"]["rows"]) if x["amount_cents"] == sign * (2500 + i)]
        assert len(row) == 1, (raw, r["content"]["rows"])
        out.append((raw, row[0][1], r["content"]["payees"][0]["name"], r["home"].get(f"r{row[0][0]}"),
                    r["home"].get("p0"), str(r["content"])))
    return out


def _masked_wrong(books, ours, engine, household, table, sign, way):
    got = _by_alias(books, ours, engine, household, [(raw, merchant) for raw, merchant, _ in table], sign)
    wrong = []
    for (raw, merchant, title), (_, row, name, home_row, home_payee, content) in zip(table, got):
        want = f"{title} {way} [name]"
        person = (merchant or "Priya").split()[0].lower()
        if row != want or name != want or "priya" in content.lower() or person not in (home_row or "").lower() \
                or person not in (home_payee or "").lower():
            wrong.append((raw, row, name, home_row, home_payee))
    return wrong


def test_every_transfer_spelling_goes_as_name_in_its_row_and_by_alias(books, ours, engine, household):
    """Review round 3: channel names and transfer words are matched as stems, and a payment or deposit word with "to"
    or "from", or a name field, marks a row with no channel. Every line in MASKED goes as "<title> to [name]", as a
    row and as a payee asked for by alias, and the tick has the name to send."""
    assert not _masked_wrong(books, ours, engine, household, MASKED, -1, "to")


def test_money_in_goes_as_from_name(books, ours, engine, household):
    """Review round 4: a deposit, a wire in or a payroll credit is from someone, so a row with money in goes as
    "<title> from [name]", and so does a payee whose rows are all money in."""
    assert not _masked_wrong(books, ours, engine, household, MASKED_IN, 1, "from")


def test_a_payee_paid_and_paying_goes_as_to_or_from_name(books, ours, engine, household):
    """Each row says its own direction; the payee alias, whose rows go both ways, says neither."""
    from steward.watch.payees import payee
    a = checking(engine)
    add_rows(engine, [(a, date(2026, 10, 18), -2500, "ONLINE TRANSFER TO PRIYA NAIR", "Priya Nair", "gifts"),
                      (a, date(2026, 10, 17), 1800, "ONLINE TRANSFER FROM PRIYA NAIR", "Priya Nair", "gifts")])
    with Session(engine) as db:
        db.add(AdvisorAlias(kind="payee", target=payee("ONLINE TRANSFER TO PRIYA NAIR", "Priya Nair").key,
                            alias="Payee 84"))
        db.commit()
    r = ask(books, ours, household, engine, payees=["Payee 84"])
    assert [x["merchant"] for x in r["content"]["rows"]] == ["Transfer to [name]", "Transfer from [name]"]
    assert r["content"]["payees"] == [{"payee": "Payee 84", "name": "Transfer to or from [name]"}]
    assert r["home"] == {"r0": "Priya Nair", "r1": "Priya Nair", "p0": "Priya Nair"}
    assert "priya" not in str(r["content"]).lower()


def test_a_square_seller_named_for_a_person_leaves_no_star():
    """"SQ *PRIYA NAIR" is a card purchase from a Square seller: it keeps its name, without the star."""
    from steward.advisor.tools import merchant as M
    from steward.watch.payees import payee
    p = payee("SQ *PRIYA NAIR")
    assert M._store(M._row_text(p, None), p.key, (), "SQ *PRIYA NAIR") == ("Priya Nair", None)


def test_store_lines_near_the_transfer_words_keep_their_names(books, ours, engine, household):
    """The other side of the stems: a wireless carrier, Sentry, Sendik's, Transform Fitness, Hotwire, The Original
    Pancake House and the like go by name, as a row and by alias, with nothing to tick."""
    got = _by_alias(books, ours, engine, household, [(raw, None) for raw in KEPT])
    wrong = [(raw, row, name) for raw, row, name, home_row, home_payee, _ in got
             if "[" in row or "[" in name or home_row is not None or home_payee is not None]
    assert not wrong, wrong


def test_the_word_lists_cover_every_spelling_review_round_3_named():
    """The tables above hold every line the review named, so dropping one from either side fails here."""
    named = ["FEDWIRE DEBIT VIA BANK OF X BNF PRIYA NAIR", "WIRED FUNDS PRIYA NAIR", "PYPL*PRIYA NAIR",
             "ZELLEPAY PRIYA NAIR", "VENMOPAY PRIYA NAIR", "PAYPALTRANSFER PRIYA NAIR", "ONLINE TFR TO PRIYA NAIR",
             "TRNSFR TO PRIYA NAIR", "MONEY TRANSFERRED TO PRIYA NAIR", "ETRANSFER PRIYA NAIR", "PYMT TO PRIYA NAIR",
             "PMNT TO PRIYA NAIR", "RECEIVED FROM PRIYA NAIR", "DEPOSIT FROM PRIYA NAIR"]
    masked = {raw for raw, _, _ in MASKED}
    assert not [n for n in named if n not in masked]
    assert {"VERIZON WIRELESS 800-922-0204", "SENTRY INSURANCE 1234", "SENDIK'S FOOD MARKET MEQUON WI"} <= set(KEPT)


def test_the_tables_hold_every_line_review_round_4_named():
    """R1, R2, R4, R5 and Argon's spellings stay in the tables, so dropping one from either side fails here."""
    masked = {raw for raw, _, _ in MASKED + MASKED_IN}
    assert not [n for n in ("CHECK TO PRIYA NAIR", "MAILED CHECK TO PRIYA NAIR", "WU*PRIYA NAIR", "CHIME*PRIYA NAIR",
                            "WESTERN UNION PRIYA NAIR", "WISE*PRIYA NAIR", "GOOGLE PAY PRIYA NAIR",
                            "CHASE QUICKPAY PRIYA NAIR", "ONLINE TRF TO PRIYA NAIR", "TRSF PRIYA NAIR",
                            "INTERAC E-TRF PRIYA NAIR", "WIREOUT PRIYA NAIR", "BNF/0012345678 PRIYA NAIR",
                            "MOBILE DEPOSIT FROM PRIYA NAIR", "DIRECT CREDIT FROM PRIYA NAIR") if n not in masked]
    assert not [n for n in ("COMCAST DES:CABLE ID:8475 INDN:JOHN DOE CO ID:0000 WEB", "NO NAME BAR", "BIG NAME PIZZA",
                            "BENE PIZZA", "ORIG PANCAKE HOUSE", "TRANSFIGURATION CHURCH", "CITY TRANSFER STATION",
                            "WIRED MAGAZINE", "LIVEWIRE LOUNGE", "BARBWIRE BBQ", "HP INSTANT INK") if n not in KEPT]
    # and the final review's: the refund pairs, the three lines, a check card written short, the Wired cafes
    assert not [n for n in ("COSTCO WHSE #1234 CREDIT TO CARD", "TARGET T-1234 CR FROM RETURN",
                            "ATM WITHDRAWAL FROM SAVINGS", "STATEMENT CREDIT FROM AMEX OFFER", "CR GOODS TO GO",
                            "CHK CARD PURCHASE FARM TO TABLE", "WIRED COFFEE", "THE WIRED CAFE") if n not in KEPT]
    assert {"FEDWIRE CREDIT PRIYA NAIR", "WIRED FUNDS PRIYA NAIR", "WITHDRAWAL TO PRIYA NAIR"} <= masked


def test_a_card_store_at_a_bank_that_writes_authorized_on_is_read_on_its_own_lines(books, ours, engine, household):
    """Public issue #49: every card line that starts "PURCHASE AUTHORIZED ON" had the payee key "AUTHORIZED ON", so one
    Apple Cash line at that bank made a store asked for by alias go as "Apple Cash to [name]", and its rows as
    "Authorized On". Each store is now its own payee, and the Apple Cash line still goes as a name."""
    from steward.watch.payees import payee
    store = "PURCHASE AUTHORIZED ON 03/15 STARBUCKS STORE 05812 SEATTLE WA S382074123456789 CARD 1234"
    sent = "RECURRING PAYMENT AUTHORIZED ON 03/16 APPLE CASH SENT MONEY 877-233-8552 CA S462061234567890 CARD 1234"
    a = checking(engine)
    add_rows(engine, [(a, date(2026, 10, 16), -2500, sent, None, "gifts"),
                      (a, date(2026, 10, 15), -650, store, None, "gifts")])
    assert payee(store).key != payee(sent).key
    with Session(engine) as db:
        db.add(AdvisorAlias(kind="payee", target=payee(store).key, alias="Payee 86"))
        db.commit()
    r = ask(books, ours, household, engine, payees=["Payee 86"])
    assert r["content"]["payees"] == [{"payee": "Payee 86", "name": "Starbucks Store"}]
    assert [x["merchant"] for x in r["content"]["rows"]] == ["Starbucks Store"]
    assert r["home"] == {}
    r = ask(books, ours, household, engine, category="gifts", start="2026-10-15", end="2026-10-16")
    assert [x["merchant"] for x in r["content"]["rows"]] == ["Apple Cash to [name]", "Starbucks Store"]


# --- Review round 4: Argon's merchant findings, each reproduced first -----------------------------------------------

def test_a_row_reads_its_whole_payees_bank_lines_as_the_payee_alias_does(books, ours, engine, household):
    """A person named in Review on an Apple Cash row and on a plain card row share one payee key. The payee goes as
    "Apple Cash to [name]", so the plain row can't send the name either: rows read every line the key has."""
    a = checking(engine)
    add_rows(engine, [(a, date(2026, 10, 18), -2500, "APPLE CASH SENT MONEY", "Priya Nair", "gifts"),
                      (a, date(2026, 10, 17), -2600, "POS DEBIT 1234", "Priya Nair", "gifts")])
    r = ask(books, ours, household, engine, category="gifts", start="2026-10-17", end="2026-10-18")
    assert [x["merchant"] for x in r["content"]["rows"]] == ["Apple Cash to [name]", "Apple Cash to [name]"]
    assert "priya" not in str(r["content"]).lower()
    assert r["home"] == {"r0": "Priya Nair", "r1": "Priya Nair"}


def test_a_stores_rows_are_read_on_their_own_lines_when_the_line_holds_the_name(books, ours, engine, household):
    """The final review: reading every line of a payee key spread one stray line to all of a store's rows. A row whose
    bank line holds the name it goes under (from the line, or from a merchant field the line spells) is read on its own
    line; the payee asked for by alias still reads them all."""
    from steward.watch.payees import payee
    a = checking(engine)
    add_rows(engine, [(a, date(2026, 10, 18), -8000, "COSTCO WHSE #1234 BREA CA", None, "gifts"),
                      (a, date(2026, 10, 17), -2500, "COSTCO WHSE #1234 PAYMENT TO PRIYA NAIR", None, "gifts"),
                      (a, date(2026, 10, 16), -6000, "TARGET T-1234 BREA CA", "Target", "gifts"),
                      (a, date(2026, 10, 15), -2600, "TARGET T-1234 PAYMENT TO PRIYA NAIR", "Target", "gifts")])
    r = ask(books, ours, household, engine, category="gifts", start="2026-10-15", end="2026-10-18")
    stores = [x["merchant"] for x in r["content"]["rows"]]
    assert stores[1] == stores[3] == "Transfer to [name]", stores
    assert "[" not in stores[0] and stores[0].upper().startswith("COSTCO") and stores[2] == "Target", stores
    assert set(r["home"]) == {"r1", "r3"}
    with Session(engine) as db:
        db.add(AdvisorAlias(kind="payee", target=payee("COSTCO WHSE #1234 BREA CA").key, alias="Payee 85"))
        db.commit()
    r = ask(books, ours, household, engine, payees=["Payee 85"])
    assert r["content"]["payees"] == [{"payee": "Payee 85", "name": "Transfer to [name]"}]
    assert "priya" not in str(r["content"]).lower()


def test_a_paypal_payee_by_alias_says_its_way_in_the_name_a_tick_sends(books, ours, engine, household):
    """The final review: the alias's label wrote "Paypal to Priyanair", so money in sent "to" on a tick. The ticked
    text is the title, the way and the name, for every channel."""
    a = checking(engine)
    add_rows(engine, [(a, date(2026, 10, 18), 1800, "PAYPAL *PRIYANAIR", None, "gifts"),
                      (a, date(2026, 10, 17), 1900, "PAYPAL *PRIYANAIR", None, "gifts"),
                      (a, date(2026, 10, 16), -2500, "PAYPAL *JOHNDOE", None, "gifts"),
                      (a, date(2026, 10, 15), 1700, "PAYPAL *JOHNDOE", None, "gifts")])
    with Session(engine) as db:
        db.add(AdvisorAlias(kind="payee", target="paypal:PRIYANAIR", alias="Payee 86"))
        db.add(AdvisorAlias(kind="payee", target="paypal:JOHNDOE", alias="Payee 87"))
        db.commit()
    r = ask(books, ours, household, engine, payees=["Payee 86"])
    assert [x["merchant"] for x in r["content"]["rows"]] == ["PayPal from [name]"] * 2
    assert r["content"]["payees"] == [{"payee": "Payee 86", "name": "PayPal from [name]"}]
    assert r["home"] == {"r0": "PayPal from Priyanair", "r1": "PayPal from Priyanair", "p0": "PayPal from Priyanair"}
    r = ask(books, ours, household, engine, payees=["Payee 87"])
    assert [x["merchant"] for x in r["content"]["rows"]] == ["PayPal to [name]", "PayPal from [name]"]
    assert r["content"]["payees"] == [{"payee": "Payee 87", "name": "PayPal to or from [name]"}]
    assert r["home"] == {"r0": "PayPal to Johndoe", "r1": "PayPal from Johndoe", "p0": "PayPal to or from Johndoe"}
    assert "priya" not in str(r["content"]).lower() and "johndoe" not in str(r["content"]).lower()


def test_a_held_address_with_a_long_house_number_is_replaced_in_a_store_row(books, ours, engine, household):
    """The exact pass runs before numbers are cut to 4 digits, so "12345 Maple Ave" is still whole when it looks, and a
    name given in Review reaches it unmasked."""
    import json
    from steward.advisor.identifiers import Identifier
    from steward.advisor.tools.merchant import clean_store
    from steward.db.models import InventoryItem
    home = (Identifier("12345 Maple Ave", "address", "[address]"),)
    assert clean_store("12345 MAPLE AVE AUSTIN TX", home) == "[address] AUSTIN TX"
    with Session(engine) as db:
        db.add(InventoryItem(kind="insurance", name="Homeowners", owner="joint", status="confirmed",
                             details=json.dumps({"address": "12345 Maple Ave"})))
        db.commit()
    add_rows(engine, [(checking(engine), date(2026, 10, 18), -2500, "POS 7781", "12345 Maple Ave Austin TX", "gifts")])
    r = ask(books, ours, household, engine, category="gifts", start="2026-10-18", end="2026-10-18")
    assert [x["merchant"] for x in r["content"]["rows"]] == ["[address] Austin TX"]


def test_the_cut_never_leaves_part_of_a_hyphenated_or_apostrophe_name():
    """At 60 characters, "Jean-Luc" is never left as "Jean-" or "Jean", nor "O'Connor" as "O'" or "O"."""
    from steward.advisor.tools.merchant import clean_store
    for pad in (53, 54, 55, 56):
        for name in ("Jean-Luc Picard", "O'Connor Plumbing"):
            out = clean_store("x" * pad + " " + name, ())
            assert out == "x" * pad, (pad, name, out)
