#!/usr/bin/env python3
"""Validate a round-one Archive Desk export without contacting Telegram.

With --self-test this file also validates its own negative controls: an
interrupted byte stream is resumed from a durable checkpoint, and deliberately
broken exports must be rejected.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable


REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures"
DEFAULT_CANARIES = FIXTURES / "secret_canaries.txt"

TERMINAL_ASSET_STATUSES = {
    "DOWNLOADED",
    "SKIPPED_SIZE",
    "SKIPPED_POLICY",
    "UNAVAILABLE",
    "FAILED",
}
COUNT_KEY_BY_STATUS = {
    "DOWNLOADED": "assets_downloaded",
    "SKIPPED_SIZE": "assets_skipped_size",
    "SKIPPED_POLICY": "assets_skipped_policy",
    "UNAVAILABLE": "assets_unavailable",
    "FAILED": "assets_failed",
}
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
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
BIDI_CONTROL_CODEPOINTS = {
    *range(0x202A, 0x202F),
    *range(0x2066, 0x206A),
}
FORBIDDEN_RUNTIME_NAMES = {
    ".env",
    "archivedesk.db",
    "jobs.db",
    "runtime.db",
    "session.sqlite",
    "state.db",
}
FORBIDDEN_RUNTIME_SUFFIXES = {
    ".part",
    ".partial",
    ".session",
    ".session-journal",
    ".session-shm",
    ".session-wal",
}


class ContractError(RuntimeError):
    """Raised when an export violates the round-one contract."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ContractError(f"missing required file: {path.name}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"{path.name} is not valid UTF-8 JSON: {exc}") from exc
    require(isinstance(value, dict), f"{path.name} root must be an object")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_timestamp(value: Any, label: str) -> datetime:
    require(isinstance(value, str), f"{label} must be an RFC 3339 string")
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ContractError(f"{label} is not a valid RFC 3339 timestamp") from exc
    require(parsed.tzinfo is not None, f"{label} must include a timezone")
    return parsed


def load_canaries(path: Path) -> list[bytes]:
    if not path.exists():
        raise ContractError(f"canary file does not exist: {path}")
    values: list[bytes] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            values.append(stripped.encode("utf-8"))
    require(bool(values), "canary file contains no values")
    return values


def walk_forbidden_keys(value: Any, location: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            require(
                normalized not in FORBIDDEN_SECRET_KEYS,
                f"forbidden secret field {location}.{key}",
            )
            walk_forbidden_keys(child, f"{location}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            walk_forbidden_keys(child, f"{location}[{index}]")


def is_link_like(path: Path) -> bool:
    if path.is_symlink():
        return True
    junction_probe = getattr(path, "is_junction", None)
    return bool(junction_probe and junction_probe())


def validate_relative_path(raw: Any, root: Path | None = None) -> PurePosixPath:
    require(isinstance(raw, str) and raw, "output path must be a non-empty string")
    require(len(raw) <= 240, f"output path exceeds 240 characters: {raw!r}")
    require("\\" not in raw, f"output path must use forward slashes: {raw!r}")

    posix = PurePosixPath(raw)
    windows = PureWindowsPath(raw)
    require(not posix.is_absolute(), f"absolute output path is forbidden: {raw!r}")
    require(
        not windows.is_absolute() and not windows.drive and not windows.root,
        f"Windows absolute/UNC output path is forbidden: {raw!r}",
    )
    require(posix.as_posix() == raw, f"non-canonical output path: {raw!r}")
    require(posix.parts, f"empty output path: {raw!r}")

    for part in posix.parts:
        require(part not in {"", ".", ".."}, f"dot path component is forbidden: {raw!r}")
        require(len(part) <= 120, f"path component exceeds 120 characters: {part!r}")
        require(not part.endswith((" ", ".")), f"trailing dot/space is forbidden: {part!r}")
        require(":" not in part, f"colon/NTFS ADS is forbidden: {part!r}")
        require(
            not any(ord(character) < 32 or ord(character) == 127 for character in part),
            f"control character is forbidden in output path: {raw!r}",
        )
        require(
            not any(ord(character) in BIDI_CONTROL_CODEPOINTS for character in part),
            f"bidi control character is forbidden in output path: {raw!r}",
        )
        stem = part.split(".", 1)[0].upper()
        require(stem not in WINDOWS_RESERVED_NAMES, f"Windows reserved name: {part!r}")

    if root is not None:
        root_resolved = root.resolve(strict=True)
        lexical = root
        for part in posix.parts:
            lexical = lexical / part
            if lexical.exists():
                require(not is_link_like(lexical), f"link/junction is forbidden: {raw!r}")
        candidate = (root / Path(*posix.parts)).resolve(strict=False)
        try:
            common = Path(os.path.commonpath((str(root_resolved), str(candidate))))
        except ValueError as exc:
            raise ContractError(f"output path escapes root: {raw!r}") from exc
        require(common == root_resolved, f"output path escapes root: {raw!r}")

    return posix


def collect_disk_files(root: Path) -> dict[str, Path]:
    files: dict[str, Path] = {}
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        for name in directory_names:
            candidate = directory_path / name
            require(not is_link_like(candidate), f"link/junction directory is forbidden: {candidate}")
        for name in file_names:
            candidate = directory_path / name
            relative = candidate.relative_to(root).as_posix()
            validate_relative_path(relative, root)
            require(not is_link_like(candidate), f"link file is forbidden: {relative}")
            lower_name = name.lower()
            require(
                lower_name not in FORBIDDEN_RUNTIME_NAMES,
                f"runtime/secret file is forbidden in final export: {relative}",
            )
            require(
                not any(lower_name.endswith(suffix) for suffix in FORBIDDEN_RUNTIME_SUFFIXES),
                f"partial/session file is forbidden in final export: {relative}",
            )
            files[relative] = candidate
    return files


def scan_for_canaries(files: dict[str, Path], canaries: list[bytes]) -> None:
    for relative, path in files.items():
        content = path.read_bytes()
        for canary in canaries:
            require(
                canary not in content,
                f"secret canary leaked into final export: {relative}",
            )


def require_keys(value: dict[str, Any], keys: set[str], label: str) -> None:
    missing = sorted(keys - set(value))
    require(not missing, f"{label} missing required keys: {', '.join(missing)}")


def validate_result(result: dict[str, Any]) -> dict[str, Any]:
    require_keys(
        result,
        {
            "schema_version",
            "export_id",
            "generated_at",
            "producer",
            "account",
            "scope",
            "messages",
            "assets",
        },
        "result",
    )
    require(result["schema_version"] == "1.0", "unsupported result schema_version")
    require(isinstance(result["export_id"], str) and result["export_id"], "invalid export_id")
    parse_timestamp(result["generated_at"], "result.generated_at")
    require(
        result["producer"] == {"name": "Archive Desk", "version": "0.1.0"},
        "unexpected result producer",
    )

    account = result["account"]
    require(isinstance(account, dict), "result.account must be an object")
    require_keys(account, {"user_id", "display_name", "username"}, "result.account")
    require(
        isinstance(account["user_id"], str) and account["user_id"].isdigit(),
        "account.user_id must be a decimal string",
    )

    scope = result["scope"]
    require(isinstance(scope, dict), "result.scope must be an object")
    require_keys(
        scope,
        {"peer", "date_range", "upper_message_id", "snapshot_semantics"},
        "result.scope",
    )
    require(
        scope["snapshot_semantics"] == "BEST_EFFORT_AS_OBSERVED",
        "invalid snapshot_semantics",
    )
    peer = scope["peer"]
    require(isinstance(peer, dict), "scope.peer must be an object")
    require_keys(peer, {"peer_key", "type", "title", "username"}, "scope.peer")
    peer_key = peer["peer_key"]
    require(isinstance(peer_key, str) and peer_key, "scope.peer.peer_key is invalid")
    upper_message_id = scope["upper_message_id"]
    require(
        isinstance(upper_message_id, int) and upper_message_id >= 0,
        "scope.upper_message_id is invalid",
    )

    messages = result["messages"]
    assets = result["assets"]
    require(isinstance(messages, list), "result.messages must be an array")
    require(isinstance(assets, list), "result.assets must be an array")

    message_ids: set[int] = set()
    asset_references: Counter[str] = Counter()
    assets_by_source_message: dict[int, set[str]] = {}
    for index, message in enumerate(messages):
        label = f"messages[{index}]"
        require(isinstance(message, dict), f"{label} must be an object")
        require_keys(
            message,
            {
                "peer_key",
                "message_id",
                "kind",
                "date",
                "edit_date",
                "sender",
                "text",
                "entities",
                "reply_to_message_id",
                "asset_ids",
            },
            label,
        )
        require(message["peer_key"] == peer_key, f"{label}.peer_key does not match scope")
        message_id = message["message_id"]
        require(
            isinstance(message_id, int) and 1 <= message_id <= upper_message_id,
            f"{label}.message_id is outside snapshot bounds",
        )
        require(message_id not in message_ids, f"duplicate message id: {message_id}")
        message_ids.add(message_id)
        parse_timestamp(message["date"], f"{label}.date")
        if message["edit_date"] is not None:
            parse_timestamp(message["edit_date"], f"{label}.edit_date")
        require(isinstance(message["text"], str), f"{label}.text must be a string")
        require(isinstance(message["entities"], list), f"{label}.entities must be an array")
        utf16_length = len(message["text"].encode("utf-16-le")) // 2
        for entity_index, entity in enumerate(message["entities"]):
            entity_label = f"{label}.entities[{entity_index}]"
            require(isinstance(entity, dict), f"{entity_label} must be an object")
            require_keys(entity, {"type", "offset_utf16", "length_utf16"}, entity_label)
            offset = entity["offset_utf16"]
            length = entity["length_utf16"]
            require(
                isinstance(offset, int) and isinstance(length, int) and offset >= 0 and length >= 0,
                f"{entity_label} has invalid UTF-16 range",
            )
            require(
                offset + length <= utf16_length,
                f"{entity_label} exceeds message UTF-16 length",
            )
        asset_ids = message["asset_ids"]
        require(isinstance(asset_ids, list), f"{label}.asset_ids must be an array")
        require(len(asset_ids) == len(set(asset_ids)), f"{label}.asset_ids contains duplicates")
        for asset_id in asset_ids:
            require(isinstance(asset_id, str) and asset_id, f"{label} has invalid asset id")
            asset_references[asset_id] += 1
        assets_by_source_message[message_id] = set(asset_ids)

    asset_by_id: dict[str, dict[str, Any]] = {}
    for index, asset in enumerate(assets):
        label = f"assets[{index}]"
        require(isinstance(asset, dict), f"{label} must be an object")
        require_keys(
            asset,
            {
                "asset_id",
                "source_message_id",
                "kind",
                "original_name",
                "mime_type",
                "expected_size",
                "status",
                "relative_path",
                "size_bytes",
                "sha256",
                "reason_code",
            },
            label,
        )
        asset_id = asset["asset_id"]
        require(isinstance(asset_id, str) and asset_id, f"{label}.asset_id is invalid")
        require(asset_id not in asset_by_id, f"duplicate asset id: {asset_id}")
        source_message_id = asset["source_message_id"]
        require(source_message_id in message_ids, f"{label} references unknown source message")
        require(
            asset_id in assets_by_source_message[source_message_id],
            f"{label} is not referenced by its source message",
        )
        status = asset["status"]
        require(status in TERMINAL_ASSET_STATUSES, f"{label} has non-terminal status: {status}")
        require(asset_references[asset_id] > 0, f"orphan asset: {asset_id}")
        if status == "DOWNLOADED":
            relative = validate_relative_path(asset["relative_path"])
            require(relative.as_posix().startswith("media/"), f"{label} is outside media/")
            require(
                isinstance(asset["size_bytes"], int) and asset["size_bytes"] >= 0,
                f"{label}.size_bytes is invalid",
            )
            require(
                isinstance(asset["sha256"], str)
                and len(asset["sha256"]) == 64
                and all(char in "0123456789abcdef" for char in asset["sha256"]),
                f"{label}.sha256 is invalid",
            )
            require(asset["reason_code"] is None, f"{label} downloaded asset has a reason")
            if asset["expected_size"] is not None:
                require(
                    asset["expected_size"] == asset["size_bytes"],
                    f"{label} expected and actual sizes differ",
                )
        else:
            require(asset["relative_path"] is None, f"{label} non-download has a local path")
            require(asset["size_bytes"] is None, f"{label} non-download has a local size")
            require(asset["sha256"] is None, f"{label} non-download has a hash")
            require(
                isinstance(asset["reason_code"], str) and asset["reason_code"],
                f"{label} non-download is missing reason_code",
            )
        asset_by_id[asset_id] = asset

    unknown_references = sorted(set(asset_references) - set(asset_by_id))
    require(not unknown_references, f"messages reference unknown assets: {unknown_references}")

    return {
        "export_id": result["export_id"],
        "peer_key": peer_key,
        "messages": messages,
        "assets": assets,
        "asset_by_id": asset_by_id,
    }


def derived_counts(facts: dict[str, Any]) -> dict[str, int]:
    statuses = Counter(asset["status"] for asset in facts["assets"])
    counts = {
        "messages": len(facts["messages"]),
        "assets_discovered": len(facts["assets"]),
        "bytes_downloaded": sum(
            asset["size_bytes"]
            for asset in facts["assets"]
            if asset["status"] == "DOWNLOADED"
        ),
    }
    for status, key in COUNT_KEY_BY_STATUS.items():
        counts[key] = statuses[status]
    return counts


def derived_completeness(facts: dict[str, Any]) -> str:
    statuses = {asset["status"] for asset in facts["assets"]}
    if statuses & {"FAILED", "UNAVAILABLE"}:
        return "PARTIAL"
    if statuses & {"SKIPPED_SIZE", "SKIPPED_POLICY"}:
        return "POLICY_FILTERED"
    return "FULL"


def validate_manifest(
    manifest: dict[str, Any],
    result: dict[str, Any],
    facts: dict[str, Any],
    root: Path,
    disk_files: dict[str, Path],
) -> None:
    require_keys(
        manifest,
        {
            "schema_version",
            "export_id",
            "created_at",
            "producer",
            "execution_status",
            "completeness",
            "config",
            "counts",
            "files",
            "issues",
        },
        "manifest",
    )
    require(manifest["schema_version"] == "1.0", "unsupported manifest schema_version")
    require(manifest["export_id"] == result["export_id"], "result/manifest export_id mismatch")
    parse_timestamp(manifest["created_at"], "manifest.created_at")
    require(manifest["producer"] == result["producer"], "result/manifest producer mismatch")
    require(manifest["execution_status"] == "SUCCEEDED", "final manifest is not SUCCEEDED")
    require(
        manifest["completeness"] == derived_completeness(facts),
        "manifest completeness cannot be derived from asset states",
    )

    config = manifest["config"]
    require(isinstance(config, dict), "manifest.config must be an object")
    require(config.get("peer_key") == facts["peer_key"], "manifest peer_key mismatch")
    require(
        isinstance(config.get("max_file_size"), int) and config["max_file_size"] >= 0,
        "manifest max_file_size must be bytes",
    )

    expected_counts = derived_counts(facts)
    require(manifest["counts"] == expected_counts, "manifest counts are not closed")

    entries = manifest["files"]
    require(isinstance(entries, list), "manifest.files must be an array")
    manifest_paths: dict[str, dict[str, Any]] = {}
    media_asset_ids: set[str] = set()
    for index, entry in enumerate(entries):
        label = f"manifest.files[{index}]"
        require(isinstance(entry, dict), f"{label} must be an object")
        require_keys(entry, {"path", "role", "size_bytes", "sha256", "asset_id"}, label)
        relative = validate_relative_path(entry["path"], root).as_posix()
        require(relative != "manifest.json", "manifest must not list itself")
        require(relative not in manifest_paths, f"duplicate manifest path: {relative}")
        require(relative in disk_files, f"manifest path is missing on disk: {relative}")
        path = disk_files[relative]
        require(path.stat().st_size == entry["size_bytes"], f"size mismatch: {relative}")
        actual_hash = sha256_file(path)
        require(actual_hash == entry["sha256"], f"SHA-256 mismatch: {relative}")
        if entry["role"] == "RESULT":
            require(relative == "result.json", "RESULT role must point to result.json")
            require(entry["asset_id"] is None, "RESULT entry must not have asset_id")
        elif entry["role"] == "MEDIA":
            asset_id = entry["asset_id"]
            require(asset_id in facts["asset_by_id"], f"MEDIA entry has unknown asset: {asset_id}")
            require(asset_id not in media_asset_ids, f"duplicate MEDIA asset entry: {asset_id}")
            asset = facts["asset_by_id"][asset_id]
            require(asset["status"] == "DOWNLOADED", f"MEDIA entry asset is not downloaded: {asset_id}")
            require(asset["relative_path"] == relative, f"MEDIA path mismatch: {asset_id}")
            require(asset["size_bytes"] == entry["size_bytes"], f"MEDIA size mismatch: {asset_id}")
            require(asset["sha256"] == entry["sha256"], f"MEDIA hash mismatch: {asset_id}")
            media_asset_ids.add(asset_id)
        else:
            raise ContractError(f"{label} has unknown role")
        manifest_paths[relative] = entry

    expected_disk_paths = set(disk_files) - {"manifest.json"}
    require(
        set(manifest_paths) == expected_disk_paths,
        "manifest file set does not exactly match final directory",
    )
    require("result.json" in manifest_paths, "manifest does not list result.json")
    downloaded_ids = {
        asset["asset_id"] for asset in facts["assets"] if asset["status"] == "DOWNLOADED"
    }
    require(media_asset_ids == downloaded_ids, "manifest MEDIA entries do not close downloaded assets")

    issues = manifest["issues"]
    require(isinstance(issues, list), "manifest.issues must be an array")
    issue_pairs: set[tuple[str, str]] = set()
    for index, issue in enumerate(issues):
        label = f"manifest.issues[{index}]"
        require(isinstance(issue, dict), f"{label} must be an object")
        require_keys(issue, {"code", "asset_id", "message_id"}, label)
        if issue["asset_id"] is not None:
            require(issue["asset_id"] in facts["asset_by_id"], f"{label} has unknown asset")
            issue_pairs.add((issue["asset_id"], issue["code"]))
    expected_issue_pairs = {
        (asset["asset_id"], asset["reason_code"])
        for asset in facts["assets"]
        if asset["status"] != "DOWNLOADED"
    }
    require(
        expected_issue_pairs <= issue_pairs,
        "one or more non-downloaded assets have no matching manifest issue",
    )


def validate_compatibility_v1(
    result: dict[str, Any],
    manifest: dict[str, Any],
    root: Path,
    disk_files: dict[str, Path],
) -> None:
    """Validate the first implementation's integer schema_version=1 output.

    The implementation predates the normalized 1.0 schemas. This mode remains
    strict about closure, paths, hashes and privacy while allowing its embedded
    message-media representation.
    """

    require(result.get("schema_version") == 1, "unsupported compatibility result schema")
    require(manifest.get("schema_version") == 1, "result/manifest schema version mismatch")
    parse_timestamp(result.get("exported_at"), "result.exported_at")
    parse_timestamp(manifest.get("created_at"), "manifest.created_at")
    parse_timestamp(manifest.get("completed_at"), "manifest.completed_at")
    require(
        isinstance(result.get("source"), dict) and result["source"] == manifest.get("source"),
        "compatibility result/manifest source mismatch",
    )

    messages = result.get("messages")
    require(isinstance(messages, list), "compatibility result.messages must be an array")
    message_by_id: dict[int, dict[str, Any]] = {}
    for index, message in enumerate(messages):
        label = f"messages[{index}]"
        require(isinstance(message, dict), f"{label} must be an object")
        message_id = message.get("id")
        require(isinstance(message_id, int) and message_id > 0, f"{label}.id is invalid")
        require(message_id not in message_by_id, f"duplicate message id: {message_id}")
        require(isinstance(message.get("text"), str), f"{label}.text must be a string")
        parse_timestamp(message.get("date"), f"{label}.date")
        if message.get("edit_date") is not None:
            parse_timestamp(message["edit_date"], f"{label}.edit_date")
        message_by_id[message_id] = message

    entries = manifest.get("files")
    require(isinstance(entries, list), "compatibility manifest.files must be an array")
    completed_paths: set[str] = set()
    seen_assets: set[tuple[int, str]] = set()
    completed = 0
    skipped = 0
    for index, entry in enumerate(entries):
        label = f"manifest.files[{index}]"
        require(isinstance(entry, dict), f"{label} must be an object")
        require_keys(
            entry,
            {
                "message_id",
                "kind",
                "original_name",
                "path",
                "size",
                "sha256",
                "status",
                "skip_reason",
            },
            label,
        )
        message_id = entry["message_id"]
        kind = entry["kind"]
        require(message_id in message_by_id, f"{label} references unknown message")
        require(kind in {"photo", "video", "file"}, f"{label} has unsupported kind")
        identity = (message_id, kind)
        require(identity not in seen_assets, f"duplicate compatibility asset: {identity}")
        seen_assets.add(identity)
        media = message_by_id[message_id].get("media")
        require(isinstance(media, dict), f"{label} source message has no media metadata")
        require(media.get("kind") == kind, f"{label} kind differs from result media")
        require(media.get("status") == entry["status"], f"{label} status differs from result")

        if entry["status"] == "completed":
            relative = validate_relative_path(entry["path"], root).as_posix()
            require(relative.startswith("media/"), f"{label} is outside media/")
            require(relative not in completed_paths, f"duplicate media path: {relative}")
            require(relative in disk_files, f"{label} is missing on disk")
            require(
                isinstance(entry["size"], int) and entry["size"] >= 0,
                f"{label}.size is invalid",
            )
            require(
                isinstance(entry["sha256"], str)
                and len(entry["sha256"]) == 64
                and all(char in "0123456789abcdef" for char in entry["sha256"]),
                f"{label}.sha256 is invalid",
            )
            media_path = disk_files[relative]
            require(media_path.stat().st_size == entry["size"], f"size mismatch: {relative}")
            require(sha256_file(media_path) == entry["sha256"], f"SHA-256 mismatch: {relative}")
            require(media.get("path") == relative, f"{label} path differs from result media")
            require(media.get("sha256") == entry["sha256"], f"{label} hash differs from result")
            require(entry["skip_reason"] is None, f"{label} completed file has skip_reason")
            completed_paths.add(relative)
            completed += 1
        elif entry["status"] == "skipped":
            require(entry["path"] is None, f"{label} skipped file has a path")
            require(entry["sha256"] is None, f"{label} skipped file has a hash")
            require(
                isinstance(entry["skip_reason"], str) and entry["skip_reason"],
                f"{label} skipped file has no reason",
            )
            require(media.get("path") is None, f"{label} result media has a skipped path")
            require(media.get("skip_reason") == entry["skip_reason"], f"{label} skip reason mismatch")
            skipped += 1
        else:
            raise ContractError(f"{label} has non-terminal status")

    counts = manifest.get("counts")
    expected_counts = {
        "messages": len(messages),
        "files_discovered": len(entries),
        "files_downloaded": completed,
        "files_skipped": skipped,
    }
    require(counts == expected_counts, "compatibility manifest counts are not closed")
    expected_completeness = "policy_filtered" if skipped else "full"
    require(
        manifest.get("completeness") == expected_completeness,
        "compatibility manifest completeness is incorrect",
    )
    artifact_paths = {"result.json"}
    artifacts = manifest.get("artifacts")
    if artifacts is not None:
        require(isinstance(artifacts, dict), "compatibility manifest.artifacts must be an object")
        for relative, metadata in artifacts.items():
            require(relative in {"result.json", "index.html"}, f"unsupported artifact: {relative}")
            require(isinstance(metadata, dict), f"artifact metadata is invalid: {relative}")
            require(relative in disk_files, f"artifact is missing on disk: {relative}")
            require(
                metadata.get("size") == disk_files[relative].stat().st_size,
                f"artifact size mismatch: {relative}",
            )
            require(
                metadata.get("sha256") == sha256_file(disk_files[relative]),
                f"artifact hash mismatch: {relative}",
            )
            artifact_paths.add(relative)
        require("result.json" in artifacts, "manifest.artifacts does not list result.json")
    expected_disk_paths = {"manifest.json"} | artifact_paths | completed_paths
    require(
        set(disk_files) == expected_disk_paths,
        "compatibility manifest/media set does not close final directory",
    )


def validate_export(
    export_directory: Path, canary_path: Path = DEFAULT_CANARIES
) -> str:
    root = export_directory.expanduser().resolve(strict=True)
    require(root.is_dir(), f"export path is not a directory: {root}")
    require(not is_link_like(root), "export root must not be a link/junction")

    disk_files = collect_disk_files(root)
    require("result.json" in disk_files, "final export is missing result.json")
    require("manifest.json" in disk_files, "final export is missing manifest.json")
    canaries = load_canaries(canary_path)
    scan_for_canaries(disk_files, canaries)

    result = read_json(root / "result.json")
    manifest = read_json(root / "manifest.json")
    walk_forbidden_keys(result, "$result")
    walk_forbidden_keys(manifest, "$manifest")
    if result.get("schema_version") == 1:
        validate_compatibility_v1(result, manifest, root, disk_files)
        return "compatibility-v1"
    facts = validate_result(result)
    validate_manifest(manifest, result, facts, root, disk_files)
    return "normalized-v1.0"


def resume_from_checkpoint(
    source: Path,
    partial: Path,
    checkpoint_path: Path,
    *,
    chunk_size: int = 31,
) -> None:
    """Reference implementation of the byte-offset checkpoint invariant."""

    checkpoint = read_json(checkpoint_path)
    committed_offset = checkpoint.get("committed_offset")
    require(
        isinstance(committed_offset, int) and committed_offset >= 0,
        "checkpoint committed_offset is invalid",
    )
    require(partial.exists(), "checkpoint references a missing .part")
    require(
        partial.stat().st_size == committed_offset,
        "checkpoint offset does not match .part length",
    )
    require(
        committed_offset <= source.stat().st_size,
        "checkpoint offset is beyond remote source",
    )

    # Re-read the committed prefix before continuing. A production adapter also
    # compares this digest with its last durable prefix digest or safely restarts.
    sha256_file(partial)

    with source.open("rb") as remote, partial.open("ab") as destination:
        remote.seek(committed_offset)
        while True:
            block = remote.read(chunk_size)
            if not block:
                break
            destination.write(block)
            destination.flush()
            os.fsync(destination.fileno())
            committed_offset += len(block)
            write_json(
                checkpoint_path,
                {
                    "asset_id": checkpoint["asset_id"],
                    "committed_offset": committed_offset,
                },
            )


def expect_rejected(label: str, operation: Callable[[], None], contains: str | None = None) -> None:
    try:
        operation()
    except ContractError as exc:
        if contains is not None:
            require(contains.lower() in str(exc).lower(), f"{label} rejected for wrong reason: {exc}")
        print(f"[PASS] rejected {label}: {exc}")
        return
    raise ContractError(f"negative control was accepted: {label}")


def build_valid_fixture(root: Path, media_source: Path, malicious_message: str) -> None:
    media_directory = root / "media"
    media_directory.mkdir(parents=True)
    media_path = media_directory / "asset-00000042.txt"
    shutil.copyfile(media_source, media_path)
    media_hash = sha256_file(media_path)
    media_size = media_path.stat().st_size
    timestamp = "2026-07-10T09:05:00Z"

    result = {
        "schema_version": "1.0",
        "export_id": "export-round1-self-test",
        "generated_at": timestamp,
        "producer": {"name": "Archive Desk", "version": "0.1.0"},
        "account": {
            "user_id": "100000001",
            "display_name": "Fixture User",
            "username": "fixture_user",
        },
        "scope": {
            "peer": {
                "peer_key": "user:200000002",
                "type": "USER",
                "title": "Malicious Fixture",
                "username": None,
            },
            "date_range": {
                "date_from": None,
                "date_to": None,
                "time_zone": "Asia/Shanghai",
                "normalized_start_utc": None,
                "normalized_end_exclusive_utc": None,
            },
            "upper_message_id": 42,
            "snapshot_semantics": "BEST_EFFORT_AS_OBSERVED",
        },
        "messages": [
            {
                "peer_key": "user:200000002",
                "message_id": 42,
                "kind": "MESSAGE",
                "date": timestamp,
                "edit_date": None,
                "sender": {
                    "user_id": "200000002",
                    "display_name": "Fixture Sender",
                    "username": None,
                },
                "text": malicious_message,
                "entities": [],
                "reply_to_message_id": None,
                "asset_ids": ["asset-00000042"],
            }
        ],
        "assets": [
            {
                "asset_id": "asset-00000042",
                "source_message_id": 42,
                "kind": "DOCUMENT",
                "original_name": "../../report.txt:payload.js",
                "mime_type": "text/plain",
                "expected_size": media_size,
                "status": "DOWNLOADED",
                "relative_path": "media/asset-00000042.txt",
                "size_bytes": media_size,
                "sha256": media_hash,
                "reason_code": None,
            }
        ],
    }
    write_json(root / "result.json", result)

    manifest = {
        "schema_version": "1.0",
        "export_id": result["export_id"],
        "created_at": timestamp,
        "producer": result["producer"],
        "execution_status": "SUCCEEDED",
        "completeness": "FULL",
        "config": {
            "peer_key": "user:200000002",
            "date_from": None,
            "date_to": None,
            "time_zone": "Asia/Shanghai",
            "include_photos": True,
            "include_documents": True,
            "max_file_size": 4_294_967_296,
        },
        "counts": {
            "messages": 1,
            "assets_discovered": 1,
            "assets_downloaded": 1,
            "assets_skipped_size": 0,
            "assets_skipped_policy": 0,
            "assets_unavailable": 0,
            "assets_failed": 0,
            "bytes_downloaded": media_size,
        },
        "files": [
            {
                "path": "result.json",
                "role": "RESULT",
                "size_bytes": (root / "result.json").stat().st_size,
                "sha256": sha256_file(root / "result.json"),
                "asset_id": None,
            },
            {
                "path": "media/asset-00000042.txt",
                "role": "MEDIA",
                "size_bytes": media_size,
                "sha256": media_hash,
                "asset_id": "asset-00000042",
            },
        ],
        "issues": [],
    }
    write_json(root / "manifest.json", manifest)


def build_compatibility_fixture(
    root: Path, media_source: Path, malicious_message: str
) -> None:
    media_directory = root / "media"
    media_directory.mkdir(parents=True)
    media_path = media_directory / "42_file_fixture.txt"
    shutil.copyfile(media_source, media_path)
    media_hash = sha256_file(media_path)
    media_size = media_path.stat().st_size
    timestamp = "2026-07-10T09:05:00+00:00"
    source = {
        "account_id": "tg_100000001",
        "dialog_id": "200000002",
        "dialog_title": "Compatibility Fixture",
        "category": "private",
        "date_from": None,
        "date_to": None,
        "timezone": "UTC",
    }
    result = {
        "schema_version": 1,
        "exported_at": timestamp,
        "source": source,
        "messages": [
            {
                "id": 42,
                "date": timestamp,
                "edit_date": None,
                "sender_id": "200000002",
                "text": malicious_message,
                "reply_to_message_id": None,
                "forward": None,
                "media": {
                    "kind": "file",
                    "name": "../../fixture.txt",
                    "mime_type": "text/plain",
                    "size": media_size,
                    "status": "completed",
                    "path": "media/42_file_fixture.txt",
                    "sha256": media_hash,
                    "skip_reason": None,
                },
                "raw": {"entities": [], "out": False},
            }
        ],
    }
    write_json(root / "result.json", result)
    write_json(
        root / "manifest.json",
        {
            "schema_version": 1,
            "job_id": "job-round1-self-test",
            "created_at": timestamp,
            "completed_at": timestamp,
            "completeness": "full",
            "source": source,
            "counts": {
                "messages": 1,
                "files_discovered": 1,
                "files_downloaded": 1,
                "files_skipped": 0,
            },
            "files": [
                {
                    "message_id": 42,
                    "kind": "file",
                    "original_name": "../../fixture.txt",
                    "path": "media/42_file_fixture.txt",
                    "size": media_size,
                    "sha256": media_hash,
                    "status": "completed",
                    "skip_reason": None,
                }
            ],
        },
    )


def copy_case(valid_root: Path, parent: Path, name: str) -> Path:
    destination = parent / name
    shutil.copytree(valid_root, destination)
    return destination


def self_test() -> None:
    malicious = read_json(FIXTURES / "malicious_inputs.json")
    for unsafe_path in malicious["unsafe_output_paths"]:
        expect_rejected(
            f"unsafe path {unsafe_path!r}",
            lambda value=unsafe_path: validate_relative_path(value),
        )

    source = FIXTURES / "resume_source.txt"
    source_hash = sha256_file(source)
    with tempfile.TemporaryDirectory(prefix="archive-desk-round1-") as temp:
        temp_root = Path(temp)
        runtime = temp_root / "runtime"
        runtime.mkdir()

        partial = runtime / "asset-00000042.part"
        checkpoint = runtime / "checkpoint.json"
        prefix = source.read_bytes()[:47]
        partial.write_bytes(prefix)
        write_json(
            checkpoint,
            {"asset_id": "asset-00000042", "committed_offset": len(prefix)},
        )
        resume_from_checkpoint(source, partial, checkpoint, chunk_size=29)
        require(partial.stat().st_size == source.stat().st_size, "resumed file length mismatch")
        require(sha256_file(partial) == source_hash, "resumed file has duplicate or missing bytes")
        print("[PASS] byte-exact resume from durable checkpoint")

        bad_partial = runtime / "bad.part"
        bad_checkpoint = runtime / "bad-checkpoint.json"
        bad_partial.write_bytes(source.read_bytes()[:19])
        write_json(
            bad_checkpoint,
            {"asset_id": "bad", "committed_offset": 20},
        )
        expect_rejected(
            "checkpoint/.part mismatch",
            lambda: resume_from_checkpoint(source, bad_partial, bad_checkpoint),
            "checkpoint offset",
        )

        valid_root = temp_root / "valid-export"
        valid_root.mkdir()
        build_valid_fixture(valid_root, partial, malicious["messages"][0])
        validate_export(valid_root)
        print("[PASS] valid malicious-text export is closed and safe")

        compatibility_root = temp_root / "compatibility-export"
        compatibility_root.mkdir()
        build_compatibility_fixture(compatibility_root, partial, malicious["messages"][1])
        validate_export(compatibility_root)
        print("[PASS] current implementation compatibility export is closed and safe")

        tampered = copy_case(valid_root, temp_root, "tampered-media")
        with (tampered / "media" / "asset-00000042.txt").open("ab") as handle:
            handle.write(b"tamper")
        expect_rejected(
            "tampered media",
            lambda: validate_export(tampered),
            "size mismatch",
        )

        bad_counts = copy_case(valid_root, temp_root, "bad-counts")
        bad_counts_manifest = read_json(bad_counts / "manifest.json")
        bad_counts_manifest["counts"]["messages"] = 2
        write_json(bad_counts / "manifest.json", bad_counts_manifest)
        expect_rejected(
            "non-closed manifest counts",
            lambda: validate_export(bad_counts),
            "counts",
        )

        bad_reference = copy_case(valid_root, temp_root, "bad-reference")
        bad_reference_result = read_json(bad_reference / "result.json")
        bad_reference_result["messages"][0]["asset_ids"].append("missing-asset")
        write_json(bad_reference / "result.json", bad_reference_result)
        expect_rejected(
            "unknown message asset reference",
            lambda: validate_export(bad_reference),
            "unknown assets",
        )

        traversal = copy_case(valid_root, temp_root, "path-traversal")
        traversal_manifest = read_json(traversal / "manifest.json")
        traversal_manifest["files"][1]["path"] = "../outside.txt"
        write_json(traversal / "manifest.json", traversal_manifest)
        expect_rejected(
            "manifest path traversal",
            lambda: validate_export(traversal),
            "dot path",
        )

        lingering_part = copy_case(valid_root, temp_root, "lingering-part")
        (lingering_part / "media" / "stale.part").write_bytes(b"incomplete")
        expect_rejected(
            "lingering .part",
            lambda: validate_export(lingering_part),
            "partial/session",
        )

        leaked_secret = copy_case(valid_root, temp_root, "leaked-secret")
        leaked_result = read_json(leaked_secret / "result.json")
        canary_value = load_canaries(DEFAULT_CANARIES)[0].decode("utf-8")
        leaked_result["messages"][0]["text"] = canary_value
        write_json(leaked_secret / "result.json", leaked_result)
        expect_rejected(
            "secret canary leakage",
            lambda: validate_export(leaked_secret),
            "secret canary",
        )

    print("[PASS] all round-one offline acceptance controls")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate Archive Desk round-one export closure and privacy."
    )
    parser.add_argument(
        "export_directory",
        nargs="?",
        type=Path,
        help="final export directory containing result.json and manifest.json",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="run deterministic offline positive and negative controls",
    )
    parser.add_argument(
        "--canaries",
        type=Path,
        default=DEFAULT_CANARIES,
        help="newline-delimited dummy secrets that must not appear in the export",
    )
    args = parser.parse_args(argv)
    if not args.self_test and args.export_directory is None:
        parser.error("provide an export directory or use --self-test")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.self_test:
            self_test()
        if args.export_directory is not None:
            mode = validate_export(args.export_directory, args.canaries)
            print(
                f"[PASS] valid round-one export ({mode}): "
                f"{args.export_directory.resolve()}"
            )
    except (ContractError, FileNotFoundError, OSError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
