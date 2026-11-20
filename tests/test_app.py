import pytest
from fastapi import HTTPException
from sqlalchemy import select

from steward.db.models import EventLog
from steward.events import log_event


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json() == {"ok": True, "version": "0.1.2"}


def test_log_event(db):
    e = log_event(db, "warn", "sync", "Marcus needs you to sign in again at SimpleFIN", actionable=True)
    db.commit()
    got = db.scalars(select(EventLog)).one()
    assert got.id == e.id and got.actionable is True and got.level == "warn"


def test_refuses_to_start_when_migration_fails(settings, books, monkeypatch):
    from steward import app as app_module
    from steward.books.registry import DirectoryUnavailable
    from steward.db.migrate import MigrationFailed

    def boom(engine):
        raise MigrationFailed("bad")
    monkeypatch.setattr("steward.books.registry.run_directory_migrations", boom)
    from fastapi.testclient import TestClient
    with pytest.raises(DirectoryUnavailable):
        with TestClient(app_module.create_app(settings, books)):
            pass


def test_http_exception_dict_detail_is_the_body(app):
    @app.get("/api/teapot")
    def teapot():
        raise HTTPException(418, detail={"error": "teapot"})

    from fastapi.testclient import TestClient
    with TestClient(app, base_url="https://nas.tail1234.ts.net") as c:    # a name the API answers at (issue #8)
        r = c.get("/api/teapot")
    assert r.status_code == 418 and r.json() == {"error": "teapot"}
