"""0031 (kinds and checks): spending_target's kind may be commitment and it gains check_after; saved giving and
childcare rows stored as fixed become commitments, a kids row keeps its kind. Downgrade maps commitment back."""
import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from steward.db.engine import make_engine
from steward.db.keys import ensure_key
from steward.db.migrate import _config, run_migrations

AT = "2026-10-01 00:00:00.000000"
ROWS = (("giving", "fixed", 20_000), ("childcare_education", "fixed", None), ("kids", "everyday", 25_000),
        ("housing", "fixed", None), ("dining", "everyday", 50_000), ("taxes", "occasional", None),
        ("insurance", "everyday", 9_000))


def _seed(c):
    for category, kind, cents in ROWS:
        c.execute(text("INSERT INTO spending_target (category, kind, monthly_cents, updated_at, updated_by) "
                       "VALUES (:c, :k, :m, :at, 1)"), {"c": category, "k": kind, "m": cents, "at": AT})


def _at_0030(settings):
    eng = make_engine(settings.db_path, ensure_key(settings.key_path))
    command.upgrade(_config(eng), "0030")
    with eng.begin() as c:
        _seed(c)
    return eng


def test_0031_turns_saved_giving_and_childcare_into_commitments_and_keeps_kids(settings):
    eng = _at_0030(settings)
    run_migrations(eng)
    with eng.connect() as c:
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0038"
        got = {r[0]: tuple(r[1:]) for r in c.execute(text(
            "SELECT category, kind, monthly_cents, updated_by, check_after FROM spending_target"))}
    assert got == {"giving": ("commitment", 20_000, 1, None), "childcare_education": ("commitment", None, 1, None),
                   "kids": ("everyday", 25_000, 1, None), "housing": ("fixed", None, 1, None),
                   "dining": ("everyday", 50_000, 1, None), "taxes": ("occasional", None, 1, None),
                   "insurance": ("everyday", 9_000, 1, None)}
    eng.dispose()


def test_0031_checks_the_new_kinds_and_keeps_foreign_keys_on(settings):
    eng = _at_0030(settings)
    run_migrations(eng)
    with eng.begin() as c:
        c.execute(text("INSERT INTO spending_target (category, kind, monthly_cents, updated_at, check_after) "
                       "VALUES ('pets', 'commitment', 4000, :at, '2027-01-01')"), {"at": AT})
    for sql in ("INSERT INTO spending_target (category, kind, updated_at) VALUES ('gifts', 'weekly', '2026-10-01')",
                "INSERT INTO spending_target (category, kind, monthly_cents, updated_at) VALUES ('gifts', 'everyday', -1, '2026-10-01')",
                "INSERT INTO balance_snapshot (account_id, as_of, balance_cents) VALUES (999, '2026-10-01', 1)"):
        with eng.connect() as c:
            assert c.execute(text("PRAGMA foreign_keys")).scalar() == 1      # the 891c4e5 regression: still enforced
            with pytest.raises(IntegrityError):
                c.execute(text(sql))
                c.commit()
    eng.dispose()


def test_0031_downgrade_turns_commitments_back_into_bills_and_drops_check_after(settings):
    eng = _at_0030(settings)
    run_migrations(eng)
    with eng.begin() as c:
        c.execute(text("UPDATE spending_target SET check_after = '2027-01-01' WHERE category = 'dining'"))
        c.execute(text("UPDATE spending_target SET kind = 'commitment' WHERE category = 'kids'"))
    command.downgrade(_config(eng), "0030")
    with eng.connect() as c:
        got = dict(c.execute(text("SELECT category, kind FROM spending_target")).all())
    assert (got["giving"], got["childcare_education"], got["kids"], got["dining"]) == ("fixed", "fixed", "fixed", "everyday")
    assert "check_after" not in {x["name"] for x in inspect(eng).get_columns("spending_target")}
    with eng.connect() as c, pytest.raises(IntegrityError):
        c.execute(text("INSERT INTO spending_target (category, kind, updated_at) VALUES ('pets', 'commitment', '2026-10-01')"))
        c.commit()
    eng.dispose()
