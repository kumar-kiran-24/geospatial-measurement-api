import io
import pytest
from fastapi.testclient import TestClient

def test_health(client: TestClient):
    r = client.get("/health/")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"

def test_readiness(client: TestClient):
    r = client.get("/health/readiness")
    assert r.status_code == 200
    assert r.json()["status"] in ("ready", "degraded")

def test_list_files_empty(client: TestClient):
    # Skip DB-dependent test for now
    pytest.skip("DB not available in unit test")

def test_upload_invalid_file(client: TestClient):
    pytest.skip("DB not available in unit test")

# Unit tests for services can be added without DB
def test_file_validation_extension():
    from app.services.file_validation_service import FileValidationService
    from app.core.config import Settings
    settings = Settings()
    svc = FileValidationService(settings)
    ext = svc.validate_extension("test.kml")
    assert ext == ".kml"
    with pytest.raises(Exception):
        svc.validate_extension("test.exe")
