"""0036 (statements): statement_stage and statement_layout are new and empty; import_batch and transaction keep every
row, with NULL in the new columns; foreign keys stay enforced; downgrade drops it all."""
import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from steward.db.engine import make_engine
from steward.db.keys import ensure_key
from steward.db.migrate import _config, run_migrations

AT = "2026-10-01 00:00:00.000000"


def _at_0035(settings):
    eng = make_engine(settings.db_path, ensure_key(settings.key_path))
    command.upgrade(_config(eng), "0035")
    with eng.begin() as c:
        c.execute(text("INSERT INTO institution (id, name, source) VALUES (1, 'Manual', 'manual')"))
        c.execute(text("INSERT INTO account (id, institution_id, name, type, owner, source, currency, is_active, created_at) "
                       "VALUES (1, 1, 'Card', 'credit', 'joint', 'manual', 'USD', 1, :at)"), {"at": AT})
        c.execute(text("INSERT INTO import_batch (id, account_id, filename, format, row_count, imported_at, imported_by) "
                       "VALUES (1, 1, 'october.csv', 'chase_card', 1, :at, 1)"), {"at": AT})
        c.execute(text('INSERT INTO "transaction" (id, account_id, posted_at, amount_cents, raw_description, category, '
                       "is_business, is_transfer, category_source, imported_at, import_batch_id, business_pct, "
                       "business_source, reimburse_source) VALUES (1, 1, '2026-09-30', -500, 'SHOP', 'uncategorized', "
                       "0, 0, 'none', :at, 1, 0, 'none', 'none')"), {"at": AT})
    return eng


def test_0036_adds_the_statement_tables_and_leaves_old_rows(settings):
    eng = _at_0035(settings)
    run_migrations(eng)
    with eng.connect() as c:
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0038"
        for table in ("statement_stage", "statement_layout"):
            assert c.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 0
        assert c.execute(text("SELECT filename, period_start, period_end, pages, check_state, check_gap_cents, "
                              "statement_layout_id, snapshot_as_of, snapshot_cents FROM import_batch")).all() == \
            [("october.csv", None, None, None, None, None, None, None, None)]
        assert c.execute(text('SELECT raw_description, statement_page FROM "transaction"')).all() == [("SHOP", None)]
    insp = inspect(eng)
    assert {x["name"] for x in insp.get_columns("statement_layout")} == {
        "id", "key", "label", "kind", "anchors", "rules", "account_id", "model", "created_at", "confirmed_by",
        "last_used_at", "uses", "forgotten_at"}
    assert {"ix_statement_stage_state", "ix_statement_stage_expires_at"} <= {i["name"] for i in insp.get_indexes("statement_stage")}
    with eng.connect() as c:
        assert c.execute(text("PRAGMA foreign_keys")).scalar() == 1
        with pytest.raises(IntegrityError):
            c.execute(text("INSERT INTO statement_stage (added_by, added_at, updated_at, expires_at, filename, bytes, "
                           "content_sha256, state, account_id) VALUES (1, :at, :at, :at, 'a.pdf', 1, 'x', 'queued', 999)"),
                      {"at": AT})
            c.commit()
    eng.dispose()


def test_0036_downgrade_drops_it_all(settings):
    eng = _at_0035(settings)
    run_migrations(eng)
    command.downgrade(_config(eng), "0035")
    insp = inspect(eng)
    assert not {"statement_stage", "statement_layout"} & set(insp.get_table_names())
    assert "period_start" not in {x["name"] for x in insp.get_columns("import_batch")}
    assert "statement_page" not in {x["name"] for x in insp.get_columns("transaction")}
    with eng.connect() as c:
        assert c.execute(text('SELECT count(*) FROM "transaction"')).scalar() == 1
    eng.dispose()
