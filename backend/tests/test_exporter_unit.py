import asyncio
import hashlib
import json
from types import SimpleNamespace
from pathlib import Path

import pytest

from archivedesk.exporter import _safe_error, commit_directory, reconcile_partial_file
from archivedesk.integrity import IntegrityError, validate_staging_export
from archivedesk.telegram import TelethonExportSession


def test_web_preview_photo_is_not_exported_as_message_media() -> None:
    preview_photo = object()
    message = SimpleNamespace(
        id=301,
        media=SimpleNamespace(webpage=object()),
        photo=preview_photo,
        file=SimpleNamespace(ext=".jpg", mime_type="image/jpeg", size=1024),
    )

    assert TelethonExportSession._media(message) is None


def test_native_photo_remains_exportable() -> None:
    message = SimpleNamespace(
        id=380,
        media=SimpleNamespace(photo=object()),
        file=SimpleNamespace(ext=".jpg", mime_type="image/jpeg", size=2048),
    )

    media = TelethonExportSession._media(message)

    assert media is not None
    assert media.kind == "photo"
    assert media.original_name == "photo_380.jpg"
    assert media.expected_size == 2048


def test_native_video_is_classified_separately_from_regular_files() -> None:
    message = SimpleNamespace(
        id=512,
        media=SimpleNamespace(document=object()),
        video=object(),
        file=SimpleNamespace(name="meeting.mp4", ext=".mp4", mime_type="video/mp4", size=8192),
    )

    media = TelethonExportSession._media(message)

    assert media is not None
    assert media.kind == "video"
    assert media.original_name == "meeting.mp4"
    assert media.mime_type == "video/mp4"
    assert media.expected_size == 8192


def test_unsupported_media_keeps_policy_metadata() -> None:
    message = SimpleNamespace(
        id=713,
        media=SimpleNamespace(document=object()),
        voice=object(),
        file=SimpleNamespace(name=None, ext=".ogg", mime_type="audio/ogg", size=4096),
    )

    media = TelethonExportSession._media(message)

    assert media is not None
    assert media.kind == "voice"
    assert media.policy_reason == "unsupported_media_type"
    assert media.public_dict()["policy_reason"] == "unsupported_media_type"


def test_unexpected_export_error_keeps_actionable_message() -> None:
    error = TypeError("Cannot cast MessageMediaWebPage to any kind of InputFileLocation")

    assert _safe_error(error) == (
        "TypeError: Cannot cast MessageMediaWebPage to any kind of InputFileLocation"
    )


def test_directory_commit_retries_transient_permission_error(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "partial"
    target = tmp_path / "complete"
    source.mkdir()
    (source / "manifest.json").write_text("{}", encoding="utf-8")
    original_rename = Path.rename
    attempts = 0

    def flaky_rename(path: Path, destination: Path):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError("temporarily locked")
        return original_rename(path, destination)

    monkeypatch.setattr(Path, "rename", flaky_rename)

    asyncio.run(commit_directory(source, target))

    assert attempts == 3
    assert (target / "manifest.json").is_file()


def test_directory_commit_keeps_partial_when_windows_keeps_directory_locked(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "partial"
    target = tmp_path / "complete"
    source.mkdir()
    (source / "result.json").write_text('{"ok": true}', encoding="utf-8")

    def locked_rename(path: Path, destination: Path):
        raise PermissionError("directory handle is still open")

    monkeypatch.setattr(Path, "rename", locked_rename)

    with pytest.raises(PermissionError):
        asyncio.run(commit_directory(source, target))

    assert source.is_dir()
    assert not target.exists()


@pytest.mark.parametrize(
    ("file_size", "checkpoint", "expected"),
    [
        (2 * 512, 512, 512),
        (512, 2 * 512, 512),
        (512 + 173, 512 + 173, 512),
        (512, 0, 0),
    ],
)
def test_partial_reconciliation_never_advances_past_durable_checkpoint(
    tmp_path: Path, file_size: int, checkpoint: int, expected: int
) -> None:
    partial = tmp_path / "asset.part"
    partial.write_bytes(b"x" * file_size)

    offset = reconcile_partial_file(partial, checkpoint, 512)

    assert offset == expected
    assert partial.stat().st_size == expected


def test_staging_integrity_gate_rejects_tampering_and_orphans(tmp_path: Path) -> None:
    root = tmp_path / "staging"
    media = root / "media" / "file" / "sample.bin"
    media.parent.mkdir(parents=True)
    payload = b"verified-media-payload"
    media.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    source = {"account_id": "tg_1", "dialog_id": "d1", "timezone": "UTC"}
    result = {
        "schema_version": 1,
        "exported_at": "2026-07-10T00:00:00+00:00",
        "source": source,
        "messages": [{"id": 1, "text": "hello", "media": None}],
    }
    (root / "result.json").write_text(json.dumps(result), encoding="utf-8")
    result_bytes = (root / "result.json").read_bytes()
    manifest = {
        "schema_version": 1,
        "job_id": "job1",
        "created_at": "2026-07-10T00:00:00+00:00",
        "completed_at": "2026-07-10T00:01:00+00:00",
        "completeness": "full",
        "source": source,
        "counts": {
            "messages": 1,
            "files_discovered": 1,
            "files_downloaded": 1,
            "files_skipped": 0,
        },
        "artifacts": {
            "result.json": {
                "size": len(result_bytes),
                "sha256": hashlib.sha256(result_bytes).hexdigest(),
            }
        },
        "files": [
            {
                "message_id": 1,
                "kind": "file",
                "path": "media/file/sample.bin",
                "size": len(payload),
                "sha256": digest,
                "status": "completed",
                "skip_reason": None,
            }
        ],
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assets = [
        {
            "message_id": 1,
            "kind": "file",
            "status": "completed",
            "relative_path": "media/file/sample.bin",
            "bytes_done": len(payload),
            "sha256": digest,
            "skip_reason": None,
        }
    ]

    validate_staging_export(root, assets)
    media.write_bytes(b"tampered-media-payload")
    with pytest.raises(IntegrityError, match="mismatch"):
        validate_staging_export(root, assets)
    media.write_bytes(payload)
    (root / "orphan.tmp").write_bytes(b"orphan")
    with pytest.raises(IntegrityError, match="orphan"):
        validate_staging_export(root, assets)
    (root / "orphan.tmp").unlink()
    with pytest.raises(IntegrityError, match="canary"):
        validate_staging_export(root, assets, canaries=[payload])
