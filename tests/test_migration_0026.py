"""0026 (plan subpages): plan.inputs_summary, NULL for saves made before it; downgrade drops it and keeps the saves."""
from alembic import command
from sqlalchemy import inspect, text

from steward.db.engine import make_engine
from steward.db.keys import ensure_key
from steward.db.migrate import _config, run_migrations

AT = "'2026-10-12 19:00:00.000000'"


def test_0026_adds_inputs_summary_and_downgrade_drops_it(settings):
    eng = make_engine(settings.db_path, ensure_key(settings.key_path))
    command.upgrade(_config(eng), "0025")
    with eng.begin() as c:
        c.execute(text("INSERT INTO plan (id, saved_at, saved_by, levers, engine_version, comfortable_age, likely_age) "
                       f"VALUES (1, {AT}, 1, '{{}}', '2026.10.4', 63, 60)"))
    run_migrations(eng)
    with eng.connect() as c:
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0038"
        assert c.execute(text("SELECT inputs_summary FROM plan WHERE id = 1")).scalar() is None
    col = {x["name"]: x for x in inspect(eng).get_columns("plan")}["inputs_summary"]
    assert col["nullable"] is True and str(col["type"]) == "TEXT"
    command.downgrade(_config(eng), "0025")
    assert "inputs_summary" not in {x["name"] for x in inspect(eng).get_columns("plan")}
    with eng.connect() as c:
        assert c.execute(text("SELECT comfortable_age FROM plan WHERE id = 1")).scalar() == 63
    eng.dispose()
