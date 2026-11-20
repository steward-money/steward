"""0033 (home visit): home_visit is new and empty, the rest of the book is untouched, foreign keys stay enforced,
and downgrade drops it."""
import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from steward.db.engine import make_engine
from steward.db.keys import ensure_key
from steward.db.migrate import _config, run_migrations

AT = "2026-10-01 00:00:00.000000"


def _at_0032(settings):
    eng = make_engine(settings.db_path, ensure_key(settings.key_path))
    command.upgrade(_config(eng), "0032")
    with eng.begin() as c:
        c.execute(text("INSERT INTO user_setup (user_id, seen_at) VALUES (1, :at)"), {"at": AT})
        c.execute(text("INSERT INTO event_log (at, level, kind, message, actionable) "
                       "VALUES (:at, 'warn', 'sync', 'Big Bank needs you to sign in again at SimpleFIN', 1)"), {"at": AT})
    return eng


def test_0033_adds_an_empty_home_visit_and_leaves_the_rest(settings):
    eng = _at_0032(settings)
    run_migrations(eng)
    with eng.connect() as c:
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0038"
        assert c.execute(text("SELECT count(*) FROM home_visit")).scalar() == 0
        assert c.execute(text("SELECT user_id, seen_at FROM user_setup")).all() == [(1, AT)]
        assert c.execute(text("SELECT count(*) FROM event_log")).scalar() == 1
    assert {x["name"] for x in inspect(eng).get_columns("home_visit")} == {"user_id", "seen_at", "glanced_at"}
    with eng.connect() as c:
        assert c.execute(text("PRAGMA foreign_keys")).scalar() == 1      # the 891c4e5 regression: still enforced
        with pytest.raises(IntegrityError):
            c.execute(text("INSERT INTO balance_snapshot (account_id, as_of, balance_cents) VALUES (999, '2026-10-01', 1)"))
            c.commit()
    eng.dispose()


def test_0033_downgrade_drops_home_visit(settings):
    eng = _at_0032(settings)
    run_migrations(eng)
    with eng.begin() as c:
        c.execute(text("INSERT INTO home_visit (user_id, seen_at) VALUES (1, :at)"), {"at": AT})
    command.downgrade(_config(eng), "0032")
    assert "home_visit" not in inspect(eng).get_table_names()
    with eng.connect() as c:
        assert c.execute(text("SELECT count(*) FROM user_setup")).scalar() == 1
    eng.dispose()
