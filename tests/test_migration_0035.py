"""0035 (import layouts): import_layout, import_hint and import_set are new and empty; import_batch keeps every row,
with NULL set_id, layout_id and content_sha256; foreign keys stay enforced; downgrade drops it all."""
import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from steward.db.engine import make_engine
from steward.db.keys import ensure_key
from steward.db.migrate import _config, run_migrations

AT = "2026-10-01 00:00:00.000000"


def _at_0034(settings):
    eng = make_engine(settings.db_path, ensure_key(settings.key_path))
    command.upgrade(_config(eng), "0034")
    with eng.begin() as c:
        c.execute(text("INSERT INTO institution (id, name, source) VALUES (1, 'Manual', 'manual')"))
        c.execute(text("INSERT INTO account (id, institution_id, name, type, owner, source, currency, is_active, created_at) "
                       "VALUES (1, 1, 'Card', 'credit', 'joint', 'manual', 'USD', 1, :at)"), {"at": AT})
        c.execute(text("INSERT INTO import_batch (id, account_id, filename, format, row_count, imported_at, imported_by) "
                       "VALUES (1, 1, 'october.csv', 'chase_card', 3, :at, 1)"), {"at": AT})
    return eng


def test_0035_adds_the_layout_tables_and_leaves_old_batches(settings):
    eng = _at_0034(settings)
    run_migrations(eng)
    with eng.connect() as c:
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0038"
        for table in ("import_layout", "import_hint", "import_set"):
            assert c.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 0
        assert c.execute(text("SELECT filename, row_count, set_id, layout_id, content_sha256 FROM import_batch")).all() == \
            [("october.csv", 3, None, None, None)]
    cols = {x["name"] for x in inspect(eng).get_columns("import_layout")}
    assert cols == {"id", "key", "label", "header", "mapping", "negate", "date_format", "account_id", "created_at",
                    "last_used_at", "uses", "forgotten_at"}
    with eng.connect() as c:
        assert c.execute(text("PRAGMA foreign_keys")).scalar() == 1
        with pytest.raises(IntegrityError):
            c.execute(text("INSERT INTO import_hint (key, account_id, updated_at) VALUES ('ofx:1:1234', 999, :at)"), {"at": AT})
            c.commit()
    eng.dispose()


def test_0035_downgrade_drops_it_all(settings):
    eng = _at_0034(settings)
    run_migrations(eng)
    command.downgrade(_config(eng), "0034")
    names = inspect(eng).get_table_names()
    assert not {"import_layout", "import_hint", "import_set"} & set(names)
    assert "set_id" not in {x["name"] for x in inspect(eng).get_columns("import_batch")}
    with eng.connect() as c:
        assert c.execute(text("SELECT count(*) FROM import_batch")).scalar() == 1
    eng.dispose()
