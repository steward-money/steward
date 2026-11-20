"""0024 (sorting redesign, Undo): the undo_entry table with its expiry index; downgrade drops it."""
from alembic import command
from sqlalchemy import inspect, text

from steward.db.engine import make_engine
from steward.db.keys import ensure_key
from steward.db.migrate import _config, run_migrations

AT = "'2026-10-28 17:00:00.000000'"


def test_0024_adds_the_undo_entry_table_and_downgrade_drops_it(settings):
    eng = make_engine(settings.db_path, ensure_key(settings.key_path))
    command.upgrade(_config(eng), "0023")
    before = set(inspect(eng).get_table_names())
    run_migrations(eng)
    with eng.connect() as c:
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0038"
    assert set(inspect(eng).get_table_names()) == before | {"undo_entry", "reimburser", "trip", "reimburse_rule", "reimburse_settlement", "account_feed", "held_feed", "same_account_answer", "feed_txn_alias", "expense_ratio_override", "positions_mapping", "positions_import", "undo_text", "home_visit", "import_set", "import_layout", "import_hint", "statement_stage", "statement_layout"}
    cols = {col["name"]: col for col in inspect(eng).get_columns("undo_entry")}
    assert set(cols) == {"token", "user_id", "action", "created_at", "expires_at", "used_at", "before", "after"}
    assert [n for n, col in cols.items() if col["nullable"]] == ["used_at"]
    assert inspect(eng).get_pk_constraint("undo_entry")["constrained_columns"] == ["token"]
    assert "ix_undo_entry_expires_at" in {i["name"] for i in inspect(eng).get_indexes("undo_entry")}
    with eng.begin() as c:
        c.execute(text("INSERT INTO undo_entry (token, user_id, action, created_at, expires_at, before, after) "
                       f"VALUES ('t', 1, 'sort', {AT}, {AT}, '{{}}', '{{}}')"))
    command.downgrade(_config(eng), "0023")
    assert set(inspect(eng).get_table_names()) == before
    eng.dispose()
