import zipfile
import tempfile
import stat
from pathlib import Path
import pytest
from app.utils.archive import inspect_zip
from app.core.exceptions import ArchiveSecurityError

def test_path_traversal_rejected():
    tmp = Path(tempfile.mkdtemp())
    zip_path = tmp / "bad.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        z.writestr("../evil.txt", b"bad")
    with pytest.raises(ArchiveSecurityError):
        inspect_zip(
            zip_path,
            max_entries=64,
            max_uncompressed_bytes=200*1024*1024,
            max_compression_ratio=100,
            max_member_bytes=100*1024*1024,
        )

def test_compression_ratio_rejected():
    tmp = Path(tempfile.mkdtemp())
    zip_path = tmp / "bomb.zip"
    # Highly compressible data -> high ratio
    data = b"A" * 1024 * 100  # 100KB of 'A'
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("big.txt", data)
    # Set a very low max ratio to force rejection
    with pytest.raises(ArchiveSecurityError) as exc:
        inspect_zip(
            zip_path,
            max_entries=64,
            max_uncompressed_bytes=200*1024*1024,
            max_compression_ratio=2,
            max_member_bytes=100*1024*1024,
        )
    assert exc.value.args[0] in str(exc.value)

def test_encrypted_rejected(monkeypatch):
    tmp = Path(tempfile.mkdtemp())
    zip_path = tmp / "enc.zip"
    # Create a minimal valid zip
    with zipfile.ZipFile(zip_path, "w") as z:
        z.writestr("file.txt", b"data")
    # Monkeypatch ZipFile to return an info with encrypted flag
    original_ZipFile = zipfile.ZipFile

    class FakeInfo(zipfile.ZipInfo):
        def __init__(self):
            super().__init__("file.txt")
            self.file_size = 4
            self.compress_size = 4
            self.flag_bits = 1  # encrypted
            self.external_attr = 0

    def fake_ZipFile(path, mode="r"):
        class FakeCtx:
            def __enter__(self):
                return self
            def __exit__(self, *exc):
                return False
            def infolist(self):
                return [FakeInfo()]
        return FakeCtx()

    monkeypatch.setattr(zipfile, "ZipFile", fake_ZipFile)
    # Also need is_zipfile to return True
    monkeypatch.setattr(zipfile, "is_zipfile", lambda p: True)
    with pytest.raises(ArchiveSecurityError) as exc:
        inspect_zip(
            zip_path,
            max_entries=64,
            max_uncompressed_bytes=200*1024*1024,
            max_compression_ratio=100,
            max_member_bytes=100*1024*1024,
        )
    assert exc.value.args[0]  # encrypted reason present

def test_symlink_rejected(monkeypatch):
    tmp = Path(tempfile.mkdtemp())
    zip_path = tmp / "link.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        z.writestr("file.txt", b"data")

    original_ZipFile = zipfile.ZipFile

    class FakeInfo(zipfile.ZipInfo):
        def __init__(self):
            super().__init__("link")
            self.file_size = 0
            self.compress_size = 0
            self.flag_bits = 0
            # Set symlink mode in external_attr
            self.external_attr = (stat.S_IFLNK | 0o777) << 16

    def fake_ZipFile(path, mode="r"):
        class FakeCtx:
            def __enter__(self):
                return self
            def __exit__(self, *exc):
                return False
            def infolist(self):
                return [FakeInfo()]
        return FakeCtx()

    monkeypatch.setattr(zipfile, "ZipFile", fake_ZipFile)
    monkeypatch.setattr(zipfile, "is_zipfile", lambda p: True)
    with pytest.raises(ArchiveSecurityError) as exc:
        inspect_zip(
            zip_path,
            max_entries=64,
            max_uncompressed_bytes=200*1024*1024,
            max_compression_ratio=100,
            max_member_bytes=100*1024*1024,
        )
    assert exc.value.args[0]
