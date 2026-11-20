"""0015 adds Care's three tables (parent care plan Task 2) and downgrades cleanly."""
from alembic import command
from sqlalchemy import inspect, text

from steward.db.engine import make_engine
from steward.db.keys import ensure_key
from steward.db.migrate import _config, run_migrations

NEW = {"care_setup", "care_scenario", "care_quote"}


def test_0015_adds_the_care_tables(settings):
    eng = make_engine(settings.db_path, ensure_key(settings.key_path))
    command.upgrade(_config(eng), "0014")
    before = set(inspect(eng).get_table_names())
    run_migrations(eng)
    assert set(inspect(eng).get_table_names()) == before | NEW | {"tax_state", "spending_target", "spending_prefs", "set_aside", "set_aside_match", "set_aside_dismissed", "transaction_note", "undo_entry", "reimburser", "trip", "reimburse_rule", "reimburse_settlement", "account_feed", "held_feed", "same_account_answer", "feed_txn_alias", "expense_ratio_override", "positions_mapping", "positions_import", "undo_text", "home_visit", "import_set", "import_layout", "import_hint", "statement_stage", "statement_layout"}
    with eng.connect() as c:
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0038"
    cols = {c["name"] for c in inspect(eng).get_columns("care_scenario")}
    assert {"position", "level", "start_year", "hours_week", "adult_day_days", "room", "sell_home_year",
            "home_price_cents", "home_basis_cents", "selling_cost_bp", "family_monthly_cents",
            "spending_change_monthly_cents"} <= cols
    with eng.begin() as c:
        c.execute(text("INSERT INTO care_scenario (position, level, selling_cost_bp, family_monthly_cents, "
                       "spending_change_monthly_cents, created_at, updated_at) VALUES (0, 'at_home', 600, 0, 0, "
                       "'2026-10-13 00:00:00', '2026-10-13 00:00:00')"))
    with eng.begin() as c:
        try:
            c.execute(text("INSERT INTO care_scenario (position, level, selling_cost_bp, family_monthly_cents, "
                           "spending_change_monthly_cents, created_at, updated_at) VALUES (1, 'hospice', 600, 0, 0, "
                           "'2026-10-13 00:00:00', '2026-10-13 00:00:00')"))
            raised = False
        except Exception:  # noqa: BLE001 - the check constraint refuses an unknown level
            raised = True
    assert raised
    command.downgrade(_config(eng), "0014")
    assert not (NEW & set(inspect(eng).get_table_names()))
    eng.dispose()
