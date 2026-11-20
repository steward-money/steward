"""The encrypted backup (spec P26-P29; The encrypted backup: crypto design): keys from Argon2id over the passphrase
alone, never steward.key; a single-book copy under the new key; the access URL and the undo journal stripped; a
parent book's files sealed with the files key; a 0700 folder outside backups/, swept after 10 minutes."""
import base64
import json
import os
import stat
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidTag
from sqlalchemy import text

from steward.db.engine import WrongKey, check_key, make_engine
from steward.db.models import SimplefinLink, StoredFile, UndoEntry, UndoText
from steward.services import book_export as X
from steward.settings import EXPORT_KDF, TEST_EXPORT_KDF, Settings
from tests.factories import make_account, make_txn

TODAY = date(2026, 10, 30)
NOW = datetime(2026, 10, 30, 19, 0, tzinfo=timezone.utc)
SECRET = "https://user:s3cret-token@bridge.simplefin.org/simplefin"
PASS = "correct horse battery"
NFC = "Café au lait 99"          # explicit escapes: an editor that normalizes must not turn these into one string
NFD = "Café au lait 99"


def _seed(db):
    a = make_account(db, "Joint checking")
    for i in range(3):
        make_txn(db, a, date(2026, 10, 1 + i), -100 * (i + 1), f"SHOP {i}")
    db.add(SimplefinLink(access_url=SECRET, claimed_at=NOW))
    db.add(UndoEntry(token="t" * 40, user_id=1, action="sort", created_at=NOW, expires_at=NOW, before="{}", after="{}"))
    db.add(UndoText(token="t" * 40, model="txn", row_key="1", field="nickname", value="zorblax", written=None))
    db.commit()


def _open(made: X.Made, tmp_path: Path, passphrase: str = PASS):
    with zipfile.ZipFile(made.path) as z:
        meta = json.loads(z.read("export.json"))
        (tmp_path / "out").mkdir(exist_ok=True)
        z.extract("book.db", tmp_path / "out")
    k = meta["kdf"]
    k_db, k_file = X.keys(passphrase, base64.b64decode(k["salt"]), k["t"], k["m_kib"], k["p"])
    return meta, make_engine(tmp_path / "out" / "book.db", k_db.hex()), k_file


def test_the_kdf_constants_are_pinned(settings):
    assert EXPORT_KDF == (3, 262144, 1) and TEST_EXPORT_KDF == (1, 8192, 1)
    assert settings.export_kdf == TEST_EXPORT_KDF
    assert Settings(_env_file=None, data_dir=settings.data_dir, base_url="https://x", env="prod").export_kdf == EXPORT_KDF


def test_an_nfc_and_an_nfd_passphrase_derive_the_same_keys():
    """Review focus 1 (P28): the same passphrase typed on a phone and on a laptop."""
    assert NFC != NFD
    salt = b"\x01" * 16
    assert X.keys(NFC, salt, 1, 8192, 1) == X.keys(NFD, salt, 1, 8192, 1)
    assert X.keys(NFC, salt, 1, 8192, 1) != X.keys("Cafe au lait 99", salt, 1, 8192, 1)
    db_key, file_key = X.keys(PASS, salt, 1, 8192, 1)
    assert len(db_key) == len(file_key) == 32 and db_key != file_key


def test_the_passphrase_rules():
    with pytest.raises(X.ExportRefused) as e:
        X.check_passphrase("eleven char", "eleven char")
    assert (e.value.status, e.value.message) == (422, "Use at least 12 characters.")
    with pytest.raises(X.ExportRefused) as e:
        X.check_passphrase("twelve chars", "twelve charz")
    assert e.value.message == "The two don't match."
    assert X.check_passphrase("Café au lait", "Café au lait") == "Café au lait"


def test_the_file_holds_export_json_and_book_db_and_names_nothing_about_the_book(books, ours, db, settings, tmp_path):
    _seed(db)
    made = X.make(books, ours, PASS, settings, TODAY, NOW)
    assert made.filename == "steward-ours-2026-10-30.steward-backup" and made.size == made.path.stat().st_size
    with zipfile.ZipFile(made.path) as z:
        assert sorted(z.namelist()) == ["book.db", "export.json"]
        assert all(i.compress_type == zipfile.ZIP_STORED for i in z.infolist())
        meta = json.loads(z.read("export.json"))
    assert set(meta) == {"format", "version", "kdf"} and (meta["format"], meta["version"]) == ("steward-book-export", 1)
    assert {k: meta["kdf"][k] for k in ("name", "t", "m_kib", "p")} == {"name": "argon2id", "t": 1, "m_kib": 8192, "p": 1}
    assert len(base64.b64decode(meta["kdf"]["salt"])) == 16


def test_the_file_never_holds_the_master_or_the_book_key_and_the_book_key_doesnt_open_it(books, ours, db, settings, tmp_path):
    _seed(db)
    made = X.make(books, ours, PASS, settings, TODAY, NOW)
    blob = made.path.read_bytes()
    for key in (books.master, books.key_for(ours)):
        assert key.encode() not in blob and key.upper().encode() not in blob and bytes.fromhex(key) not in blob
    _, eng, _ = _open(made, tmp_path)
    with pytest.raises(WrongKey):
        check_key(make_engine(tmp_path / "out" / "book.db", books.key_for(ours)))
    check_key(eng)
    eng.dispose()


def test_secrets_are_stripped_and_the_meta_is_inside(books, ours, db, settings, tmp_path):
    _seed(db)
    made = X.make(books, ours, PASS, settings, TODAY, NOW)
    _, eng, _ = _open(made, tmp_path)
    with eng.connect() as c:
        assert c.execute(text("SELECT access_url FROM simplefin_link")).scalars().all() == [""]
        assert c.execute(text("SELECT count(*) FROM undo_entry")).scalar() == 0
        assert c.execute(text("SELECT count(*) FROM undo_text")).scalar() == 0
        assert c.execute(text("SELECT count(*) FROM \"transaction\"")).scalar() == 3
        meta = dict(c.execute(text("SELECT key, value FROM export_meta")).all())
        assert c.execute(text("PRAGMA integrity_check")).scalar() == "ok"
    eng.dispose()
    assert {k: meta[k] for k in ("kind", "name", "revision")} == {"kind": "household", "name": ours.name, "revision": "0038"}
    assert json.loads(meta["owner_names"]) and meta["exported_at"] == NOW.isoformat()
    raw = (tmp_path / "out" / "book.db").read_bytes()
    assert b"s3cret-token" not in raw and b"zorblax" not in raw
    db.rollback()
    assert db.query(SimplefinLink).one().access_url == SECRET              # the live book is untouched


def test_nobodys_ask_conversations_or_queued_mail_travel_in_a_backup(books, ours, db, settings, tmp_path):
    """Ask conversations are private to each person, and the manager making the backup chooses its passphrase."""
    _seed(db)
    db.execute(text("INSERT INTO chat_conversation (user_id, title, created_at, updated_at, last_input_tokens) "
                    "VALUES (99, 'Sam asks about the zorblax surprise', :t, :t, 0)"), {"t": NOW})
    db.execute(text("INSERT INTO chat_seen (user_id, seen_at) VALUES (99, :t)"), {"t": NOW})
    db.execute(text("INSERT INTO mail_outbox (kind, flag_ids, status, created_at, next_try_at, tries, to_user_ids, "
                    "sent_to, subject, text_body, html_body) VALUES ('urgent', '[]', 'pending', :t, :t, 0, '[99]', "
                    "'[]', 'zorblax', 'zorblax', 'zorblax')"), {"t": NOW})
    db.commit()
    made = X.make(books, ours, PASS, settings, TODAY, NOW)
    _, eng, _ = _open(made, tmp_path)
    with eng.connect() as c:
        for t in ("chat_turn", "chat_message", "chat_seen", "chat_conversation", "mail_outbox"):
            assert c.execute(text(f'SELECT count(*) FROM "{t}"')).scalar() == 0, t
        assert c.execute(text("PRAGMA integrity_check")).scalar() == "ok"
    eng.dispose()
    assert db.execute(text("SELECT count(*) FROM chat_conversation")).scalar() == 1     # the live book is untouched


def test_a_parent_books_files_are_sealed_with_the_files_key(books, margaret, mdb, settings, tmp_path):
    f = StoredFile(name="statement.pdf", content_type="application/pdf", size=20, sha256="0" * 64, uploaded_by=1)
    mdb.add(f)
    mdb.commit()
    books.file_store(margaret).write(f.id, b"%PDF-1.4 zorblax pdf")
    made = X.make(books, margaret, PASS, settings, TODAY, NOW)
    with zipfile.ZipFile(made.path) as z:
        assert sorted(z.namelist()) == ["book.db", "export.json", f"files/{f.id}"]
        sealed = z.read(f"files/{f.id}")
    assert sealed.startswith(b"SWX1") and b"zorblax" not in made.path.read_bytes()
    _, eng, k_file = _open(made, tmp_path)
    eng.dispose()
    assert X.open_sealed(k_file, f.id, sealed) == b"%PDF-1.4 zorblax pdf"
    with pytest.raises(InvalidTag):
        X.open_sealed(k_file, f.id + 1, sealed)                           # the id is in the associated data


def test_the_folder_is_0700_the_zip_0600_and_outside_backups(books, ours, db, settings):
    made = X.make(books, ours, PASS, settings, TODAY, NOW)
    assert made.folder.name.startswith("steward-export-") and made.folder.parent == Path(settings.export_tmp_dir)
    assert stat.S_IMODE(made.folder.stat().st_mode) == 0o700 and stat.S_IMODE(made.path.stat().st_mode) == 0o600
    assert settings.backups_dir not in made.folder.parents
    assert sorted(p.name for p in made.folder.iterdir()) == [made.filename]      # book.db went into the ZIP


def test_a_failed_backup_leaves_no_folder(books, margaret, mdb, settings):
    """Review focus 3: a stored file that won't decrypt stops the backup, and nothing half-made is left."""
    f = StoredFile(name="gone.pdf", content_type="application/pdf", size=1, sha256="0" * 64, uploaded_by=1)
    mdb.add(f)
    mdb.commit()                                                           # no bytes on disk for it
    with pytest.raises(X.ExportRefused) as e:
        X.make(books, margaret, PASS, settings, TODAY, NOW)
    assert (e.value.status, e.value.message) == (409, "One of this book's files can't be read, so no backup was made.")
    assert list(Path(settings.export_tmp_dir).glob("steward-export-*")) == []
    assert not X.LOCK.locked()


def test_one_backup_at_a_time(books, ours, settings):
    X.LOCK.acquire()
    try:
        with pytest.raises(X.ExportRefused) as e:
            X.make(books, ours, PASS, settings, TODAY, NOW)
        assert (e.value.status, e.value.message) == (429, "Another backup is being made. Try again in a minute.")
    finally:
        X.LOCK.release()


def test_the_sweep_removes_old_folders_and_everything_at_startup(tmp_path):
    old, young, other = tmp_path / "steward-export-old", tmp_path / "steward-export-young", tmp_path / "keep-me"
    for d in (old, young, other):
        d.mkdir()
    os.utime(old, (1_000_000, 1_000_000))
    os.utime(young, (1_000_000 + 9 * 60, 1_000_000 + 9 * 60))
    assert X.sweep(tmp_path, now_ts=1_000_000 + 10 * 60) == 1
    assert sorted(p.name for p in tmp_path.iterdir()) == ["keep-me", "steward-export-young"]
    assert X.sweep(tmp_path, now_ts=0, everything=True) == 1
    assert [p.name for p in tmp_path.iterdir()] == ["keep-me"]
