"""0016 adds account.rate_bp (Next dollar plan Task 4), keeps every row, and downgrades cleanly."""
from alembic import command
from sqlalchemy import inspect, text

from steward.db.engine import make_engine
from steward.db.keys import ensure_key
from steward.db.migrate import _config, run_migrations


def test_0016_adds_the_rate_column(settings):
    eng = make_engine(settings.db_path, ensure_key(settings.key_path))
    command.upgrade(_config(eng), "0015")
    before = {c["name"] for c in inspect(eng).get_columns("account")}
    with eng.begin() as c:
        c.execute(text("INSERT INTO institution (id, name, source) VALUES (1, 'M', 'manual')"))
        c.execute(text("INSERT INTO account (id, institution_id, name, type, owner, source, currency, is_active, "
                       "created_at) VALUES (1, 1, 'Card', 'credit', 'joint', 'manual', 'USD', 1, '2026-01-01 00:00:00')"))
    run_migrations(eng)
    assert {c["name"] for c in inspect(eng).get_columns("account")} == before | {"rate_bp", "advisory_fee_bp", "loan_id", "sends_holdings", "sends_transactions", "holdings_seen_at", "transactions_seen_at"}
    with eng.connect() as c:
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0038"
        assert c.execute(text("SELECT name, rate_bp FROM account")).all() == [("Card", None)]
    command.downgrade(_config(eng), "0015")
    assert "rate_bp" not in {c["name"] for c in inspect(eng).get_columns("account")}
    with eng.connect() as c:
        assert c.execute(text("SELECT name FROM account")).scalar() == "Card"
    eng.dispose()
