"""Integration tests use a unique PostgreSQL schema, never application tables."""

import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

TEST_PASSWORD = "integration-test-password-only"


@pytest.fixture
def database(monkeypatch):
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to a disposable PostgreSQL database")
    monkeypatch.setenv("APP_NAME", "Cafe_Integration_Test")
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("JWT_SECRET_KEY", "integration-test-jwt-signing-key-32-chars")
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", TEST_PASSWORD)
    schema = f"test_{uuid4().hex}"
    control = create_engine(url)
    with control.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    from app.core.config import get_settings
    get_settings.cache_clear()
    try:
        yield engine
    finally:
        engine.dispose()
        with control.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        control.dispose()
        get_settings.cache_clear()


@pytest.fixture
def api(database, monkeypatch):
    from fastapi.testclient import TestClient
    from app.databases import script
    from app.databases.session import get_db
    from app.main import app
    from app.modules.realtime import router as realtime
    from app.modules.realtime.events import broker

    monkeypatch.setattr(script, "engine", database)
    script.initialize_database()
    sessions = sessionmaker(bind=database, expire_on_commit=False)
    monkeypatch.setattr(realtime, "SessionLocal", sessions)

    def test_session():
        with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = test_session
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.pop(get_db, None)
    broker.subscribers.clear()


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def accounts(api):
    result = {}
    for name, role in (("alice", "CUSTOMER"), ("bob", "CUSTOMER"), ("cashier", "CASHIER")):
        response = api.post("/auth/register", json={
            "user_name": name, "email": f"{name}@example.com", "password": TEST_PASSWORD, "role": role,
        })
        assert response.status_code == 201, response.text
        result[name] = {"user": response.json()}
    result["admin"] = {}
    for name in result:
        response = api.post("/auth/login", json={"identifier": name, "password": TEST_PASSWORD})
        assert response.status_code == 200, response.text
        result[name].update(response.json())
        result[name]["headers"] = auth_headers(response.json()["access_token"])
    return result
