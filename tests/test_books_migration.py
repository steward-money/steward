import os
import shutil

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from steward.app import create_app
from steward.auth.cookies import HOST_SESSION as COOKIE
from steward.books import bootstrap
from steward.books.registry import BookRegistry, DirectoryUnavailable
from steward.db.engine import make_engine
from tests.books_fixtures import CREDENTIAL, TOKEN_JR, make_pre_books_vault, sign_in_rows, snapshot

BASE = "https://nas.tail1234.ts.net"


def _start(settings):
    return TestClient(create_app(settings), base_url=BASE)


def test_cookies_passkeys_and_every_ours_row_survive_the_move(settings):
    key = make_pre_books_vault(settings)
    old = make_engine(settings.db_path, key)
    rows_before, signin_before = snapshot(old), sign_in_rows(old)
    old.dispose()
    with _start(settings) as c:
        books = c.app.state.books
        assert sign_in_rows(books.directory) == signin_before   # before any request touches last_seen_at
        c.cookies.set(COOKIE, TOKEN_JR)
        r = c.get("/api/auth/me")
        assert r.status_code == 200 and r.json()["user"] == {"id": 1, "username": "jr", "display_name": "JR", "email": "jr@example.com",
                                       "prefs": {"celebrations": None, "sound": None}}
        assert [p["label"] for p in c.get("/api/auth/passkeys").json()["passkeys"]] == ["Phone"]
        with books.directory.connect() as d:
            assert d.execute(text("SELECT credential_id, sign_count FROM passkey")).one() == (CREDENTIAL, 7)
    after = make_engine(settings.db_path, key)
    # 0009 and 0010 add empty tables; 0010 adds transaction.watched_at (last in snapshot's sorted column order) and
    # marks the made-up transactions, all older than 14 days, as checked; every other value is untouched
    rows_after = snapshot(after)
    assert all(r[-1] is not None for r in rows_after["transaction"])
    # 0036: transaction gains statement_page (16th of its sorted columns, just before transfer_peer_id and watched_at): empty on the old rows
    assert all(r[15] is None for r in rows_after["transaction"])
    rows_after["transaction"] = [r[:15] + r[16:-1] for r in rows_after["transaction"]]
    # 0032: merchant_rule gains amount_cents and note (first and seventh of its sorted columns): empty on the old rule
    assert [(r[0], r[6]) for r in rows_after["merchant_rule"]] == [(None, None)] * len(rows_before["merchant_rule"])
    rows_after["merchant_rule"] = [r[1:6] + r[7:] for r in rows_after["merchant_rule"]]
    # the start-up's same-account check marks the book once it has run (its only row; nothing here looks alike)
    assert [r[0] for r in rows_after.pop("watch_state")] == ["same_account_check"]
    new_tables = ("change_record", "file", "inventory_item", "suggestion", "tax_form", "flag", "flag_event",
                  "mail_outbox", "watch_known", "watch_recipient",
                  "advisor_alias", "advisor_outgoing", "advisor_state", "chat_conversation", "chat_message", "chat_seen", "chat_turn",
                  "business_rule", "business_state", "care_setup", "care_scenario", "care_quote", "tax_state", "spending_target", "spending_prefs", "set_aside", "set_aside_match", "set_aside_dismissed", "transaction_note", "undo_entry", "reimburser", "trip", "reimburse_rule", "reimburse_settlement", "account_feed", "held_feed", "same_account_answer", "feed_txn_alias", "expense_ratio_override", "positions_mapping", "positions_import", "undo_text", "home_visit",
                      "import_layout", "import_hint", "import_set", "statement_layout", "statement_stage")
    # 0035 and 0036: import_batch gains content_sha256, layout_id and set_id, then the statement columns (check_gap_cents,
    # check_state, pages, period_end, period_start, snapshot_as_of, snapshot_cents, statement_layout_id), all empty on the
    # old batch: of its sorted columns they are every one but the original eight (0, 4-8, 13 and 14)
    kept = (0, 4, 5, 6, 7, 8, 13, 14)
    assert all(r[i] is None for r in rows_after["import_batch"] for i in range(19) if i not in kept)
    rows_after["import_batch"] = [tuple(r[i] for i in kept) for r in rows_after["import_batch"]]
    # 0030: setup steps became Profile's sections; the made-up skipped Pay step is now a skipped You and your partner
    rows_before["setup_step"] = [(at, status, "you" if step == "pay" else step, uid)
                                 for at, status, step, uid in rows_before["setup_step"]]
    assert rows_after == {**rows_before, **{t: [] for t in new_tables}}
    with after.connect() as c2:
        assert c2.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0038"
    after.dispose()


def test_a_second_start_changes_nothing(settings):
    key = make_pre_books_vault(settings)
    with _start(settings):
        pass
    eng = make_engine(settings.db_path, key)
    first = snapshot(eng, skip=())
    eng.dispose()
    with _start(settings):
        pass
    eng = make_engine(settings.db_path, key)
    assert snapshot(eng, skip=()) == first
    eng.dispose()
    assert len(list(settings.backups_dir.glob("pre-books-steward-*.db"))) == 1


def test_a_crash_after_the_rename_finishes_on_the_next_start(settings, monkeypatch):
    key = make_pre_books_vault(settings)
    from steward.books import registry as reg_module

    def boom(engine, **_):
        raise reg_module.MigrationFailed("killed")
    monkeypatch.setattr(reg_module, "run_migrations", boom)
    books = BookRegistry(settings)
    books.prepare()   # the directory is built; ours's upgrade "dies" and ours is marked unavailable
    assert books.unavailable
    books.dispose()
    monkeypatch.undo()
    with _start(settings) as c:
        c.cookies.set(COOKIE, TOKEN_JR)
        assert c.get("/api/auth/me").status_code == 200
    eng = make_engine(settings.db_path, key)
    with eng.connect() as c2:
        assert c2.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0038"
    eng.dispose()


def test_a_restored_steward_db_with_a_stale_directory_refuses_to_start(settings, monkeypatch):
    key = make_pre_books_vault(settings)
    bootstrap.ensure_directory(settings, key)   # the directory exists, steward.db still at 0007
    eng = make_engine(settings.db_path, key)
    with eng.begin() as c:   # a sign-in made later, as if steward.db came back from a newer backup
        c.execute(text("INSERT INTO session (id, user_id, token_hash, device_label, created_at, last_seen_at, "
                       "expires_at, revoked_at) VALUES (9, 2, 'bbbb', 'Laptop', '2026-10-18 00:00:00.000000', "
                       "'2026-10-18 00:00:00.000000', '2099-01-01 00:00:00.000000', NULL)"))
    eng.dispose()
    with pytest.raises(DirectoryUnavailable, match="move directory.db aside"):
        with _start(settings):
            pass
    eng = make_engine(settings.db_path, key)
    with eng.connect() as c:
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0007"
    eng.dispose()


def test_a_failed_deploy_restored_by_nas_ops_leaves_the_previous_release_its_files(settings):
    make_pre_books_vault(settings)
    saved = settings.data_dir / "before-deploy.db"
    shutil.copyfile(settings.db_path, saved)   # nas-ops' before-<commit>.db (made by the old release's backup)
    with _start(settings):
        pass
    assert settings.directory_path.exists()
    # nas-ops' undo (DEPLOY_DATA_FILE list): move steward.db and directory.db aside, put steward.db back.
    for f in (settings.db_path, settings.directory_path):
        for p in (f, f.with_name(f.name + "-wal"), f.with_name(f.name + "-shm")):
            if p.exists():
                os.remove(p)
    shutil.copyfile(saved, settings.db_path)
    assert settings.db_path.read_bytes() == saved.read_bytes()
    assert not settings.directory_path.exists()
    # What the previous release needs: steward.db at 0007 with its sign-in rows, opened with the raw key.
    key = settings.key_path.read_text().strip()
    eng = make_engine(settings.db_path, key)
    with eng.connect() as c:
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0007"
        assert c.execute(text('SELECT count(*) FROM "user"')).scalar() == 2
    eng.dispose()
    with _start(settings) as c:   # and the next try at the new release migrates again from scratch
        c.cookies.set(COOKIE, TOKEN_JR)
        assert c.get("/api/auth/me").status_code == 200


def test_a_missing_directory_with_a_book_already_past_0008_refuses_to_start(settings):
    key = make_pre_books_vault(settings)
    with _start(settings):   # the move runs: ours is at head and has no user table
        pass
    settings.directory_path.unlink()
    backups = settings.backups_dir
    backups.mkdir(parents=True, exist_ok=True)
    for stamp in ("20260101-000001", "20260101-000002"):
        (backups / f"steward-directory-{stamp}.db").write_bytes(b"x")
    # A book named "Directory Stuff" backs up under a later-sorting name; it is not the directory's backup.
    (backups / "steward-directory-stuff-20261018-130000.db").write_bytes(b"x")
    with pytest.raises(DirectoryUnavailable) as e:
        with _start(settings):
            pass
    msg = str(e.value)
    assert "data/directory.db" in msg and "steward-directory-20260101-000002.db" in msg
    assert "000001.db" not in msg and "directory-stuff" not in msg
    assert not settings.directory_path.exists()
    settings.directory_path.with_name("directory.db.partial").unlink(missing_ok=True)
    for p in backups.glob("steward-directory-2*.db"):
        p.unlink()
    with pytest.raises(DirectoryUnavailable, match="No copy of directory.db was found"):
        with _start(settings):
            pass
    assert not settings.directory_path.exists()


def test_book_revision_reads_a_books_alembic_head_and_none_for_a_bare_file(books, engine, tmp_path):
    from sqlalchemy import create_engine
    from steward.db.migrate import book_revision
    assert book_revision(engine) == "0038"
    bare = create_engine(f"sqlite:///{tmp_path / 'bare.db'}")
    assert book_revision(bare) is None
    bare.dispose()
