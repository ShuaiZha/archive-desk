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


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    host: str = "127.0.0.1"
    port: int = 8000

    @classmethod
    def from_env(cls) -> "Settings":
        host = os.environ.get("ARCHIVEDESK_HOST", "127.0.0.1")
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("ARCHIVEDESK_HOST must be a loopback address")
        return cls(
            data_dir=Path(os.environ.get("ARCHIVEDESK_DATA_DIR", _default_data_dir())),
            host=host,
            port=int(os.environ.get("ARCHIVEDESK_PORT", "8000")),
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
        if os.name != "nt":
            self.data_dir.chmod(0o700)
            self.session_dir.chmod(0o700)
