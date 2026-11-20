"""Decision 2: card purchases are named by the cleaned-up merchant (store numbers and cities ignored); Zelle, checks,
wires and bill pay by the recipient as the bank gives it, normalised for case and spacing. The scam and cash word
lists live in a data file covered here, like the categorizer's rules."""
import json
import re

import pytest

from steward.watch import vocab as V
from steward.watch.payees import DATA, Payee, display, is_cash, known_label, payee, scam_kind


@pytest.mark.parametrize("raw, merchant, expected", [
    ("CVS/PHARMACY #09812 GIFT CARD FULLERTON CA", None, Payee("CVS PHARMACY", "CVS Pharmacy", "card")),
    ("CVS/PHARMACY #04411 BREA CA", None, Payee("CVS PHARMACY", "CVS Pharmacy", "card")),
    ("RALPHS #0214 FULLERTON CA", None, Payee("RALPHS", "Ralphs", "card")),
    ("TARGET BREA CA", None, Payee("TARGET", "Target", "card")),
    ("BREA FAMILY DENTAL", None, Payee("BREA FAMILY DENTAL", "Brea Family Dental", "card")),
    ("POS PURCHASE TRADER JOE'S 512", None, Payee("TRADER JOES", "Trader Joe's", "card")),
    ("STREAMBOX *MONTHLY", "StreamBox", Payee("STREAMBOX", "StreamBox", "card")),
    ("ZELLE PAYMENT TO J. SMITH CONF# QX81234", None, Payee("zelle:J SMITH", "J. Smith", "zelle")),
    ("Zelle payment to  j smith  on 10/16 REF # PP1", None, Payee("zelle:J SMITH", "J Smith", "zelle")),
    ("ZELLE TO ROSA DELGADO ON 09/26 REF # PP123", "Zelle", Payee("zelle:ROSA DELGADO", "Rosa Delgado", "zelle")),
    ("WIRE TRANSFER TO GLOBAL TRADING LTD REF 991", None, Payee("wire:GLOBAL TRADING LTD", "Global Trading Ltd", "wire")),
    ("ONLINE BILL PAYMENT TO PACIFIC SHIELD INS 4108", None, Payee("bill_pay:PACIFIC SHIELD INS", "Pacific Shield Ins", "bill_pay")),
    ("CHECK 1043", None, Payee("", "Check", "check")),
    ("CHECKCARD 1015 HOME DEPOT 6611", None, Payee("HOME DEPOT", "Home Depot", "card")),
])
def test_payee_names(raw, merchant, expected):
    assert payee(raw, merchant) == expected


@pytest.mark.parametrize("raw, merchant, expected", [
    ("PAYPAL *JOHNDOE", "PayPal", Payee("paypal:JOHNDOE", "PayPal to Johndoe", "card")),
    ("PAYPAL *JOHNDOE 4029357733 CA", None, Payee("paypal:JOHNDOE", "PayPal to Johndoe", "card")),
    ("PAYPAL *SCAMMERLLC", None, Payee("paypal:SCAMMERLLC", "PayPal to Scammerllc", "card")),
    ("VENMO *ALICE", "Venmo", Payee("venmo:ALICE", "Venmo to Alice", "card")),
    ("Venmo * alice 855-999-4410", None, Payee("venmo:ALICE", "Venmo to Alice", "card")),
])
def test_payment_app_lines_are_named_by_their_recipient(raw, merchant, expected):
    assert payee(raw, merchant) == expected
    assert known_label(expected) == expected.name


def test_two_recipients_through_one_app_are_two_payees():
    assert payee("PAYPAL *JOHNDOE").key != payee("PAYPAL *SCAMMERLLC").key
    assert payee("PAYPAL *JOHNDOE").key != payee("VENMO *JOHNDOE").key


def test_a_channel_is_part_of_a_payees_identity():
    keys = {payee("ALICE").key, payee("PAYPAL *ALICE").key, payee("VENMO *ALICE").key,
            payee("ZELLE PAYMENT TO ALICE").key, payee("WIRE TRANSFER TO ALICE").key}
    assert len(keys) == 5
    # a name cannot spell another channel's prefix
    assert payee("ZELLE:ALICE").key != payee("ZELLE PAYMENT TO ALICE").key
    assert payee("PAYPAL:ALICE", "PAYPAL:ALICE").key != payee("PAYPAL *ALICE").key


# Wells Fargo writes every debit card line as "PURCHASE AUTHORIZED ON <date> <store> <city> <state> S<ref> CARD <last 4>"
# (public issue #49). Each line gives the store it names, the same payee the store's line at another bank gives.
AUTHORIZED = [
    ("PURCHASE AUTHORIZED ON 03/15 STARBUCKS STORE 05812 SEATTLE WA S382074123456789 CARD 1234",
     Payee("STARBUCKS STORE", "Starbucks Store", "card"), "STARBUCKS STORE 05812 SEATTLE WA"),
    ("PURCHASE AUTHORIZED ON 03/15 STARBUCKS 800-782-7282 WA S582074123456789 CARD 1234",
     Payee("STARBUCKS", "Starbucks", "card"), "STARBUCKS 800-782-7282 WA"),
    ("PURCHASE AUTHORIZED ON 03/14 SHELL 57444212400 HOUSTON TX P000000381234567 CARD 1234",
     Payee("SHELL", "Shell", "card"), "SHELL 57444212400 HOUSTON TX"),
    ("PURCHASE AUTHORIZED ON 03/14 SHELL OIL 57444212400 HOUSTON TX P000000381234567 CARD 1234",
     Payee("SHELL OIL", "Shell Oil", "card"), "SHELL OIL 57444212400 HOUSTON TX"),
    ("RECURRING PAYMENT AUTHORIZED ON 03/01 NETFLIX.COM NETFLIX.COM CA S462061234567890 CARD 1234",
     Payee("NETFLIXCOM", "Netflix.com", "card"), "NETFLIX.COM NETFLIX.COM CA"),
    ("PURCHASE AUTHORIZED ON 03/01 NETFLIX.COM CARD 1234", Payee("NETFLIXCOM", "Netflix.com", "card"), "NETFLIX.COM"),
    ("PURCHASE AUTHORIZED ON 03/01 NETFLIX.COM S462061234567890 CARD 1234",
     Payee("NETFLIXCOM", "Netflix.com", "card"), "NETFLIX.COM"),
    ("PURCHASE RETURN AUTHORIZED ON 03/16 TARGET T-1234 BREA CA S303075012345678 CARD 1234",
     Payee("TARGET", "Target", "card"), "TARGET T-1234 BREA CA"),
    ("PURCHASE AUTHORIZED ON 10/14 GREENLEAF MARKET S000000000 CARD 0000",
     Payee("GREENLEAF MARKET", "Greenleaf Market", "card"), "GREENLEAF MARKET"),
    ("Purchase authorized on 03/15 Trader Joe's #512 Brea CA S382074123456789 Card 1234",
     Payee("TRADER JOES", "Trader Joe's", "card"), "TRADER JOE'S #512 BREA CA"),
    # cash back at the register, a card used abroad, and their returns: the store, not "WITH CASH BACK $" or "INTL ..."
    ("PURCHASE WITH CASH BACK $ 20.00 AUTHORIZED ON 03/15 SAFEWAY #1234 BREA CA P000000381234567 CARD 1234",
     Payee("SAFEWAY", "Safeway", "card"), "SAFEWAY #1234 BREA CA"),
    ("PURCHASE WITH CASH BACK $ 40.00 AUTHORIZED ON 03/15 KROGER #567 PLANO TX S382074123456789 CARD 1234",
     Payee("KROGER", "Kroger", "card"), "KROGER #567 PLANO TX"),
    ("PURCHASE WITH CASH BACK $20 AUTHORIZED ON 03/15 KROGER #567 PLANO TX S382074123456789 CARD 1234",
     Payee("KROGER", "Kroger", "card"), "KROGER #567 PLANO TX"),
    ("PURCHASE WITH CASH BACK $ 20.00 RETURN AUTHORIZED ON 03/15 SAFEWAY #1234 BREA CA S382074123456789 CARD 1234",
     Payee("SAFEWAY", "Safeway", "card"), "SAFEWAY #1234 BREA CA"),
    ("PURCHASE INTL AUTHORIZED ON 03/15 TESCO STORES LONDON GB S382074123456789 CARD 1234",
     Payee("TESCO STORES LONDON GB", "Tesco Stores London Gb", "card"), "TESCO STORES LONDON GB"),
    ("PURCHASE INTL AUTHORIZED ON 03/15 CAFE NERO LONDON GB S382074123456789 CARD 1234",
     Payee("CAFE NERO LONDON GB", "Cafe Nero London Gb", "card"), "CAFE NERO LONDON GB"),
    ("PURCHASE INTL RETURN AUTHORIZED ON 03/16 TESCO STORES LONDON GB S382074123456789 CARD 1234",
     Payee("TESCO STORES LONDON GB", "Tesco Stores London Gb", "card"), "TESCO STORES LONDON GB"),
    ("PURCHASE AUTHORIZED ON 03/15 SQ *BLUE BOTTLE COF OAKLAND CA S382074123456789 CARD 1234",
     payee("SQ *BLUE BOTTLE COF OAKLAND CA"), "SQ *BLUE BOTTLE COF OAKLAND CA"),
    ("MONEY TRANSFER AUTHORIZED ON 03/15 APPLE CASH SENT MONEY 877-233-8552 CA S382074123456789 CARD 1234",
     Payee("APPLE CASH SENT MONEY", "Apple Cash Sent Money", "card"), "APPLE CASH SENT MONEY 877-233-8552 CA"),
    ("MONEY TRANSFER AUTHORIZED ON 03/15 VENMO *JOHNDOE VISA DIRECT NY S382074123456789 CARD 1234",
     Payee("venmo:JOHNDOE", "Venmo to Johndoe", "card"), "VENMO *JOHNDOE VISA DIRECT NY"),
    ("PURCHASE AUTHORIZED ON 03/15 PAYPAL *JOHNDOE 4029357733 CA S382074123456789 CARD 1234",
     Payee("paypal:JOHNDOE", "PayPal to Johndoe", "card"), "PAYPAL *JOHNDOE 4029357733 CA"),
]


@pytest.mark.parametrize("raw, expected, elsewhere", AUTHORIZED)
def test_card_lines_that_start_authorized_on_are_named_by_their_store(raw, expected, elsewhere):
    assert payee(raw) == expected
    assert payee(raw).key == payee(elsewhere).key


def test_two_stores_at_a_bank_that_writes_authorized_on_are_two_payees():
    keys = {payee(raw).key for raw, _, _ in AUTHORIZED}
    assert "AUTHORIZED ON" not in keys and not any("AUTHORIZED" in k or k.endswith(" CARD") for k in keys)
    assert not any("CASH BACK" in k or k.startswith("INTL") for k in keys)
    assert payee(AUTHORIZED[0][0]).key != payee(AUTHORIZED[2][0]).key
    assert {payee(raw).key for raw, _, _ in AUTHORIZED if "CASH BACK" in raw or "INTL" in raw} == {
        "SAFEWAY", "KROGER", "TESCO STORES LONDON GB", "CAFE NERO LONDON GB"}


@pytest.mark.parametrize("raw, expected", [
    # the lead-in is read only at the start, and "CARD" and a reference code only on a line that starts with it
    ("CHASE CARD SERVICES 1234", Payee("CHASE CARD SERVICES", "Chase Card Services", "card")),
    ("NETFLIX.COM CARD 1234", Payee("NETFLIXCOM CARD", "Netflix.com Card", "card")),
    ("SIGNS AUTHORIZED ON MAIN", Payee("SIGNS AUTHORIZED ON MAIN", "Signs Authorized On Main", "card")),
    ("RECURRING PAYMENT NETFLIX.COM", Payee("NETFLIXCOM", "Netflix.com", "card")),
    ("PURCHASE TARGET 1234 BREA CA", Payee("TARGET", "Target", "card")),
    ("PURCHASE AUTHORIZED ON 03/15 ZELLE TO JOHN SMITH", Payee("zelle:JOHN SMITH", "John Smith", "zelle")),
])
def test_lines_without_the_lead_in_keep_their_payee(raw, expected):
    assert payee(raw) == expected


def test_the_same_store_in_another_city_is_the_same_payee():
    assert payee("RALPHS #0214 FULLERTON CA").key == payee("RALPHS #0088 BREA CA").key


def test_display_keeps_acronyms_and_initials():
    assert display("CVS PHARMACY") == "CVS Pharmacy"
    assert display("J. SMITH") == "J. Smith"
    assert display("CLEARPATH RX") == "Clearpath Rx"


@pytest.mark.parametrize("raw, kind", [
    ("CVS/PHARMACY #09812 GIFT CARD FULLERTON CA", "gift_card"),
    ("WIRE TRANSFER TO GLOBAL TRADING LTD", "wire"),
    ("COINBASE.COM 8889087930", "crypto"),
    ("WESTERN UNION 1234 ONLINE", "money_transfer"),
    ("REMOTE SUPPORT SVCS 800-555-0100", "remote_support"),
])
def test_scam_kinds(raw, kind):
    assert scam_kind(raw)["kind"] == kind


@pytest.mark.parametrize("raw", ["CVS/PHARMACY #04411 BREA CA", "GIFT SHOP AT THE ZOO", "CARDINAL HEALTH",
                                 "WIRELESS ONE 445", "STEAMBOAT CAFE"])
def test_near_misses_are_not_scams(raw):
    assert scam_kind(raw) is None


def test_cash_words():
    assert is_cash("ATM WITHDRAWAL 1200 MAIN ST")
    assert is_cash("CASH WITHDRAWAL BRANCH 12")
    assert not is_cash("ATM FEE")
    assert not is_cash("CASHMERE BOUTIQUE")


def test_known_labels():
    assert known_label(Payee("BREA FAMILY DENTAL", "Brea Family Dental", "card")) == "Brea Family Dental"
    assert known_label(Payee("zelle:J SMITH", "J. Smith", "zelle")) == "Zelle to J. Smith"


def test_the_data_file_is_complete():
    raw = json.loads(DATA.read_text())
    for entry in raw["scam"]:
        assert set(entry) == {"kind", "word", "title", "words"}
        assert "{amount}" in entry["title"] and "{payee}" in entry["title"] and entry["words"]
    assert {m["method"] for m in raw["methods"]} == {"zelle", "wire", "bill_pay", "check"}
    for m in raw["methods"]:
        re.compile(m["pattern"])
    assert set(raw["recipient"]) == {"to", "name", "end"}
    for part in raw["recipient"].values():
        re.compile(part)
    assert len(raw["states"]) == 51 and all(len(s) == 2 for s in raw["states"])
    assert set(raw["payment_apps"]) == {"Zelle", "Venmo", "PayPal", "Cash App"}
    assert {"PURCHASE", "PURCHASE INTL", "PURCHASE WITH CASH BACK"} <= set(raw["authorized"])
    assert all(re.fullmatch(r"[A-Z]+(?: [A-Z]+)*", w) for w in raw["authorized"])


def test_every_check_has_its_words():
    for table in (V.CHECK_LABELS, V.CHECK_ICONS, V.WHY):
        assert set(table) == set(V.RANK)
    assert V.check_label("unusual", "Margaret") == "Unusual for Margaret"
    assert V.why("large", 75_000).startswith("This check flags any payment of $750.00 or more")
    for check in V.RANK:
        line = V.why(check, 50_000)
        assert line.startswith("This check flags") and not re.search(r"\b(her|hers|she)\b", line)


@pytest.mark.parametrize("raw", ["SHOP ¹²³⁴⁵⁶⁷ STORE", "SHOP ①②③④⑤⑥⑦ STORE", "SHOP ¹²³⁴⁵⁶⁷", "¹²³⁴⁵⁶⁷ SHOP"])
def test_card_payee_names_never_show_more_than_four_digits(raw):
    for p in (payee(raw), payee("X", raw)):
        assert sum(c.isdigit() for c in p.name) <= 4
        assert sum(c.isdigit() for c in known_label(p)) <= 4
