import pytest
from app.services.file_validation_service import FileValidationService
from app.core.config import Settings
from app.utils.archive import inspect_zip, ZIP_LOCAL_HEADER_MAGIC
from pathlib import Path
import tempfile
import zipfile

def test_validate_extension_allowed():
    settings = Settings()
    svc = FileValidationService(settings)
    assert svc.validate_extension("test.kml") == ".kml"
    assert svc.validate_extension("TEST.ZIP") == ".zip"

def test_validate_extension_disallowed():
    settings = Settings()
    svc = FileValidationService(settings)
    with pytest.raises(Exception):
        svc.validate_extension("test.exe")

def test_sniff_kml():
    settings = Settings()
    svc = FileValidationService(settings)
    with tempfile.NamedTemporaryFile(suffix=".kml", delete=False) as f:
        f.write(b'<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"></kml>')
        path = Path(f.name)
    try:
        fmt = svc.sniff(path, ".kml")
        from app.models.enums import FileFormat
        assert fmt == FileFormat.KML
    finally:
        path.unlink()

def test_archive_inspection_safe():
    tmp = Path(tempfile.mkdtemp())
    zip_path = tmp / "test.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        z.writestr("shp/test.shp", b"")
        z.writestr("shp/test.shx", b"")
        z.writestr("shp/test.dbf", b"")
    insp = inspect_zip(
        zip_path,
        max_entries=64,
        max_uncompressed_bytes=200*1024*1024,
        max_compression_ratio=100,
        max_member_bytes=100*1024*1024,
    )
    assert len(insp.members) == 3
    zip_path.unlink()

def test_storage_cleanup_idempotent(tmp_path):
    from app.services.file_storage_service import FileStorageService
    service = FileStorageService(tmp_path, max_upload_size_bytes=1024*1024)
    file_id = "test-id"
    ext_dir = service.extraction_directory(file_id)
    # extraction_directory already ensures dir exists
    (ext_dir / "tmp.txt").write_text("x")
    service.cleanup_extraction(file_id)
    # second call should be safe
    service.cleanup_extraction(file_id)
    assert not ext_dir.exists()

def test_file_record_processing_error_property():
    import uuid
    from app.models.file_record import FileRecord
    rec = FileRecord(
        id=uuid.uuid4(),
        original_filename='a.kml',
        storage_path='x',
        file_format='kml',
        size_bytes=1,
        checksum_sha256='0'*64,
        status='FAILED',
        error_code='TEST_ERR',
        error_message='something went wrong'
    )
    err = rec.processing_error
    assert err is not None
    assert err.code == 'TEST_ERR'
    assert err.message == 'something went wrong'
    rec2 = FileRecord(
        id=uuid.uuid4(),
        original_filename='b.kml',
        storage_path='y',
        file_format='kml',
        size_bytes=1,
        checksum_sha256='0'*64,
        status='COMPLETED',
    )
    assert rec2.processing_error is None
