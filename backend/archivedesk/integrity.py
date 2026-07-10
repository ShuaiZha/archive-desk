from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable


class IntegrityError(RuntimeError):
    pass


FORBIDDEN_SECRET_KEYS = {
    "access_hash",
    "api_hash",
    "auth_key",
    "file_reference",
    "password",
    "phone_code",
    "phone_code_hash",
    "phone_number",
    "session",
    "session_string",
    "takeout_id",
    "two_factor_password",
}
FORBIDDEN_NAMES = {".env", "archivedesk.sqlite3", "archivedesk.db", "jobs.db", "runtime.db"}
FORBIDDEN_SUFFIXES = (".part", ".partial", ".session", ".session-journal", ".session-shm", ".session-wal")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise IntegrityError(message)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _walk_forbidden_keys(value: Any, location: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            _require(
                normalized not in FORBIDDEN_SECRET_KEYS,
                f"forbidden secret field: {location}.{key}",
            )
            _walk_forbidden_keys(child, f"{location}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _walk_forbidden_keys(child, f"{location}[{index}]")


def _is_link_like(path: Path) -> bool:
    if path.is_symlink():
        return True
    junction_probe = getattr(path, "is_junction", None)
    return bool(junction_probe and junction_probe())


def _disk_files(root: Path) -> dict[str, Path]:
    files: dict[str, Path] = {}
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        for name in directory_names:
            _require(not _is_link_like(directory_path / name), "link directory in export")
        for name in file_names:
            path = directory_path / name
            relative = path.relative_to(root).as_posix()
            lowered = name.casefold()
            _require(not _is_link_like(path), f"link file in export: {relative}")
            _require(lowered not in FORBIDDEN_NAMES, f"runtime file in export: {relative}")
            _require(
                not any(lowered.endswith(suffix) for suffix in FORBIDDEN_SUFFIXES),
                f"partial or session file in export: {relative}",
            )
            files[relative] = path
    return files


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IntegrityError(f"invalid JSON: {path.name}") from exc
    _require(isinstance(value, dict), f"{path.name} root is not an object")
    return value


def _scan_canaries(files: Iterable[Path], canaries: Iterable[bytes]) -> None:
    values = [value for value in canaries if value]
    if not values:
        return
    overlap = max(len(value) for value in values) - 1
    for path in files:
        tail = b""
        with path.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                content = tail + block
                for value in values:
                    _require(value not in content, f"secret canary leaked into {path.name}")
                tail = content[-overlap:] if overlap > 0 else b""


def validate_staging_export(
    root: Path,
    assets: Iterable[dict[str, Any]],
    *,
    canaries: Iterable[bytes] = (),
) -> None:
    """Independently read back and close a compatibility-v1 export before commit."""
    root = root.resolve(strict=True)
    files = _disk_files(root)
    _require("result.json" in files, "result.json is missing")
    _require("manifest.json" in files, "manifest.json is missing")
    result = _read_json(files["result.json"])
    manifest = _read_json(files["manifest.json"])
    _walk_forbidden_keys(result, "$result")
    _walk_forbidden_keys(manifest, "$manifest")
    _require(result.get("schema_version") == 1, "unsupported result schema")
    _require(manifest.get("schema_version") == 1, "unsupported manifest schema")
    _require(result.get("source") == manifest.get("source"), "result/manifest source mismatch")

    messages = result.get("messages")
    _require(isinstance(messages, list), "result.messages is not an array")
    message_ids: set[int] = set()
    for item in messages:
        _require(isinstance(item, dict), "message is not an object")
        message_id = item.get("id")
        _require(isinstance(message_id, int), "message id is invalid")
        _require(message_id not in message_ids, f"duplicate message id: {message_id}")
        message_ids.add(message_id)

    asset_list = list(assets)
    entries = manifest.get("files")
    _require(isinstance(entries, list), "manifest.files is not an array")
    entries_by_source: dict[tuple[int, str], dict[str, Any]] = {}
    for entry in entries:
        _require(isinstance(entry, dict), "manifest file entry is not an object")
        key = (int(entry.get("message_id")), str(entry.get("kind")))
        _require(key not in entries_by_source, f"duplicate manifest asset: {key}")
        entries_by_source[key] = entry

    expected_paths = {"result.json", "manifest.json"}
    completed = 0
    skipped = 0
    for asset in asset_list:
        key = (int(asset["message_id"]), str(asset["kind"]))
        entry = entries_by_source.get(key)
        _require(entry is not None, f"manifest asset is missing: {key}")
        _require(entry.get("status") == asset["status"], f"asset status mismatch: {key}")
        if asset["status"] == "completed":
            completed += 1
            relative = asset.get("relative_path")
            _require(isinstance(relative, str) and relative, f"completed asset has no path: {key}")
            _require(relative in files, f"completed file is missing: {relative}")
            expected_paths.add(relative)
            actual_size = files[relative].stat().st_size
            actual_hash = _sha256(files[relative])
            _require(actual_size == asset["bytes_done"], f"asset size mismatch: {relative}")
            _require(actual_hash == asset["sha256"], f"asset hash mismatch: {relative}")
            _require(entry.get("path") == relative, f"manifest path mismatch: {relative}")
            _require(entry.get("size") == actual_size, f"manifest size mismatch: {relative}")
            _require(entry.get("sha256") == actual_hash, f"manifest hash mismatch: {relative}")
        else:
            skipped += 1
            _require(asset["status"] == "skipped", f"asset is not terminal: {key}")
            _require(bool(asset.get("skip_reason")), f"skipped asset has no reason: {key}")
            _require(entry.get("path") is None, f"skipped asset has a path: {key}")

    _require(len(entries_by_source) == len(asset_list), "manifest contains unknown assets")
    _require(set(files) == expected_paths, "final directory contains orphan or missing files")
    counts = manifest.get("counts")
    _require(isinstance(counts, dict), "manifest counts are missing")
    _require(counts.get("messages") == len(messages), "message count is not closed")
    _require(counts.get("files_discovered") == len(asset_list), "asset count is not closed")
    _require(counts.get("files_downloaded") == completed, "download count is not closed")
    _require(counts.get("files_skipped") == skipped, "skip count is not closed")
    artifact = (manifest.get("artifacts") or {}).get("result.json")
    _require(isinstance(artifact, dict), "result artifact is missing")
    _require(artifact.get("size") == files["result.json"].stat().st_size, "result size mismatch")
    _require(artifact.get("sha256") == _sha256(files["result.json"]), "result hash mismatch")
    _scan_canaries(files.values(), canaries)
