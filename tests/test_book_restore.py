"""The CLI restore (spec P31; CLI restore): the ZIP and the KDF are checked before anything is derived; the
passphrase comes from a prompt; a new book, a new id, a key from this NAS's steward.key; never an existing book."""
import base64
import json
import re
import shutil
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from steward.db.directory_models import Book, Membership
from steward.db.engine import make_engine
from steward.db.keys import ensure_key
from steward.db.models import SimplefinLink, StoredFile, UndoEntry
from steward.services import book_export as X
from steward.services import book_restore as R
from steward.settings import Settings
from tests.factories import make_account, make_person, make_txn

TODAY = date(2026, 10, 30)
NOW = datetime(2026, 10, 30, 19, 0, tzinfo=timezone.utc)
PASS = "Café au lait 1999"          # composed (U+00E9); explicit escapes so no editor can normalize the pair
PASS_DECOMPOSED = "Café au lait 1999"   # decomposed (U+0065 U+0301)


@pytest.fixture
def exported(books, ours, db, settings, tmp_path) -> Path:
    a = make_account(db, "Joint checking")
    for i in range(4):
        make_txn(db, a, date(2026, 10, 1 + i), -100 * (i + 1), f"SHOP {i}")
    db.add(SimplefinLink(access_url="https://user:s3cret@bridge.simplefin.org/simplefin", claimed_at=NOW))
    db.add(UndoEntry(token="t" * 40, user_id=1, action="sort", created_at=NOW, expires_at=NOW, before="{}", after="{}"))
    db.commit()
    made = X.make(books, ours, PASS, settings, TODAY, NOW)
    keep = tmp_path / made.filename
    shutil.copyfile(made.path, keep)
    return keep


@pytest.fixture
def other(tmp_path):
    """A different NAS: another data dir and another steward.key."""
    from steward.books.registry import open_books
    s = Settings(_env_file=None, data_dir=tmp_path / "other", base_url="https://nas.tail1234.ts.net", env="test",
                 simplefin_hosts="testserver", web_dir=tmp_path / "no-web")
    ensure_key(s.key_path)
    reg = open_books(s)
    yield reg
    reg.dispose()


def _manager(reg) -> int:
    with Session(reg.directory) as ddb:
        return make_person(ddb, "jr").id


def test_a_round_trip_onto_another_nas(exported, other, books):
    assert other.master != books.master
    info, counts = R.restore(other, exported, PASS, "Ours (restored)", [_manager(other)], NOW)
    assert (info.name, info.kind, info.key_scheme, counts) == ("Ours (restored)", "household", "derived",
                                                              {"transactions": 4, "files": 0})
    eng = make_engine(other.path_for(info), other.key_for(info))
    with eng.connect() as c:
        assert c.execute(text("PRAGMA integrity_check")).scalar() == "ok"
        assert c.execute(text("SELECT count(*) FROM \"transaction\"")).scalar() == 4
        assert c.execute(text("SELECT count(*) FROM simplefin_link")).scalar() == 0
        assert c.execute(text("SELECT count(*) FROM undo_entry")).scalar() == 0
        assert c.execute(text("SELECT count(*) FROM sqlite_master WHERE name = 'export_meta'")).scalar() == 0
    eng.dispose()
    with Session(other.directory) as ddb:
        assert [m.role for m in ddb.scalars(select(Membership).where(Membership.book_id == info.id))] == ["manager"]


def test_a_restore_welcomes_the_managers_it_gives_the_new_book(exported, other):
    info, _ = R.restore(other, exported, PASS, "Ours (restored)", [_manager(other)], NOW)
    with Session(other.directory) as ddb:
        rows = ddb.scalars(select(Membership).where(Membership.book_id == info.id)).all()
        assert rows and all(m.welcome_seen_at is not None for m in rows)


def test_the_restore_takes_the_passphrase_in_either_form(exported, other):
    """Review focus 1: composed on one device, decomposed on another."""
    assert PASS != PASS_DECOMPOSED
    info, _ = R.restore(other, exported, PASS_DECOMPOSED, None, [_manager(other)], NOW)
    assert info.name == "Ours (restored)"


def test_a_wrong_passphrase_makes_nothing(exported, other):
    before = _books(other)
    with pytest.raises(R.RestoreRefused) as e:
        R.restore(other, exported, "not the passphrase", None, [_manager(other)], NOW)
    assert e.value.message == "That passphrase doesn't open this backup."
    assert _books(other) == before


def _books(reg) -> list[str]:
    with Session(reg.directory) as ddb:
        return sorted(ddb.scalars(select(Book.slug)))


def _rezip(src: Path, dst: Path, change) -> Path:
    with zipfile.ZipFile(src) as z:
        parts = {n: z.read(n) for n in z.namelist()}
    change(parts)
    with zipfile.ZipFile(dst, "w", compression=zipfile.ZIP_STORED) as z:
        for n, b in parts.items():
            z.writestr(n, b)
    return dst


def test_a_member_outside_the_file_is_refused(exported, other, tmp_path):
    """Review focus 4."""
    for bad in ("../x", "/etc/passwd", "files/12/../../x", "notes.txt", "files/abc", "files/012",
                "files/\u0661\u0662"):
        crafted = _rezip(exported, tmp_path / "crafted.zip", lambda p, b=bad: p.__setitem__(b, b"x"))
        with pytest.raises(R.RestoreRefused) as e:
            R.restore(other, crafted, PASS, None, [], NOW)
        assert e.value.message == "That isn't a Steward backup.", bad


def test_a_huge_book_db_or_too_many_members_is_refused_before_reading(exported, other, tmp_path, monkeypatch):
    """A crafted file can't fill the scratch disk with a 4 GiB book.db, or make the check walk a million members."""
    monkeypatch.setattr(X, "keys", lambda *a, **k: (_ for _ in ()).throw(AssertionError("derived")))
    monkeypatch.setattr(R, "MAX_DB", 1024)                       # the real book.db is far past 1 KiB
    with pytest.raises(R.RestoreRefused) as e:
        R.restore(other, exported, PASS, None, [], NOW)
    assert e.value.message == "That isn't a Steward backup."
    monkeypatch.undo()
    monkeypatch.setattr(X, "keys", lambda *a, **k: (_ for _ in ()).throw(AssertionError("derived")))
    monkeypatch.setattr(R, "MAX_MEMBERS", 2)                     # export.json and book.db, plus one file
    crafted = _rezip(exported, tmp_path / "members.zip", lambda p: p.__setitem__("files/1", b"x"))
    with pytest.raises(R.RestoreRefused) as e:
        R.restore(other, crafted, PASS, None, [], NOW)
    assert e.value.message == "That isn't a Steward backup."


def test_kdf_parameters_over_the_bounds_are_refused_before_deriving(exported, other, tmp_path, monkeypatch):
    """Review focus 4: a crafted file can't make the restore spend more than a real backup does: never past the
    export's own 256 MiB, 4 passes or 4 lanes, let alone 64 GiB."""
    monkeypatch.setattr(X, "keys", lambda *a, **k: (_ for _ in ()).throw(AssertionError("derived")))
    def over(field, value):
        def change(parts):
            meta = json.loads(parts["export.json"])
            meta["kdf"][field] = value
            parts["export.json"] = json.dumps(meta).encode()
        return change
    for field, value in (("m_kib", 64 * 1024 * 1024), ("m_kib", 256 * 1024 + 1), ("t", 11), ("t", 5), ("p", 9),
                         ("p", 5), ("t", True), ("salt", base64.b64encode(b"x").decode()), ("name", "scrypt")):
        crafted = _rezip(exported, tmp_path / f"{field}.zip", over(field, value))
        with pytest.raises(R.RestoreRefused) as e:
            R.restore(other, crafted, PASS, None, [], NOW)
        assert e.value.message == "That isn't a Steward backup.", field


def test_a_file_too_big_in_all_is_refused(exported, other, monkeypatch):
    monkeypatch.setattr(R, "MAX_TOTAL", 1024)
    with pytest.raises(R.RestoreRefused):
        R.restore(other, exported, PASS, None, [], NOW)


def test_a_backup_from_a_newer_steward_is_refused(exported, other, tmp_path):
    def newer(parts):
        meta = json.loads(parts["export.json"])
        k = meta["kdf"]
        db_key, _ = X.keys(PASS, base64.b64decode(k["salt"]), k["t"], k["m_kib"], k["p"])
        path = tmp_path / "newer.db"
        path.write_bytes(parts["book.db"])
        eng = make_engine(path, db_key.hex())
        with eng.begin() as c:
            c.execute(text("UPDATE alembic_version SET version_num = '9999'"))
        eng.dispose()
        parts["book.db"] = path.read_bytes()
    crafted = _rezip(exported, tmp_path / "newer.zip", newer)
    with pytest.raises(R.RestoreRefused) as e:
        R.restore(other, crafted, PASS, None, [], NOW)
    assert e.value.message == "This backup is from a newer Steward. Update first."


def _tamper(exported: Path, tmp_path: Path, *statements: str) -> Path:
    """A crafted backup from someone who knows the passphrase: the copy opened with its own key, changed, sealed."""
    def change(parts):
        k = json.loads(parts["export.json"])["kdf"]
        db_key, _ = X.keys(PASS, base64.b64decode(k["salt"]), k["t"], k["m_kib"], k["p"])
        path = tmp_path / "tampered.db"
        path.unlink(missing_ok=True)
        path.write_bytes(parts["book.db"])
        eng = make_engine(path, db_key.hex())
        raw = eng.raw_connection()
        try:
            raw.driver_connection.execute("PRAGMA foreign_keys = OFF")
            for sql in statements:
                raw.driver_connection.execute(sql)
            raw.driver_connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            raw.close()
        eng.dispose()
        parts["book.db"] = path.read_bytes()
    return _rezip(exported, tmp_path / "tampered.zip", change)


def _folders(reg) -> list[str]:
    d = reg.settings.books_dir
    return sorted(p.name for p in d.iterdir()) if d.is_dir() else []


def _refused_as_damaged(reg, crafted: Path, monkeypatch, message: str = R.DAMAGED, name: str | None = None) -> None:
    """DAMAGED (or the message given), no directory row, no book folder, and the scratch folder gone."""
    made_dirs: list[Path] = []
    real = R.tempfile.mkdtemp
    monkeypatch.setattr(R.tempfile, "mkdtemp", lambda *a, **k: made_dirs.append(Path(real(*a, **k))) or made_dirs[-1])
    before = _books(reg)
    folders = _folders(reg)
    with pytest.raises(R.RestoreRefused) as e:
        R.restore(reg, crafted, PASS, name, [], NOW)
    assert e.value.message == message
    assert _books(reg) == before
    assert _folders(reg) == folders
    assert (message != R.DAMAGED or made_dirs) and not any(d.exists() for d in made_dirs)
    monkeypatch.setattr(R.tempfile, "mkdtemp", real)


DATA = ("transaction", "account", "institution")


def _objects(eng) -> set[tuple[str, str]]:
    with eng.connect() as c:
        return set(c.execute(text("SELECT type, name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")).all())


def _rows(eng, table: str) -> list[tuple]:
    with eng.connect() as c:
        return list(c.execute(text(f'SELECT * FROM "{table}" ORDER BY rowid')).all())


def _restores_as_steward_built_it(books, ours, reg, crafted: Path):
    """The restored book has exactly the live book's objects (Steward's own schema, nothing from the backup's) and
    exactly its rows."""
    info, counts = R.restore(reg, crafted, PASS, None, [_manager(reg)], NOW)
    eng = make_engine(reg.path_for(info), reg.key_for(info))
    try:
        assert _objects(eng) == _objects(books.engine_for(ours))
        for t in DATA:
            assert _rows(eng, t) == _rows(books.engine_for(ours), t), t
        with eng.connect() as c:
            assert c.execute(text("PRAGMA integrity_check")).scalar() == "ok"
    finally:
        eng.dispose()
    return info, counts


def test_a_resealed_backup_with_nothing_changed_still_restores(exported, other, tmp_path, books, ours):
    """The tamper helper alone changes nothing, so each outcome below is its one change's."""
    info, counts = _restores_as_steward_built_it(books, ours, other, _tamper(exported, tmp_path, "SELECT 1"))
    assert (info.kind, counts["transactions"]) == ("household", 4)


def test_a_trigger_in_the_backup_is_never_copied(exported, other, tmp_path, books, ours):
    """(a) A trigger would fire later, when Steward writes to the restored book: the book is built from Steward's
    migrations and only rows are copied, so it isn't there."""
    crafted = _tamper(exported, tmp_path, 'CREATE TRIGGER wipe AFTER INSERT ON "transaction" BEGIN DELETE FROM account; END')
    _restores_as_steward_built_it(books, ours, other, crafted)


def test_a_view_in_the_backup_is_never_copied(exported, other, tmp_path, books, ours):
    """(b)"""
    _restores_as_steward_built_it(books, ours, other, _tamper(exported, tmp_path, "CREATE VIEW peek AS SELECT 1 AS x"))


def test_an_extra_table_in_the_backup_is_never_copied(exported, other, tmp_path, books, ours):
    """(c)"""
    crafted = _tamper(exported, tmp_path, "CREATE TABLE extra (x TEXT)", "INSERT INTO extra VALUES ('x')")
    _restores_as_steward_built_it(books, ours, other, crafted)


def test_a_table_replaced_by_a_view_of_its_name_is_refused(exported, other, tmp_path, monkeypatch):
    """(g) Reading it would run the backup's own SQL."""
    crafted = _tamper(exported, tmp_path, 'ALTER TABLE "transaction" RENAME TO txn_old',
                      'CREATE VIEW "transaction" AS SELECT * FROM txn_old')
    _refused_as_damaged(other, crafted, monkeypatch)


def test_a_table_replaced_by_a_virtual_table_of_its_name_is_refused(exported, other, tmp_path, monkeypatch):
    """Reading a virtual table runs its module over the backup's own shadow tables."""
    crafted = _tamper(exported, tmp_path, "DROP TABLE import_hint",
                      "CREATE VIRTUAL TABLE import_hint USING fts5(key, account_id, updated_at)")
    _refused_as_damaged(other, crafted, monkeypatch)


def test_an_extra_column_is_dropped(exported, other, tmp_path, books, ours):
    """(h)"""
    crafted = _tamper(exported, tmp_path, "ALTER TABLE account ADD COLUMN spare TEXT DEFAULT 'x'")
    info, _ = _restores_as_steward_built_it(books, ours, other, crafted)
    eng = make_engine(other.path_for(info), other.key_for(info))
    with eng.connect() as c:
        assert "spare" not in {r[1] for r in c.execute(text("PRAGMA table_xinfo(account)"))}
    eng.dispose()


def test_a_missing_column_is_refused(exported, other, tmp_path, monkeypatch):
    """(i)"""
    _refused_as_damaged(other, _tamper(exported, tmp_path, "ALTER TABLE account DROP COLUMN rate_bp"), monkeypatch)


def test_a_generated_column_in_place_of_a_real_one_is_refused(exported, other, tmp_path, monkeypatch):
    """Reading a generated column runs the backup's expression."""
    crafted = _tamper(exported, tmp_path, "ALTER TABLE account DROP COLUMN rate_bp",
                      "ALTER TABLE account ADD COLUMN rate_bp INTEGER GENERATED ALWAYS AS (length(name)) VIRTUAL")
    _refused_as_damaged(other, crafted, monkeypatch)


def test_a_table_recreated_without_its_check_and_a_row_breaking_it_is_refused(exported, other, tmp_path, monkeypatch):
    """(d) Only reimburser's row in the schema changes: its CHECK is gone, its name, columns and indexes are as before,
    and it holds a row the CHECK forbids, which Steward's own table refuses."""
    k = json.loads(zipfile.ZipFile(exported).read("export.json"))["kdf"]
    db_key, _ = X.keys(PASS, base64.b64decode(k["salt"]), k["t"], k["m_kib"], k["p"])
    probe = tmp_path / "probe.db"
    probe.write_bytes(zipfile.ZipFile(exported).read("book.db"))
    eng = make_engine(probe, db_key.hex())
    with eng.connect() as c:
        schema = dict(c.execute(text("SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL")).all())
        indexes = [s for (s,) in c.execute(text(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND tbl_name = 'reimburser' AND sql IS NOT NULL"))]
    eng.dispose()
    loose = re.sub(r",\s*CONSTRAINT ck_reimburser_name CHECK \(length\(name\) BETWEEN 1 AND 60\)", "",
                   schema["reimburser"])
    assert "CHECK" in schema["reimburser"] and "CHECK" not in loose
    crafted = _tamper(exported, tmp_path, "CREATE TABLE keep AS SELECT * FROM reimburser", "DROP TABLE reimburser",
                      loose, "INSERT INTO reimburser SELECT * FROM keep", "DROP TABLE keep", *indexes,
                      "INSERT INTO reimburser (name, created_at) VALUES ('', '2026-10-30 19:00:00.000000')")
    k2 = json.loads(zipfile.ZipFile(crafted).read("export.json"))["kdf"]
    probe.unlink()
    probe.write_bytes(zipfile.ZipFile(crafted).read("book.db"))
    eng = make_engine(probe, X.keys(PASS, base64.b64decode(k2["salt"]), k2["t"], k2["m_kib"], k2["p"])[0].hex())
    with eng.connect() as c:
        after = dict(c.execute(text("SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL")).all())
    eng.dispose()
    assert {n for n in schema if schema[n] != after.get(n)} | (set(after) - set(schema)) == {"reimburser"}
    _refused_as_damaged(other, crafted, monkeypatch)


def test_a_revision_lower_than_the_schema_really_is(exported, other, tmp_path, monkeypatch, books, ours):
    """(e) 0034 claimed with 0035's tables is below the first revision an export can have: DAMAGED. Were 0034
    supported, the book is built at Steward's 0034, only 0034's tables and columns are copied (0035's extra columns on
    import_batch are dropped), and 0035 then runs on Steward's own schema: a correct book, never a skipped step."""
    crafted = _tamper(exported, tmp_path, "UPDATE alembic_version SET version_num = '0034'")
    _refused_as_damaged(other, crafted, monkeypatch)
    monkeypatch.setattr(R, "FIRST_REVISION", "0034")
    info, _ = _restores_as_steward_built_it(books, ours, other, crafted)
    eng = make_engine(other.path_for(info), other.key_for(info))
    with eng.connect() as c:
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0038"
    eng.dispose()


def test_an_unknown_book_kind_is_refused(exported, other, tmp_path, monkeypatch):
    crafted = _tamper(exported, tmp_path, "UPDATE export_meta SET value = 'admin' WHERE key = 'kind'")
    _refused_as_damaged(other, crafted, monkeypatch)


def test_a_huge_export_json_is_refused_before_it_is_read(exported, other, tmp_path, monkeypatch):
    def pad(parts):
        meta = json.loads(parts["export.json"])
        meta["pad"] = "x" * (64 * 1024)
        parts["export.json"] = json.dumps(meta).encode()
    crafted = _rezip(exported, tmp_path / "huge.zip", pad)
    real = zipfile.ZipFile.read
    monkeypatch.setattr(zipfile.ZipFile, "read", lambda self, name, *a: (_ for _ in ()).throw(
        AssertionError("read")) if name == "export.json" else real(self, name, *a))
    with pytest.raises(R.RestoreRefused) as e:
        R.restore(other, crafted, PASS, None, [], NOW)
    assert e.value.message == R.NOT_EXPORT


T0 = "'2026-10-30 19:00:00.000000'"


def test_a_generated_export_meta_value_is_refused(exported, other, tmp_path, monkeypatch):
    """Reading a generated column runs the backup's own expression."""
    crafted = _tamper(exported, tmp_path, "CREATE TABLE keep AS SELECT * FROM export_meta", "DROP TABLE export_meta",
                      "CREATE TABLE export_meta (key TEXT PRIMARY KEY, v TEXT, "
                      "value TEXT GENERATED ALWAYS AS (CASE key WHEN 'name' THEN 'OURS VIA BACKUP SQL' ELSE v END) "
                      "VIRTUAL)",
                      "INSERT INTO export_meta (key, v) SELECT key, value FROM keep", "DROP TABLE keep")
    _refused_as_damaged(other, crafted, monkeypatch)


def test_a_generated_version_num_is_refused(exported, other, tmp_path, monkeypatch):
    crafted = _tamper(exported, tmp_path, "DROP TABLE alembic_version",
                      "CREATE TABLE alembic_version (x TEXT, version_num TEXT GENERATED ALWAYS AS ('0035') VIRTUAL)",
                      "INSERT INTO alembic_version (x) VALUES ('a')")
    _refused_as_damaged(other, crafted, monkeypatch)


def test_a_generated_sqlite_sequence_is_refused(exported, other, tmp_path, monkeypatch):
    crafted = _tamper(exported, tmp_path, "INSERT INTO sqlite_sequence (name, seq) VALUES ('file', 3)",
                      "PRAGMA writable_schema = ON",
                      "UPDATE sqlite_master SET sql = 'CREATE TABLE sqlite_sequence(name, s, seq GENERATED ALWAYS AS "
                      "(s * 2) VIRTUAL)' WHERE name = 'sqlite_sequence'", "PRAGMA writable_schema = OFF")
    _refused_as_damaged(other, crafted, monkeypatch)


def test_a_huge_sequence_is_refused(exported, other, tmp_path, monkeypatch):
    """A seq of 2^63-1 would make the next upload fail forever."""
    crafted = _tamper(exported, tmp_path, "INSERT INTO sqlite_sequence (name, seq) VALUES ('file', 9223372036854775807)")
    _refused_as_damaged(other, crafted, monkeypatch)


def test_a_column_matching_only_by_unicode_case_is_refused(exported, other, tmp_path, monkeypatch):
    """SQLite folds only ASCII: "Key" (a Kelvin sign) isn't key, and "key" unknown would read as a string."""
    crafted = _tamper(exported, tmp_path, 'ALTER TABLE import_hint RENAME COLUMN "key" TO "Key"')
    _refused_as_damaged(other, crafted, monkeypatch)


def test_a_dangling_foreign_key_is_refused(exported, other, tmp_path, monkeypatch):
    crafted = _tamper(exported, tmp_path,
                      'UPDATE "transaction" SET account_id = 99999 WHERE id = (SELECT min(id) FROM "transaction")')
    _refused_as_damaged(other, crafted, monkeypatch)


def test_genuine_backups_are_foreign_key_clean(exported, other, books, margaret, mdb, settings, tmp_path):
    """Honest backups pass the foreign key check: the household book and a parent book with a file."""
    f = StoredFile(name="statement.pdf", content_type="application/pdf", size=20, sha256="0" * 64, uploaded_by=1)
    mdb.add(f)
    mdb.commit()
    books.file_store(margaret).write(f.id, b"%PDF-1.4 statement")
    for path in (exported, X.make(books, margaret, PASS, settings, TODAY, NOW).path):
        info, _ = R.restore(other, path, PASS, None, [], NOW)
        eng = make_engine(other.path_for(info), other.key_for(info))
        with eng.connect() as c:
            assert c.execute(text("PRAGMA foreign_key_check")).all() == []
        eng.dispose()


def test_operational_rows_never_survive_a_restore(exported, other, tmp_path):
    """A planted pending email would go from Steward's own sender to the new book's managers; undo entries, email
    switches, per-person marks and Ask conversations belong to another NAS's people."""
    from steward.watch.outbox import due_rows
    crafted = _tamper(
        exported, tmp_path,
        "INSERT INTO mail_outbox (kind, flag_ids, status, created_at, next_try_at, tries, to_user_ids, sent_to, subject, "
        f"text_body, html_body) VALUES ('urgent', '[]', 'pending', {T0}, {T0}, 0, '[1]', '[]', 'Your bank needs you', "
        "'Sign in at https://evil.example', '<a href=\"https://evil.example\">Sign in</a>')",
        f"INSERT INTO undo_entry (token, user_id, action, created_at, expires_at, before, after) VALUES "
        f"('{'u' * 40}', 1, 'sort', {T0}, '2099-01-01 00:00:00.000000', '{{}}', '{{}}')",
        f"INSERT INTO undo_text (token, model, row_key, field, value) VALUES ('{'u' * 40}', 'txn', '1', 'name', 'x')",
        f"INSERT INTO watch_recipient (user_id, urgent, summary, updated_at) VALUES (77, 1, 1, {T0})",
        "INSERT INTO user_setup (user_id) VALUES (77)", "INSERT INTO home_visit (user_id) VALUES (77)",
        f"INSERT INTO chat_seen (user_id, seen_at) VALUES (77, {T0})",
        f"INSERT INTO chat_conversation (user_id, title, created_at, updated_at, last_input_tokens) VALUES "
        f"(77, 'Someone else''s question', {T0}, {T0}, 0)")
    info, _ = R.restore(other, crafted, PASS, None, [_manager(other)], NOW)
    eng = make_engine(other.path_for(info), other.key_for(info))
    with eng.connect() as c:
        for t in X.RESTORE_BLANKS:
            assert c.execute(text(f'SELECT count(*) FROM "{t}"')).scalar() == 0, t
    with Session(eng) as s:
        assert due_rows(s, datetime(2099, 1, 1, tzinfo=timezone.utc)) == []
    eng.dispose()


@pytest.fixture
def parent_exported(books, margaret, settings, tmp_path) -> Path:
    made = X.make(books, margaret, PASS, settings, TODAY, NOW)
    keep = tmp_path / "parent.steward-backup"
    shutil.copyfile(made.path, keep)
    return keep


def test_a_household_backup_keeps_the_household_owner_names(exported, other, tmp_path):
    crafted = _tamper(exported, tmp_path, "UPDATE export_meta SET value = '[]' WHERE key = 'owner_names'")
    info, _ = R.restore(other, crafted, PASS, None, [], NOW)
    from steward.books.owners import HOUSEHOLD_OWNER_NAMES
    assert tuple(info.owner_names) == HOUSEHOLD_OWNER_NAMES


@pytest.mark.parametrize("owners", [
    "[]", "not json", "{}", '[{"key": "a", "label": 5}]', '[{"label": "Margaret"}]',
    '[{"key": "someone-else", "label": "Margaret"}]',
    json.dumps([{"key": f"o{i}", "label": "x" * 500} for i in range(200)]),
])
def test_bad_owner_names_in_a_parent_backup_are_refused(parent_exported, other, tmp_path, monkeypatch, owners):
    crafted = _tamper(parent_exported, tmp_path, f"UPDATE export_meta SET value = '{owners}' WHERE key = 'owner_names'")
    _refused_as_damaged(other, crafted, monkeypatch)


def test_a_parent_backups_owner_names_restore_as_they_were(parent_exported, other, margaret):
    info, _ = R.restore(other, parent_exported, PASS, None, [], NOW)
    assert tuple(info.owner_names) == tuple(margaret.owner_names)


def test_a_blank_name_is_refused(exported, other, monkeypatch):
    _refused_as_damaged(other, exported, monkeypatch, message="Enter a book name of up to 60 characters.", name="   ")
    _refused_as_damaged(other, exported, monkeypatch, message="Enter a book name of up to 60 characters.", name="x" * 61)


def test_a_revision_that_is_digits_only_to_python_is_refused(exported, other, tmp_path, monkeypatch):
    """'³'.isdigit() is true and int('³') raises."""
    _refused_as_damaged(other, _tamper(exported, tmp_path, "UPDATE alembic_version SET version_num = '³'"), monkeypatch)


def test_a_5000_digit_member_name_is_not_a_backup(exported, other, tmp_path, monkeypatch):
    crafted = _rezip(exported, tmp_path / "long.zip", lambda p: p.__setitem__("files/" + "1" * 5000, b"x"))
    _refused_as_damaged(other, crafted, monkeypatch, message=R.NOT_EXPORT)


def test_a_household_backup_with_files_is_refused(exported, other, tmp_path, monkeypatch):
    crafted = _rezip(exported, tmp_path / "files.zip", lambda p: p.__setitem__("files/1", b"x"))
    _refused_as_damaged(other, crafted, monkeypatch)


def test_file_members_must_be_exactly_the_books_files(books, margaret, mdb, settings, other, tmp_path, monkeypatch):
    f = StoredFile(name="statement.pdf", content_type="application/pdf", size=20, sha256="0" * 64, uploaded_by=1)
    mdb.add(f)
    mdb.commit()
    books.file_store(margaret).write(f.id, b"%PDF-1.4 statement")
    made = X.make(books, margaret, PASS, settings, TODAY, NOW)
    orphan = _rezip(made.path, tmp_path / "orphan.zip", lambda p: p.__setitem__("files/999", p[f"files/{f.id}"]))
    _refused_as_damaged(other, orphan, monkeypatch)
    missing = _rezip(made.path, tmp_path / "missing.zip", lambda p: p.pop(f"files/{f.id}"))
    _refused_as_damaged(other, missing, monkeypatch)


def test_a_file_member_over_the_upload_limit_is_refused_before_reading(exported, other, tmp_path, monkeypatch):
    monkeypatch.setattr(R, "MAX_FILE_MEMBER", 4)
    crafted = _rezip(exported, tmp_path / "big.zip", lambda p: p.__setitem__("files/1", b"x" * 5))
    _refused_as_damaged(other, crafted, monkeypatch, message=R.NOT_EXPORT)


def test_a_corrupt_database_says_damaged_not_wrong_passphrase(exported, other, tmp_path, monkeypatch):
    """A page of the schema itself fails its HMAC under the right key: the very first read is "malformed"."""
    import sqlcipher3
    k = json.loads(zipfile.ZipFile(exported).read("export.json"))["kdf"]
    key = X.keys(PASS, base64.b64decode(k["salt"]), k["t"], k["m_kib"], k["p"])[0].hex()
    good = zipfile.ZipFile(exported).read("book.db")
    probe = tmp_path / "probe.db"

    def flipped(page: int) -> bytes:
        b = bytearray(good)
        b[4096 * (page - 1) + 200] ^= 0xFF
        return bytes(b)

    def breaks_the_schema(page: int) -> bool:
        probe.write_bytes(flipped(page))
        c = sqlcipher3.connect(str(probe))
        try:
            c.execute(f"PRAGMA key = \"x'{key}'\"")
            c.execute("SELECT count(*) FROM sqlite_master").fetchall()
            return False
        except sqlcipher3.DatabaseError as e:
            return "malformed" in str(e)
        finally:
            c.close()
    page = next(p for p in range(2, len(good) // 4096 + 1) if breaks_the_schema(p))
    crafted = _rezip(exported, tmp_path / "corrupt.zip", lambda parts: parts.__setitem__("book.db", flipped(page)))
    _refused_as_damaged(other, crafted, monkeypatch)


def test_a_parent_books_files_open_after_restore(books, margaret, mdb, settings, other, tmp_path):
    f = StoredFile(name="statement.pdf", content_type="application/pdf", size=20, sha256="0" * 64, uploaded_by=1)
    mdb.add(f)
    mdb.commit()
    books.file_store(margaret).write(f.id, b"%PDF-1.4 statement")
    made = X.make(books, margaret, PASS, settings, TODAY, NOW)
    info, counts = R.restore(other, made.path, PASS, None, [_manager(other)], NOW)
    assert (info.kind, counts["files"]) == ("parent", 1)
    assert other.file_store(info).read(f.id) == b"%PDF-1.4 statement"
    assert info.name == "Margaret Ellis (restored)"


def test_a_stored_files_id_is_never_reused_after_restore(books, margaret, mdb, settings, other):
    """stored_file is AUTOINCREMENT (a file's AAD names its id): the restored book keeps the high-water mark."""
    for n in ("a.pdf", "b.pdf"):
        mdb.add(StoredFile(name=n, content_type="application/pdf", size=1, sha256="0" * 64, uploaded_by=1))
    mdb.commit()
    mdb.execute(text("DELETE FROM file WHERE name = 'b.pdf'"))
    mdb.commit()
    seq = mdb.execute(text("SELECT seq FROM sqlite_sequence WHERE name = 'file'")).scalar()
    for fid in mdb.scalars(select(StoredFile.id)):
        books.file_store(margaret).write(fid, b"x")
    made = X.make(books, margaret, PASS, settings, TODAY, NOW)
    info, _ = R.restore(other, made.path, PASS, None, [_manager(other)], NOW)
    eng = make_engine(other.path_for(info), other.key_for(info))
    with eng.connect() as c:
        assert c.execute(text("SELECT seq FROM sqlite_sequence WHERE name = 'file'")).scalar() == seq
        assert c.execute(text("SELECT max(id) FROM file")).scalar() < seq
    eng.dispose()


def test_a_sealed_file_that_fails_to_open_leaves_no_new_book_and_no_scratch(books, margaret, mdb, settings, other,
                                                                           tmp_path, monkeypatch):
    f = StoredFile(name="statement.pdf", content_type="application/pdf", size=20, sha256="0" * 64, uploaded_by=1)
    mdb.add(f)
    mdb.commit()
    books.file_store(margaret).write(f.id, b"%PDF-1.4 statement")
    made = X.make(books, margaret, PASS, settings, TODAY, NOW)

    def flip(parts):
        b = bytearray(parts[f"files/{f.id}"])
        b[-1] ^= 1
        parts[f"files/{f.id}"] = bytes(b)
    crafted = _rezip(made.path, tmp_path / "flipped.zip", flip)
    made_dirs: list[Path] = []
    real = R.tempfile.mkdtemp
    monkeypatch.setattr(R.tempfile, "mkdtemp", lambda *a, **k: made_dirs.append(Path(real(*a, **k))) or made_dirs[-1])
    before = _books(other)
    with pytest.raises(R.RestoreRefused) as e:
        R.restore(other, crafted, PASS, None, [_manager(other)], NOW)
    assert e.value.message == R.BAD_FILE
    assert _books(other) == before
    assert not (other.settings.books_dir / "margaret-ellis-restored").exists()
    assert len(made_dirs) == 1 and not made_dirs[0].exists()


def test_the_cli_asks_for_the_passphrase_and_prints_only_the_slug_and_counts(exported, books, ddb, ours, monkeypatch):
    from steward.cli import app
    make_person(ddb, "jr", [(ours.id, "manager")])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": PASS)
    before = _books(books)
    r = CliRunner().invoke(app, ["restore-from-export", str(exported), "--name", "Mom (restored)", "--manager", "jr"])
    assert r.exit_code == 0, r.output
    assert r.output.strip() == "Restored book mom-restored: 4 transactions, 0 files"
    assert PASS not in r.output
    assert _books(books) == sorted(before + ["mom-restored"])          # a new book; ours untouched
    bad = CliRunner().invoke(app, ["restore-from-export", str(exported), "--manager", "nobody"])
    assert (bad.exit_code, bad.output.strip()) == (1, "No user nobody.")
