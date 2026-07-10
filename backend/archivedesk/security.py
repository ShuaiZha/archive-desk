from __future__ import annotations

import ctypes
import base64
import binascii
import json
import os
from ctypes import wintypes
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


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
    _AES_GCM_PREFIX = b"AESGCM1\0"
    _AES_GCM_AAD = b"ArchiveDesk.credentials.v1"

    def __init__(self, path: Path, encryption_key: bytes | None = None):
        if encryption_key is not None and len(encryption_key) != 32:
            raise ValueError("Archive Desk encryption key must contain exactly 32 bytes")
        self.path = path
        self.encryption_key = encryption_key

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        payload = self.path.read_bytes()
        migrate_plaintext = False
        if payload.startswith(self._AES_GCM_PREFIX):
            if self.encryption_key is None:
                raise ValueError("Encrypted credentials require the configured master key")
            encrypted = payload[len(self._AES_GCM_PREFIX) :]
            if len(encrypted) < 12 + 16:
                raise ValueError("Encrypted credentials are truncated")
            nonce, ciphertext = encrypted[:12], encrypted[12:]
            try:
                raw = AESGCM(self.encryption_key).decrypt(
                    nonce,
                    ciphertext,
                    self._AES_GCM_AAD,
                )
            except InvalidTag as exc:
                raise ValueError("Encrypted credentials failed authentication") from exc
        elif payload.startswith(self._WINDOWS_PREFIX):
            if os.name != "nt":
                raise ValueError("Windows DPAPI credentials cannot be read on this platform")
            raw = _dpapi_unprotect(payload[len(self._WINDOWS_PREFIX) :])
        elif payload.startswith(self._PORTABLE_PREFIX):
            raw = payload[len(self._PORTABLE_PREFIX) :]
            migrate_plaintext = self.encryption_key is not None
        else:
            raise ValueError("Unsupported credentials file format")
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Invalid credentials file")
        if migrate_plaintext:
            self.save(value)
        return value

    def save(self, value: dict[str, Any]) -> None:
        raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if self.encryption_key is not None:
            nonce = os.urandom(12)
            ciphertext = AESGCM(self.encryption_key).encrypt(
                nonce,
                raw,
                self._AES_GCM_AAD,
            )
            payload = self._AES_GCM_PREFIX + nonce + ciphertext
        elif os.name == "nt":
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


def load_master_key(path: Path | None) -> bytes | None:
    if path is None:
        return None
    encoded = path.read_bytes().strip()
    if not encoded:
        raise ValueError("Archive Desk master key file is empty")
    try:
        key = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Archive Desk master key must be standard base64") from exc
    if len(key) != 32:
        raise ValueError("Archive Desk master key must decode to exactly 32 bytes")
    return key


def mask_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    normalized = phone.strip()
    if len(normalized) <= 5:
        return "*" * len(normalized)
    return f"{normalized[:3]}{'*' * max(3, len(normalized) - 5)}{normalized[-2:]}"
