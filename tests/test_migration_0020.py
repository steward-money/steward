"""0020 adds set_aside, set_aside_match and set_aside_dismissed (phase 11c, Data) and downgrades cleanly."""
import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from steward.db.engine import make_engine
from steward.db.keys import ensure_key
from steward.db.migrate import _config, run_migrations

NEW = {"set_aside", "set_aside_match", "set_aside_dismissed"}
AT = "'2026-10-25 00:00:00.000000'"


def test_0020_adds_the_set_aside_tables(settings):
    eng = make_engine(settings.db_path, ensure_key(settings.key_path))
    command.upgrade(_config(eng), "0019")
    before = set(inspect(eng).get_table_names())
    run_migrations(eng)
    assert set(inspect(eng).get_table_names()) == before | NEW | {"transaction_note", "undo_entry", "reimburser", "trip", "reimburse_rule", "reimburse_settlement", "account_feed", "held_feed", "same_account_answer", "feed_txn_alias", "expense_ratio_override", "positions_mapping", "positions_import", "undo_text", "home_visit", "import_set", "import_layout", "import_hint", "statement_stage", "statement_layout"}
    with eng.connect() as c:
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0038"
    assert {c["name"] for c in inspect(eng).get_columns("set_aside")} == {
        "id", "name", "kind", "amount_cents", "due_month", "due_day", "second_month", "second_day", "due_year",
        "first_month", "account_id", "payee_key", "category", "estimate", "notes", "archived_at", "created_at",
        "updated_at", "updated_by"}
    assert {c["name"] for c in inspect(eng).get_columns("set_aside_match")} == {
        "id", "transaction_id", "set_aside_id", "due_on", "how", "late", "created_at", "created_by"}
    assert {c["name"] for c in inspect(eng).get_columns("set_aside_dismissed")} == {
        "id", "payee_key", "category", "created_at", "created_by"}
    command.downgrade(_config(eng), "0019")
    assert set(inspect(eng).get_table_names()) == before
    eng.dispose()


def _sa(kind: str, cols: str) -> str:
    return (f"INSERT INTO set_aside (name, kind, amount_cents, first_month, created_at, updated_at{cols[0]}) "
            f"VALUES ('X', '{kind}', 100, '2026-11-01', {AT}, {AT}{cols[1]})")


def test_each_kind_needs_its_own_fields(settings):
    eng = make_engine(settings.db_path, ensure_key(settings.key_path))
    run_migrations(eng)
    good = [_sa("yearly", (", due_month, due_day", ", 8, 14")),
            _sa("twice_yearly", (", due_month, due_day, second_month, second_day", ", 11, 1, 4, 10")),
            _sa("one_off", (", due_year, due_month", ", 2028, 12"))]
    for sql in good:
        with eng.begin() as c:
            c.execute(text(sql))
    bad = [_sa("yearly", ("", "")),                                                   # no due month and day
           _sa("twice_yearly", (", due_month, due_day", ", 11, 1")),                   # no second date
           _sa("one_off", (", due_month", ", 12")),                                    # no due year
           _sa("weekly", (", due_month, due_day", ", 1, 1")),                          # not a kind
           _sa("yearly", (", due_month, due_day", ", 13, 1")),                         # no 13th month
           _sa("yearly", (", due_month, due_day", ", 1, 32")),                         # no 32nd day
           _sa("yearly", (", due_month, due_day", ", 1, 1")).replace("100,", "0,")]    # nothing to set aside
    for sql in bad:
        with pytest.raises(IntegrityError), eng.begin() as c:
            c.execute(text(sql))
    eng.dispose()


def test_a_match_is_one_per_payment_and_one_per_installment(settings):
    eng = make_engine(settings.db_path, ensure_key(settings.key_path))
    run_migrations(eng)
    with eng.begin() as c:
        c.execute(text("INSERT INTO institution (id, name, source) VALUES (1, 'Manual', 'manual')"))
        c.execute(text("INSERT INTO account (id, institution_id, name, type, owner, source, currency, is_active, created_at) "
                       f"VALUES (1, 1, 'Checking', 'checking', 'joint', 'manual', 'USD', 1, {AT})"))
        for i in (1, 2):
            c.execute(text(f"INSERT INTO \"transaction\" (id, account_id, posted_at, amount_cents, raw_description, category, "
                           f"is_business, is_transfer, category_source, imported_at, business_pct, business_source) "
                           f"VALUES ({i}, 1, '2026-11-01', -421000, 'COUNTY TAX', 'taxes', 0, 0, 'none', {AT}, 0, 'none')"))
        c.execute(text(_sa("twice_yearly", (", id, due_month, due_day, second_month, second_day", ", 1, 11, 1, 4, 10"))))
        c.execute(text(f"INSERT INTO set_aside_match (transaction_id, set_aside_id, due_on, how, late, created_at) "
                       f"VALUES (1, 1, '2026-11-01', 'auto', 0, {AT})"))
        c.execute(text(f"INSERT INTO set_aside_match (transaction_id, set_aside_id, due_on, how, late, created_at) "
                       f"VALUES (NULL, 1, '2027-04-10', 'skipped', 0, {AT})"))
        c.execute(text(f"INSERT INTO set_aside_match (transaction_id, set_aside_id, due_on, how, late, created_at) "
                       f"VALUES (2, 1, '2026-11-01', 'refused', 0, {AT})"))      # a refusal of the same installment is fine
    bad = [f"INSERT INTO set_aside_match (transaction_id, set_aside_id, due_on, how, late, created_at) VALUES (1, 1, '2027-04-10', 'hand', 0, {AT})",   # the payment twice
           f"INSERT INTO set_aside_match (transaction_id, set_aside_id, due_on, how, late, created_at) VALUES (NULL, 1, '2026-11-01', 'skipped', 0, {AT})",  # the installment twice
           f"INSERT INTO set_aside_match (transaction_id, set_aside_id, due_on, how, late, created_at) VALUES (NULL, 1, '2028-11-01', 'auto', 0, {AT})",     # auto needs a payment
           f"INSERT INTO set_aside_match (transaction_id, set_aside_id, due_on, how, late, created_at) VALUES (2, 1, '2028-11-01', 'maybe', 0, {AT})",
           f"INSERT INTO set_aside_dismissed (payee_key, category, created_at) VALUES ('BIG MUTUAL', 'insurance', {AT}), ('BIG MUTUAL', 'insurance', {AT})"]
    for sql in bad:
        with pytest.raises(IntegrityError), eng.begin() as c:
            c.execute(text(sql))
    eng.dispose()
