import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Use SQLite in-memory for unit tests to avoid DB dependency
os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from app.main import create_app
from app.core.config import get_settings
from app.db.base import Base
from app.db.session import get_engine, get_session_factory
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

@pytest.fixture(scope="session")
def app():
    get_settings.cache_clear()
    settings = get_settings()
    # Override storage root to temp
    tmp = Path(tempfile.mkdtemp())
    settings.storage_root = tmp
    settings.ensure_storage_root()
    # Use SQLite for tests
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    app = create_app(settings)
    yield app
    Base.metadata.drop_all(engine)

@pytest.fixture
def client(app):
    return TestClient(app)

@pytest.fixture
def db_session(app):
    # Provide a fresh session per test
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()
    Base.metadata.drop_all(engine)
