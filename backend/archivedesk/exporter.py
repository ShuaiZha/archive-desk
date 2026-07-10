from __future__ import annotations

import asyncio
import errno
import hashlib
import json
import logging
import os
import re
import shutil
import unicodedata
import uuid
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

from .database import Database, utc_now
from .integrity import validate_staging_export
from .telegram import TelegramError, TelegramFloodWait, TelegramProvider


TERMINAL_STATUSES = {"succeeded", "partial", "failed", "cancelled"}
_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}
_INVALID_FILENAME = re.compile(r"[\x00-\x1f<>:\"/\\|?*]")
logger = logging.getLogger(__name__)


class JobCancelled(Exception):
    pass


def safe_filename(value: str, *, fallback: str = "file", max_length: int = 140) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    normalized = _INVALID_FILENAME.sub("_", normalized).strip(" .")
    if not normalized:
        normalized = fallback
    stem, extension = os.path.splitext(normalized)
    if stem.upper() in _WINDOWS_RESERVED:
        stem = f"_{stem}"
    extension = extension[:16]
    available = max(1, max_length - len(extension))
    stem = stem[:available].rstrip(" .") or fallback
    return f"{stem}{extension}"


def _json_default(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def atomic_json_with_array(
    path: Path,
    fields: dict[str, Any],
    array_name: str,
    items: Iterable[dict[str, Any]],
) -> None:
    """Write a large JSON object without retaining its array in memory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write("{\n")
        for key, value in fields.items():
            stream.write("  ")
            json.dump(key, stream, ensure_ascii=False)
            stream.write(": ")
            json.dump(value, stream, ensure_ascii=False, default=_json_default)
            stream.write(",\n")
        stream.write("  ")
        json.dump(array_name, stream, ensure_ascii=False)
        stream.write(": [\n")
        first = True
        for item in items:
            if not first:
                stream.write(",\n")
            stream.write("    ")
            json.dump(item, stream, ensure_ascii=False, default=_json_default)
            first = False
        stream.write("\n  ]\n}\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def reconcile_partial_file(path: Path, committed_offset: int, chunk_size: int) -> int:
    """Roll a partial file back to the last boundary confirmed by both disk and DB."""
    size = path.stat().st_size if path.exists() else 0
    file_aligned = size - (size % chunk_size)
    checkpoint_aligned = max(0, committed_offset) - (max(0, committed_offset) % chunk_size)
    safe_offset = min(file_aligned, checkpoint_aligned)
    if path.exists() and size != safe_offset:
        with path.open("r+b") as stream:
            stream.truncate(safe_offset)
            stream.flush()
            os.fsync(stream.fileno())
    return safe_offset


def ensure_within(root: Path, candidate: Path) -> Path:
    resolved_root = root.resolve()
    resolved_candidate = candidate.resolve(strict=False)
    if resolved_candidate != resolved_root and resolved_root not in resolved_candidate.parents:
        raise ValueError("Export path escaped the authorized output root")
    return resolved_candidate


async def commit_directory(source: Path, target: Path) -> None:
    """Atomically publish an export directory without duplicating large media trees."""
    for attempt in range(6):
        if target.exists():
            raise FileExistsError("The final export directory already exists")
        try:
            source.rename(target)
            return
        except PermissionError:
            if attempt == 5:
                raise
            await asyncio.sleep(0.15 * (attempt + 1))


def _utc_date(
    value: str | None,
    *,
    time_zone: str = "UTC",
    exclusive_end: bool = False,
) -> datetime | None:
    if not value:
        return None
    parsed = date.fromisoformat(value)
    if exclusive_end:
        parsed += timedelta(days=1)
    local_midnight = datetime.combine(parsed, time.min, tzinfo=ZoneInfo(time_zone))
    return local_midnight.astimezone(UTC)


def _safe_error(exc: BaseException) -> str:
    if isinstance(exc, TelegramError):
        return str(exc)
    if isinstance(exc, OSError):
        return f"Filesystem error: {exc.strerror or str(exc) or type(exc).__name__}"
    message = " ".join(str(exc).split())[:500]
    return f"{type(exc).__name__}: {message or 'export failed'}"


class ExportJobManager:
    CHUNK_SIZE = 512 * 1024
    SCAN_BATCH_SIZE = 250
    ASSET_PAGE_SIZE = 500
    PROGRESS_WRITE_INTERVAL = 1.0
    MIN_DISK_RESERVE = 1024 * 1024 * 1024

    def __init__(
        self,
        database: Database,
        provider: TelegramProvider,
        secret_values: Callable[[], Iterable[bytes]] | None = None,
    ):
        self.database = database
        self.provider = provider
        self.secret_values = secret_values or (lambda: ())
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._task_lock = asyncio.Lock()

    async def recover(self) -> None:
        self.database.recover_jobs()

    async def close(self) -> None:
        tasks = [task for task in self._tasks.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def start(self, job_id: str) -> dict[str, Any]:
        job = self.database.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        if job["status"] in TERMINAL_STATUSES - {"failed"}:
            return job
        if job["status"] == "awaiting_confirmation" and not job["progress"].get(
            "download_confirmed"
        ):
            return job
        self.database.update_job(job_id, status="running", error=None)
        self._emit(job_id, "job.running")
        async with self._task_lock:
            current = self._tasks.get(job_id)
            if current is None or current.done():
                task = asyncio.create_task(self._run(job_id), name=f"archive-export-{job_id}")
                self._tasks[job_id] = task
        return self.database.get_job(job_id)  # type: ignore[return-value]

    async def pause(self, job_id: str) -> dict[str, Any]:
        job = self._require_job(job_id)
        if job["status"] in TERMINAL_STATUSES:
            return job
        updated = self.database.update_job(job_id, status="paused")
        self._emit(job_id, "job.paused")
        return updated  # type: ignore[return-value]

    async def resume(self, job_id: str) -> dict[str, Any]:
        job = self._require_job(job_id)
        if job["status"] in TERMINAL_STATUSES - {"failed"}:
            return job
        return await self.start(job_id)

    async def confirm(self, job_id: str) -> dict[str, Any]:
        job = self._require_job(job_id)
        if job["status"] != "awaiting_confirmation":
            return job
        progress = dict(job.get("progress") or {})
        if progress.get("capacity_sufficient") is False:
            raise ValueError("Disk capacity must be rechecked before download can start")
        progress["download_confirmed"] = True
        self.database.update_job(
            job_id,
            status="running",
            stage="downloading",
            progress=progress,
            error=None,
        )
        self._emit(job_id, "job.confirmed")
        return await self.start(job_id)

    async def recheck_capacity(self, job_id: str) -> dict[str, Any]:
        job = self._require_job(job_id)
        progress = dict(job.get("progress") or {})
        if not progress.get("enumeration_completed"):
            return await self.start(job_id)
        progress["download_confirmed"] = False
        self.database.update_job(
            job_id,
            status="running",
            stage="capacity_check",
            progress=progress,
            error=None,
        )
        self._emit(job_id, "job.capacity_recheck")
        return await self.start(job_id)

    async def cancel(self, job_id: str) -> dict[str, Any]:
        job = self._require_job(job_id)
        if job["status"] in TERMINAL_STATUSES:
            return job
        task = self._tasks.get(job_id)
        if task is None or task.done():
            updated = self.database.update_job(job_id, status="cancelled", stage="cancelled")
            self._emit(job_id, "job.cancelled")
            return updated  # type: ignore[return-value]
        updated = self.database.update_job(job_id, status="cancelling")
        self._emit(job_id, "job.cancelling")
        return updated  # type: ignore[return-value]

    def _require_job(self, job_id: str) -> dict[str, Any]:
        job = self.database.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        return job

    def _emit(self, job_id: str, event_type: str, data: dict[str, Any] | None = None) -> None:
        job = self.database.get_job(job_id)
        self.database.add_event(
            job_id,
            event_type,
            {
                "job_id": job_id,
                "status": job.get("status") if job else None,
                "stage": job.get("stage") if job else None,
                "progress": job.get("progress") if job else None,
                **(data or {}),
            },
        )

    async def _control(self, job_id: str) -> None:
        while True:
            job = self._require_job(job_id)
            status = job["status"]
            if status in {"cancelling", "cancelled"}:
                raise JobCancelled()
            if status != "paused":
                return
            await asyncio.sleep(0.25)

    def _refresh_progress(self, job_id: str, **extra: Any) -> dict[str, Any]:
        job = self.database.get_job(job_id)
        current = dict(job.get("progress") or {}) if job else {}
        current.update(self.database.counts(job_id))
        current.update(extra)
        self.database.update_job(job_id, progress=current)
        return current

    def _update_transfer_progress(
        self,
        job_id: str,
        *,
        bytes_delta: int = 0,
        files_done_delta: int = 0,
    ) -> dict[str, Any]:
        job = self._require_job(job_id)
        progress = dict(job.get("progress") or {})
        progress["bytes_done"] = max(0, int(progress.get("bytes_done") or 0) + bytes_delta)
        progress["files_done"] = max(
            0, int(progress.get("files_done") or 0) + files_done_delta
        )
        progress["bytes_remaining"] = max(
            0, int(progress.get("bytes_total") or 0) - progress["bytes_done"]
        )
        self.database.update_job(job_id, progress=progress)
        return progress

    async def _download(
        self,
        job_id: str,
        session: Any,
        dialog_id: str,
        asset: dict[str, Any],
        partial_dir: Path,
    ) -> int:
        relative_path = asset.get("relative_path") or f"media/{asset['safe_name']}"
        final_file = ensure_within(partial_dir, partial_dir / Path(relative_path))
        final_file.parent.mkdir(parents=True, exist_ok=True)
        part_file = final_file.with_suffix(final_file.suffix + ".part")
        expected = asset.get("expected_size")
        persisted_bytes = int(asset.get("bytes_done") or 0)
        checkpoint_sha256 = asset.get("checkpoint_sha256")
        if final_file.exists():
            size = final_file.stat().st_size
            if expected is None or size == expected:
                digest = sha256_file(final_file)
                self.database.update_asset(
                    asset["id"],
                    status="completed",
                    bytes_done=size,
                    expected_size=size,
                    relative_path=relative_path,
                    sha256=digest,
                    checkpoint_sha256=None,
                    error=None,
                )
                self._update_transfer_progress(
                    job_id,
                    bytes_delta=size - persisted_bytes,
                    files_done_delta=1,
                )
                return size
        existing_size = part_file.stat().st_size if part_file.exists() else 0
        if (
            expected is not None
            and existing_size == expected
            and persisted_bytes == expected
            and checkpoint_sha256
            and sha256_file(part_file) == checkpoint_sha256
        ):
            os.replace(part_file, final_file)
            digest = sha256_file(final_file)
            self.database.update_asset(
                asset["id"],
                status="completed",
                bytes_done=expected,
                expected_size=expected,
                relative_path=relative_path,
                sha256=digest,
                checkpoint_sha256=None,
                error=None,
            )
            self._update_transfer_progress(
                job_id,
                bytes_delta=expected - persisted_bytes,
                files_done_delta=1,
            )
            return expected
        def durable_offset() -> int:
            return reconcile_partial_file(part_file, persisted_bytes, self.CHUNK_SIZE)

        offset = durable_offset()
        if offset > 0 and (
            not checkpoint_sha256 or sha256_file(part_file) != checkpoint_sha256
        ):
            with part_file.open("r+b") as stream:
                stream.truncate(0)
                stream.flush()
                os.fsync(stream.fileno())
            offset = 0
            checkpoint_sha256 = None
        self.database.update_asset(
            asset["id"],
            status="downloading",
            bytes_done=offset,
            checkpoint_sha256=checkpoint_sha256 if offset else None,
            error=None,
        )
        self._update_transfer_progress(job_id, bytes_delta=offset - persisted_bytes)
        persisted_bytes = offset

        def digest_for_partial() -> Any:
            digest = hashlib.sha256()
            if part_file.exists():
                with part_file.open("rb") as stream:
                    while chunk := stream.read(1024 * 1024):
                        digest.update(chunk)
            return digest

        checkpoint_digest = digest_for_partial()

        last_progress_write = 0.0

        async def progress(bytes_done: int, chunk: bytes | None = None) -> None:
            nonlocal last_progress_write, persisted_bytes, checkpoint_digest, checkpoint_sha256
            if chunk is not None:
                checkpoint_digest.update(chunk)
            now = asyncio.get_running_loop().time()
            if now - last_progress_write < self.PROGRESS_WRITE_INTERVAL:
                return
            if chunk is None:
                checkpoint_digest = digest_for_partial()
            checkpoint_sha256 = checkpoint_digest.hexdigest()
            self.database.update_asset(
                asset["id"],
                bytes_done=bytes_done,
                checkpoint_sha256=checkpoint_sha256,
            )
            self._update_transfer_progress(job_id, bytes_delta=bytes_done - persisted_bytes)
            persisted_bytes = bytes_done
            last_progress_write = now

        retry_count = 0
        while True:
            await self._control(job_id)
            try:
                actual_size = await session.download_media(
                    dialog_id,
                    int(asset["message_id"]),
                    part_file,
                    offset,
                    progress,
                    lambda: self._control(job_id),
                )
                break
            except TelegramFloodWait as wait:
                wait_until = (datetime.now(UTC) + timedelta(seconds=wait.seconds)).isoformat()
                wait_progress = dict(self._require_job(job_id).get("progress") or {})
                wait_progress["wait_until"] = wait_until
                self.database.update_job(
                    job_id,
                    status="waiting",
                    stage="flood_wait",
                    progress=wait_progress,
                )
                self._emit(job_id, "job.waiting", {"wait_seconds": wait.seconds})
                remaining = wait.seconds
                while remaining > 0:
                    await self._control(job_id)
                    await asyncio.sleep(min(1, remaining))
                    remaining -= 1
                wait_progress.pop("wait_until", None)
                self.database.update_job(
                    job_id,
                    status="running",
                    stage="downloading",
                    progress=wait_progress,
                )
                offset = durable_offset()
                checkpoint_digest = digest_for_partial()
            except (TelegramError, OSError) as exc:
                if isinstance(exc, OSError) and exc.errno in {
                    errno.ENOSPC,
                    errno.EACCES,
                    errno.EROFS,
                }:
                    self.database.update_asset(asset["id"], status="failed", error=_safe_error(exc))
                    raise
                retry_count += 1
                if "no longer available" in str(exc).lower() or retry_count >= 5:
                    self.database.update_asset(asset["id"], status="failed", error=_safe_error(exc))
                    raise
                wait_seconds = min(30, 2**retry_count)
                self.database.update_asset(asset["id"], error=_safe_error(exc))
                wait_until = (datetime.now(UTC) + timedelta(seconds=wait_seconds)).isoformat()
                wait_progress = dict(self._require_job(job_id).get("progress") or {})
                wait_progress["wait_until"] = wait_until
                self.database.update_job(
                    job_id,
                    status="waiting",
                    stage="retry_wait",
                    progress=wait_progress,
                )
                self._emit(
                    job_id,
                    "job.retrying",
                    {"attempt": retry_count, "wait_seconds": wait_seconds},
                )
                remaining = wait_seconds
                while remaining > 0:
                    await self._control(job_id)
                    await asyncio.sleep(min(1, remaining))
                    remaining -= 1
                wait_progress.pop("wait_until", None)
                self.database.update_job(
                    job_id,
                    status="running",
                    stage="downloading",
                    progress=wait_progress,
                )
                offset = durable_offset()
                checkpoint_digest = digest_for_partial()
            except Exception as exc:
                self.database.update_asset(asset["id"], status="failed", error=_safe_error(exc))
                raise
        if expected is not None and actual_size != expected:
            raise IOError(f"Downloaded size mismatch for asset {asset['id']}")
        os.replace(part_file, final_file)
        digest = sha256_file(final_file)
        self.database.update_asset(
            asset["id"],
            status="completed",
            bytes_done=actual_size,
            expected_size=actual_size,
            relative_path=relative_path,
            sha256=digest,
            checkpoint_sha256=None,
            error=None,
        )
        self._update_transfer_progress(
            job_id,
            bytes_delta=actual_size - persisted_bytes,
            files_done_delta=1,
        )
        return actual_size

    async def _run(self, job_id: str) -> None:
        try:
            await self._execute(job_id)
        except JobCancelled:
            self.database.update_job(job_id, status="cancelled", stage="cancelled")
            self._emit(job_id, "job.cancelled")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Export job %s failed", job_id)
            self.database.update_job(job_id, status="failed", stage="failed", error=_safe_error(exc))
            self._emit(job_id, "job.failed")

    async def _execute(self, job_id: str) -> None:
        job = self._require_job(job_id)
        account = self.database.get_account(job["account_id"])
        dialog = self.database.get_dialog(job["account_id"], job["dialog_id"])
        root = self.database.get_output_root(job["output_root_id"])
        if account is None or dialog is None or root is None:
            raise RuntimeError("The account, dialog, or output root no longer exists")
        root_dir = Path(root["path"]).resolve()
        final_dir = ensure_within(root_dir, Path(job["output_path"]))
        partial_dir = ensure_within(root_dir, root_dir / f".archivedesk-{job_id}.partial")
        media_dir = partial_dir / "media"
        if final_dir.exists() and (final_dir / "manifest.json").exists():
            self.database.update_job(job_id, status="succeeded", stage="completed")
            self._emit(job_id, "job.succeeded")
            return
        partial_dir.mkdir(parents=True, exist_ok=True)
        media_dir.mkdir(parents=True, exist_ok=True)
        ensure_within(root_dir, partial_dir)
        ensure_within(root_dir, media_dir)

        config = job["config"]
        time_zone = str(config.get("time_zone") or "UTC")
        date_from = _utc_date(config.get("date_from"), time_zone=time_zone)
        date_to = _utc_date(
            config.get("date_to"), time_zone=time_zone, exclusive_end=True
        )
        selected_media = set(config.get("media_types") or [])
        max_mb = config.get("max_file_size_mb")
        max_bytes = None if max_mb is None else int(max_mb) * 1024 * 1024
        initial_stage = (
            "capacity_check" if job.get("progress", {}).get("enumeration_completed") else "enumerating"
        )
        self.database.update_job(job_id, status="running", stage=initial_stage, error=None)
        self._emit(job_id, "job.progress")

        progress_state = dict(job.get("progress") or {})
        async with self.provider.export_session(account) as session:
            if not progress_state.get("enumeration_completed"):
                before_message_id = progress_state.get("scan_before_message_id")
                before_message_id = int(before_message_id) if before_message_id is not None else None
                upper_message_id = progress_state.get("upper_message_id")
                upper_message_id = int(upper_message_id) if upper_message_id is not None else None
                latest_message_id = getattr(session, "latest_message_id", None)
                if upper_message_id is None and callable(latest_message_id):
                    upper_message_id = await latest_message_id(job["dialog_id"])
                    progress_state = self._refresh_progress(
                        job_id,
                        upper_message_id=upper_message_id,
                        enumeration_completed=False,
                    )
                iteration_before_id = before_message_id
                if iteration_before_id is None and upper_message_id is not None:
                    iteration_before_id = upper_message_id + 1
                message_batch: list[dict[str, Any]] = []
                asset_batch: list[dict[str, Any]] = []
                last_scanned_message_id: int | None = before_message_id

                def flush_scan_batch() -> None:
                    if not message_batch:
                        return
                    self.database.upsert_messages_batch(job_id, job["dialog_id"], message_batch)
                    self.database.upsert_assets_batch(asset_batch)
                    progress = self._refresh_progress(
                        job_id,
                        scan_before_message_id=last_scanned_message_id,
                        upper_message_id=upper_message_id,
                        enumeration_completed=False,
                    )
                    self._emit(job_id, "job.progress", {"progress": progress})
                    message_batch.clear()
                    asset_batch.clear()

                async for message in session.iter_messages(
                    job["dialog_id"], date_from, date_to, iteration_before_id
                ):
                    await self._control(job_id)
                    if upper_message_id is None:
                        upper_message_id = message.id
                        progress_state = self._refresh_progress(
                            job_id,
                            upper_message_id=upper_message_id,
                            enumeration_completed=False,
                        )
                    message_batch.append(message.database_dict())
                    last_scanned_message_id = message.id
                    media = message.media
                    if media is not None and (
                        media.kind in selected_media or media.policy_reason is not None
                    ):
                        safe_remote = safe_filename(
                            media.original_name, fallback=f"{media.kind}_{message.id}"
                        )
                        safe_name = safe_filename(
                            f"{message.id}_{media.kind}_{safe_remote}", max_length=120
                        )
                        try:
                            message_date = datetime.fromisoformat(message.date)
                            year, month = message_date.year, message_date.month
                        except ValueError:
                            year, month = 0, 0
                        planned_path = (
                            Path("media")
                            / media.kind
                            / f"{year:04d}"
                            / f"{month:02d}"
                            / f"{message.id % 256:02x}"
                            / safe_name
                        ).as_posix()
                        asset_id = uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            f"archivedesk:{job_id}:{job['dialog_id']}:{message.id}:{media.kind}",
                        ).hex
                        is_policy_skipped = media.policy_reason is not None
                        is_too_large = (
                            not is_policy_skipped
                            and media.kind in selected_media
                            and max_bytes is not None
                            and media.expected_size is not None
                            and media.expected_size > max_bytes
                        )
                        is_skipped = is_policy_skipped or is_too_large
                        asset_batch.append(
                            {
                                "id": asset_id,
                                "job_id": job_id,
                                "dialog_id": job["dialog_id"],
                                "message_id": message.id,
                                "kind": media.kind,
                                "original_name": media.original_name,
                                "safe_name": safe_name,
                                "mime_type": media.mime_type,
                                "expected_size": media.expected_size,
                                "status": "skipped" if is_skipped else "pending",
                                "relative_path": None if is_skipped else planned_path,
                                "skip_reason": (
                                    media.policy_reason
                                    if is_policy_skipped
                                    else "size_limit" if is_too_large else None
                                ),
                            }
                        )
                    if len(message_batch) >= self.SCAN_BATCH_SIZE:
                        flush_scan_batch()
                flush_scan_batch()
                progress_state = self._refresh_progress(
                    job_id,
                    scan_before_message_id=None,
                    upper_message_id=upper_message_id,
                    enumeration_completed=True,
                )

            await self._control(job_id)
            self.database.update_job(job_id, stage="capacity_check")
            capacity = self.database.counts(job_id)
            disk_free = shutil.disk_usage(root_dir).free
            bytes_remaining = int(capacity["bytes_remaining"])
            has_remaining_files = bytes_remaining > 0 or capacity["unknown_size_files"] > 0
            disk_reserve = (
                max(self.MIN_DISK_RESERVE, bytes_remaining // 10)
                if has_remaining_files
                else 0
            )
            disk_required = bytes_remaining + disk_reserve
            disk_shortfall = max(0, disk_required - disk_free)
            capacity_sufficient = disk_shortfall == 0
            progress_state = self._refresh_progress(
                job_id,
                capacity_checked=True,
                disk_free_bytes=disk_free,
                disk_required_bytes=disk_required,
                disk_reserve_bytes=disk_reserve,
                disk_shortfall_bytes=disk_shortfall,
                capacity_sufficient=capacity_sufficient,
            )
            self._emit(job_id, "job.capacity", {"progress": progress_state})
            if not capacity_sufficient:
                progress_state["download_confirmed"] = False
                self.database.update_job(
                    job_id,
                    status="awaiting_confirmation",
                    stage="preflight_ready",
                    progress=progress_state,
                    error=(
                        f"Insufficient disk space: {disk_free} bytes free, "
                        f"{disk_required} bytes required including reserve"
                    ),
                )
                self._emit(job_id, "job.preflight_ready", {"progress": progress_state})
                return

            if not progress_state.get("download_confirmed"):
                self.database.update_job(
                    job_id,
                    status="awaiting_confirmation",
                    stage="preflight_ready",
                    progress=progress_state,
                    error=None,
                )
                self._emit(job_id, "job.preflight_ready", {"progress": progress_state})
                return

            self.database.update_job(job_id, stage="downloading")
            after_message_id = -1
            after_asset_id = ""
            while True:
                page = self.database.list_assets_page(
                    job_id, after_message_id, after_asset_id, self.ASSET_PAGE_SIZE
                )
                if not page:
                    break
                for asset in page:
                    await self._control(job_id)
                    if asset["status"] not in {"completed", "skipped"}:
                        await self._download(
                            job_id,
                            session,
                            job["dialog_id"],
                            asset,
                            partial_dir,
                        )
                        current_job = self._require_job(job_id)
                        progress = dict(current_job.get("progress") or {})
                        self._emit(job_id, "job.progress", {"progress": progress})
                    after_message_id = int(asset["message_id"])
                    after_asset_id = str(asset["id"])

        await self._control(job_id)
        self.database.update_job(job_id, stage="rendering")
        self._emit(job_id, "job.progress")
        source = {
            "account_id": account["id"],
            "dialog_id": dialog["id"],
            "dialog_title": dialog["title"],
            "category": dialog["category"],
            "date_from": config.get("date_from"),
            "date_to": config.get("date_to"),
            "timezone": time_zone,
            "upper_message_id": progress_state.get("upper_message_id"),
        }
        atomic_json_with_array(
            partial_dir / "result.json",
            {
                "schema_version": 1,
                "exported_at": utc_now(),
                "source": source,
            },
            "messages",
            self.database.iter_messages(job_id),
        )

        self.database.update_job(job_id, stage="verifying")
        counts = self.database.counts(job_id)
        for asset in self.database.iter_assets(job_id):
            if asset["status"] == "completed":
                relative = asset["relative_path"]
                if not relative:
                    raise RuntimeError("A completed asset has no path")
                path = ensure_within(partial_dir, partial_dir / Path(relative))
                if (
                    not path.is_file()
                    or path.stat().st_size != asset["bytes_done"]
                    or not asset["sha256"]
                ):
                    raise RuntimeError("Asset verification failed")
            elif asset["status"] != "skipped":
                raise RuntimeError(f"Asset did not reach a terminal state: {asset['id']}")

        def manifest_files() -> Iterable[dict[str, Any]]:
            for asset in self.database.iter_assets(job_id):
                yield {
                    "message_id": asset["message_id"],
                    "kind": asset["kind"],
                    "original_name": asset["original_name"],
                    "path": asset["relative_path"],
                    "size": asset["bytes_done"],
                    "sha256": asset["sha256"],
                    "status": asset["status"],
                    "skip_reason": asset["skip_reason"],
                }

        atomic_json_with_array(
            partial_dir / "manifest.json",
            {
                "schema_version": 1,
                "job_id": job_id,
                "created_at": job["created_at"],
                "completed_at": utc_now(),
                "completeness": "policy_filtered" if counts["files_skipped"] else "full",
                "source": source,
                "counts": {
                    "messages": counts["messages_saved"],
                    "files_discovered": counts["files_total"],
                    "files_downloaded": counts["files_done"],
                    "files_skipped": counts["files_skipped"],
                },
                "storage": {
                    "layout": "media/{kind}/{year}/{month}/{shard}/{filename}",
                    "disk_free_at_preflight": progress_state.get("disk_free_bytes"),
                    "disk_required_at_preflight": progress_state.get("disk_required_bytes"),
                    "unknown_size_files": counts["unknown_size_files"],
                },
                "artifacts": {
                    "result.json": {
                        "size": (partial_dir / "result.json").stat().st_size,
                        "sha256": sha256_file(partial_dir / "result.json"),
                    },
                },
            },
            "files",
            manifest_files(),
        )
        validate_staging_export(
            partial_dir,
            self.database.iter_assets(job_id),
            canaries=self.secret_values(),
        )
        self.database.update_job(job_id, stage="committing")
        await commit_directory(partial_dir, final_dir)
        progress = self._refresh_progress(job_id)
        self.database.update_job(job_id, status="succeeded", stage="completed", progress=progress, error=None)
        self._emit(job_id, "job.succeeded", {"manifest": "manifest.json"})
