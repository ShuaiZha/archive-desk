from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Protocol

from .config import Settings
from .security import SecretStore, mask_phone


class TelegramError(RuntimeError):
    pass


class TelegramNotConfigured(TelegramError):
    pass


class TelegramUnauthorized(TelegramError):
    pass


class TelegramFloodWait(TelegramError):
    def __init__(self, seconds: int):
        super().__init__(f"Telegram requested a {seconds} second wait")
        self.seconds = max(1, int(seconds))


@dataclass(slots=True)
class MediaDescriptor:
    kind: str
    original_name: str
    mime_type: str | None
    expected_size: int | None
    policy_reason: str | None = None

    def public_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.original_name,
            "mime_type": self.mime_type,
            "size": self.expected_size,
            "policy_reason": self.policy_reason,
        }


@dataclass(slots=True)
class ExportMessage:
    id: int
    date: str
    edit_date: str | None
    sender_id: str | None
    text: str
    reply_to_message_id: int | None
    forward: dict[str, Any] | None
    media: MediaDescriptor | None
    raw: dict[str, Any]

    def database_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "date": self.date,
            "edit_date": self.edit_date,
            "sender_id": self.sender_id,
            "text": self.text,
            "reply_to_message_id": self.reply_to_message_id,
            "forward": self.forward,
            "media": self.media.public_dict() if self.media else None,
            "raw": self.raw,
        }


ProgressCallback = Callable[..., Awaitable[None]]
ControlCallback = Callable[[], Awaitable[None]]


class ExportSession(Protocol):
    async def latest_message_id(self, dialog_id: str) -> int | None: ...

    async def iter_messages(
        self,
        dialog_id: str,
        date_from: datetime | None,
        date_to_exclusive: datetime | None,
        before_message_id: int | None = None,
    ) -> AsyncIterator[ExportMessage]: ...

    async def download_media(
        self,
        dialog_id: str,
        message_id: int,
        target: Path,
        offset: int,
        progress: ProgressCallback,
        control: ControlCallback,
    ) -> int: ...


class TelegramProvider(Protocol):
    async def begin_auth(self, flow_id: str, phone: str) -> dict[str, Any]: ...

    async def submit_code(self, flow_id: str, code: str) -> dict[str, Any]: ...

    async def submit_password(self, flow_id: str, password: str) -> dict[str, Any]: ...

    async def resend_auth(self, flow_id: str) -> dict[str, Any]: ...

    async def cancel_auth(self, flow_id: str) -> None: ...

    async def list_dialogs(self, account: dict[str, Any]) -> list[dict[str, Any]]: ...

    async def dialog_bounds(self, account: dict[str, Any], dialog_id: str) -> dict[str, str | None]: ...

    def export_session(self, account: dict[str, Any]): ...

    async def delete_session(self, account: dict[str, Any]) -> None: ...

    async def close(self) -> None: ...


@dataclass(slots=True)
class _AuthFlow:
    client: Any
    phone: str
    phone_code_hash: str
    session_base: Path


def _telethon():
    try:
        import telethon
        from telethon import TelegramClient, errors, utils
    except ImportError as exc:  # pragma: no cover - only when installation is incomplete
        raise TelegramError("Telethon is not installed") from exc
    return telethon, TelegramClient, errors, utils


class TelethonProvider:
    def __init__(self, settings: Settings, secrets: SecretStore):
        self.settings = settings
        self.secrets = secrets
        self._flows: dict[str, _AuthFlow] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}

    def _session_lock(self, session_name: str) -> asyncio.Lock:
        return self._session_locks.setdefault(session_name, asyncio.Lock())

    def _credentials(self) -> tuple[int, str]:
        credentials = self.secrets.credentials()
        if credentials is None:
            raise TelegramNotConfigured("Configure API ID and API Hash first")
        return credentials

    def _new_client(self, session_base: Path):
        _, TelegramClient, _, _ = _telethon()
        api_id, api_hash = self._credentials()
        return TelegramClient(
            str(session_base),
            api_id,
            api_hash,
            device_model="Archive Desk",
            system_version="Windows",
            app_version="0.1.0",
            lang_code="zh-hans",
            system_lang_code="zh-hans",
        )

    @staticmethod
    def _raise_auth_error(exc: Exception, errors: Any) -> None:
        if isinstance(exc, errors.FloodWaitError):
            raise TelegramFloodWait(int(exc.seconds)) from exc
        messages = (
            ("PhoneNumberInvalidError", "The phone number is invalid"),
            ("PhoneNumberBannedError", "This phone number is banned by Telegram"),
            ("ApiIdInvalidError", "The API ID or API Hash is invalid"),
            ("PhoneCodeInvalidError", "The verification code is invalid"),
            ("PhoneCodeExpiredError", "The verification code has expired"),
            ("PasswordHashInvalidError", "The two-step verification password is incorrect"),
        )
        for class_name, message in messages:
            error_type = getattr(errors, class_name, None)
            if error_type is not None and isinstance(exc, error_type):
                raise TelegramError(message) from exc
        rpc_error = getattr(errors, "RPCError", None)
        if rpc_error is not None and isinstance(exc, rpc_error):
            raise TelegramError("Telegram rejected the authentication request") from exc
        raise exc

    async def begin_auth(self, flow_id: str, phone: str) -> dict[str, Any]:
        _, _, errors, _ = _telethon()
        session_base = self.settings.session_dir / f"auth_{flow_id}"
        client = self._new_client(session_base)
        try:
            await client.connect()
            sent = await client.send_code_request(phone)
        except Exception as exc:
            await client.disconnect()
            self._raise_auth_error(exc, errors)
        self._flows[flow_id] = _AuthFlow(
            client=client,
            phone=phone,
            phone_code_hash=sent.phone_code_hash,
            session_base=session_base,
        )
        return {"id": flow_id, "status": "code_required", "phone_masked": mask_phone(phone)}

    def _flow(self, flow_id: str) -> _AuthFlow:
        flow = self._flows.get(flow_id)
        if flow is None:
            raise TelegramError("Login flow expired; request a new code")
        return flow

    async def submit_code(self, flow_id: str, code: str) -> dict[str, Any]:
        flow = self._flow(flow_id)
        _, _, errors, _ = _telethon()
        try:
            await flow.client.sign_in(
                phone=flow.phone,
                code=code.strip(),
                phone_code_hash=flow.phone_code_hash,
            )
        except errors.SessionPasswordNeededError:
            return {"id": flow_id, "status": "password_required", "phone_masked": mask_phone(flow.phone)}
        except (errors.PhoneCodeInvalidError, errors.PhoneCodeExpiredError) as exc:
            raise TelegramError("The verification code is invalid or expired") from exc
        except Exception as exc:
            self._raise_auth_error(exc, errors)
        return await self._finalize(flow_id)

    async def submit_password(self, flow_id: str, password: str) -> dict[str, Any]:
        flow = self._flow(flow_id)
        _, _, errors, _ = _telethon()
        try:
            await flow.client.sign_in(password=password)
        except errors.PasswordHashInvalidError as exc:
            raise TelegramError("The two-step verification password is incorrect") from exc
        except Exception as exc:
            self._raise_auth_error(exc, errors)
        return await self._finalize(flow_id)

    async def resend_auth(self, flow_id: str) -> dict[str, Any]:
        flow = self._flow(flow_id)
        _, _, errors, _ = _telethon()
        try:
            sent = await flow.client.send_code_request(flow.phone)
        except Exception as exc:
            self._raise_auth_error(exc, errors)
        flow.phone_code_hash = sent.phone_code_hash
        return {
            "id": flow_id,
            "status": "code_required",
            "phone_masked": mask_phone(flow.phone),
        }

    async def cancel_auth(self, flow_id: str) -> None:
        flow = self._flows.pop(flow_id, None)
        if flow is None:
            return
        try:
            await flow.client.disconnect()
        finally:
            for suffix in (".session", ".session-journal", ".session-shm", ".session-wal"):
                path = flow.session_base.with_suffix(suffix)
                if path.exists():
                    path.unlink()

    async def _finalize(self, flow_id: str) -> dict[str, Any]:
        flow = self._flow(flow_id)
        me = await flow.client.get_me()
        if me is None:
            raise TelegramUnauthorized("Telegram did not return the authorized account")
        telegram_user_id = int(me.id)
        await flow.client.disconnect()
        source = flow.session_base.with_suffix(".session")
        session_name = f"account_{telegram_user_id}"
        destination = (self.settings.session_dir / session_name).with_suffix(".session")
        async with self._session_lock(session_name):
            if source.exists() and source != destination:
                os.replace(source, destination)
        self._flows.pop(flow_id, None)
        display_name = " ".join(part for part in (getattr(me, "first_name", None), getattr(me, "last_name", None)) if part)
        account = {
            "id": f"tg_{telegram_user_id}",
            "telegram_user_id": telegram_user_id,
            "session_name": session_name,
            "display_name": display_name or getattr(me, "username", None) or str(telegram_user_id),
            "username": getattr(me, "username", None),
            "phone_masked": mask_phone(getattr(me, "phone", None)),
        }
        return {"id": flow_id, "status": "authorized", "phone_masked": mask_phone(flow.phone), "account": account}

    def _account_client(self, account: dict[str, Any]):
        session_base = self.settings.session_dir / account["session_name"]
        return self._new_client(session_base)

    async def list_dialogs(self, account: dict[str, Any]) -> list[dict[str, Any]]:
        _, _, _, utils = _telethon()
        dialogs: list[dict[str, Any]] = []
        async with self._session_lock(account["session_name"]):
            client = self._account_client(account)
            await client.connect()
            try:
                if not await client.is_user_authorized():
                    raise TelegramUnauthorized("The Telegram session has been revoked")
                async for dialog in client.iter_dialogs():
                    peer_id = str(utils.get_peer_id(dialog.entity))
                    if dialog.is_user:
                        category = "private"
                    elif dialog.is_group:
                        category = "group"
                    else:
                        category = "channel"
                    dialogs.append(
                        {
                            "id": peer_id,
                            "peer_id": peer_id,
                            "title": dialog.name or peer_id,
                            "category": category,
                            "username": getattr(dialog.entity, "username", None),
                            "unread_count": int(dialog.unread_count or 0),
                            "message_count": None,
                        }
                    )
            finally:
                await client.disconnect()
        return dialogs

    async def dialog_bounds(self, account: dict[str, Any], dialog_id: str) -> dict[str, str | None]:
        async with self._session_lock(account["session_name"]):
            client = self._account_client(account)
            await client.connect()
            try:
                if not await client.is_user_authorized():
                    raise TelegramUnauthorized("The Telegram session has been revoked")
                try:
                    entity = await client.get_entity(int(dialog_id))
                except (TypeError, ValueError) as exc:
                    raise TelegramError("Invalid dialog identifier") from exc
                newest = await client.get_messages(entity, limit=1)
                oldest = await client.get_messages(entity, limit=1, reverse=True)
                newest_message = newest[0] if newest else None
                oldest_message = oldest[0] if oldest else None
                return {
                    "earliest_message_at": _iso(getattr(oldest_message, "date", None)),
                    "latest_message_at": _iso(getattr(newest_message, "date", None)),
                }
            finally:
                await client.disconnect()

    @asynccontextmanager
    async def export_session(self, account: dict[str, Any]):
        async with self._session_lock(account["session_name"]):
            client = self._account_client(account)
            await client.connect()
            try:
                if not await client.is_user_authorized():
                    raise TelegramUnauthorized("The Telegram session has been revoked")
                yield TelethonExportSession(client)
            finally:
                await client.disconnect()

    async def delete_session(self, account: dict[str, Any]) -> None:
        session_base = self.settings.session_dir / account["session_name"]
        async with self._session_lock(account["session_name"]):
            client = self._new_client(session_base)
            try:
                await client.connect()
                if await client.is_user_authorized():
                    await client.log_out()
            finally:
                await client.disconnect()
            for suffix in (".session", ".session-journal", ".session-shm", ".session-wal"):
                path = session_base.with_suffix(suffix)
                if path.exists():
                    path.unlink()

    async def close(self) -> None:
        flows = list(self._flows.values())
        self._flows.clear()
        for flow in flows:
            try:
                await flow.client.disconnect()
            except Exception:
                pass


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


class TelethonExportSession:
    _CHUNK_SIZE = 512 * 1024

    def __init__(self, client: Any):
        self.client = client

    async def _entity(self, dialog_id: str):
        try:
            return await self.client.get_entity(int(dialog_id))
        except (TypeError, ValueError) as exc:
            raise TelegramError("Invalid dialog identifier") from exc

    @staticmethod
    def _media(message: Any) -> MediaDescriptor | None:
        # Telethon's convenience properties also expose photos and documents
        # embedded in web previews. Those are not standalone message media and
        # passing MessageMediaWebPage to iter_download raises a TypeError.
        message_media = getattr(message, "media", None)
        file = getattr(message, "file", None)
        if getattr(message_media, "photo", None) is not None:
            extension = getattr(file, "ext", None) or ".jpg"
            return MediaDescriptor(
                kind="photo",
                original_name=f"photo_{message.id}{extension}",
                mime_type=getattr(file, "mime_type", None) or "image/jpeg",
                expected_size=getattr(file, "size", None),
            )
        if getattr(message_media, "document", None) is None:
            return None
        unsupported = next(
            (
                kind
                for kind in ("video_note", "voice", "audio", "gif", "sticker")
                if getattr(message, kind, None) is not None
            ),
            None,
        )
        if unsupported is not None:
            extension = getattr(file, "ext", None) or ""
            name = getattr(file, "name", None) or f"{unsupported}_{message.id}{extension}"
            return MediaDescriptor(
                kind=unsupported,
                original_name=name,
                mime_type=getattr(file, "mime_type", None),
                expected_size=getattr(file, "size", None),
                policy_reason="unsupported_media_type",
            )
        if getattr(message, "video", None) is not None:
            extension = getattr(file, "ext", None) or ".mp4"
            name = getattr(file, "name", None) or f"video_{message.id}{extension}"
            return MediaDescriptor(
                kind="video",
                original_name=name,
                mime_type=getattr(file, "mime_type", None) or "video/mp4",
                expected_size=getattr(file, "size", None),
            )
        extension = getattr(file, "ext", None) or ""
        name = getattr(file, "name", None) or f"document_{message.id}{extension}"
        return MediaDescriptor(
            kind="file",
            original_name=name,
            mime_type=getattr(file, "mime_type", None),
            expected_size=getattr(file, "size", None),
        )

    @staticmethod
    def _forward(message: Any) -> dict[str, Any] | None:
        forward = getattr(message, "fwd_from", None)
        if forward is None:
            return None
        from_id = getattr(forward, "from_id", None)
        return {
            "date": _iso(getattr(forward, "date", None)),
            "from_id": str(from_id) if from_id is not None else None,
            "from_name": getattr(forward, "from_name", None),
            "channel_post": getattr(forward, "channel_post", None),
            "post_author": getattr(forward, "post_author", None),
        }

    @staticmethod
    def _entities(message: Any) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for entity in getattr(message, "entities", None) or []:
            item = entity.to_dict()
            result.append({key: value for key, value in item.items() if key != "_"} | {"type": item.get("_")})
        return result

    async def latest_message_id(self, dialog_id: str) -> int | None:
        entity = await self._entity(dialog_id)
        messages = await self.client.get_messages(entity, limit=1)
        return int(messages[0].id) if messages else None

    async def iter_messages(
        self,
        dialog_id: str,
        date_from: datetime | None,
        date_to_exclusive: datetime | None,
        before_message_id: int | None = None,
    ) -> AsyncIterator[ExportMessage]:
        entity = await self._entity(dialog_id)
        kwargs: dict[str, Any] = {}
        if date_to_exclusive is not None:
            kwargs["offset_date"] = date_to_exclusive
        if before_message_id is not None:
            kwargs["max_id"] = before_message_id
        async for message in self.client.iter_messages(entity, **kwargs):
            message_date = message.date
            if message_date.tzinfo is None:
                message_date = message_date.replace(tzinfo=UTC)
            message_date = message_date.astimezone(UTC)
            if date_to_exclusive is not None and message_date >= date_to_exclusive:
                continue
            if date_from is not None and message_date < date_from:
                break
            reply_to = getattr(message, "reply_to", None)
            reply_id = getattr(reply_to, "reply_to_msg_id", None) if reply_to else None
            grouped_id = getattr(message, "grouped_id", None)
            sender = getattr(message, "sender", None)
            sender_name = " ".join(
                part
                for part in (
                    getattr(sender, "first_name", None),
                    getattr(sender, "last_name", None),
                )
                if part
            ) or getattr(sender, "title", None) or getattr(sender, "username", None)
            raw = {
                "out": bool(getattr(message, "out", False)),
                "sender_name": sender_name,
                "mentioned": bool(getattr(message, "mentioned", False)),
                "silent": bool(getattr(message, "silent", False)),
                "post": bool(getattr(message, "post", False)),
                "views": getattr(message, "views", None),
                "forwards": getattr(message, "forwards", None),
                "grouped_id": str(grouped_id) if grouped_id is not None else None,
                "post_author": getattr(message, "post_author", None),
                "entities": self._entities(message),
            }
            yield ExportMessage(
                id=int(message.id),
                date=message_date.isoformat(),
                edit_date=_iso(getattr(message, "edit_date", None)),
                sender_id=str(message.sender_id) if message.sender_id is not None else None,
                text=message.raw_text or "",
                reply_to_message_id=reply_id,
                forward=self._forward(message),
                media=self._media(message),
                raw=raw,
            )

    async def download_media(
        self,
        dialog_id: str,
        message_id: int,
        target: Path,
        offset: int,
        progress: ProgressCallback,
        control: ControlCallback,
    ) -> int:
        _, _, errors, _ = _telethon()
        entity = await self._entity(dialog_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        current = offset
        for attempt in range(3):
            message = await self.client.get_messages(entity, ids=message_id)
            if message is None or message.media is None:
                raise TelegramError("The media is no longer available")
            mode = "r+b" if target.exists() else "wb"
            try:
                with target.open(mode) as stream:
                    stream.truncate(current)
                    stream.seek(current)
                    async for chunk in self.client.iter_download(
                        message.media,
                        offset=current,
                        chunk_size=self._CHUNK_SIZE,
                        request_size=self._CHUNK_SIZE,
                    ):
                        await control()
                        stream.write(chunk)
                        stream.flush()
                        os.fsync(stream.fileno())
                        current += len(chunk)
                        await progress(current, chunk)
                return current
            except errors.FloodWaitError as exc:
                raise TelegramFloodWait(int(exc.seconds)) from exc
            except Exception as exc:
                if "FileReference" in type(exc).__name__ and attempt < 2:
                    await asyncio.sleep(0)
                    continue
                raise TelegramError(
                    f"Telegram media download failed: {type(exc).__name__}"
                ) from exc
        return current
