"""Licence activation from a key file / any text, and finding key files."""
from __future__ import annotations

import os
import time

import pytest

import licensing.store as store
from licensing import LicenseError
from licensing.models import License

VALID = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA.BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
EXPIRED = "EEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE.BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"


@pytest.fixture(autouse=True)
def fake_verifier(monkeypatch):
    def verify(key, public_key_b64=None):
        if key == VALID:
            return License(email="client@example.com", plan="pro")
        if key == EXPIRED:
            raise LicenseError("license expired")
        raise LicenseError("malformed license key")

    monkeypatch.setattr(store, "verify_license", verify)


class TestExtract:
    @pytest.mark.parametrize("text", [
        VALID,
        f"  {VALID}\n",
        "\ufeff" + VALID,                                   # Notepad BOM
        VALID[:30] + "\n" + VALID[30:60] + "\r\n" + VALID[60:],   # wrapped by a mail client
        f"Здравствуйте! Ваш ключ:\n\n{VALID}\n\nСпасибо за покупку.",
        # wrapped by a mail client inside a message — the real-world case
        f"Здравствуйте!\nВаш ключ Fluxion Pro:\n{VALID[:30]}\n{VALID[30:60]}\n{VALID[60:]}\nСпасибо!",
    ])
    def test_finds_key(self, text):
        assert store.extract_key(text) == VALID

    def test_no_key(self):
        with pytest.raises(LicenseError, match="не найден"):
            store.extract_key("просто текст без ключа")

    def test_reports_real_reason(self):
        with pytest.raises(LicenseError, match="expired"):
            store.extract_key(f"ключ: {EXPIRED}")


class TestFiles:
    def test_activate_file(self, tmp_path):
        key_file = tmp_path / "client@example.com-20261001.key"
        key_file.write_text("\ufeff" + VALID + "\n", encoding="utf-8")
        target = tmp_path / "license.key"
        lic = store.activate_file(key_file, target)
        assert lic.email == "client@example.com"
        assert target.read_text(encoding="utf-8").strip() == VALID

    def test_huge_or_missing_file(self, tmp_path):
        big = tmp_path / "big.key"
        big.write_text("x" * (store.MAX_KEY_FILE_BYTES + 1), encoding="utf-8")
        with pytest.raises(LicenseError, match="большой"):
            store.activate_file(big, tmp_path / "license.key")
        with pytest.raises(LicenseError, match="не найден"):
            store.activate_file(tmp_path / "nope.key", tmp_path / "license.key")

    def test_find_key_files(self, tmp_path, monkeypatch):
        monkeypatch.setattr(store, "DEFAULT_LICENSE_PATH", tmp_path / "active" / "license.key")
        downloads = tmp_path / "Downloads"
        downloads.mkdir()
        old = downloads / "old.key"
        old.write_text(VALID, encoding="utf-8")
        newer = downloads / "fluxion-license.txt"
        newer.write_text(f"ключ: {VALID}", encoding="utf-8")
        os.utime(old, (time.time() - 100, time.time() - 100))
        (downloads / "garbage.key").write_text("not a key", encoding="utf-8")
        (downloads / "expired.key").write_text(EXPIRED, encoding="utf-8")
        (downloads / "photo.png").write_bytes(b"\x89PNG")
        (downloads / "huge.key").write_text("x" * (store.MAX_KEY_FILE_BYTES + 1), encoding="utf-8")
        found = store.find_key_files([downloads])
        assert found == [newer, old]

    def test_find_skips_already_active_key(self, tmp_path, monkeypatch):
        active = tmp_path / "license.key"
        active.write_text(VALID + "\n", encoding="utf-8")
        monkeypatch.setattr(store, "DEFAULT_LICENSE_PATH", active)
        folder = tmp_path / "d"
        folder.mkdir()
        (folder / "same.key").write_text(VALID, encoding="utf-8")
        assert store.find_key_files([folder]) == []
