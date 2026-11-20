"""Who a payment went to (watching decision 2). Card purchases: Transactions' own merchant name when it has one (the
categorizer's rules and corrections), else the bank text cleaned by services.transactions.tidy and then of store
numbers, cities and gift-card words. A line that starts "PURCHASE AUTHORIZED ON 03/15" (Wells Fargo's card lines, and
its "PURCHASE INTL", "PURCHASE WITH CASH BACK $ 20.00" and RETURN forms) is read from after the date, without the
"S<digits>" reference and "CARD 1234" at its end (public issue #49). Zelle, wires, checks and bill pay: the recipient
after "TO", normalised for case and spacing. The word lists are in data/watch.json (tests/test_watch_payees.py)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import cache, lru_cache
from pathlib import Path

from steward.services.transactions import tidy
from steward.inventory.present import mask_numbers
from steward.watch.vocab import METHOD_TITLES

DATA = Path(__file__).parent / "data" / "watch.json"


@dataclass(frozen=True)
class Payee:
    key: str      # "" when there is no name to compare (a numbered paper check, ruling 14)
    name: str
    method: str = "card"


@dataclass(frozen=True)
class Words:
    scam: tuple[tuple[str, str, str, re.Pattern], ...]
    methods: tuple[tuple[str, re.Pattern], ...]
    to: re.Pattern                 # the "TO" before a recipient (_recipient)
    name: re.Pattern               # a recipient's name: a capital, then letters, spaces and . , ' & -
    end: re.Pattern                # what may follow the name: "ON", "CONF", "#", a word with a digit, the end
    cash: re.Pattern
    lead: tuple[str, ...]
    authorized: re.Pattern         # "PURCHASE AUTHORIZED ON 03/15 ": a lead-in and its date, matched at the start only.
    #                                "PURCHASE WITH CASH BACK $ 20.00 RETURN AUTHORIZED ON": a cash-back amount and a
    #                                RETURN may come between the lead-in and AUTHORIZED ON
    drop: re.Pattern
    states: frozenset[str]
    apps: frozenset[str]
    app: re.Pattern                # "PAYPAL *JOHNDOE": a payment app, a star and the person paid
    title_words: frozenset[str]    # vowel-less words that are still words, not acronyms ("Ltd")


def _either(words) -> str:
    return "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))


def _alts(words) -> re.Pattern:
    return re.compile(rf"(?<![A-Z0-9])(?:{_either(words)})(?![A-Z0-9])")


@cache
def words() -> Words:
    """Every pattern here is read by tests/test_linear_parsing.py, since they all run on a transaction's raw text."""
    raw = json.loads(DATA.read_text())
    to = raw["recipient"]
    apps = frozenset(raw["payment_apps"])
    authorized = raw["authorized"]
    return Words(
        scam=tuple((s["kind"], s["word"], s["title"], _alts(s["words"])) for s in raw["scam"]),
        methods=tuple((m["method"], re.compile(m["pattern"])) for m in raw["methods"]),
        to=re.compile(to["to"]), name=re.compile(to["name"]), end=re.compile(to["end"]),
        cash=_alts(raw["cash"]),
        lead=tuple(sorted(raw["lead"], key=len, reverse=True)), drop=_alts(raw["drop"]),
        authorized=re.compile(rf"(?:{_either(authorized)})(?: \$ ?\d+(?:\.\d{{2}})?)?(?: RETURN)? AUTHORIZED ON"
                              rf"(?: \d{{1,2}}/\d{{1,2}}(?:/\d{{2,4}})?)?(?= |$)"),
        states=frozenset(raw["states"]), apps=apps,
        app=re.compile(rf"(?<![A-Z0-9])({_either(a.upper() for a in apps)})\s*\*\s*(?P<name>[^\s*]+)"),
        title_words=frozenset(raw["title_words"]),
    )


def _upper(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().upper())


def make_key(channel: str, name: str) -> str:
    """The one place a payee's identity is built, for matching, Known payees and display alike. A card merchant's key
    is its bare name; every other channel ("zelle", "wire", "bill_pay", "paypal", "venmo", ...) is "channel:NAME".
    ":" and "|" are dropped from names, so no name can spell another channel's prefix."""
    bare = " ".join(re.sub(r"[.,':|]", "", name.upper()).split())
    return bare if channel == "card" or not bare else f"{channel}:{bare}"


def display(text: str) -> str:
    """"CVS PHARMACY" → "CVS Pharmacy": a word of 3 or more letters with no vowel stays in capitals."""
    out = []
    titled = words().title_words
    for w in text.split():
        letters = re.sub(r"[^A-Z]", "", w.upper())
        out.append(w.upper() if len(letters) >= 3 and not re.search(r"[AEIOUY]", letters) and letters not in titled
                   else w[:1].upper() + w[1:].lower())
    return " ".join(out)


_REF = re.compile(r"[SP]\d+")         # the reference code before "CARD 1234" on a Wells Fargo card line


def _after_authorized(s: str) -> str:
    """"STARBUCKS STORE 05812 SEATTLE WA S382074123456789 CARD" (tidy() has taken the "1234"): the store and its place,
    without the trailing "CARD", a card's last 4 and the reference codes. Word by word from the end, like tidy()."""
    toks = s.split()
    if len(toks) >= 2 and toks[-2] == "CARD" and re.fullmatch(r"\d{4}", toks[-1]):
        toks = toks[:-1]
    if toks and toks[-1] == "CARD":
        toks.pop()
    while toks and _REF.fullmatch(toks[-1]):
        toks.pop()
    return " ".join(toks)


def _clean_card(s: str) -> str:
    W = words()
    s = tidy(s)
    if (m := W.authorized.match(s)) is not None:
        s = _after_authorized(s[m.end():])     # every card line at that bank would otherwise be "AUTHORIZED ON"
    s = W.drop.sub(" ", s).replace("/", " ")
    s = re.sub(r"\s+", " ", s).strip()
    for lead in W.lead:
        if s.startswith(lead + " "):
            s = s[len(lead) + 1:]
            break
    out: list[str] = []
    for tok in s.split():
        if tok.startswith("#") or re.search(r"\d", tok):
            if out:
                break          # a store number ends the name; everything after it is the place
            continue           # a number before the name ("CHECKCARD 1015 HOME DEPOT") is skipped
        out.append(tok)
    if len(out) >= 3 and out[-1] in W.states:
        out = out[:-2]         # "TARGET BREA CA": the city and the state
    return " ".join(out)


def _app_recipient(s: str) -> Payee | None:
    """"PAYPAL *JOHNDOE 4029357733 CA": the person paid is the name after the star, whatever follows it. Two people
    paid through the same app are two payees."""
    W = words()
    apps = {a.upper(): a for a in W.apps}
    m = W.app.search(s)
    if not m:
        return None
    name = re.sub(r"[^A-Z0-9&'-]", "", m.group("name").upper())
    if not re.search(r"[A-Z]", name):
        return None
    app = m.group(1)
    return Payee(make_key(app.lower().replace(" ", "_"), name), f"{apps[app]} to {display(mask_numbers(name))}", "card")


def _recipient(s: str) -> str:
    """The name after "TO" in `s` (upper case, one space at a time), or "": from its first capital to the first place
    `end` matches, inside one run of name characters. When a run ends with no `end` in it, no later "TO" inside the
    run can find one either, since its name would stop at the same place, so the search goes on after the run and
    each character is read a fixed number of times. One pattern with a lazy name read the run again from every "TO"
    in it: a 5 MB upload's "ZELLE TO A TO A ..." held the server for hours (SEC-3's class)."""
    W = words()
    at = 0
    while (to := W.to.search(s, at)) is not None:
        run = W.name.match(s, to.end())
        if run is None:
            at = to.end()
            continue
        for stop in range(run.start() + 1, run.end() + 1):
            if W.end.match(s, stop):
                return s[run.start():stop]
        at = run.end()
    return ""


@lru_cache(maxsize=65_536)
def payee(raw: str, merchant: str | None = None) -> Payee:
    W = words()
    s = _upper(raw)
    for method, rx in W.methods:
        if rx.search(s):
            name = re.sub(r"\s+", " ", _recipient(s)).strip(" .,-")
            if not re.search(r"[A-Z]", name):
                return Payee("", METHOD_TITLES[method], method)
            return Payee(make_key(method, name), display(mask_numbers(name)), method)
    app = _app_recipient(s)
    if app is not None:
        return app
    if merchant and merchant.strip() and merchant.strip() not in W.apps:
        return Payee(make_key("card", merchant), mask_numbers(merchant.strip()), "card")
    name = _clean_card(s)
    return Payee(make_key("card", name), display(mask_numbers(name)), "card") if name else Payee("", mask_numbers(raw.strip()), "card")


def scam_kind(raw: str) -> dict | None:
    s = _upper(raw)
    for kind, word, title, rx in words().scam:
        if rx.search(s):
            return {"kind": kind, "word": word, "title": title}
    return None


def is_cash(raw: str) -> bool:
    return bool(words().cash.search(_upper(raw)))


def known_label(p: Payee) -> str:
    """How Known payees lists it: "Brea Family Dental", or "Zelle to J. Smith" (ruling 24)."""
    return p.name if p.method == "card" else f"{METHOD_TITLES[p.method]} to {p.name}"
