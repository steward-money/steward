"""0027 (same account, two feeds): account_feed with a row for every SimpleFIN account, institution.org_id,
held_feed, same_account_answer, feed_txn_alias, and transaction.feed_id set on the rows sync brought. Downgrade drops
them all and keeps the rows."""
from alembic import command
from sqlalchemy import inspect, text

from steward.db.engine import make_engine
from steward.db.keys import ensure_key
from steward.db.migrate import _config, run_migrations

AT = "'2026-10-20 17:00:00.000000'"
SYNCED = "'2026-10-27 13:00:00.000000'"
NEW = {"account_feed", "held_feed", "same_account_answer", "feed_txn_alias"}
INVESTMENTS = {"expense_ratio_override", "positions_mapping", "positions_import"}   # 0028, run to the head with it


def _fks(i, table):
    return {f["constrained_columns"][0]: (f["referred_table"], f["options"].get("ondelete"))
            for f in i.get_foreign_keys(table)}


def test_0027_adds_feeds_backfills_one_per_simplefin_account_and_downgrade_drops_them(settings):
    eng = make_engine(settings.db_path, ensure_key(settings.key_path))
    command.upgrade(_config(eng), "0026")
    before = set(inspect(eng).get_table_names())
    txn_before = {c["name"] for c in inspect(eng).get_columns("transaction")}
    with eng.begin() as c:
        c.execute(text("INSERT INTO institution (id, name, source) VALUES (1, 'Manual', 'manual')"))
        c.execute(text("INSERT INTO institution (id, name, source, simplefin_org_id, last_sync_at) "
                       f"VALUES (2, 'Betterment', 'simplefin', 'CON-1', {SYNCED})"))
        for aid, inst, name, src, ext in ((1, 2, "Joint Taxable", "simplefin", "'sfin:ACT-1'"),
                                          (2, 2, "Kids 529", "simplefin", "'sfin:ACT-2'"),
                                          (3, 1, "House", "manual", "NULL")):
            c.execute(text("INSERT INTO account (id, institution_id, name, type, owner, source, external_id, currency, "
                           f"is_active, created_at) VALUES ({aid}, {inst}, '{name}', 'brokerage_taxable', 'joint', "
                           f"'{src}', {ext}, 'USD', 1, {AT})"))
        c.execute(text("INSERT INTO import_batch (id, account_id, filename, format, row_count, imported_at, imported_by) "
                       f"VALUES (1, 1, 'old.csv', 'generic', 1, {AT}, 1)"))
        for tid, aid, ext, batch in ((1, 1, "'TRN-1'", "NULL"), (2, 1, "'file-1'", "1"), (3, 1, "NULL", "NULL"),
                                     (4, 3, "NULL", "NULL")):
            c.execute(text('INSERT INTO "transaction" (id, account_id, posted_at, amount_cents, raw_description, '
                           "external_id, category, is_business, is_transfer, category_source, imported_at, "
                           f"import_batch_id) VALUES ({tid}, {aid}, '2026-10-01', -500, 'COFFEE', {ext}, 'dining', 0, 0, "
                           f"'rule', {AT}, {batch})"))
    run_migrations(eng)
    i = inspect(eng)
    with eng.connect() as c:
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0038"
        feeds = c.execute(text("SELECT account_id, external_id, institution_id, name, added_at, last_seen_at "
                               "FROM account_feed ORDER BY account_id")).all()
        assert [tuple(f) for f in feeds] == [
            (1, "sfin:ACT-1", 2, "Joint Taxable", "2026-10-20 17:00:00.000000", "2026-10-27 13:00:00.000000"),
            (2, "sfin:ACT-2", 2, "Kids 529", "2026-10-20 17:00:00.000000", "2026-10-27 13:00:00.000000")]
        feed_1 = c.execute(text("SELECT id FROM account_feed WHERE account_id = 1")).scalar()
        # only the bank's row (a bank id, no import) came through the feed; a file's row and a typed one did not
        assert dict(c.execute(text('SELECT id, feed_id FROM "transaction"')).all()) == {1: feed_1, 2: None, 3: None, 4: None}
        assert c.execute(text("SELECT org_id FROM institution WHERE id = 2")).scalar() is None
    assert set(i.get_table_names()) == before | NEW | INVESTMENTS | {"undo_text", "home_visit", "import_set", "import_layout", "import_hint", "statement_stage", "statement_layout"}
    assert {c["name"] for c in i.get_columns("transaction")} - txn_before == {"feed_id", "nickname", "statement_page"}
    assert _fks(i, "transaction")["feed_id"] == ("account_feed", "SET NULL")
    assert _fks(i, "account_feed") == {"account_id": ("account", "CASCADE"), "institution_id": ("institution", None)}
    assert _fks(i, "held_feed") == {"institution_id": ("institution", None),
                                    "candidate_account_id": ("account", "SET NULL")}
    assert _fks(i, "feed_txn_alias") == {"account_id": ("account", "CASCADE"), "feed_id": ("account_feed", "CASCADE"),
                                         "transaction_id": ("transaction", "CASCADE")}
    assert {c["name"] for c in i.get_columns("held_feed")} == {
        "id", "external_id", "institution_id", "name", "currency", "candidate_account_id", "balance_cents",
        "balance_date", "payload", "first_seen_at", "last_seen_at"}
    assert {c["name"] for c in i.get_columns("same_account_answer")} == {
        "id", "account_id", "other_account_id", "held_external_id", "answer", "match_count", "created_at",
        "answered_at", "answered_by"}
    assert "uq_feed_txn_alias" in {u["name"] for u in i.get_unique_constraints("feed_txn_alias")}
    assert "uq_same_account_pair" in {u["name"] for u in i.get_unique_constraints("same_account_answer")}
    assert "ix_account_feed_account_id" in {x["name"] for x in i.get_indexes("account_feed")}
    command.downgrade(_config(eng), "0026")
    assert set(inspect(eng).get_table_names()) == before
    assert {c["name"] for c in inspect(eng).get_columns("transaction")} == txn_before
    assert "org_id" not in {c["name"] for c in inspect(eng).get_columns("institution")}
    with eng.connect() as c:
        assert c.execute(text('SELECT count(*) FROM "transaction"')).scalar() == 4
        assert c.execute(text("SELECT external_id FROM account WHERE id = 1")).scalar() == "sfin:ACT-1"
    eng.dispose()
