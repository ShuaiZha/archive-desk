from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import secrets as random_secrets
import shutil
import sqlite3
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, StreamingResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.staticfiles import StaticFiles

from . import __version__
from .config import Settings
from .database import Database
from .exporter import (
    ExportJobManager,
    ensure_within,
    safe_filename,
)
from .errors import problem_response, telegram_problem
from .schemas import (
    CodeInput,
    CredentialsInput,
    ExportJobInput,
    OutputRootInput,
    PasswordInput,
    PhoneInput,
)
from .security import SecretStore, load_master_key, mask_phone
from .telegram import TelegramError, TelegramProvider, TelethonProvider


logger = logging.getLogger(__name__)


def _public_account(account: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": account["id"],
        "telegram_user_id": account["telegram_user_id"],
        "display_name": account["display_name"],
        "username": account.get("username"),
        "phone_masked": account.get("phone_masked"),
        "created_at": account.get("created_at"),
        "updated_at": account.get("updated_at"),
    }


class LocalRequestGuard(BaseHTTPMiddleware):
    """Reject browser requests originating outside this local application."""

    async def dispatch(self, request: Request, call_next):
        request.state.request_id = uuid.uuid4().hex
        host = request.headers.get("host", "").lower()
        hostname = host.rsplit(":", 1)[0].strip("[]") if host else ""
        if hostname not in {"127.0.0.1", "localhost", "::1", "testserver"}:
            return problem_response(
                request,
                status_code=400,
                code="INVALID_HOST",
                category="CONFIG",
                message="Invalid local host header",
            )
        origin = request.headers.get("origin")
        if origin:
            parsed_origin = urlsplit(origin)
            if (
                parsed_origin.scheme != "http"
                or parsed_origin.hostname not in {"127.0.0.1", "localhost", "::1"}
                or parsed_origin.username is not None
                or parsed_origin.password is not None
            ):
                return problem_response(
                    request,
                    status_code=403,
                    code="CROSS_ORIGIN_REJECTED",
                    category="CONFIG",
                    message="Cross-origin request rejected",
                )
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response


def create_app(
    settings: Settings | None = None,
    provider: TelegramProvider | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.prepare()
    database = Database(settings.database_path)
    encryption_key = load_master_key(settings.master_key_file)
    secret_store = SecretStore(settings.secret_path, encryption_key=encryption_key)
    if settings.container_mode and settings.secret_path.exists():
        secret_store.load()
    if settings.default_output_root is not None:
        default_output_root = settings.default_output_root.resolve()
        database.add_output_root("container-exports", str(default_output_root))
    telegram_provider = provider or TelethonProvider(settings, secret_store)
    def current_secret_values():
        credentials = secret_store.credentials()
        return [credentials[1].encode("utf-8")] if credentials else []

    jobs = ExportJobManager(database, telegram_provider, current_secret_values)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        await jobs.recover()
        yield
        await jobs.close()
        await telegram_provider.close()

    app = FastAPI(
        title="Archive Desk local API",
        version=__version__,
        docs_url="/docs" if os.environ.get("ARCHIVEDESK_DEV") == "1" else None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(LocalRequestGuard)
    app.state.settings = settings
    app.state.database = database
    app.state.secret_store = secret_store
    app.state.telegram_provider = telegram_provider
    app.state.jobs = jobs
    app.state.instance_token = random_secrets.token_urlsafe(32)

    def credentials_response() -> dict[str, Any]:
        credentials = secret_store.credentials()
        return {
            "configured": credentials is not None,
            "api_id": credentials[0] if credentials else None,
            "api_hash_masked": "••••••••" if credentials else None,
        }

    def job_or_404(job_id: str) -> dict[str, Any]:
        job = database.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Export job not found")
        return job

    def public_job(job: dict[str, Any]) -> dict[str, Any]:
        dialog = database.get_dialog(job["account_id"], job["dialog_id"])
        account = database.get_account(job["account_id"])
        return {
            **job,
            "revision": database.latest_event_id(job["id"]),
            "dialog_title": (dialog or {}).get("title"),
            "account_display_name": (account or {}).get("display_name"),
        }

    def completed_output(job: dict[str, Any]) -> Path:
        if job["status"] not in {"succeeded", "partial"} or not job.get("output_path"):
            raise HTTPException(status_code=409, detail="Export output is not available")
        root = database.get_output_root(job["output_root_id"])
        if root is None:
            raise HTTPException(status_code=409, detail="Output root no longer exists")
        output = ensure_within(Path(root["path"]), Path(job["output_path"]))
        if not output.is_dir():
            raise HTTPException(status_code=404, detail="Export output directory was not found")
        return output

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        fields = [".".join(str(part) for part in error.get("loc", ())[1:]) for error in exc.errors()]
        return problem_response(
            request,
            status_code=422,
            code="INVALID_REQUEST",
            category="CONFIG",
            message="请求参数无效，请检查后重试。",
            user_action="EDIT_REQUEST",
            details={"fields": [field for field in fields if field]},
        )

    @app.exception_handler(HTTPException)
    async def http_error_handler(request: Request, exc: HTTPException):
        detail = exc.detail
        message = detail if isinstance(detail, str) else "请求无法完成。"
        default_codes = {
            400: ("INVALID_REQUEST", "CONFIG"),
            401: ("UNAUTHORIZED", "AUTH"),
            403: ("FORBIDDEN", "AUTH"),
            404: ("NOT_FOUND", "CONFIG"),
            409: ("CONFLICT", "JOB"),
            507: ("DISK_FULL", "STORAGE"),
        }
        code, category = default_codes.get(exc.status_code, ("REQUEST_FAILED", "INTERNAL"))
        if isinstance(detail, dict):
            code = str(detail.get("code") or code)
            category = str(detail.get("category") or category)
            message = str(detail.get("message") or message)
        return problem_response(
            request,
            status_code=exc.status_code,
            code=code,
            category=category,
            message=message,
            retryable=exc.status_code in {429, 503},
        )

    @app.exception_handler(TelegramError)
    async def telegram_error_handler(request: Request, exc: TelegramError):
        status, code, category, retryable, action = telegram_problem(str(exc))
        return problem_response(
            request,
            status_code=status,
            code=code,
            category=category,
            message=str(exc),
            retryable=retryable,
            user_action=action,
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, exc: Exception):
        logger.exception("Unhandled request error %s", request.state.request_id)
        return problem_response(
            request,
            status_code=500,
            code="UNEXPECTED_ERROR",
            category="INTERNAL",
            message="本地服务发生未预期错误，请使用请求编号排查。",
            retryable=False,
        )

    @app.get("/api/v1/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "version": __version__}

    @app.get("/api/v1/bootstrap")
    async def bootstrap() -> dict[str, Any]:
        return {
            "api_version": "v1",
            "version": __version__,
            "credentials_configured": secret_store.credentials() is not None,
            "accounts": [_public_account(account) for account in database.list_accounts()],
            "output_roots": database.list_output_roots(),
            "capabilities": {
                "container_mode": settings.container_mode,
                "open_local_folder": os.name == "nt" and not settings.container_mode,
            },
        }

    @app.get("/api/v1/telegram/credentials")
    async def get_credentials() -> dict[str, Any]:
        return credentials_response()

    @app.put("/api/v1/telegram/credentials")
    async def put_credentials(payload: CredentialsInput) -> dict[str, Any]:
        secret_store.save_credentials(payload.api_id, payload.api_hash)
        return credentials_response()

    @app.post("/api/v1/auth/flows", status_code=201)
    async def begin_auth(payload: PhoneInput) -> dict[str, Any]:
        flow_id = uuid.uuid4().hex
        phone = payload.phone.strip()
        database.save_auth_flow(flow_id, "starting", mask_phone(phone))
        try:
            result = await telegram_provider.begin_auth(flow_id, phone)
        except Exception:
            database.save_auth_flow(flow_id, "failed", mask_phone(phone), error="Unable to send code")
            raise
        database.save_auth_flow(flow_id, result["status"], result.get("phone_masked"))
        return result

    @app.get("/api/v1/auth/flows/{flow_id}")
    async def get_auth_flow(flow_id: str) -> dict[str, Any]:
        flow = database.get_auth_flow(flow_id)
        if flow is None:
            raise HTTPException(status_code=404, detail="Login flow not found")
        return {
            "id": flow["id"],
            "status": flow["status"],
            "phone_masked": flow.get("phone_masked"),
            "account_id": flow.get("account_id"),
            "created_at": flow.get("created_at"),
            "updated_at": flow.get("updated_at"),
        }

    @app.post("/api/v1/auth/flows/{flow_id}/resend")
    async def resend_auth_code(flow_id: str) -> dict[str, Any]:
        flow = database.get_auth_flow(flow_id)
        if flow is None:
            raise HTTPException(status_code=404, detail="Login flow not found")
        resend = getattr(telegram_provider, "resend_auth", None)
        if resend is None:
            raise HTTPException(status_code=501, detail="Code resend is not supported")
        result = await resend(flow_id)
        database.save_auth_flow(flow_id, result["status"], result.get("phone_masked"))
        return result

    @app.delete("/api/v1/auth/flows/{flow_id}", status_code=204)
    async def cancel_auth_flow(flow_id: str) -> Response:
        if database.get_auth_flow(flow_id) is None:
            raise HTTPException(status_code=404, detail="Login flow not found")
        cancel = getattr(telegram_provider, "cancel_auth", None)
        if cancel is not None:
            await cancel(flow_id)
        database.delete_auth_flow(flow_id)
        return Response(status_code=204)

    async def finish_auth(flow_id: str, result: dict[str, Any]) -> dict[str, Any]:
        account = result.get("account")
        if account is not None:
            stored = database.upsert_account(account)
            result = {**result, "account": _public_account(stored)}
            database.save_auth_flow(
                flow_id,
                result["status"],
                result.get("phone_masked"),
                account_id=stored["id"],
            )
        else:
            flow = database.get_auth_flow(flow_id)
            database.save_auth_flow(
                flow_id,
                result["status"],
                result.get("phone_masked") or (flow or {}).get("phone_masked"),
            )
        return result

    @app.post("/api/v1/auth/flows/{flow_id}/code")
    async def submit_code(flow_id: str, payload: CodeInput) -> dict[str, Any]:
        if database.get_auth_flow(flow_id) is None:
            raise HTTPException(status_code=404, detail="Login flow not found")
        return await finish_auth(flow_id, await telegram_provider.submit_code(flow_id, payload.code))

    @app.post("/api/v1/auth/flows/{flow_id}/password")
    async def submit_password(flow_id: str, payload: PasswordInput) -> dict[str, Any]:
        if database.get_auth_flow(flow_id) is None:
            raise HTTPException(status_code=404, detail="Login flow not found")
        return await finish_auth(flow_id, await telegram_provider.submit_password(flow_id, payload.password))

    @app.get("/api/v1/accounts")
    async def list_accounts() -> dict[str, Any]:
        return {"items": [_public_account(account) for account in database.list_accounts()]}

    @app.delete("/api/v1/accounts/{account_id}", status_code=204)
    async def delete_account(account_id: str) -> Response:
        account = database.get_account(account_id)
        if account is None:
            raise HTTPException(status_code=404, detail="Account not found")
        if any(job["account_id"] == account_id for job in database.list_jobs()):
            raise HTTPException(status_code=409, detail="Account has export jobs and cannot be removed")
        await telegram_provider.delete_session(account)
        database.delete_account(account_id)
        return Response(status_code=204)

    async def refresh_dialog_cache(account_id: str) -> list[dict[str, Any]]:
        account = database.get_account(account_id)
        if account is None:
            raise HTTPException(status_code=404, detail="Account not found")
        dialogs = await telegram_provider.list_dialogs(account)
        database.replace_dialogs(account_id, dialogs)
        return database.list_dialogs(account_id)

    @app.get("/api/v1/accounts/{account_id}/dialogs")
    async def list_dialogs(
        account_id: str,
        search: str | None = None,
        cursor: str | None = None,
        limit: int = Query(default=50, ge=1, le=100),
        dialog_type: str | None = Query(default=None, alias="type"),
    ) -> dict[str, Any]:
        if cursor is None:
            items = await refresh_dialog_cache(account_id)
        else:
            if database.get_account(account_id) is None:
                raise HTTPException(status_code=404, detail="Account not found")
            items = database.list_dialogs(account_id)
        if search and search.strip():
            query = search.strip().casefold()
            items = [
                item
                for item in items
                if query in item["title"].casefold()
                or query in (item.get("username") or "").casefold()
            ]
        if dialog_type and dialog_type != "all":
            items = [item for item in items if item.get("category") == dialog_type]
        try:
            offset = int(cursor or 0)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Dialog cursor must be an integer") from exc
        if offset < 0:
            raise HTTPException(status_code=400, detail="Dialog cursor cannot be negative")
        page = items[offset : offset + limit]
        next_cursor = str(offset + limit) if offset + limit < len(items) else None
        return {"items": page, "next_cursor": next_cursor, "next_offset": next_cursor}

    @app.post("/api/v1/accounts/{account_id}/dialogs/refresh")
    async def refresh_dialogs(account_id: str) -> dict[str, Any]:
        items = await refresh_dialog_cache(account_id)
        next_cursor = "100" if len(items) > 100 else None
        return {"items": items[:100], "next_cursor": next_cursor, "next_offset": next_cursor}

    @app.get("/api/v1/accounts/{account_id}/dialogs/{dialog_id}/bounds")
    async def get_dialog_bounds(account_id: str, dialog_id: str) -> dict[str, Any]:
        account = database.get_account(account_id)
        if account is None:
            raise HTTPException(status_code=404, detail="Account not found")
        if database.get_dialog(account_id, dialog_id) is None:
            raise HTTPException(status_code=404, detail="Dialog not found; refresh the dialog list")
        return {
            "dialog_id": dialog_id,
            **await telegram_provider.dialog_bounds(account, dialog_id),
        }

    @app.get("/api/v1/output-roots")
    async def list_output_roots() -> dict[str, Any]:
        return {"items": database.list_output_roots()}

    @app.post("/api/v1/output-roots", status_code=201)
    async def add_output_root(payload: OutputRootInput) -> dict[str, Any]:
        try:
            path = Path(payload.path).expanduser().resolve()
            if settings.container_mode and settings.default_output_root is not None:
                path = ensure_within(settings.default_output_root.resolve(), path)
            path.mkdir(parents=True, exist_ok=True)
            if not path.is_dir():
                raise ValueError("Not a directory")
            probe = path / f".archivedesk-write-test-{uuid.uuid4().hex}"
            with probe.open("x", encoding="utf-8"):
                pass
            probe.unlink()
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="The output directory is not writable") from exc
        return database.add_output_root(uuid.uuid4().hex, str(path))

    @app.post("/api/v1/export-jobs", status_code=201)
    async def create_export_job(
        payload: ExportJobInput,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        account = database.get_account(payload.account_id)
        dialog = database.get_dialog(payload.account_id, payload.dialog_id)
        root = database.get_output_root(payload.output_root_id)
        if account is None:
            raise HTTPException(status_code=404, detail="Account not found")
        if dialog is None:
            raise HTTPException(status_code=404, detail="Dialog not found; refresh the dialog list")
        if root is None:
            raise HTTPException(status_code=404, detail="Output root not found")
        config = payload.model_dump(mode="json")
        request_hash = hashlib.sha256(
            json.dumps(
                config,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if idempotency_key is not None:
            idempotency_key = idempotency_key.strip()
            if not idempotency_key or len(idempotency_key) > 200:
                raise HTTPException(
                    status_code=400,
                    detail="Idempotency-Key must contain 1 to 200 characters",
                )
            existing_key = database.get_idempotency_key(payload.account_id, idempotency_key)
            if existing_key is not None:
                if existing_key["request_hash"] != request_hash:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "IDEMPOTENCY_CONFLICT",
                            "category": "JOB",
                            "message": "相同 Idempotency-Key 已用于不同的导出配置。",
                        },
                    )
                existing_job = database.get_job(existing_key["job_id"])
                if existing_job is not None:
                    return public_job(existing_job)
        active_job = database.get_active_job_for_account(payload.account_id)
        if active_job is not None:
            raise HTTPException(
                status_code=409,
                detail=f"Account already has an active export job: {active_job['id']}",
            )
        job_id = uuid.uuid4().hex
        directory_name = safe_filename(
            f"ArchiveDesk-{dialog['title']}-{job_id[:8]}", fallback=f"ArchiveDesk-{job_id[:8]}"
        )
        final_path = Path(root["path"]) / directory_name
        try:
            job = database.create_job(
                {
                    "id": job_id,
                    "account_id": payload.account_id,
                    "dialog_id": payload.dialog_id,
                    "output_root_id": payload.output_root_id,
                    "status": "queued",
                    "stage": "queued",
                    "config": config,
                    "progress": {
                        "messages_seen": 0,
                        "messages_saved": 0,
                        "files_total": 0,
                        "files_done": 0,
                        "files_skipped": 0,
                        "bytes_done": 0,
                        "bytes_total": 0,
                        "upper_message_id": None,
                        "download_confirmed": False,
                    },
                    "output_path": str(final_path),
                }
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="Account already has an active export job") from exc
        if idempotency_key is not None:
            database.save_idempotency_key(
                payload.account_id, idempotency_key, request_hash, job_id
            )
        database.add_event(job_id, "job.created", {"job_id": job_id, "status": "queued"})
        await jobs.start(job_id)
        return public_job(database.get_job(job_id) or job)

    @app.get("/api/v1/export-jobs")
    async def list_export_jobs() -> dict[str, Any]:
        return {"items": [public_job(job) for job in database.list_jobs()]}

    @app.get("/api/v1/export-jobs/{job_id}")
    async def get_export_job(job_id: str) -> dict[str, Any]:
        return public_job(job_or_404(job_id))

    @app.delete("/api/v1/export-jobs/{job_id}", status_code=204)
    async def delete_export_job(job_id: str, delete_files: bool = False) -> Response:
        job = job_or_404(job_id)
        if job["status"] in {"queued", "running", "waiting", "pausing", "paused", "cancelling", "awaiting_confirmation"}:
            raise HTTPException(status_code=409, detail="Active export jobs cannot be deleted")
        if delete_files:
            root = database.get_output_root(job["output_root_id"])
            if root is None:
                raise HTTPException(status_code=409, detail="Output root no longer exists")
            root_path = Path(root["path"])
            candidates = [root_path / f".archivedesk-{job_id}.partial"]
            if job.get("output_path"):
                candidates.append(Path(job["output_path"]))
            try:
                for candidate in candidates:
                    safe_candidate = ensure_within(root_path, candidate)
                    if safe_candidate.is_dir():
                        shutil.rmtree(safe_candidate)
                    elif safe_candidate.exists():
                        safe_candidate.unlink()
            except OSError as exc:
                raise HTTPException(status_code=409, detail="Export files could not be removed") from exc
        database.delete_job(job_id)
        return Response(status_code=204)

    @app.post("/api/v1/export-jobs/{job_id}/actions/{action}")
    async def export_job_action(job_id: str, action: str) -> dict[str, Any]:
        job_or_404(job_id)
        if action == "pause":
            return public_job(await jobs.pause(job_id))
        if action == "resume":
            return public_job(await jobs.resume(job_id))
        if action == "recheck":
            return public_job(await jobs.recheck_capacity(job_id))
        if action == "confirm":
            job = job_or_404(job_id)
            if job.get("progress", {}).get("capacity_sufficient") is False:
                raise HTTPException(
                    status_code=409,
                    detail="Disk space is insufficient; recheck capacity before downloading",
                )
            return public_job(await jobs.confirm(job_id))
        if action == "cancel":
            return public_job(await jobs.cancel(job_id))
        raise HTTPException(status_code=404, detail="Unknown export action")

    @app.get("/api/v1/export-jobs/{job_id}/manifest")
    async def get_manifest(job_id: str) -> FileResponse:
        job = job_or_404(job_id)
        manifest_path = completed_output(job) / "manifest.json"
        if not manifest_path.is_file():
            raise HTTPException(status_code=404, detail="manifest.json was not found")
        return FileResponse(
            manifest_path,
            media_type="application/json",
            content_disposition_type="inline",
        )

    @app.get("/api/v1/export-jobs/{job_id}/result.json")
    async def get_export_result(job_id: str) -> FileResponse:
        result_path = completed_output(job_or_404(job_id)) / "result.json"
        if not result_path.is_file():
            raise HTTPException(status_code=404, detail="result.json was not found")
        return FileResponse(result_path, media_type="application/json", content_disposition_type="inline")

    @app.post("/api/v1/export-jobs/{job_id}/open-folder", status_code=204)
    async def open_export_folder(job_id: str) -> Response:
        output = completed_output(job_or_404(job_id))
        if settings.container_mode or os.name != "nt" or not hasattr(os, "startfile"):
            raise HTTPException(status_code=501, detail="Opening folders is only supported on Windows")
        try:
            await asyncio.to_thread(os.startfile, str(output))  # type: ignore[attr-defined]
        except OSError as exc:
            raise HTTPException(status_code=500, detail="Export folder could not be opened") from exc
        return Response(status_code=204)

    @app.get("/api/v1/export-jobs/{job_id}/media/{filename:path}")
    async def get_export_media(job_id: str, filename: str) -> FileResponse:
        output = completed_output(job_or_404(job_id))
        media_root = ensure_within(output, output / "media")
        media_path = ensure_within(media_root, media_root / filename)
        if not media_path.is_file():
            raise HTTPException(status_code=404, detail="Media file was not found")
        return FileResponse(media_path, content_disposition_type="inline")

    @app.get("/api/v1/export-jobs/{job_id}/events")
    async def stream_job_events(
        job_id: str,
        request: Request,
        after: int = 0,
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        job_or_404(job_id)
        try:
            cursor = max(after, int(last_event_id or 0))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Last-Event-ID must be an integer") from exc

        async def event_stream():
            nonlocal cursor
            idle_ticks = 0
            while True:
                if await request.is_disconnected():
                    return
                events = database.events_after(job_id, cursor)
                if events:
                    idle_ticks = 0
                    for event in events:
                        cursor = event["id"]
                        data = {
                            "schema_version": 1,
                            "event_id": str(event["id"]),
                            "revision": event["id"],
                            "type": event["type"],
                            "job_id": job_id,
                            "occurred_at": event["created_at"],
                            "data": event["data"],
                        }
                        yield f"id: {event['id']}\nevent: job\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                    continue
                idle_ticks += 1
                if idle_ticks >= 30:
                    idle_ticks = 0
                    yield ": keep-alive\n\n"
                current = database.get_job(job_id)
                if current and current["status"] in TERMINAL_STATUSES:
                    return
                await asyncio.sleep(0.5)

        from .exporter import TERMINAL_STATUSES

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    if settings.static_dir is not None:
        static_dir = settings.static_dir.resolve()
        assets_dir = static_dir / "assets"
        if assets_dir.is_dir():
            app.mount(
                "/assets",
                StaticFiles(directory=assets_dir),
                name="frontend-assets",
            )

        @app.get("/{frontend_path:path}", include_in_schema=False)
        async def frontend(frontend_path: str) -> FileResponse:
            if frontend_path.startswith("api/"):
                raise HTTPException(status_code=404, detail="API route not found")
            requested = ensure_within(static_dir, static_dir / frontend_path)
            if requested.is_file():
                return FileResponse(requested)
            return FileResponse(static_dir / "index.html", media_type="text/html")

    return app
