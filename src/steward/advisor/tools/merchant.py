"""merchant_detail (spec 4.4): transactions with their store or payee names, for a manager to approve; approved rows go
with the conversation's later questions. Names are put in the canonical normal form, masked to 4 digits by the strict stored-text path, and then put
through the exact pass with every identifier the book knows except its payee and institution names, which are what was
asked for (plan B ruling 4): a member's name in a Zelle line becomes [name]. A payment-app or transfer recipient goes
as "Zelle to [name]", or "Zelle from [name]" for money in, unless the manager ticks that row on the card (public issue
#1): the name it would carry stays on the NAS in HomeOnly.home, keyed by row. Detection fails closed: a payee key with
a channel (Zelle, wire, bill pay, a check with a name, a payment app's star), or a row whose text names a payment app
or a wire, holds a transfer word or a name field, or has a payment or deposit word with "TO" or "FROM" and a word after
it (_channel_title, the word lists above it). A row is read on its own bank line, and also on every line Steward holds
for its payee, in either book, when it goes under a name its line doesn't hold (one Review or a rule gave it). A payee
asked for by alias is read on all of its lines, and is masked when there are none. The rows this returns are exactly
what the card shows and what the NAS sends when nothing is ticked. The gateway then replaces a person's name its model
finds in each unticked row with [name] (public issue #53; turn.py sends the ticks beside the rows)."""
from __future__ import annotations

import re
from dataclasses import replace
from datetime import date

from sqlalchemy import select

from steward.advisor.aliases import Aliases, payee_label
from steward.advisor.identifiers import Identifier, load_identifiers
from steward.advisor.redact import APPROVABLE_KINDS, Refused, exact_redact, survives
from steward.advisor.tools import HomeOnly, Ref, ToolEnv, ToolError, register
from steward.advisor.tools.common import clauses
from steward.db.models import Account, AdvisorAlias, Transaction
from steward.inventory.present import clean_account, mask_numbers
from steward.watch.payees import Payee, known_label, make_key, payee, words
from steward.watch.vocab import METHOD_TITLES
from steward_boundary import tools as T
from steward_boundary import vocab as V
from steward_boundary.aliases import number_of
from steward_boundary.normal import plain
from steward_boundary.text import is_stored_safe, stored_text

BAD_INPUT = "That input wasn't understood."
TOO_LONG = "That period is too long."
BRACKET = {"name": "[name]", "account": "[institution]", "email": "[email]", "phone": "[phone]",
           "address": "[address]", "number": "[account number]", "dob": "[date of birth]"}


def _day(value: object) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise ToolError(BAD_INPUT) from None


def store_identifiers(env: ToolEnv) -> tuple[Identifier, ...]:
    aliases = Aliases(env.db, env.book, env.people, save=False)
    found = load_identifiers(env.db, env.book, env.people, env.directory, aliases, env.today)
    return tuple(replace(i, replacement=BRACKET[i.kind]) for i in found if i.kind not in APPROVABLE_KINDS)


STORE_CHARS = T.MERCHANT_STORE_CHARS       # what a row shows and sends (plan B ruling 2)
RAW_CHARS = 400            # bank text read before cleaning; anything past it can't reach the 60 kept


def _cut(text: str) -> str:
    """At most 60 characters, never ending inside a word or a bracket token: "Samantha" is never left as "Sama", nor
    "[name]" as "[na"."""
    text = text.strip()
    if len(text) <= STORE_CHARS:
        return text
    cut = text[:STORE_CHARS]
    if text[STORE_CHARS].isalnum() or text[STORE_CHARS] in "]_-'":
        cut = re.sub(r"[\w\[\]•'-]+$", "", cut)      # a word or token the cut went through ("Jean-Luc" too)
    if cut.rfind("[") > cut.rfind("]"):
        cut = cut[:cut.rfind("[")]
    return cut.strip()


def clean_store(name: str, identifiers: tuple[Identifier, ...]) -> str:
    """A store or payee name safe to show and send, or "[payee]" (Task 6.5 order). The canonical normaliser comes
    first (so look-alike letters, zero-width characters and full-width digits can't hide a name or a number), then
    the exact pass, while a held number or address is still whole ("12345 Maple Ave", review round 4), then
    mask_numbers (clean_account: at most 4 digits), then the strict stored-text path, then the exact pass again over
    the whole name with its survivors check, and only then the 60-character cut, which can't split a known name or a
    token. What is left is checked again: no known identifier may survive and it must pass the stored-text rule."""
    text = plain(name or "")[:RAW_CHARS]
    try:
        text, _ = exact_redact(text, identifiers)
    except Refused:
        pass                    # the pass below runs on the masked text and refuses for itself
    text = stored_text(clean_account(text)).strip()
    if not text:
        return "[payee]"
    try:
        out, _ = exact_redact(text, identifiers)
    except Refused:
        return "[payee]"
    out = _cut(out)
    if not out or survives(out, identifiers) or not is_stored_safe(out):
        return "[payee]"
    return out


# A row the payee parser reads as a card purchase can still be money sent to or from a person: Cash App's card
# descriptor ("SQC*PRIYA NAIR"), an app written without its star ("VENMO PRIYA NAIR"), a bank's own transfer line
# ("ONLINE TFR TO PRIYA NAIR"), a wire ("FEDWIRE DEBIT ... BNF PRIYA NAIR"). The row's raw text, its shown text and its
# payee key are read in capitals, each run of other characters made one space, against the words below; a star and a
# name field's ":", "=" or "/" are read before that. Review round 3 made them stems, since banks spell them many ways;
# round 4 added the exceptions: words and phrases that a match inside doesn't count. A store that matches by chance
# goes masked too and the manager can tick it, never the other way round. docs/explanations/privacy.md ("Which store
# rows hide a name") lists these words exactly, and tests/test_privacy_docs.py fails when the two differ.
APPS = (  # title, words that may run on ("ZELLEPAY", "PYPL*NAME"), whole words, words right before a star ("WU*NAME")
    ("Cash App", ("CASHAPP",), ("CASH APP", "SQC", "SQUARE CASH"), ()),
    ("Apple Cash", ("APPLECASH",), ("APPLE CASH", "APL CASH"), ()),
    ("Venmo", ("VENMO",), (), ()),
    ("PayPal", ("PAYPAL", "PYPL"), (), ()),
    ("Zelle", ("ZELLE",), ("ZEL",), ()),
    ("Google Pay", (), ("GOOGLE PAY", "GPAY"), ()),
    ("Chime", (), ("PAY ANYONE",), ("CHIME",)),
    ("QuickPay", ("QUICKPAY",), (), ()),
    ("Popmoney", ("POPMONEY",), (), ()),
    ("Western Union", (), ("WESTERN UNION",), ("WU",)),
    ("MoneyGram", ("MONEYGRAM",), (), ()),
    ("Remitly", ("REMITLY",), (), ()),
    ("Xoom", ("XOOM",), (), ()),
    ("WorldRemit", ("WORLDREMIT",), ("WORLD REMIT",), ()),
    ("Wise", (), ("WISE US", "WISE INC", "WISE PAYMENTS"), ("WISE",)))
WIRE_ENDS = ("WIRE", "WIRED", "WIRES", "WIREOUT", "WIREIN", "WIRETRN")      # the end of a word ("FEDWIRE")
WIRE_EXCEPT = ("HOTWIRE", "LIVEWIRE", "BARBWIRE", "WIRED MAGAZINE", "WIRED CAFE", "WIRED COFFEE")
TRANSFER_INSIDE = ("TRANSF", "TRNSF")           # anywhere in a word ("ETRANSFER", "TRANSFERRED")
TRANSFER_STARTS = ("XFER", "XFR")               # at the start of a word ("XFERS")
TRANSFER_WORDS = ("TFR", "TFRS", "TRF", "TRFS", "TRSF", "ETFR", "SEND", "SENT", "SENDS", "SENDER", "SENDING", "P2P",
                  "RTP", "FEDNOW", "INTERAC EMT", "INSTANT PAYMENT", "INSTANT PMT", "REAL TIME PAYMENT")
TRANSFER_EXCEPT = ("TRANSFORM", "TRANSFIGURATION", "TRANSFUSION", "TRANSFER STATION")
# "Payment" is on most bills, so these count only with "TO" or "FROM" and a word somewhere after them. DEBIT isn't one:
# card purchases start "DEBIT CARD" or "POS DEBIT", and store names hold "to" ("Farm to Table"). CREDIT, CR and
# WITHDRAWAL count only right before the "TO" or "FROM", since a store's line can read "CR GOODS TO GO". None of them
# counts when the word after "TO" or "FROM" is one of PAYMENT_TARGET_EXCEPT ("CREDIT TO CARD", "CR FROM RETURN").
PAYMENT_WORDS = ("PAYMENT", "PAYMENTS", "PAYMNT", "PAYMT", "PYMT", "PMNT", "PMT", "PYT", "PAY", "PAID", "ACH", "EFT",
                 "DEPOSIT", "DEPOSITS", "DEP", "RECEIVED", "RCVD", "RECD", "CHECK", "CHK", "CHEQUE")
PAYMENT_BESIDE = ("CREDIT", "CR", "WITHDRAWAL")
PAYMENT_EXCEPT = ("CHECK CARD", "CHK CARD", "CHECKCARD", "CREDIT CARD", "STATEMENT CREDIT")
PAYMENT_TARGET_EXCEPT = ("CARD", "SAVINGS", "CHECKING", "ACCOUNT", "RETURN", "OFFER")
# A name field counts with ":", "=" or "/" right after it ("BNF/0012345678 PRIYA NAIR"). INDN and IND NAME are left out:
# Bank of America and Chase put the account holder there on every ACH line, bills and pay included.
NAME_FIELDS = ("NAME", "BNF", "BENE", "ORIG", "RCVR", "RECEIVER", "RECIPIENT", "PAYEE")
NAME_EXCEPT = ("IND NAME",)
NAME_WORDS = ("BENEFICIARY", "ORIGINATOR")      # then a word, with or without the punctuation

_B, _E = r"(?<![A-Z0-9])", r"(?![A-Z0-9])"


def _any(words) -> str:
    return "(?:" + "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True)) + ")"


def _not(words) -> re.Pattern:
    """Where a match doesn't count: inside one of these words or phrases, from its start to the end of its last word."""
    return re.compile(rf"{_B}{_any(words)}[A-Z0-9]*")


def _app(runs, whole) -> re.Pattern:
    parts = ([rf"{_B}{_any(runs)}"] if runs else []) + ([rf"{_B}{_any(whole)}{_E}"] if whole else [])
    return re.compile("|".join(parts))


_APP_RES = tuple((title, _app(runs, whole), re.compile(rf"{_B}{_any(stars)} ?\*") if stars else None)
                 for title, runs, whole, stars in APPS)
_WIRE = re.compile(rf"{_B}[A-Z0-9]*?{_any(WIRE_ENDS)}{_E}")
_TRANSFER = re.compile(rf"{_B}[A-Z0-9]*?{_any(TRANSFER_INSIDE)}|{_B}{_any(TRANSFER_STARTS)}"
                       rf"|{_B}{_any(TRANSFER_WORDS)}{_E}")
_TARGET = rf"(?:TO|FROM) (?!{_any(PAYMENT_TARGET_EXCEPT)}{_E})[A-Z0-9]"       # "TO PRIYA", not "TO CARD"
_PAYMENT_TO = re.compile(rf"{_B}{_any(PAYMENT_WORDS)}{_E}(?=.*?{_B}{_TARGET})|{_B}{_any(PAYMENT_BESIDE)}(?= {_TARGET})")
_NAME_FIELD = re.compile(rf"{_B}{_any(NAME_FIELDS)}(?=[^\S\n]*[:=/][^\S\n]*[A-Z0-9])")
_NAME_WORD = re.compile(rf"{_B}{_any(NAME_WORDS)} [A-Z0-9]")
_NOT_WIRE, _NOT_TRANSFER, _NOT_PAYMENT, _NOT_NAME = (_not(WIRE_EXCEPT), _not(TRANSFER_EXCEPT), _not(PAYMENT_EXCEPT),
                                                     _not(NAME_EXCEPT))


def _found(rx: re.Pattern, text: str, never: re.Pattern) -> bool:
    """Whether `rx` matches somewhere in `text` outside every match of `never`."""
    outside = [m.span() for m in never.finditer(text)]
    return any(not any(a <= m.start() and m.end() <= b for a, b in outside) for m in rx.finditer(text))


def _channel_title(key: str, *texts: str) -> str | None:
    """"Zelle", "Venmo", "Bill pay" for a payee key with a channel ("zelle:PRIYA NAIR", "venmo:JOHNDOE"). For a key
    without one, "Cash App", "Apple Cash", ... when the key or the row's texts name a payment app, "Wire" for a wire,
    or "Transfer" for a transfer word, a name field, or a payment or deposit word with "TO" or "FROM" and a word after
    it. A recipient, who can be a person. None for a card merchant."""
    if ":" in key:
        channel = key.split(":", 1)[0]
        apps = {a.lower().replace(" ", "_"): a for a in words().apps}
        return METHOD_TITLES.get(channel) or apps.get(channel) or channel.replace("_", " ").capitalize()
    marked = "\n".join(" ".join(plain(t or "")[:RAW_CHARS].upper().split()) for t in (key, *texts))
    text = re.sub(r"[^A-Z0-9\n]+", " ", marked)
    for title, rx, star in _APP_RES:
        if rx.search(text) or (star is not None and star.search(marked)):
            return title
    if _found(_WIRE, text, _NOT_WIRE):
        return METHOD_TITLES["wire"]
    if (_found(_TRANSFER, text, _NOT_TRANSFER) or _found(_PAYMENT_TO, text, _NOT_PAYMENT)
            or _found(_NAME_FIELD, marked, _NOT_NAME) or _NAME_WORD.search(text)):
        return "Transfer"
    return None


def _store(text: str, key: str, idents: tuple[Identifier, ...], *raws: str, found: bool = True, way: str = "to",
           cleaned: dict[str, str] | None = None) -> tuple[str, str | None]:
    """What goes unless ticked, and what a tick would send instead (None when a tick would add nothing: a card
    merchant, or a recipient whose name the exact pass already replaced). `raws` are the bank lines the row or payee
    came from; found=False (no row holds the payee) masks it as "[payee]", since nothing says what it is. `way` is
    "to" for money out, "from" for money in, and "to or from" for a payee whose rows go both ways (review round 4).
    `cleaned` keeps clean_store's answers for one call, since a store's rows share a name."""
    def clean(name: str) -> str:
        if cleaned is None:
            return clean_store(name, idents)
        if name not in cleaned:
            cleaned[name] = clean_store(name, idents)
        return cleaned[name]

    text = re.sub(r"^[*\s]+", "", text)                      # "SQ *PRIYA NAIR" leaves no star in front
    text = text[:1].upper() + text[1:]
    nameless = not key and text in METHOD_TITLES.values()      # "Zelle", "Check": the line names no one
    title = None if nameless else _channel_title(key, text, *raws)
    if title is None:
        full = clean(text)
        if not found and ":" not in key:
            return "[payee]", (full if full != "[payee]" else None)
        return full, None
    masked = f"{title} {way} [name]"
    if ":" in key and " to " in text:       # "Paypal to Priyanair" from the alias: the title, the way, then the name
        text = f"{title} {way} {text.split(' to ', 1)[1]}"
    full = clean(text)
    return masked, (full if full not in (masked, "[payee]") else None)


def _row_text(p: Payee, merchant: str | None) -> str:
    """"Zelle to Priya Nair" for a recipient, the store's name for a card purchase, "Check" for a numbered check. A name
    given in Review goes unmasked, so the exact pass sees a held number or address whole; clean_store masks it after."""
    if ":" in p.key:
        return known_label(p)
    if merchant and merchant.strip() and p.name == mask_numbers(merchant.strip()):
        return merchant.strip()
    return p.name or merchant or ""


def _words_of(text: str) -> str:
    return " " + " ".join(re.sub(r"[^A-Z0-9]+", " ", make_key("card", text)).split()) + " "


def _named_elsewhere(raw: str | None, merchant: str | None, p: Payee) -> bool:
    """Whether a row goes under a name its own bank line doesn't hold: its payee key is the merchant field, which Review
    or a rule filled in ("Priya Nair" on "POS DEBIT 1234"), and the line doesn't spell it. Such a row also reads every
    line of its payee, since another of them can show a channel (review round 4, A-A6). Any other row is read on its
    own line, so a store's one refund line can't hide every row of that store."""
    if ":" in p.key or not p.key or not merchant or p.key != make_key("card", merchant):
        return False
    return _words_of(p.key) not in _words_of(raw or "")


def _lines_of(env: ToolEnv, wanted: set[str]) -> dict[str, tuple[list[str], set[bool]]]:
    """Every distinct bank line Steward holds for each wanted payee key, and whether its rows bring money in, out or
    both, over all time and in either book, transfers and business rows included, since any of them can show that the
    key is a recipient. A key made from the merchant field ("PRIYA NAIR", after a person named who an Apple Cash row
    paid) carries no channel, so its rows say whether it is a recipient (review round 2). A row named elsewhere reads
    the same lines as its payee alias (_named_elsewhere). "" (a check with no name) is no one's key. payee() is cached
    per (raw, merchant), and load_identifiers has just read the same pairs, so this costs a scan, not a parse."""
    out: dict[str, tuple[list[str], set[bool]]] = {}
    wanted = wanted - {""}
    if not wanted:
        return out
    q = select(Transaction.raw_description, Transaction.merchant, Transaction.amount_cents > 0).distinct()
    for raw, merchant, money_in in env.db.execute(q):
        key = payee(raw, merchant).key
        if key in wanted:
            lines, ways = out.setdefault(key, ([], set()))
            if (raw or "") not in lines:
                lines.append(raw or "")
            ways.add(bool(money_in))
    return out


def _way(ways: set[bool]) -> str:
    """"to" when every row is money out, "from" when every row is money in, "to or from" for both or none."""
    return "from" if ways == {True} else "to" if ways == {False} else "to or from"


@register(T.MERCHANT)
def merchant_detail(inp: dict, env: ToolEnv) -> dict:
    start, end = _day(inp["start"]), _day(inp["end"])
    if end < start:
        raise ToolError(BAD_INPUT)
    if (end - start).days + 1 > T.MERCHANT_DAYS:
        raise ToolError(TOO_LONG)
    wanted = inp.get("payees") or []
    if any(not isinstance(p, str) or (number_of(p) or ("",))[0] != "Payee" for p in wanted):
        raise ToolError(BAD_INPUT)
    keys = {r.target: r.alias for r in env.db.scalars(
        select(AdvisorAlias).where(AdvisorAlias.kind == "payee", AdvisorAlias.alias.in_(wanted)))} if wanted else {}
    idents = store_identifiers(env)
    q = (select(Transaction.posted_at, Transaction.amount_cents, Transaction.category, Transaction.raw_description,
                Transaction.merchant, Transaction.account_id)
         .join(Account, Account.id == Transaction.account_id)
         .where(*clauses(env.book.kind), Transaction.posted_at >= start, Transaction.posted_at <= end)
         .order_by(Transaction.posted_at.desc(), Transaction.id.desc()))
    if inp.get("category"):
        q = q.where(Transaction.category == inp["category"])
    found: list[tuple] = []
    more = False
    if not wanted or keys:
        for posted, cents, category, raw, merchant, account_id in env.db.execute(q):
            p = payee(raw, merchant)
            if wanted and p.key not in keys:
                continue
            if len(found) == T.MERCHANT_ROWS:
                more = True
                break
            found.append((posted, cents, category, raw, merchant, account_id, p, _named_elsewhere(raw, merchant, p)))
    lines = _lines_of(env, set(keys) | {f[6].key for f in found if f[7]})
    rows: list[dict] = []
    home: dict[str, str] = {}
    cleaned: dict[str, str] = {}            # a store's rows share a name, so each name is cleaned once per call
    for posted, cents, category, raw, merchant, account_id, p, elsewhere in found:
        others = lines.get(p.key, ([], set()))[0] if elsewhere else []
        shown, full = _store(_row_text(p, merchant), p.key, idents, raw or "", *others,
                             way="from" if cents > 0 else "to", cleaned=cleaned)
        if full is not None:
            home[f"r{len(rows)}"] = full
        rows.append({"date": posted.isoformat(), "amount_cents": cents,
                     "category": category if category in V.CATEGORIES else "uncategorized",
                     "merchant": shown, "account": Ref("account", account_id)})
    names = []
    for key, alias in sorted(keys.items(), key=lambda kv: number_of(kv[1]) or ("", 0)):
        raws, ways = lines.get(key, ([], set()))
        shown, full = _store(payee_label(key), key, idents, *raws, found=key in lines, way=_way(ways),
                             cleaned=cleaned)
        if full is not None:
            home[f"p{len(names)}"] = full
        names.append({"payee": alias, "name": shown})
    return HomeOnly({"rows": rows, "payees": names, "more": more}, home)
