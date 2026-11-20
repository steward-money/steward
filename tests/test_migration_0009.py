"""0009 adds the inventory's tables to every book and alters nothing that holds data."""
from alembic import command
from sqlalchemy import inspect, text

from steward.db.engine import make_engine
from steward.db.keys import ensure_key
from steward.db.migrate import _config, run_migrations

NEW = {"inventory_item", "file", "change_record", "suggestion", "tax_form"}


def test_0009_adds_the_tables_and_keeps_existing_rows(settings):
    eng = make_engine(settings.db_path, ensure_key(settings.key_path))
    command.upgrade(_config(eng), "0008")
    with eng.begin() as c:
        c.execute(text("INSERT INTO institution (id, name, source) VALUES (1, 'Manual', 'manual')"))
        c.execute(text("INSERT INTO account (id, institution_id, name, type, owner, source, currency, is_active, "
                       "created_at) VALUES (1, 1, 'Checking', 'checking', 'margaret', 'manual', 'USD', 1, "
                       "'2026-01-01 00:00:00.000000')"))
    run_migrations(eng)
    names = set(inspect(eng).get_table_names())
    assert NEW <= names
    with eng.connect() as c:
        assert c.execute(text("SELECT name FROM account")).scalar() == "Checking"
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0038"
    cols = {c["name"] for c in inspect(eng).get_columns("inventory_item")}
    assert {"full_number", "last4", "check_reason", "data_source", "account_id", "details", "login_kept_in",
            "closed_on", "closed_reason", "created_by", "updated_by"} <= cols
    command.downgrade(_config(eng), "0008")
    assert not (NEW & set(inspect(eng).get_table_names()))
    eng.dispose()
