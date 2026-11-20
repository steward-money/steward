"""0028 (investment views): the account's advisory fee, vehicle loan and what the bank sends; the holding's import;
the expense ratio overrides, remembered position columns and positions imports. A vehicle is a plain account.type
value. Downgrade drops it all and keeps the rows."""
from alembic import command
from sqlalchemy import inspect, text

from steward.db.engine import make_engine
from steward.db.keys import ensure_key
from steward.db.migrate import _config, run_migrations

AT = "'2026-10-28 17:00:00.000000'"
NEW = {"expense_ratio_override", "positions_mapping", "positions_import"}
ACCOUNT_COLS = {"advisory_fee_bp", "loan_id", "sends_holdings", "sends_transactions", "holdings_seen_at",
                "transactions_seen_at"}


def _fks(i, table):
    return {f["constrained_columns"][0]: (f["referred_table"], f["options"].get("ondelete"))
            for f in i.get_foreign_keys(table)}


def test_0028_adds_the_investment_columns_and_tables_and_downgrade_drops_them(settings):
    eng = make_engine(settings.db_path, ensure_key(settings.key_path))
    command.upgrade(_config(eng), "0027")
    before = set(inspect(eng).get_table_names())
    acct_before = {c["name"] for c in inspect(eng).get_columns("account")}
    hold_before = {c["name"] for c in inspect(eng).get_columns("holding")}
    with eng.begin() as c:
        c.execute(text("INSERT INTO institution (id, name, source) VALUES (1, 'Manual', 'manual')"))
        c.execute(text("INSERT INTO account (id, institution_id, name, type, owner, source, currency, is_active, created_at) "
                       f"VALUES (1, 1, 'Brokerage', 'brokerage_taxable', 'joint', 'manual', 'USD', 1, {AT})"))
        c.execute(text("INSERT INTO holding (id, account_id, as_of, symbol, name, shares_milli, market_value_cents) "
                       "VALUES (1, 1, '2026-10-01', 'VTI', 'Total Stock', 1000, 50000)"))
    run_migrations(eng)
    i = inspect(eng)
    with eng.connect() as c:
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0038"
        assert c.execute(text("SELECT advisory_fee_bp, loan_id, sends_holdings, sends_transactions FROM account "
                              "WHERE id = 1")).one() == (None, None, None, None)
        assert c.execute(text("SELECT import_id, market_value_cents FROM holding WHERE id = 1")).one() == (None, 50000)
    assert set(i.get_table_names()) == before | NEW | {"undo_text", "home_visit", "import_set", "import_layout", "import_hint", "statement_stage", "statement_layout"}
    cols = {c["name"]: c for c in i.get_columns("account")}
    assert set(cols) - acct_before == ACCOUNT_COLS and all(cols[n]["nullable"] for n in ACCOUNT_COLS)
    assert {c["name"] for c in i.get_columns("holding")} - hold_before == {"import_id"}
    assert _fks(i, "account")["loan_id"] == ("account", "SET NULL")
    assert _fks(i, "holding")["import_id"] == ("positions_import", "SET NULL")
    assert {c["name"] for c in i.get_columns("expense_ratio_override")} == {"symbol", "ratio_pct", "updated_at", "updated_by"}
    assert i.get_pk_constraint("expense_ratio_override")["constrained_columns"] == ["symbol"]
    assert {c["name"] for c in i.get_columns("positions_mapping")} == {"key", "mapping", "updated_at"}
    assert {c["name"] for c in i.get_columns("positions_import")} == {
        "id", "account_id", "as_of", "filename", "row_count", "imported_at", "imported_by",
        "source", "set_id", "batch_id", "period_start", "check_state", "balance_cents", "snapshot_as_of", "rolled_back_at"}   # 0037's too
    assert "ix_positions_import_account_id" in {x["name"] for x in i.get_indexes("positions_import")}
    with eng.begin() as c:   # account.type is plain text: no CHECK stands in a vehicle's way
        c.execute(text("INSERT INTO account (id, institution_id, name, type, owner, source, currency, is_active, created_at) "
                       f"VALUES (2, 1, 'Subaru', 'vehicle', 'joint', 'manual', 'USD', 1, {AT})"))
    command.downgrade(_config(eng), "0027")
    assert set(inspect(eng).get_table_names()) == before
    assert {c["name"] for c in inspect(eng).get_columns("account")} == acct_before
    assert {c["name"] for c in inspect(eng).get_columns("holding")} == hold_before
    with eng.connect() as c:
        assert c.execute(text("SELECT symbol FROM holding WHERE id = 1")).scalar() == "VTI"
        assert c.execute(text("SELECT type FROM account WHERE id = 2")).scalar() == "vehicle"
    eng.dispose()
