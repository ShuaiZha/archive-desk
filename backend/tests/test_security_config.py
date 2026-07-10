from __future__ import annotations

import json

import pytest

from archivedesk.config import Settings
from archivedesk.security import SecretStore


def test_portable_credentials_file_can_be_read(tmp_path) -> None:
    path = tmp_path / "credentials.bin"
    value = {"api_id": 123456, "api_hash": "0123456789abcdef0123456789abcdef"}
    path.write_bytes(b"PLAIN1\0" + json.dumps(value).encode("utf-8"))

    assert SecretStore(path).credentials() == (
        123456,
        "0123456789abcdef0123456789abcdef",
    )


def test_container_environment_allows_internal_wildcard(tmp_path, monkeypatch) -> None:
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("container frontend", encoding="utf-8")

    monkeypatch.setenv("ARCHIVEDESK_CONTAINER", "1")
    monkeypatch.setenv("ARCHIVEDESK_HOST", "0.0.0.0")
    monkeypatch.setenv("ARCHIVEDESK_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ARCHIVEDESK_STATIC_DIR", str(static_dir))
    monkeypatch.setenv("ARCHIVEDESK_DEFAULT_OUTPUT_ROOT", str(tmp_path / "exports"))

    settings = Settings.from_env()
    settings.prepare()

    assert settings.container_mode is True
    assert settings.host == "0.0.0.0"
    assert settings.default_output_root == tmp_path / "exports"
    assert settings.default_output_root.is_dir()


def test_non_container_environment_rejects_wildcard_host(monkeypatch) -> None:
    monkeypatch.delenv("ARCHIVEDESK_CONTAINER", raising=False)
    monkeypatch.setenv("ARCHIVEDESK_HOST", "0.0.0.0")

    with pytest.raises(ValueError, match="loopback"):
        Settings.from_env()
