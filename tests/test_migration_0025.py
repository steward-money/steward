"""0025 (reimbursable expenses): the payer, trip, rule and settlement tables and the transaction's three columns. A
trip can't end before it starts and a settlement is above $0; a payer in use can't go, and a trip that goes leaves its
rows. Downgrade drops it all."""
from datetime import date

import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from steward.db.engine import make_engine
from steward.db.keys import ensure_key
from steward.db.migrate import _config, run_migrations
from steward.db.models import Reimburser, Trip
from tests.factories import make_account, make_txn

AT = "'2026-10-28 17:00:00.000000'"
NEW = {"reimburser", "trip", "reimburse_rule", "reimburse_settlement", "account_feed", "held_feed", "same_account_answer", "feed_txn_alias"}


def test_0025_adds_the_tables_and_columns_and_downgrade_drops_them(settings):
    eng = make_engine(settings.db_path, ensure_key(settings.key_path))
    command.upgrade(_config(eng), "0024")
    before = set(inspect(eng).get_table_names())
    before_cols = {c["name"] for c in inspect(eng).get_columns("transaction")}
    run_migrations(eng)
    with eng.connect() as c:
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0038"
    i = inspect(eng)
    assert set(i.get_table_names()) == before | NEW | {"expense_ratio_override", "positions_mapping", "positions_import", "undo_text", "home_visit", "import_set", "import_layout", "import_hint", "statement_stage", "statement_layout"}
    cols = {c["name"]: c for c in i.get_columns("transaction")}
    assert set(cols) - before_cols == {"reimburser_id", "trip_id", "reimburse_source", "feed_id", "nickname", "statement_page"}
    assert cols["reimburser_id"]["nullable"] and cols["trip_id"]["nullable"] and not cols["reimburse_source"]["nullable"]
    fks = {f["constrained_columns"][0]: (f["referred_table"], f["options"].get("ondelete"))
           for f in i.get_foreign_keys("transaction")}
    assert fks["reimburser_id"] == ("reimburser", "RESTRICT") and fks["trip_id"] == ("trip", "SET NULL")
    assert {"ix_transaction_reimburser_id", "ix_transaction_trip_id"} <= {x["name"] for x in i.get_indexes("transaction")}
    assert {c["name"] for c in i.get_columns("trip")} == {"id", "name", "start", "end", "city", "reimburser_id",
                                                           "created_at", "created_by"}
    assert {c["name"] for c in i.get_columns("reimburse_rule")} == {"id", "pattern", "reimburser_id", "created_at"}
    assert {c["name"] for c in i.get_columns("reimburse_settlement")} == {
        "id", "reimburser_id", "amount_cents", "category", "settled_at", "created_by", "note"}
    with eng.begin() as c:
        c.execute(text(f"INSERT INTO reimburser (id, name, created_at) VALUES (1, 'Acme', {AT})"))
        c.execute(text('INSERT INTO trip (id, name, start, "end", city, reimburser_id, created_at) '
                       f"VALUES (1, 'Chicago trip', '2026-10-06', '2026-10-09', 'Chicago', 1, {AT})"))
    with pytest.raises(IntegrityError):
        with eng.begin() as c:
            c.execute(text('INSERT INTO trip (id, name, start, "end", reimburser_id, created_at) '
                           f"VALUES (2, 'Backwards', '2026-10-09', '2026-10-06', 1, {AT})"))
    with pytest.raises(IntegrityError):
        with eng.begin() as c:
            c.execute(text("INSERT INTO reimburse_settlement (reimburser_id, amount_cents, category, settled_at) "
                           f"VALUES (1, 0, 'travel', {AT})"))
    command.downgrade(_config(eng), "0024")
    assert set(inspect(eng).get_table_names()) == before
    assert {c["name"] for c in inspect(eng).get_columns("transaction")} == before_cols
    eng.dispose()


def _enforce_foreign_keys(db):
    """Every pooled connection has enforcement on since alembic/env.py ends Alembic's open read before its closing
    PRAGMA (tests/test_db_engine.py); this still switches it on, outside a transaction, so the test says so itself."""
    db.commit()
    raw = db.get_bind().raw_connection()
    raw.driver_connection.execute("PRAGMA foreign_keys = ON")
    raw.close()
    assert db.execute(text("PRAGMA foreign_keys")).scalar() == 1
    db.commit()


def test_a_payer_in_use_cannot_go_and_a_trip_that_goes_leaves_its_rows(db):
    _enforce_foreign_keys(db)
    acme = Reimburser(name="Acme")
    db.add(acme)
    db.flush()
    trip = Trip(name="Chicago trip", start=date(2026, 10, 6), end=date(2026, 10, 9), reimburser_id=acme.id)
    db.add(trip)
    db.commit()
    t = make_txn(db, make_account(db), date(2026, 10, 7), -4_000, "HILTON CHICAGO IL")
    assert t.reimburse_source == "none"
    t.reimburser_id, t.trip_id = acme.id, trip.id
    db.commit()
    db.delete(trip)
    db.commit()
    db.refresh(t)
    assert (t.trip_id, t.reimburser_id) == (None, acme.id)
    db.delete(acme)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
