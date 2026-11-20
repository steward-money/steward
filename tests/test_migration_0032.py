"""0032 (rule labels): merchant_rule gains an amount, a name, a note and its author; its category may be empty when a
name or a note is there; pattern is unique among plain rules, (pattern, amount) among the rest. transaction gains
nickname with no rebuild, and undo_text is new. Downgrade is lossy for names, rule notes and amount rules."""
import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from steward.db.engine import make_engine
from steward.db.keys import ensure_key
from steward.db.migrate import _config, run_migrations

AT = "2026-10-01 00:00:00.000000"
RULE = ("INSERT INTO merchant_rule (pattern, merchant, category, is_business, source, created_at, amount_cents, nickname, note) "
        "VALUES (:p, 'PayPal', :c, NULL, 'correction', :at, :a, :n, :note)")


def _seed(c):
    c.execute(text("INSERT INTO institution (id, name, source) VALUES (1, 'Manual', 'manual')"))
    c.execute(text("INSERT INTO account (id, institution_id, name, type, owner, source, currency, is_active, created_at) "
                   "VALUES (1, 1, 'Checking', 'checking', 'joint', 'manual', 'USD', 1, :at)"), {"at": AT})
    c.execute(text('INSERT INTO "transaction" (id, account_id, posted_at, amount_cents, raw_description, category, is_business, '
                   "is_transfer, category_source, imported_at) VALUES (1, 1, '2026-10-03', -1399, 'PAYPAL *DISNEYPLUS', "
                   "'uncategorized', 0, 0, 'none', :at)"), {"at": AT})
    for rid, pattern, merchant, category, source in ((1, "PAYPAL", "PayPal", "shopping", "correction"),
                                                     (2, "STREAMFLIX", "Streamflix", "subscriptions", "rule")):
        c.execute(text("INSERT INTO merchant_rule (id, pattern, merchant, category, is_business, source, created_at) "
                       "VALUES (:i, :p, :m, :c, NULL, :s, :at)"), {"i": rid, "p": pattern, "m": merchant, "c": category, "s": source, "at": AT})


def _at_0031(settings):
    eng = make_engine(settings.db_path, ensure_key(settings.key_path))
    command.upgrade(_config(eng), "0031")
    with eng.begin() as c:
        _seed(c)
    return eng


def test_0032_keeps_every_rule_and_adds_partial_uniques(settings):
    eng = _at_0031(settings)
    run_migrations(eng)
    with eng.connect() as c:
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0038"
        assert [tuple(r) for r in c.execute(text(
            "SELECT id, pattern, merchant, category, source, amount_cents, nickname, note, note_by, note_by_name "
            "FROM merchant_rule ORDER BY id"))] == [
            (1, "PAYPAL", "PayPal", "shopping", "correction", None, None, None, None, None),
            (2, "STREAMFLIX", "Streamflix", "subscriptions", "rule", None, None, None, None, None)]
        assert c.execute(text('SELECT nickname FROM "transaction" WHERE id = 1')).scalar() is None
    with eng.begin() as c:
        c.execute(text(RULE), {"p": "PAYPAL", "c": None, "at": AT, "a": -1399, "n": "Disney+", "note": None})   # names only
        c.execute(text(RULE), {"p": "PAYPAL", "c": "shopping", "at": AT, "a": -999, "n": None, "note": None})   # another amount
        c.execute(text('UPDATE "transaction" SET nickname = \'Disney+\' WHERE id = 1'))
        c.execute(text("INSERT INTO undo_text (token, model, row_key, field, value, written) "
                       "VALUES ('t', 'txn', '1', 'nickname', NULL, 'Disney+')"))
    for params in ({"p": "PAYPAL", "c": "shopping", "at": AT, "a": -1399, "n": None, "note": None},   # the same amount twice
                   {"p": "PAYPAL", "c": "gifts", "at": AT, "a": None, "n": None, "note": None},       # a second plain PAYPAL
                   {"p": "VENMO", "c": None, "at": AT, "a": None, "n": None, "note": None}):          # a rule that does nothing
        with eng.connect() as c, pytest.raises(IntegrityError):
            c.execute(text(RULE), params)
            c.commit()
    with eng.connect() as c:
        assert c.execute(text("PRAGMA foreign_keys")).scalar() == 1     # the 891c4e5 regression: still enforced
        with pytest.raises(IntegrityError):
            c.execute(text('INSERT INTO "transaction" (account_id, posted_at, amount_cents, raw_description, category, '
                           "is_business, is_transfer, category_source, imported_at) VALUES (999, '2026-10-03', -1, 'X', "
                           "'uncategorized', 0, 0, 'none', :at)"), {"at": AT})
            c.commit()
    eng.dispose()


def test_0032_downgrade_keeps_one_plain_rule_per_pattern_and_drops_the_labels(settings):
    eng = _at_0031(settings)
    run_migrations(eng)
    with eng.begin() as c:
        c.execute(text(RULE), {"p": "PAYPAL", "c": None, "at": AT, "a": -1399, "n": "Disney+", "note": "for Dr. Alvarez"})
        c.execute(text(RULE), {"p": "PAYPAL", "c": "gifts", "at": "2026-10-20 00:00:00.000000", "a": -999, "n": None, "note": None})
        c.execute(text(RULE), {"p": "VENMO", "c": "gifts", "at": AT, "a": -2500, "n": None, "note": None})   # an amount rule alone
    command.downgrade(_config(eng), "0031")
    with eng.connect() as c:
        assert [tuple(r) for r in c.execute(text("SELECT id, pattern, category FROM merchant_rule ORDER BY id"))] == [
            (1, "PAYPAL", "shopping"), (2, "STREAMFLIX", "subscriptions"), (5, "VENMO", "gifts")]
    assert {x["name"] for x in inspect(eng).get_columns("merchant_rule")} == {
        "id", "pattern", "merchant", "category", "is_business", "source", "created_at"}
    assert "nickname" not in {x["name"] for x in inspect(eng).get_columns("transaction")}
    assert "undo_text" not in inspect(eng).get_table_names()
    with eng.connect() as c, pytest.raises(IntegrityError):
        c.execute(text("INSERT INTO merchant_rule (pattern, merchant, category, source, created_at) "
                       "VALUES ('PAYPAL', 'PayPal', 'gifts', 'rule', :at)"), {"at": AT})
        c.commit()
    eng.dispose()
