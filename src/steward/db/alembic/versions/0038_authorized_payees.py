"""authorized payees: a set-aside keyed on "AUTHORIZED ON" takes its store's payee key

Revision ID: 0038
Revises: 0037

watch.payees.payee() read every card line that starts "PURCHASE AUTHORIZED ON 03/15" (Wells Fargo writes its debit
card lines that way) as the payee "AUTHORIZED ON", and its "PURCHASE RETURN", "PURCHASE INTL", "PURCHASE WITH CASH BACK
$ 20.00" and "MONEY TRANSFER" lines as more keys like it (public issue #49). A line with no date gave "AUTHORIZED ON"
and the store's first words. It now reads the store after the date. Payee keys are worked out from a row's text each
time they are needed, so Transactions, Watching, Review and the advisor's tools take the new keys with no change here.
Rules are keyed on normalize() patterns, not payee keys, so none moves.

Five places keep a payee key. set_aside matches its payments by key, so one keyed on an old key would match nothing
from now on and nothing would say so. Each such set-aside takes the key its matched payments (auto or by hand) give
now, when they all give the same one; otherwise (no payments, or two stores) it keeps no payee, so it matches by its
category the way a set-aside with no payee does, and its page shows no payee to choose again. Each change is logged
by id. A flag's facts keep the payee it was raised with; Mark Expected and the flag page read the payee from the
flag's transaction as it is now (watch.flags.flag_payee), so an old flag adds the store's key to Known payees, not the
old one, and needs no change here. The other three are left: a Known payee "Authorized On" stays listed and can be
removed, a dismissed suggestion for it never comes back on its own, and an advisor alias is never changed and goes as
[payee] when no row holds its key. Downgrade leaves the new keys; under the old code a re-keyed set-aside matches only
that store's lines at other banks, since the old code names every Wells Fargo line "AUTHORIZED ON" again.

authorized_key() is a frozen copy of what payee() gives now on a line the old parser named with an old key (a
migration must not import app code that can change later); tests/test_migration_0038.py checks that it agrees today,
and that was_old() holds every key the old parser gave such a line.
"""
import logging
import re

import sqlalchemy as sa
from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.runtime.migration")

# What the old parser gave a dated line: it dropped one lead word ("PURCHASE", "RECURRING PAYMENT") and stopped at the
# first word with a digit (the date, or the cash-back amount).
OLD_KEYS = frozenset({"AUTHORIZED ON", "RETURN AUTHORIZED ON", "MONEY TRANSFER AUTHORIZED ON",
                      "MONEY TRANSFER RETURN AUTHORIZED ON", "INTL AUTHORIZED ON", "INTL RETURN AUTHORIZED ON",
                      "WITH CASH BACK $", "WITH CASH BACK"})
# A line with no date ran on into the store: "AUTHORIZED ON STARBUCKS".
_OLD_DATELESS = re.compile(r"(?:MONEY TRANSFER |INTL )?(?:RETURN )?AUTHORIZED ON [^\n]+")


def was_old(key: str | None) -> bool:
    return bool(key) and (key in OLD_KEYS or _OLD_DATELESS.fullmatch(key) is not None)


# ---- a frozen copy of watch.payees.payee() on a Wells Fargo card line, as of this revision --------------------------
_WS = re.compile(r"\s+")
_CODE = re.compile(r"#?\d[\d/\-]*|\*\w+")
_STAR = re.compile(r"\*\w+$")
_AUTHORIZED = re.compile(r"(?:PURCHASE WITH CASH BACK|RECURRING PAYMENT|MONEY TRANSFER|PURCHASE INTL|PURCHASE)"
                         r"(?: \$ ?\d+(?:\.\d{2})?)?(?: RETURN)? AUTHORIZED ON"
                         r"(?: \d{1,2}/\d{1,2}(?:/\d{2,4})?)?(?= |$)")
# A line payee() reads by its channel (Zelle, a wire, bill pay, a payment app's star) never had an old key.
_CHANNEL = re.compile(r"\bZELLE\b|\bWIRE\b|\bBILL ?PAY(MENT)?\b|\bBILLPAY\b"
                      r"|(?<![A-Z0-9])(?:CASH APP|PAYPAL|VENMO|ZELLE)\s*\*\s*[^\s*]*[A-Z]")
_DROP = re.compile(r"(?<![A-Z0-9])(?:PREPAID CARD|GIFTCARD|GIFT CARD|GIFT CRD)(?![A-Z0-9])")
_LEAD = ("DEBIT CARD PURCHASE", "RECURRING PAYMENT", "POS PURCHASE", "DEBIT CARD", "CHECK CARD", "CHECKCARD",
         "PURCHASE", "POS", "SQ", "TST")
_REF = re.compile(r"[SP]\d+")
_STATES = frozenset("AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM "
                    "NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split())
_APPS = frozenset({"Zelle", "Venmo", "PayPal", "Cash App"})


def _tidy(s: str) -> str:
    s = _STAR.sub("", s)
    words = s.split(" ")
    while len(words) > 1 and _CODE.fullmatch(words[-1]):
        words.pop()
    return " ".join(words).strip()[:200]


def authorized_key(raw: str) -> str | None:
    """The payee key payee() gives now on a line that starts with a Wells Fargo lead-in and that no channel names, or
    None for any other line."""
    s = _WS.sub(" ", (raw or "").strip().upper())
    if _CHANNEL.search(s):
        return None
    s = _tidy(s)
    m = _AUTHORIZED.match(s)
    if m is None:
        return None
    toks = s[m.end():].split()
    if len(toks) >= 2 and toks[-2] == "CARD" and re.fullmatch(r"\d{4}", toks[-1]):
        toks = toks[:-1]
    if toks and toks[-1] == "CARD":
        toks.pop()
    while toks and _REF.fullmatch(toks[-1]):
        toks.pop()
    s = _WS.sub(" ", _DROP.sub(" ", " ".join(toks)).replace("/", " ")).strip()
    for lead in _LEAD:
        if s.startswith(lead + " "):
            s = s[len(lead) + 1:]
            break
    out: list[str] = []
    for tok in s.split():
        if tok.startswith("#") or re.search(r"\d", tok):
            if out:
                break
            continue
        out.append(tok)
    if len(out) >= 3 and out[-1] in _STATES:
        out = out[:-2]
    return " ".join(re.sub(r"[.,':|]", "", " ".join(out)).split())
# ---------------------------------------------------------------------------------------------------------------------


def upgrade() -> None:
    c = op.get_bind()
    found = [sid for sid, key in c.execute(sa.text("SELECT id, payee_key FROM set_aside WHERE payee_key IS NOT NULL "
                                                    "ORDER BY id")).all() if was_old(key)]
    for sid in found:
        rows = c.execute(sa.text('SELECT t.raw_description, t.merchant FROM set_aside_match m JOIN "transaction" t '
                                 "ON t.id = m.transaction_id WHERE m.set_aside_id = :s AND m.how IN ('auto', 'hand')"),
                         {"s": sid}).all()
        # A payment named in Review or by a rule (its merchant field) never had an old key, so says nothing here.
        keys = {authorized_key(raw) for raw, merchant in rows
                if not (merchant or "").strip() or (merchant or "").strip() in _APPS} - {None, ""}
        new = next(iter(keys)) if len(keys) == 1 else None
        c.execute(sa.text("UPDATE set_aside SET payee_key = :k WHERE id = :id"), {"k": new, "id": sid})
        # By id only: a payee key is a piece of a bank line.
        log.info("0038 set_aside #%d: %s", sid, "payee key now its payments' store" if new else
                 f"no payee now ({len(keys)} stores among its payments); it matches by category")


def downgrade() -> None:
    pass
