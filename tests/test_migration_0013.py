"""0013 adds chat_turn and an index on advisor_outgoing.conversation_id (plan B ruling 1), and changes nothing else."""
from alembic import command
from sqlalchemy import inspect, text

from steward.db.engine import make_engine
from steward.db.keys import ensure_key
from steward.db.migrate import _config, run_migrations


def test_0013_adds_chat_turn_and_downgrades_cleanly(settings):
    eng = make_engine(settings.db_path, ensure_key(settings.key_path))
    command.upgrade(_config(eng), "0012")
    before = set(inspect(eng).get_table_names())
    run_migrations(eng)
    assert set(inspect(eng).get_table_names()) == before | {"chat_turn", "business_rule", "business_state", "care_setup", "care_quote", "care_scenario", "tax_state", "spending_target", "spending_prefs", "set_aside", "set_aside_match", "set_aside_dismissed", "transaction_note", "undo_entry", "reimburser", "trip", "reimburse_rule", "reimburse_settlement", "account_feed", "held_feed", "same_account_answer", "feed_txn_alias", "expense_ratio_override", "positions_mapping", "positions_import", "undo_text", "home_visit", "import_set", "import_layout", "import_hint", "statement_stage", "statement_layout"}
    with eng.connect() as c:
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0038"
    assert {c["name"] for c in inspect(eng).get_columns("chat_turn")} == {
        "id", "conversation_id", "turn_id", "user_id", "asked", "sent", "chips", "steps", "stop", "line", "cost_cents",
        "ms", "approval", "store_rows", "approved_by", "about", "started_at", "ended_at"}
    assert "ix_advisor_outgoing_conversation_id" in {i["name"] for i in inspect(eng).get_indexes("advisor_outgoing")}
    command.downgrade(_config(eng), "0012")
    assert set(inspect(eng).get_table_names()) == before
    assert "ix_advisor_outgoing_conversation_id" not in {i["name"] for i in inspect(eng).get_indexes("advisor_outgoing")}
    eng.dispose()
