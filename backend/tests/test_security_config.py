from __future__ import annotations

import base64

import pytest

from archivedesk.config import Settings
from archivedesk.security import SecretStore, load_master_key


def test_aes_gcm_secret_store_round_trip_and_tamper_detection(tmp_path) -> None:
    secret_path = tmp_path / "credentials.bin"
    key = bytes(range(32))
    store = SecretStore(secret_path, encryption_key=key)

    store.save_credentials(123456, "0123456789abcdef0123456789abcdef")

    payload = secret_path.read_bytes()
    assert payload.startswith(b"AESGCM1\0")
    assert b"0123456789abcdef" not in payload
    assert store.credentials() == (123456, "0123456789abcdef0123456789abcdef")

    secret_path.write_bytes(payload[:-1] + bytes([payload[-1] ^ 0x01]))
    with pytest.raises(ValueError, match="failed authentication"):
        store.load()


def test_master_key_file_requires_32_base64_decoded_bytes(tmp_path) -> None:
    path = tmp_path / "master-key"
    key = b"k" * 32
    path.write_text(base64.b64encode(key).decode("ascii"), encoding="ascii")

    assert load_master_key(path) == key

    path.write_text(base64.b64encode(b"too-short").decode("ascii"), encoding="ascii")
    with pytest.raises(ValueError, match="exactly 32 bytes"):
        load_master_key(path)


def test_container_environment_requires_secret_and_allows_internal_wildcard(
    tmp_path,
    monkeypatch,
) -> None:
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("container frontend", encoding="utf-8")
    master_key = tmp_path / "master-key"
    master_key.write_text(base64.b64encode(b"m" * 32).decode("ascii"), encoding="ascii")

    monkeypatch.setenv("ARCHIVEDESK_CONTAINER", "1")
    monkeypatch.setenv("ARCHIVEDESK_HOST", "0.0.0.0")
    monkeypatch.setenv("ARCHIVEDESK_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ARCHIVEDESK_STATIC_DIR", str(static_dir))
    monkeypatch.setenv("ARCHIVEDESK_DEFAULT_OUTPUT_ROOT", str(tmp_path / "exports"))
    monkeypatch.setenv("ARCHIVEDESK_MASTER_KEY_FILE", str(master_key))

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


def test_container_prepare_rejects_missing_master_key(tmp_path) -> None:
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("container frontend", encoding="utf-8")
    settings = Settings(
        data_dir=tmp_path / "data",
        container_mode=True,
        static_dir=static_dir,
        default_output_root=tmp_path / "exports",
    )

    with pytest.raises(ValueError, match="MASTER_KEY_FILE is required"):
        settings.prepare()
