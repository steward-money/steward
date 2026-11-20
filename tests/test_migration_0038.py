"""0038 (authorized payees): a set-aside keyed on a payee key the old parser gave every Wells Fargo card line
("AUTHORIZED ON", public issue #49) takes the key its matched payments give now when they agree, and otherwise keeps no
payee; every other set-aside, Known payee, dismissed suggestion and advisor alias is left as it was."""
import importlib.util
import logging
import re
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import text

from steward.db.engine import make_engine
from steward.db.keys import ensure_key
from steward.db.migrate import _config, run_migrations
from steward.services.transactions import tidy
from steward.watch.payees import _upper, make_key, payee, words
from tests.test_watch_payees import AUTHORIZED

AT = "2026-10-01 00:00:00.000000"
MIGRATION = Path(__file__).resolve().parents[1] / "src/steward/db/alembic/versions/0038_authorized_payees.py"

NETFLIX = "RECURRING PAYMENT AUTHORIZED ON 09/01 NETFLIX.COM NETFLIX.COM CA S462061234567890 CARD 1234"
NETFLIX_2 = "RECURRING PAYMENT AUTHORIZED ON 10/01 NETFLIX.COM NETFLIX.COM CA S462071234567890 CARD 1234"
SHELL = "PURCHASE AUTHORIZED ON 09/14 SHELL OIL 57444212400 HOUSTON TX P000000381234567 CARD 1234"
TARGET = "PURCHASE RETURN AUTHORIZED ON 09/16 TARGET T-1234 BREA CA S303075012345678 CARD 1234"
SAFEWAY = "PURCHASE WITH CASH BACK $ 20.00 AUTHORIZED ON 09/15 SAFEWAY #1234 BREA CA P000000381234567 CARD 1234"
TESCO = "PURCHASE INTL AUTHORIZED ON 09/15 TESCO STORES LONDON GB S382074123456789 CARD 1234"
# Lines with no date: the old parser ran on into the store's name ("AUTHORIZED ON STARBUCKS").
DATELESS = ["PURCHASE AUTHORIZED ON STARBUCKS SEATTLE WA", "PURCHASE RETURN AUTHORIZED ON TARGET T-1234 BREA CA",
            "MONEY TRANSFER AUTHORIZED ON APPLE CASH SENT MONEY CA"]


def _frozen():
    spec = importlib.util.spec_from_file_location("m0038", MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _txn(c, tid, raw, merchant=None):
    c.execute(text('INSERT INTO "transaction" (id, account_id, posted_at, amount_cents, raw_description, merchant, '
                   "category, is_business, is_transfer, category_source, imported_at) VALUES (:id, 1, '2026-09-01', "
                   "-1599, :raw, :m, 'subscriptions', 0, 0, 'none', :at)"), {"id": tid, "raw": raw, "m": merchant, "at": AT})


def _set_aside(c, sid, key, category="subscriptions"):
    c.execute(text("INSERT INTO set_aside (id, name, kind, amount_cents, due_month, due_day, first_month, payee_key, "
                   "category, created_at, updated_at) VALUES (:id, 'Plan', 'yearly', 1599, 9, 1, '2026-09-01', :k, :c, "
                   ":at, :at)"), {"id": sid, "k": key, "c": category, "at": AT})


def _match(c, mid, sid, tid, due, how="auto"):
    c.execute(text("INSERT INTO set_aside_match (id, transaction_id, set_aside_id, due_on, how, late, created_at) "
                   "VALUES (:id, :t, :s, :d, :h, 0, :at)"), {"id": mid, "t": tid, "s": sid, "d": due, "h": how, "at": AT})


def _at_0037(settings):
    eng = make_engine(settings.db_path, ensure_key(settings.key_path))
    command.upgrade(_config(eng), "0037")
    with eng.begin() as c:
        c.execute(text("INSERT INTO institution (id, name, source) VALUES (1, 'Manual', 'manual')"))
        c.execute(text("INSERT INTO account (id, institution_id, name, type, owner, source, currency, is_active, "
                       "created_at) VALUES (1, 1, 'Checking', 'checking', 'joint', 'manual', 'USD', 1, :at)"), {"at": AT})
        for tid, raw in ((1, NETFLIX), (2, NETFLIX_2), (3, SHELL), (4, TARGET), (5, "STREAMBOX 800-555-0100"),
                         (6, NETFLIX.replace("09/01", "08/01")), (7, SAFEWAY), (8, TESCO), (9, DATELESS[0])):
            _txn(c, tid, raw)
        _set_aside(c, 1, "AUTHORIZED ON")                  # its payments are all Netflix: takes Netflix's key
        _match(c, 1, 1, 1, "2025-09-01")
        _match(c, 2, 1, 2, "2026-09-01", "hand")
        _set_aside(c, 2, "AUTHORIZED ON")                  # Netflix and Shell: no one store, so no payee
        _match(c, 3, 2, 3, "2025-09-01")
        _match(c, 6, 2, 6, "2026-09-01")
        _set_aside(c, 3, "RETURN AUTHORIZED ON", None)     # nothing matched yet: no payee either
        _set_aside(c, 4, "STREAMBOX")                      # another store's key: left alone
        _match(c, 4, 4, 5, "2026-09-01")
        _set_aside(c, 5, "AUTHORIZED ON")                  # only a refused pairing: that says nothing of the store
        _match(c, 5, 5, 4, "2026-09-01", "refused")
        _set_aside(c, 6, "WITH CASH BACK $")               # cash back at Safeway: takes Safeway's key
        _match(c, 7, 6, 7, "2026-09-01")
        _set_aside(c, 7, "INTL AUTHORIZED ON")             # a card used abroad: takes Tesco's key
        _match(c, 8, 7, 8, "2026-09-01")
        _set_aside(c, 8, "AUTHORIZED ON STARBUCKS")        # a dateless line's old key: takes Starbucks's
        _match(c, 9, 8, 9, "2026-09-01")
    return eng


def test_0038_rekeys_set_asides_from_their_payments_and_leaves_the_rest(settings, caplog):
    eng = _at_0037(settings)
    with eng.begin() as c:
        c.execute(text("INSERT INTO set_aside_dismissed (id, payee_key, category, created_at) "
                       "VALUES (1, 'AUTHORIZED ON', 'subscriptions', :at)"), {"at": AT})
        c.execute(text("INSERT INTO advisor_alias (kind, target, alias, created_at) "
                       "VALUES ('payee', 'AUTHORIZED ON', 'Payee 7', :at)"), {"at": AT})
        c.execute(text("INSERT INTO watch_known (id, payee_key, payee_name, check_name, added_at, added_by) "
                       "VALUES (1, 'AUTHORIZED ON', 'Authorized On', 'payee', :at, 1)"), {"at": AT})
    with caplog.at_level(logging.INFO, logger="alembic.runtime.migration"):
        run_migrations(eng)
    netflix = payee(NETFLIX).key
    with eng.connect() as c:
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0038"
        assert [tuple(r) for r in c.execute(text("SELECT id, payee_key, category FROM set_aside ORDER BY id"))] == [
            (1, netflix, "subscriptions"), (2, None, "subscriptions"), (3, None, None), (4, "STREAMBOX", "subscriptions"),
            (5, None, "subscriptions"), (6, "SAFEWAY", "subscriptions"), (7, "TESCO STORES LONDON GB", "subscriptions"),
            (8, "STARBUCKS", "subscriptions")]
        assert c.execute(text("SELECT payee_key FROM set_aside_dismissed")).scalar() == "AUTHORIZED ON"
        assert c.execute(text("SELECT target FROM advisor_alias WHERE alias = 'Payee 7'")).scalar() == "AUTHORIZED ON"
        assert c.execute(text("SELECT payee_key FROM watch_known")).scalar() == "AUTHORIZED ON"
    logged = "\n".join(r.getMessage() for r in caplog.records if "0038" in r.getMessage())
    assert all(f"#{i}" in logged for i in (1, 2, 3, 5, 6, 7, 8)) and "#4" not in logged
    assert "NETFLIX" not in logged.upper()        # by id only: a payee key is a piece of a bank line


def test_0038_on_an_empty_book_changes_nothing(settings):
    eng = make_engine(settings.db_path, ensure_key(settings.key_path))
    run_migrations(eng)
    with eng.connect() as c:
        assert c.execute(text("SELECT count(*) FROM set_aside")).scalar() == 0


CORPUS = [(raw, expected.key) for raw, expected, _ in AUTHORIZED] + [(raw, payee(raw).key) for raw in DATELESS]


@pytest.mark.parametrize("raw, key", CORPUS)
def test_the_migrations_frozen_copy_agrees_today(raw, key):
    """On a line the old parser named "AUTHORIZED ON", the frozen copy gives the key payee() gives now; on a line
    payee() reads by its channel (a payment app's star), it gives nothing, since that line never had the old key."""
    assert _frozen().authorized_key(raw) == (None if ":" in key else payee(raw).key)


def _old_card_key(raw: str) -> str:
    """payee() on a card line before this revision: watch/payees.py's _clean_card with no AUTHORIZED ON step (the
    word lists and tidy() it reads are unchanged by it)."""
    W = words()
    s = re.sub(r"\s+", " ", W.drop.sub(" ", tidy(_upper(raw))).replace("/", " ")).strip()
    for lead in W.lead:
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
    if len(out) >= 3 and out[-1] in W.states:
        out = out[:-2]
    return make_key("card", " ".join(out))


@pytest.mark.parametrize("raw, key", [(raw, key) for raw, key in CORPUS if ":" not in key])
def test_was_old_holds_the_key_the_old_parser_gave_each_line_and_not_the_new_one(raw, key):
    m = _frozen()
    assert m.was_old(_old_card_key(raw)), _old_card_key(raw)
    assert not m.was_old(key)


def test_the_old_parser_copy_is_the_old_parser():
    """The copy gives what the old code gave on the issue's lines, and agrees with payee() today on lines no lead-in
    starts."""
    assert _old_card_key(AUTHORIZED[0][0]) == "AUTHORIZED ON"
    assert _old_card_key(SAFEWAY) == "WITH CASH BACK $" and _old_card_key(TESCO) == "INTL AUTHORIZED ON"
    for raw in ("STARBUCKS STORE 05812 SEATTLE WA", "CHECKCARD 1015 HOME DEPOT #4410 BREA CA", "SQ *BLUE BOTTLE COF"):
        assert _old_card_key(raw) == payee(raw).key
