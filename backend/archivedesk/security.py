from __future__ import annotations

import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path
from typing import Any


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes) -> tuple[_DataBlob, Any]:
    buffer = ctypes.create_string_buffer(data)
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def _dpapi_protect(data: bytes) -> bytes:
    in_blob, keepalive = _blob(data)
    out_blob = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    if not crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        "ArchiveDesk",
        None,
        None,
        None,
        0x1,
        ctypes.byref(out_blob),
    ):
        raise ctypes.WinError()
    try:
        _ = keepalive  # keep the input buffer alive until CryptProtectData returns
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _dpapi_unprotect(data: bytes) -> bytes:
    in_blob, keepalive = _blob(data)
    out_blob = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    if not crypt32.CryptUnprotectData(
        ctypes.byref(in_blob), None, None, None, None, 0x1, ctypes.byref(out_blob)
    ):
        raise ctypes.WinError()
    try:
        _ = keepalive  # keep the input buffer alive until CryptUnprotectData returns
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


class SecretStore:
    """Small user-local secret store.

    Windows uses DPAPI bound to the current user. Other platforms rely on a
    mode-0600 file; production packaging can replace this class with a native
    keyring without changing the API.
    """

    _WINDOWS_PREFIX = b"DPAPI1\0"
    _PORTABLE_PREFIX = b"PLAIN1\0"

    def __init__(self, path: Path):
        self.path = path

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        payload = self.path.read_bytes()
        if payload.startswith(self._WINDOWS_PREFIX):
            raw = _dpapi_unprotect(payload[len(self._WINDOWS_PREFIX) :])
        elif payload.startswith(self._PORTABLE_PREFIX):
            raw = payload[len(self._PORTABLE_PREFIX) :]
        else:
            raise ValueError("Unsupported credentials file format")
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Invalid credentials file")
        return value

    def save(self, value: dict[str, Any]) -> None:
        raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if os.name == "nt":
            payload = self._WINDOWS_PREFIX + _dpapi_protect(raw)
        else:
            payload = self._PORTABLE_PREFIX + raw
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_bytes(payload)
        if os.name != "nt":
            temporary.chmod(0o600)
        os.replace(temporary, self.path)

    def credentials(self) -> tuple[int, str] | None:
        value = self.load()
        api_id = value.get("api_id")
        api_hash = value.get("api_hash")
        if isinstance(api_id, int) and isinstance(api_hash, str) and api_hash:
            return api_id, api_hash
        return None

    def save_credentials(self, api_id: int, api_hash: str) -> None:
        self.save({"api_id": api_id, "api_hash": api_hash})


def mask_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    normalized = phone.strip()
    if len(normalized) <= 5:
        return "*" * len(normalized)
    return f"{normalized[:3]}{'*' * max(3, len(normalized) - 5)}{normalized[-2:]}"
