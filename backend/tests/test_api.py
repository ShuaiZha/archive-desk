from __future__ import annotations

import asyncio
import hashlib
import json
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncIterator

import pytest
from fastapi.testclient import TestClient

from archivedesk.app import create_app
from archivedesk.config import Settings
from archivedesk.telegram import ExportMessage, MediaDescriptor


PHOTO_BYTES = b"photo-payload-for-archive-desk"
DOCUMENT_BYTES = b"document-payload-for-archive-desk"
VIDEO_BYTES = b"video-payload-for-archive-desk"


class FakeExportSession:
    def __init__(self):
        self.download_offsets: list[int] = []
        self.payloads = {2: PHOTO_BYTES, 3: b"not downloaded", 4: DOCUMENT_BYTES, 5: VIDEO_BYTES}

    async def latest_message_id(self, dialog_id: str) -> int | None:
        return 5

    async def iter_messages(
        self,
        dialog_id: str,
        date_from: datetime | None,
        date_to_exclusive: datetime | None,
        before_message_id: int | None = None,
    ) -> AsyncIterator[ExportMessage]:
        assert dialog_id == "-1009001"
        assert before_message_id in {None, 6}
        base = dict(
            edit_date=None,
            sender_id="42",
            reply_to_message_id=None,
            forward=None,
            raw={"out": False, "entities": []},
        )
        yield ExportMessage(
            id=1,
            date="2026-07-01T00:00:00+00:00",
            text="hello",
            media=None,
            **base,
        )
        yield ExportMessage(
            id=2,
            date="2026-07-01T00:01:00+00:00",
            text="photo",
            media=MediaDescriptor("photo", "camera.jpg", "image/jpeg", len(PHOTO_BYTES)),
            **base,
        )
        yield ExportMessage(
            id=3,
            date="2026-07-01T00:02:00+00:00",
            text="large file",
            media=MediaDescriptor("file", "large.zip", "application/zip", 2 * 1024 * 1024),
            **base,
        )
        yield ExportMessage(
            id=4,
            date="2026-07-01T00:03:00+00:00",
            text="document",
            media=MediaDescriptor("file", "notes.txt", "text/plain", len(DOCUMENT_BYTES)),
            **base,
        )
        yield ExportMessage(
            id=5,
            date="2026-07-01T00:04:00+00:00",
            text="video",
            media=MediaDescriptor("video", "clip.mp4", "video/mp4", len(VIDEO_BYTES)),
            **base,
        )

    async def download_media(self, dialog_id, message_id, target, offset, progress, control) -> int:
        await control()
        self.download_offsets.append(offset)
        payload = self.payloads[message_id]
        mode = "r+b" if target.exists() else "wb"
        with target.open(mode) as stream:
            stream.truncate(offset)
            stream.seek(offset)
            stream.write(payload[offset:])
        await progress(len(payload))
        return len(payload)


class FakeProvider:
    def __init__(self, export: Any | None = None):
        self.export = export or FakeExportSession()
        self.deleted: list[str] = []
        self.cancelled_flows: list[str] = []

    async def begin_auth(self, flow_id: str, phone: str) -> dict[str, Any]:
        return {"id": flow_id, "status": "code_required", "phone_masked": "+86******00"}

    async def submit_code(self, flow_id: str, code: str) -> dict[str, Any]:
        assert code == "12345"
        return {
            "id": flow_id,
            "status": "authorized",
            "phone_masked": "+86******00",
            "account": {
                "id": "tg_42",
                "telegram_user_id": 42,
                "session_name": "fake_session_secret",
                "display_name": "Test User",
                "username": "tester",
                "phone_masked": "+86******00",
            },
        }

    async def submit_password(self, flow_id: str, password: str) -> dict[str, Any]:
        raise AssertionError("not used")

    async def resend_auth(self, flow_id: str) -> dict[str, Any]:
        return {"id": flow_id, "status": "code_required", "phone_masked": "+86******00"}

    async def cancel_auth(self, flow_id: str) -> None:
        self.cancelled_flows.append(flow_id)

    async def list_dialogs(self, account: dict[str, Any]) -> list[dict[str, Any]]:
        assert account["session_name"] == "fake_session_secret"
        return [
            {
                "id": "-1009001",
                "peer_id": "-1009001",
                "title": "Test Channel",
                "category": "channel",
                "username": "testchannel",
                "unread_count": 0,
                "message_count": None,
            }
        ]

    async def dialog_bounds(self, account: dict[str, Any], dialog_id: str) -> dict[str, str | None]:
        assert account["session_name"] == "fake_session_secret"
        assert dialog_id == "-1009001"
        return {
            "earliest_message_at": "2026-07-01T00:00:00+00:00",
            "latest_message_at": "2026-07-01T00:04:00+00:00",
        }

    @asynccontextmanager
    async def export_session(self, account: dict[str, Any]):
        yield self.export

    async def delete_session(self, account: dict[str, Any]) -> None:
        self.deleted.append(account["id"])

    async def close(self) -> None:
        pass


class ResumeFakeSession:
    payload = b"x" * (4 * 512 * 1024 + 173)

    def __init__(self, block_after_first_chunk: bool, block_boundary: int = 512 * 1024):
        self.block_after_first_chunk = block_after_first_chunk
        self.block_boundary = block_boundary
        self.download_offsets: list[int] = []

    async def latest_message_id(self, dialog_id: str) -> int | None:
        return 9

    async def iter_messages(
        self, dialog_id, date_from, date_to_exclusive, before_message_id=None
    ):
        yield ExportMessage(
            id=9,
            date="2026-07-01T00:00:00+00:00",
            edit_date=None,
            sender_id="42",
            text="resumable",
            reply_to_message_id=None,
            forward=None,
            media=MediaDescriptor("file", "resumable.bin", "application/octet-stream", len(self.payload)),
            raw={"out": False, "entities": []},
        )

    async def download_media(self, dialog_id, message_id, target, offset, progress, control):
        self.download_offsets.append(offset)
        mode = "r+b" if target.exists() else "wb"
        if self.block_after_first_chunk:
            first_boundary = self.block_boundary
            with target.open(mode) as stream:
                stream.truncate(offset)
                stream.seek(offset)
                stream.write(self.payload[offset:first_boundary])
            await progress(first_boundary)
            await asyncio.Event().wait()
            raise AssertionError("unreachable")
        with target.open(mode) as stream:
            stream.truncate(offset)
            stream.seek(offset)
            stream.write(self.payload[offset:])
        await progress(len(self.payload))
        return len(self.payload)


class CursorFakeSession:
    def __init__(self, *, fail_after: int | None = None):
        self.fail_after = fail_after
        self.before_message_ids: list[int | None] = []

    async def latest_message_id(self, dialog_id: str) -> int | None:
        return 600

    async def iter_messages(
        self, dialog_id, date_from, date_to_exclusive, before_message_id=None
    ):
        self.before_message_ids.append(before_message_id)
        upper = (before_message_id - 1) if before_message_id is not None else 600
        emitted = 0
        for message_id in range(upper, 0, -1):
            yield ExportMessage(
                id=message_id,
                date="2026-07-01T00:00:00+00:00",
                edit_date=None,
                sender_id="42",
                text=f"message {message_id}",
                reply_to_message_id=None,
                forward=None,
                media=None,
                raw={"out": False, "entities": []},
            )
            emitted += 1
            if self.fail_after is not None and emitted >= self.fail_after:
                raise RuntimeError("simulated scan interruption")

    async def download_media(self, *args, **kwargs):
        raise AssertionError("no media should be downloaded")


def wait_for_job(
    client: TestClient,
    job_id: str,
    timeout: float = 5,
    *,
    auto_confirm: bool = True,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/export-jobs/{job_id}")
        response.raise_for_status()
        job = response.json()
        if job["status"] == "awaiting_confirmation":
            if not auto_confirm:
                return job
            client.post(f"/api/v1/export-jobs/{job_id}/actions/confirm").raise_for_status()
            time.sleep(0.02)
            continue
        if job["status"] in {"succeeded", "failed", "cancelled"}:
            return job
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def test_round_one_login_dialog_and_real_export(tmp_path: Path) -> None:
    provider = FakeProvider()
    app = create_app(Settings(data_dir=tmp_path / "state"), provider)
    with TestClient(app) as client:
        assert client.get("/api/v1/health").json()["status"] == "ok"
        initial = client.get("/api/v1/bootstrap").json()
        assert initial["credentials_configured"] is False

        credentials = client.put(
            "/api/v1/telegram/credentials",
            json={"api_id": 123456, "api_hash": "0123456789abcdef0123456789abcdef"},
        )
        assert credentials.status_code == 200
        assert credentials.json() == {
            "configured": True,
            "api_id": 123456,
            "api_hash_masked": "••••••••",
        }
        assert "0123456789abcdef" not in credentials.text

        flow = client.post("/api/v1/auth/flows", json={"phone": "+8613800000000"}).json()
        authorized = client.post(
            f"/api/v1/auth/flows/{flow['id']}/code", json={"code": "12345"}
        )
        assert authorized.status_code == 200
        account = authorized.json()["account"]
        assert account["id"] == "tg_42"
        assert "session_name" not in account
        assert "fake_session_secret" not in authorized.text

        dialogs = client.get("/api/v1/accounts/tg_42/dialogs")
        assert dialogs.status_code == 200
        assert dialogs.json()["items"][0]["id"] == "-1009001"
        bounds = client.get("/api/v1/accounts/tg_42/dialogs/-1009001/bounds")
        assert bounds.status_code == 200
        assert bounds.json() == {
            "dialog_id": "-1009001",
            "earliest_message_at": "2026-07-01T00:00:00+00:00",
            "latest_message_at": "2026-07-01T00:04:00+00:00",
        }

        output = tmp_path / "exports"
        root = client.post("/api/v1/output-roots", json={"path": str(output)})
        assert root.status_code == 201
        job_payload = {
            "account_id": "tg_42",
            "dialog_id": "-1009001",
            "output_root_id": root.json()["id"],
            "max_file_size_mb": 1,
            "media_types": ["photo", "video", "file"],
            "time_zone": "Asia/Shanghai",
        }
        job_response = client.post(
            "/api/v1/export-jobs",
            json=job_payload,
            headers={"Idempotency-Key": "round-one-export"},
        )
        assert job_response.status_code == 201
        replay = client.post(
            "/api/v1/export-jobs",
            json=job_payload,
            headers={"Idempotency-Key": "round-one-export"},
        )
        assert replay.status_code == 201
        assert replay.json()["id"] == job_response.json()["id"]
        conflict = client.post(
            "/api/v1/export-jobs",
            json={**job_payload, "max_file_size_mb": 2},
            headers={"Idempotency-Key": "round-one-export"},
        )
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
        preflight = wait_for_job(client, job_response.json()["id"], auto_confirm=False)
        assert preflight["status"] == "awaiting_confirmation"
        assert preflight["stage"] == "preflight_ready"
        assert preflight["config"]["time_zone"] == "Asia/Shanghai"
        assert preflight["progress"]["upper_message_id"] == 5
        assert preflight["progress"]["photos_bytes_total"] == len(PHOTO_BYTES)
        assert preflight["progress"]["videos_bytes_total"] == len(VIDEO_BYTES)
        assert preflight["progress"]["regular_files_bytes_total"] == len(DOCUMENT_BYTES)
        assert preflight["progress"]["photos_unknown_size"] == 0
        assert preflight["progress"]["videos_unknown_size"] == 0
        assert preflight["progress"]["regular_files_unknown_size"] == 0
        assert provider.export.download_offsets == []
        client.post(
            f"/api/v1/export-jobs/{preflight['id']}/actions/confirm"
        ).raise_for_status()
        job = wait_for_job(client, job_response.json()["id"])
        assert job["status"] == "succeeded", job
        assert job["progress"]["messages_saved"] == 5
        assert job["progress"]["files_done"] == 3
        assert job["progress"]["enumeration_completed"] is True
        assert job["progress"]["capacity_checked"] is True
        assert job["progress"]["disk_free_bytes"] >= job["progress"]["disk_required_bytes"]
        assert job["revision"] > 0
        events = client.get(f"/api/v1/export-jobs/{job['id']}/events?after=0")
        assert events.status_code == 200
        assert "event: job" in events.text
        assert '"revision":' in events.text

        export_path = Path(job["output_path"])
        result = json_load(export_path / "result.json")
        manifest = client.get(f"/api/v1/export-jobs/{job['id']}/manifest")
        assert manifest.status_code == 200
        manifest_json = manifest.json()
        assert len(result["messages"]) == 5
        assert manifest_json["completeness"] == "policy_filtered"
        assert manifest_json["counts"] == {
            "messages": 5,
            "files_discovered": 4,
            "files_downloaded": 3,
            "files_skipped": 1,
        }
        assert manifest_json["storage"]["layout"] == "media/{kind}/{year}/{month}/{shard}/{filename}"
        assert manifest_json["artifacts"]["result.json"]["sha256"] == hashlib.sha256(
            (export_path / "result.json").read_bytes()
        ).hexdigest()
        assert set(manifest_json["artifacts"]) == {"result.json"}
        files = {item["message_id"]: item for item in manifest_json["files"]}
        assert files[2]["sha256"] == hashlib.sha256(PHOTO_BYTES).hexdigest()
        assert files[3]["status"] == "skipped"
        assert files[3]["skip_reason"] == "size_limit"
        assert files[4]["sha256"] == hashlib.sha256(DOCUMENT_BYTES).hexdigest()
        assert files[5]["sha256"] == hashlib.sha256(VIDEO_BYTES).hexdigest()
        assert files[2]["path"].startswith("media/photo/2026/07/")
        assert files[4]["path"].startswith("media/file/2026/07/")
        assert files[5]["path"].startswith("media/video/2026/07/")
        assert provider.export.download_offsets == [0, 0, 0]
        assert not list(output.rglob("*.part"))
        assert not (export_path / "index.html").exists()
        jobs = client.get("/api/v1/export-jobs").json()["items"]
        assert jobs[0]["dialog_title"] == "Test Channel"
        assert client.get(f"/api/v1/export-jobs/{job['id']}/viewer").status_code == 404
        video_path = files[5]["path"].removeprefix("media/")
        media = client.get(f"/api/v1/export-jobs/{job['id']}/media/{video_path}")
        assert media.content == VIDEO_BYTES
        deleted = client.delete(f"/api/v1/export-jobs/{job['id']}?delete_files=true")
        assert deleted.status_code == 204
        assert not export_path.exists()
        assert client.get(f"/api/v1/export-jobs/{job['id']}").status_code == 404

        unlimited_response = client.post(
            "/api/v1/export-jobs",
            json={
                "account_id": "tg_42",
                "dialog_id": "-1009001",
                "output_root_id": root.json()["id"],
                "max_file_size_mb": None,
                "media_types": ["photo", "video", "file"],
            },
        )
        assert unlimited_response.status_code == 201
        unlimited = wait_for_job(client, unlimited_response.json()["id"], auto_confirm=False)
        assert unlimited["config"]["max_file_size_mb"] is None
        assert unlimited["progress"]["files_total"] == 4
        assert unlimited["progress"]["files_skipped"] == 0
        assert unlimited["progress"]["regular_files_bytes_total"] == (
            2 * 1024 * 1024 + len(DOCUMENT_BYTES)
        )
        client.post(f"/api/v1/export-jobs/{unlimited['id']}/actions/cancel").raise_for_status()
        client.delete(f"/api/v1/export-jobs/{unlimited['id']}?delete_files=true").raise_for_status()


def test_insufficient_disk_preserves_preflight_estimate(tmp_path: Path, monkeypatch) -> None:
    provider = FakeProvider()
    app = create_app(Settings(data_dir=tmp_path / "state"), provider)
    free_bytes = 512 * 1024 * 1024
    monkeypatch.setattr(
        "archivedesk.exporter.shutil.disk_usage",
        lambda _path: SimpleNamespace(free=free_bytes),
    )

    with TestClient(app) as client:
        client.put(
            "/api/v1/telegram/credentials",
            json={"api_id": 123456, "api_hash": "0123456789abcdef0123456789abcdef"},
        ).raise_for_status()
        flow = client.post("/api/v1/auth/flows", json={"phone": "+8613800000000"}).json()
        client.post(f"/api/v1/auth/flows/{flow['id']}/code", json={"code": "12345"}).raise_for_status()
        client.get("/api/v1/accounts/tg_42/dialogs").raise_for_status()
        root = client.post(
            "/api/v1/output-roots",
            json={"path": str(tmp_path / "exports")},
        ).json()
        created = client.post(
            "/api/v1/export-jobs",
            json={
                "account_id": "tg_42",
                "dialog_id": "-1009001",
                "output_root_id": root["id"],
                "max_file_size_mb": 1,
                "media_types": ["photo", "video", "file"],
            },
        ).json()

        blocked = wait_for_job(client, created["id"], auto_confirm=False)
        progress = blocked["progress"]
        expected_download = len(PHOTO_BYTES) + len(DOCUMENT_BYTES) + len(VIDEO_BYTES)
        assert blocked["status"] == "awaiting_confirmation"
        assert blocked["stage"] == "preflight_ready"
        assert progress["bytes_total"] == expected_download
        assert progress["disk_free_bytes"] == free_bytes
        assert progress["capacity_sufficient"] is False
        assert progress["disk_shortfall_bytes"] == progress["disk_required_bytes"] - free_bytes
        assert provider.export.download_offsets == []
        assert client.post(
            f"/api/v1/export-jobs/{created['id']}/actions/confirm"
        ).status_code == 409

        free_bytes = 4 * 1024 * 1024 * 1024
        client.post(
            f"/api/v1/export-jobs/{created['id']}/actions/recheck"
        ).raise_for_status()
        ready = wait_for_job(client, created["id"], auto_confirm=False)
        assert ready["progress"]["capacity_sufficient"] is True
        assert ready["progress"]["disk_shortfall_bytes"] == 0
        assert provider.export.download_offsets == []


def json_load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def test_rejects_non_local_browser_origin(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path / "state"), FakeProvider())
    with TestClient(app) as client:
        response = client.put(
            "/api/v1/telegram/credentials",
            headers={"Origin": "https://evil.example"},
            json={"api_id": 123, "api_hash": "0123456789abcdef"},
        )
        assert response.status_code == 403


def test_auth_flow_controls_dialog_pagination_and_error_envelope(tmp_path: Path) -> None:
    class ManyDialogsProvider(FakeProvider):
        async def list_dialogs(self, account: dict[str, Any]) -> list[dict[str, Any]]:
            return [
                {
                    "id": str(index),
                    "peer_id": str(index),
                    "title": f"Dialog {index:03d}",
                    "category": "channel" if index % 2 else "group",
                    "username": None,
                    "unread_count": 0,
                    "message_count": None,
                }
                for index in range(135)
            ]

    provider = ManyDialogsProvider()
    app = create_app(Settings(data_dir=tmp_path / "state"), provider)
    with TestClient(app) as client:
        invalid = client.post("/api/v1/export-jobs", json={})
        assert invalid.status_code == 422
        assert invalid.json()["error"]["code"] == "INVALID_REQUEST"
        assert invalid.json()["error"]["request_id"] == invalid.headers["X-Request-ID"]

        client.put(
            "/api/v1/telegram/credentials",
            json={"api_id": 123456, "api_hash": "0123456789abcdef0123456789abcdef"},
        ).raise_for_status()
        cancelled = client.post("/api/v1/auth/flows", json={"phone": "+8613800000000"}).json()
        queried = client.get(f"/api/v1/auth/flows/{cancelled['id']}")
        assert queried.status_code == 200
        assert queried.json()["status"] == "code_required"
        resent = client.post(f"/api/v1/auth/flows/{cancelled['id']}/resend")
        assert resent.status_code == 200
        assert resent.json()["status"] == "code_required"
        assert client.delete(f"/api/v1/auth/flows/{cancelled['id']}").status_code == 204
        assert provider.cancelled_flows == [cancelled["id"]]

        flow = client.post("/api/v1/auth/flows", json={"phone": "+8613800000000"}).json()
        client.post(f"/api/v1/auth/flows/{flow['id']}/code", json={"code": "12345"}).raise_for_status()
        first_page = client.get("/api/v1/accounts/tg_42/dialogs?limit=100").json()
        assert len(first_page["items"]) == 100
        assert first_page["next_cursor"] == "100"
        second_page = client.get(
            "/api/v1/accounts/tg_42/dialogs?limit=100&cursor=100"
        ).json()
        assert len(second_page["items"]) == 35
        assert second_page["next_cursor"] is None
        refreshed = client.post("/api/v1/accounts/tg_42/dialogs/refresh")
        assert refreshed.status_code == 200
        assert len(refreshed.json()["items"]) == 100
        deceptive = client.put(
            "/api/v1/telegram/credentials",
            headers={"Origin": "http://127.0.0.1.evil.example:8000"},
            json={"api_id": 123, "api_hash": "0123456789abcdef"},
        )
        assert deceptive.status_code == 403


@pytest.mark.parametrize(
    ("checkpoint", "corrupt_partial", "expected_offset"),
    [
        (512 * 1024, False, 512 * 1024),
        (2 * 512 * 1024, False, 2 * 512 * 1024),
        (4 * 512 * 1024, False, 4 * 512 * 1024),
        (512 * 1024, True, 0),
    ],
)
def test_process_restart_validates_checkpoint_before_resume(
    tmp_path: Path, checkpoint: int, corrupt_partial: bool, expected_offset: int
) -> None:
    settings = Settings(data_dir=tmp_path / "state")
    output = tmp_path / "exports"
    first_session = ResumeFakeSession(
        block_after_first_chunk=True, block_boundary=checkpoint
    )
    with TestClient(create_app(settings, FakeProvider(first_session))) as client:
        client.put(
            "/api/v1/telegram/credentials",
            json={"api_id": 123456, "api_hash": "0123456789abcdef0123456789abcdef"},
        ).raise_for_status()
        flow = client.post("/api/v1/auth/flows", json={"phone": "+8613800000000"}).json()
        client.post(f"/api/v1/auth/flows/{flow['id']}/code", json={"code": "12345"}).raise_for_status()
        client.get("/api/v1/accounts/tg_42/dialogs").raise_for_status()
        root = client.post("/api/v1/output-roots", json={"path": str(output)}).json()
        created = client.post(
            "/api/v1/export-jobs",
            json={
                "account_id": "tg_42",
                "dialog_id": "-1009001",
                "output_root_id": root["id"],
                "media_types": ["file"],
            },
        ).json()
        job_id = created["id"]
        preflight = wait_for_job(client, job_id, auto_confirm=False)
        assert preflight["status"] == "awaiting_confirmation"
        client.post(f"/api/v1/export-jobs/{job_id}/actions/confirm").raise_for_status()
        deadline = time.monotonic() + 3
        part_files: list[Path] = []
        while time.monotonic() < deadline:
            part_files = list(output.rglob("*.part"))
            if part_files and part_files[0].stat().st_size == checkpoint:
                break
            time.sleep(0.02)
        assert part_files and part_files[0].stat().st_size == checkpoint
        duplicate = client.post(
            "/api/v1/export-jobs",
            json={
                "account_id": "tg_42",
                "dialog_id": "-1009001",
                "output_root_id": root["id"],
                "media_types": ["file"],
            },
        )
        assert duplicate.status_code == 409

    if corrupt_partial:
        with part_files[0].open("r+b") as stream:
            stream.seek(17)
            stream.write(b"CORRUPTED")

    second_session = ResumeFakeSession(block_after_first_chunk=False)
    with TestClient(create_app(settings, FakeProvider(second_session))) as client:
        interrupted = client.get(f"/api/v1/export-jobs/{job_id}").json()
        assert interrupted["status"] == "paused"
        client.post(f"/api/v1/export-jobs/{job_id}/actions/resume").raise_for_status()
        completed = wait_for_job(client, job_id)
        assert completed["status"] == "succeeded", completed
        assert second_session.download_offsets == [expected_offset]
        exported_file = next(Path(completed["output_path"]).glob("media/**/*.bin"))
        assert exported_file.read_bytes() == ResumeFakeSession.payload


def test_failed_scan_resumes_from_persisted_batch_cursor(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "state")
    output = tmp_path / "exports"
    first_session = CursorFakeSession(fail_after=260)
    provider = FakeProvider(first_session)

    with TestClient(create_app(settings, provider)) as client:
        client.put(
            "/api/v1/telegram/credentials",
            json={"api_id": 123456, "api_hash": "0123456789abcdef0123456789abcdef"},
        ).raise_for_status()
        flow = client.post("/api/v1/auth/flows", json={"phone": "+8613800000000"}).json()
        client.post(f"/api/v1/auth/flows/{flow['id']}/code", json={"code": "12345"}).raise_for_status()
        client.get("/api/v1/accounts/tg_42/dialogs").raise_for_status()
        root = client.post("/api/v1/output-roots", json={"path": str(output)}).json()
        created = client.post(
            "/api/v1/export-jobs",
            json={
                "account_id": "tg_42",
                "dialog_id": "-1009001",
                "output_root_id": root["id"],
                "media_types": ["photo", "video"],
            },
        ).json()
        failed = wait_for_job(client, created["id"])
        assert failed["status"] == "failed"
        assert failed["progress"]["messages_saved"] == 250
        assert failed["progress"]["scan_before_message_id"] == 351

        second_session = CursorFakeSession()
        provider.export = second_session
        client.post(f"/api/v1/export-jobs/{created['id']}/actions/resume").raise_for_status()
        completed = wait_for_job(client, created["id"])
        assert completed["status"] == "succeeded", completed
        assert completed["progress"]["messages_saved"] == 600
        assert completed["progress"]["enumeration_completed"] is True
        assert completed["progress"]["capacity_checked"] is True
        assert second_session.before_message_ids == [351]
        assert len(json_load(Path(completed["output_path"]) / "result.json")["messages"]) == 600


def test_container_mode_serves_spa_and_registers_persistent_output_root(tmp_path: Path) -> None:
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<main>Archive Desk container</main>", encoding="utf-8")
    (static_dir / "robots.txt").write_text("User-agent: *", encoding="utf-8")
    output = tmp_path / "exports"
    settings = Settings(
        data_dir=tmp_path / "state",
        host="0.0.0.0",
        container_mode=True,
        static_dir=static_dir,
        default_output_root=output,
    )

    with TestClient(create_app(settings, FakeProvider())) as client:
        bootstrap = client.get("/api/v1/bootstrap").json()
        assert bootstrap["capabilities"] == {
            "container_mode": True,
            "open_local_folder": False,
        }
        assert bootstrap["output_roots"] == [
            {"id": "container-exports", "path": str(output.resolve()), "created_at": bootstrap["output_roots"][0]["created_at"]}
        ]
        assert client.get("/").text == "<main>Archive Desk container</main>"
        assert client.get("/jobs/example").text == "<main>Archive Desk container</main>"
        assert client.get("/robots.txt").text == "User-agent: *"
        assert client.get("/api/v1/health").json()["status"] == "ok"
        assert client.get("/api/v1/missing").status_code == 404
        assert client.post(
            "/api/v1/output-roots",
            json={"path": str(tmp_path / "outside-exports")},
        ).status_code == 400
        nested = client.post(
            "/api/v1/output-roots",
            json={"path": str(output / "nested")},
        )
        assert nested.status_code == 201
        assert nested.json()["path"] == str((output / "nested").resolve())
