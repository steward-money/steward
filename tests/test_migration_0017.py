"""0017 adds tax_state (tax decisions plan Task 4) and downgrades cleanly."""
from alembic import command
from sqlalchemy import inspect, text

from steward.db.engine import make_engine
from steward.db.keys import ensure_key
from steward.db.migrate import _config, run_migrations


def test_0017_adds_tax_state(settings):
    eng = make_engine(settings.db_path, ensure_key(settings.key_path))
    command.upgrade(_config(eng), "0016")
    before = set(inspect(eng).get_table_names())
    run_migrations(eng)
    assert set(inspect(eng).get_table_names()) == before | {"tax_state", "spending_target", "spending_prefs", "set_aside", "set_aside_match", "set_aside_dismissed", "transaction_note", "undo_entry", "reimburser", "trip", "reimburse_rule", "reimburse_settlement", "account_feed", "held_feed", "same_account_answer", "feed_txn_alias", "expense_ratio_override", "positions_mapping", "positions_import", "undo_text", "home_visit", "import_set", "import_layout", "import_hint", "statement_stage", "statement_layout"}
    with eng.connect() as c:
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0038"
    assert {c["name"] for c in inspect(eng).get_columns("tax_state")} == {"key", "value", "updated_at", "updated_by"}
    command.downgrade(_config(eng), "0016")
    assert "tax_state" not in set(inspect(eng).get_table_names())
    eng.dispose()
