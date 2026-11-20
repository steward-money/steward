"""0010 adds the watching tables and transaction.watched_at. Transactions older than 14 days are marked checked, so
the first run after the deploy looks at the last two weeks only (ruling 2)."""
from alembic import command
from sqlalchemy import inspect, text

from steward.db.engine import make_engine
from steward.db.keys import ensure_key
from steward.db.migrate import _config, run_migrations

NEW = {"flag", "flag_event", "watch_known", "watch_state", "watch_recipient", "mail_outbox"}


def test_0010_adds_the_tables_and_marks_old_transactions_checked(settings):
    eng = make_engine(settings.db_path, ensure_key(settings.key_path))
    command.upgrade(_config(eng), "0009")
    with eng.begin() as c:
        c.execute(text("INSERT INTO institution (id, name, source) VALUES (1, 'Manual', 'manual')"))
        c.execute(text("INSERT INTO account (id, institution_id, name, type, owner, source, currency, is_active, "
                       "created_at) VALUES (1, 1, 'Joint checking', 'checking', 'joint', 'manual', 'USD', 1, "
                       "'2026-01-01 00:00:00.000000')"))
        for tid, posted in ((1, "2020-01-02"), (2, None)):
            c.execute(text(
                "INSERT INTO \"transaction\" (id, account_id, posted_at, amount_cents, raw_description, category, "
                "is_business, is_transfer, category_source, imported_at) VALUES (:id, 1, COALESCE(:p, date('now')), "
                "-1000, 'STATER BROS 123', 'groceries', 0, 0, 'rule', '2026-01-01 00:00:00.000000')"),
                {"id": tid, "p": posted})
    run_migrations(eng)
    assert NEW <= set(inspect(eng).get_table_names())
    with eng.connect() as c:
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0038"
        rows = dict(c.execute(text('SELECT id, watched_at FROM "transaction"')).all())
    assert rows[1] is not None and rows[2] is None
    cols = {c["name"] for c in inspect(eng).get_columns("flag")}
    assert {"check_name", "severity", "status", "transaction_id", "item_id", "account_id", "due_on", "summary",
            "title", "facts", "also", "note", "status_by", "status_at", "resolved_at", "resolved_by",
            "summarized_at"} <= cols
    command.downgrade(_config(eng), "0009")
    assert not (NEW & set(inspect(eng).get_table_names()))
    assert "watched_at" not in {c["name"] for c in inspect(eng).get_columns("transaction")}
    eng.dispose()
