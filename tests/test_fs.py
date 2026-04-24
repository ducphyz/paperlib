from pathlib import Path
import hashlib

from paperlib.store.fs import sha256_file


def test_sha256_file_matches_hashlib(tmp_path: Path):
    p = tmp_path / "sample.bin"
    data = b"paperlib test data"
    p.write_bytes(data)

    assert sha256_file(p) == hashlib.sha256(data).hexdigest()
