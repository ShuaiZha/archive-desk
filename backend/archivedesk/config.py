from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _default_data_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "ArchiveDesk"
    return Path.home() / ".local" / "share" / "archivedesk"


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _optional_path(name: str) -> Path | None:
    value = os.environ.get(name, "").strip()
    return Path(value) if value else None


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    host: str = "127.0.0.1"
    port: int = 8000
    container_mode: bool = False
    static_dir: Path | None = None
    default_output_root: Path | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        container_mode = _env_flag("ARCHIVEDESK_CONTAINER")
        host = os.environ.get("ARCHIVEDESK_HOST", "127.0.0.1")
        allowed_hosts = {"127.0.0.1", "localhost", "::1"}
        if container_mode:
            allowed_hosts.update({"0.0.0.0", "::"})
        if host not in allowed_hosts:
            raise ValueError(
                "ARCHIVEDESK_HOST must be a loopback address, or a wildcard address in container mode"
            )
        return cls(
            data_dir=Path(os.environ.get("ARCHIVEDESK_DATA_DIR", _default_data_dir())),
            host=host,
            port=int(os.environ.get("ARCHIVEDESK_PORT", "8000")),
            container_mode=container_mode,
            static_dir=_optional_path("ARCHIVEDESK_STATIC_DIR"),
            default_output_root=_optional_path("ARCHIVEDESK_DEFAULT_OUTPUT_ROOT"),
        )

    @property
    def database_path(self) -> Path:
        return self.data_dir / "archivedesk.sqlite3"

    @property
    def secret_path(self) -> Path:
        return self.data_dir / "credentials.bin"

    @property
    def session_dir(self) -> Path:
        return self.data_dir / "sessions"

    def prepare(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.session_dir.mkdir(parents=True, exist_ok=True)
        if self.default_output_root is not None:
            self.default_output_root.mkdir(parents=True, exist_ok=True)
            probe = self.default_output_root / f".archivedesk-container-write-test-{os.getpid()}"
            try:
                with probe.open("x", encoding="utf-8"):
                    pass
            finally:
                probe.unlink(missing_ok=True)
        if self.static_dir is not None and not (self.static_dir / "index.html").is_file():
            raise ValueError("ARCHIVEDESK_STATIC_DIR must contain index.html")
        if os.name != "nt":
            self.data_dir.chmod(0o700)
            self.session_dir.chmod(0o700)
