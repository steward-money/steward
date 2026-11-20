"""0037 (statement holdings): positions_import keeps its rows with source "csv" and NULLs; the set_id and batch_id
foreign keys are enforced; downgrade drops the columns."""
import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from steward.db.engine import make_engine
from steward.db.keys import ensure_key
from steward.db.migrate import _config, run_migrations

AT = "2026-10-01 00:00:00.000000"


def _at_0036(settings):
    eng = make_engine(settings.db_path, ensure_key(settings.key_path))
    command.upgrade(_config(eng), "0036")
    with eng.begin() as c:
        c.execute(text("INSERT INTO institution (id, name, source) VALUES (1, 'Manual', 'manual')"))
        c.execute(text("INSERT INTO account (id, institution_id, name, type, owner, source, currency, is_active, created_at) "
                       "VALUES (1, 1, 'IRA', 'traditional_ira', 'joint', 'manual', 'USD', 1, :at)"), {"at": AT})
        c.execute(text("INSERT INTO positions_import (id, account_id, as_of, filename, row_count, imported_at, imported_by) "
                       "VALUES (1, 1, '2026-09-30', 'betterment.csv', 4, :at, 1)"), {"at": AT})
    return eng


def test_0037_adds_the_columns_and_keeps_old_imports(settings):
    eng = _at_0036(settings)
    run_migrations(eng)
    with eng.connect() as c:
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0038"
        assert c.execute(text("SELECT filename, source, set_id, batch_id, period_start, check_state, balance_cents, "
                              "snapshot_as_of, rolled_back_at FROM positions_import")).all() == \
            [("betterment.csv", "csv", None, None, None, None, None, None, None)]
    eng.dispose()


@pytest.mark.parametrize("column", ["set_id", "batch_id"])
def test_0037_enforces_the_set_and_batch_foreign_keys(settings, column):
    eng = _at_0036(settings)
    run_migrations(eng)
    with eng.connect() as c:
        assert c.execute(text("PRAGMA foreign_keys")).scalar() == 1
        with pytest.raises(IntegrityError):
            c.execute(text("INSERT INTO positions_import (account_id, as_of, filename, row_count, imported_at, imported_by, "
                           f"{column}) VALUES (1, '2026-09-30', 'a.pdf', 1, :at, 1, 999)"), {"at": AT})
            c.commit()
    eng.dispose()


def test_0037_downgrade_drops_them(settings):
    eng = _at_0036(settings)
    run_migrations(eng)
    command.downgrade(_config(eng), "0036")
    assert not {"source", "batch_id"} & {x["name"] for x in inspect(eng).get_columns("positions_import")}
    eng.dispose()
