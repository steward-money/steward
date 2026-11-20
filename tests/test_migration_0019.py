"""0019 adds spending_target and spending_prefs (phase 11b, Data) and downgrades cleanly."""
from alembic import command
from sqlalchemy import inspect, text

from steward.db.engine import make_engine
from steward.db.keys import ensure_key
from steward.db.migrate import _config, run_migrations


def test_0019_adds_the_spending_tables(settings):
    eng = make_engine(settings.db_path, ensure_key(settings.key_path))
    command.upgrade(_config(eng), "0018")
    before = set(inspect(eng).get_table_names())
    run_migrations(eng)
    assert set(inspect(eng).get_table_names()) == before | {"spending_target", "spending_prefs", "set_aside", "set_aside_match", "set_aside_dismissed", "transaction_note", "undo_entry", "reimburser", "trip", "reimburse_rule", "reimburse_settlement", "account_feed", "held_feed", "same_account_answer", "feed_txn_alias", "expense_ratio_override", "positions_mapping", "positions_import", "undo_text", "home_visit", "import_set", "import_layout", "import_hint", "statement_stage", "statement_layout"}
    with eng.connect() as c:
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0038"
    assert {c["name"] for c in inspect(eng).get_columns("spending_target")} == {
        "category", "kind", "monthly_cents", "updated_at", "updated_by", "check_after"}
    assert {c["name"] for c in inspect(eng).get_columns("spending_prefs")} == {
        "id", "warn_pct_bp", "warn_min_cents", "updated_at", "updated_by"}
    command.downgrade(_config(eng), "0018")
    assert set(inspect(eng).get_table_names()) == before
    eng.dispose()


def test_a_target_kind_and_cents_are_checked(settings):
    import pytest
    from sqlalchemy.exc import IntegrityError
    eng = make_engine(settings.db_path, ensure_key(settings.key_path))
    run_migrations(eng)
    with eng.begin() as c:
        c.execute(text("INSERT INTO spending_target (category, kind, monthly_cents, updated_at) "
                       "VALUES ('dining', 'everyday', 50000, '2026-10-25 00:00:00.000000')"))
    for bad in ("('gifts', 'weekly', 100, '2026-10-25 00:00:00.000000')",
                "('gifts', 'everyday', -1, '2026-10-25 00:00:00.000000')"):
        with pytest.raises(IntegrityError), eng.begin() as c:
            c.execute(text(f"INSERT INTO spending_target (category, kind, monthly_cents, updated_at) VALUES {bad}"))
    with pytest.raises(IntegrityError), eng.begin() as c:
        c.execute(text("INSERT INTO spending_prefs (id, warn_pct_bp, warn_min_cents, updated_at) "
                       "VALUES (2, 1000, 2500, '2026-10-25 00:00:00.000000')"))
    eng.dispose()
