from __future__ import annotations

import tempfile
import unittest
import base64
import hashlib
from unittest.mock import patch
from pathlib import Path

from formatura_distribuidor.updater import UpdateInfo, configured_manifest_url, download_update, version_tuple


class FakeResponse:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.offset = 0
        self.headers = {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self.data) - self.offset
        chunk = self.data[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk


class UpdaterTests(unittest.TestCase):
    def test_version_comparison_is_numeric(self) -> None:
        self.assertGreater(version_tuple("0.10.0"), version_tuple("0.9.9"))
        self.assertEqual(version_tuple("v1.2.3"), (1, 2, 3))

    def test_empty_configuration_disables_online_check(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "update_config.json").write_text(
                '{"manifest_url": ""}', encoding="utf-8"
            )
            self.assertEqual(configured_manifest_url(root), "")

    def test_download_decodes_base64_and_verifies_hash(self) -> None:
        package = b"PK\x03\x04formatch-test"
        encoded = base64.b64encode(package)
        info = UpdateInfo(
            version="9.9.9",
            download_url="https://example.test/update.b64",
            sha256=hashlib.sha256(package).hexdigest(),
            encoding="base64",
        )
        with patch(
            "formatura_distribuidor.updater.urllib.request.urlopen",
            return_value=FakeResponse(encoded),
        ):
            downloaded = download_update(info)
        self.assertEqual(downloaded.read_bytes(), package)
        downloaded.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
