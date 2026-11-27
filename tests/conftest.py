from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from dotenv import dotenv_values
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


# Supply every setting without depending on local credentials or services.
os.environ.update({
    key: value for key, value in dotenv_values(
        Path(__file__).resolve().parents[1] / ".env.example"
    ).items() if value is not None
})
os.environ.update(
    DB_PASSWORD="test-password",
    JWT_SECRET_KEY="test-signing-key-with-at-least-32-bytes",
    LLM_API_KEY="test-key",
)

from app.repositories.auth import AuthRepository  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.security import create_access_token  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Recommendation, User  # noqa: E402
from app.schemas import AuthenticatedUser  # noqa: E402
from app.services.catalog import CatalogLoader, CatalogService  # noqa: E402


@pytest.fixture
def api(tmp_path, monkeypatch):
    settings = get_settings().model_copy(deep=True)
    catalog = tmp_path / "menus.json"
    catalog.write_text(json.dumps({
        "version": 1,
        "menus": [
            {
                "id": menu_id, "name": name, "description": description,
                "is_active": active, "your_food": ["+---+", "| o |", "+---+"],
            }
            for menu_id, name, description, active in [
                ("menu-003", "Ikan laut", "Paket pesisir", True),
                ("menu-001", "Ikan bakar", "Paket ikan", True),
                ("menu-002", "Ayam", "Paket daratan", True),
                ("menu-004", "Ikan nonaktif", "Tidak tersedia", False),
            ]
        ],
    }))
    loader = CatalogLoader(catalog)
    loader.load()
    catalog_service = CatalogService(loader)
    owner, other = uuid4(), uuid4()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    identities = {}

    def token_for(user_id, *, expired=False, revoked=False):
        session_id = uuid4()
        expires_at = now + timedelta(minutes=-1 if expired else 30)
        if not revoked:
            identities[session_id] = AuthenticatedUser(
                user_id=user_id, session_id=session_id, email="user@example.com",
                session_expires_at=expires_at,
            )
        return create_access_token(
            user_id=user_id, session_id=session_id,
            issued_at=now - timedelta(minutes=5), expires_at=expires_at,
            settings=settings,
        )

    async def valid_identity(repository, *, user_id, session_id, now):
        identity = identities.get(session_id)
        return identity if identity and identity.user_id == user_id else None

    monkeypatch.setattr(AuthRepository, "get_valid_identity", valid_identity)
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False},
    )
    # SQLite runs the real repository's WHERE/ORDER BY queries. Production DDL
    # remains PostgreSQL-specific, so only this test table is created manually.
    with engine.begin() as connection:
        User.__table__.create(connection)
        connection.execute(text("""
            CREATE TABLE recommendations (
                id VARCHAR(32) PRIMARY KEY, user_id VARCHAR(32) NOT NULL,
                menu_id VARCHAR(64) NOT NULL, region_name VARCHAR(150) NOT NULL,
                geographic_description TEXT NOT NULL, selection_reason TEXT NOT NULL,
                menu_snapshot JSON NOT NULL, llm_model VARCHAR(255) NOT NULL,
                created_at DATETIME NOT NULL
            )
        """))
    snapshot = {
        "id": "removed-menu", "name": "Menu saat dibuat",
        "description": "Snapshot tersimpan", "your_food": ["[o]"],
    }
    with Session(engine) as session:
        for number, user_id, created_at in [
            (1, owner, now - timedelta(days=1)),
            (2, owner, now), (3, owner, now), (4, other, now),
        ]:
            session.add(Recommendation(
                id=UUID(int=number), user_id=user_id, menu_id=snapshot["id"],
                region_name="Pesisir", geographic_description="Daerah pesisir utara",
                selection_reason="Bahan lokal", menu_snapshot=snapshot,
                llm_model="test-model", created_at=created_at,
            ))
        session.commit()

    class TestSession:
        async def __aenter__(self):
            self.session = Session(engine, expire_on_commit=False)
            return self

        async def __aexit__(self, *args):
            self.session.close()

        @asynccontextmanager
        async def begin(self):
            with self.session.begin():
                yield

        def add(self, record):
            self.session.add(record)

        async def flush(self):
            self.session.flush()

        @staticmethod
        def restore_timezone(record):
            # SQLite drops DateTime timezone info; PostgreSQL preserves it.
            if record is not None:
                record.created_at = record.created_at.replace(tzinfo=timezone.utc)
                if isinstance(record, User):
                    record.updated_at = record.updated_at.replace(tzinfo=timezone.utc)
            return record

        async def scalar(self, statement):
            return self.restore_timezone(self.session.scalar(statement))

        async def scalars(self, statement):
            records = [
                self.restore_timezone(record) for record in self.session.scalars(statement)
            ]
            return SimpleNamespace(all=lambda: records)

    app = create_app()
    limiter = SimpleNamespace(enforce=AsyncMock())
    app.state.settings = settings
    app.state.session_factory = TestSession
    app.state.catalog_service = catalog_service
    app.state.rate_limiter = limiter
    app.state.llm_client = SimpleNamespace()
    client = TestClient(app)
    yield SimpleNamespace(
        client=client, app=app, owner=owner, other=other, settings=settings,
        limiter=limiter, token_for=token_for, snapshot=snapshot,
        headers={"Authorization": f"Bearer {token_for(owner)}"},
    )
    client.close()
    engine.dispose()
