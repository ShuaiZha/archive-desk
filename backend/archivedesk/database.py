from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._write_lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._write_lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()

    def _initialize(self) -> None:
        # journal_mode cannot be changed inside the BEGIN IMMEDIATE used by
        # _transaction, so initialize on a standalone, explicitly committed connection.
        with self._write_lock, self._read() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    id TEXT PRIMARY KEY,
                    telegram_user_id INTEGER NOT NULL UNIQUE,
                    session_name TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    username TEXT,
                    phone_masked TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS auth_flows (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    phone_masked TEXT,
                    account_id TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS dialogs (
                    account_id TEXT NOT NULL,
                    id TEXT NOT NULL,
                    peer_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    category TEXT NOT NULL,
                    username TEXT,
                    unread_count INTEGER NOT NULL DEFAULT 0,
                    message_count INTEGER,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (account_id, id),
                    FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS output_roots (
                    id TEXT PRIMARY KEY,
                    path TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS export_jobs (
                    id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    dialog_id TEXT NOT NULL,
                    output_root_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    progress_json TEXT NOT NULL,
                    output_path TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (account_id) REFERENCES accounts(id),
                    FOREIGN KEY (output_root_id) REFERENCES output_roots(id)
                );

                CREATE TABLE IF NOT EXISTS messages (
                    job_id TEXT NOT NULL,
                    dialog_id TEXT NOT NULL,
                    message_id INTEGER NOT NULL,
                    date TEXT NOT NULL,
                    edit_date TEXT,
                    sender_id TEXT,
                    text TEXT NOT NULL,
                    reply_to_message_id INTEGER,
                    forward_json TEXT,
                    media_json TEXT,
                    raw_json TEXT,
                    PRIMARY KEY (job_id, dialog_id, message_id),
                    FOREIGN KEY (job_id) REFERENCES export_jobs(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS assets (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    dialog_id TEXT NOT NULL,
                    message_id INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    original_name TEXT,
                    safe_name TEXT NOT NULL,
                    mime_type TEXT,
                    expected_size INTEGER,
                    status TEXT NOT NULL,
                    bytes_done INTEGER NOT NULL DEFAULT 0,
                    relative_path TEXT,
                    sha256 TEXT,
                    checkpoint_sha256 TEXT,
                    skip_reason TEXT,
                    error TEXT,
                    updated_at TEXT NOT NULL,
                    UNIQUE(job_id, dialog_id, message_id, kind),
                    FOREIGN KEY (job_id) REFERENCES export_jobs(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS job_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (job_id) REFERENCES export_jobs(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS idempotency_keys (
                    account_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (account_id, key),
                    FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE,
                    FOREIGN KEY (job_id) REFERENCES export_jobs(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS ix_events_job_id ON job_events(job_id, id);
                CREATE INDEX IF NOT EXISTS ix_assets_job_status ON assets(job_id, status);
                CREATE INDEX IF NOT EXISTS ix_assets_job_message ON assets(job_id, message_id, id);
                CREATE INDEX IF NOT EXISTS ix_messages_job_date ON messages(job_id, date, message_id);
                DROP INDEX IF EXISTS ux_one_active_job_per_account;
                CREATE UNIQUE INDEX ux_one_active_job_per_account
                ON export_jobs(account_id)
                WHERE status IN ('queued','running','waiting','pausing','paused','cancelling','awaiting_confirmation');
                """
            )
            asset_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(assets)").fetchall()
            }
            if "checkpoint_sha256" not in asset_columns:
                connection.execute("ALTER TABLE assets ADD COLUMN checkpoint_sha256 TEXT")
            connection.commit()

    @staticmethod
    def _dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    def save_auth_flow(
        self,
        flow_id: str,
        status: str,
        phone_masked: str | None,
        *,
        account_id: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO auth_flows(id,status,phone_masked,account_id,error,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    status=excluded.status,
                    phone_masked=excluded.phone_masked,
                    account_id=excluded.account_id,
                    error=excluded.error,
                    updated_at=excluded.updated_at
                """,
                (flow_id, status, phone_masked, account_id, error, now, now),
            )
        return self.get_auth_flow(flow_id)  # type: ignore[return-value]

    def get_auth_flow(self, flow_id: str) -> dict[str, Any] | None:
        with self._read() as connection:
            return self._dict(connection.execute("SELECT * FROM auth_flows WHERE id=?", (flow_id,)).fetchone())

    def delete_auth_flow(self, flow_id: str) -> bool:
        with self._transaction() as connection:
            cursor = connection.execute("DELETE FROM auth_flows WHERE id=?", (flow_id,))
            return cursor.rowcount > 0

    def upsert_account(self, account: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT id,created_at FROM accounts WHERE telegram_user_id=?",
                (account["telegram_user_id"],),
            ).fetchone()
            account_id = existing["id"] if existing else account["id"]
            created_at = existing["created_at"] if existing else now
            connection.execute(
                """
                INSERT INTO accounts(id,telegram_user_id,session_name,display_name,username,phone_masked,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    session_name=excluded.session_name,
                    display_name=excluded.display_name,
                    username=excluded.username,
                    phone_masked=excluded.phone_masked,
                    updated_at=excluded.updated_at
                """,
                (
                    account_id,
                    account["telegram_user_id"],
                    account["session_name"],
                    account["display_name"],
                    account.get("username"),
                    account.get("phone_masked"),
                    created_at,
                    now,
                ),
            )
        return self.get_account(account_id)  # type: ignore[return-value]

    def get_account(self, account_id: str) -> dict[str, Any] | None:
        with self._read() as connection:
            return self._dict(connection.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone())

    def list_accounts(self) -> list[dict[str, Any]]:
        with self._read() as connection:
            rows = connection.execute("SELECT * FROM accounts ORDER BY created_at").fetchall()
            return [dict(row) for row in rows]

    def delete_account(self, account_id: str) -> dict[str, Any] | None:
        account = self.get_account(account_id)
        if account is None:
            return None
        with self._transaction() as connection:
            connection.execute("DELETE FROM accounts WHERE id=?", (account_id,))
        return account

    def replace_dialogs(self, account_id: str, dialogs: list[dict[str, Any]]) -> None:
        now = utc_now()
        with self._transaction() as connection:
            connection.execute("DELETE FROM dialogs WHERE account_id=?", (account_id,))
            for item in dialogs:
                connection.execute(
                    """
                    INSERT INTO dialogs(account_id,id,peer_id,title,category,username,unread_count,message_count,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(account_id,id) DO UPDATE SET
                        peer_id=excluded.peer_id,
                        title=excluded.title,
                        category=excluded.category,
                        username=excluded.username,
                        unread_count=excluded.unread_count,
                        message_count=excluded.message_count,
                        updated_at=excluded.updated_at
                    """,
                    (
                        account_id,
                        item["id"],
                        item["peer_id"],
                        item["title"],
                        item["category"],
                        item.get("username"),
                        item.get("unread_count", 0),
                        item.get("message_count"),
                        now,
                    ),
                )

    def list_dialogs(self, account_id: str) -> list[dict[str, Any]]:
        with self._read() as connection:
            rows = connection.execute(
                "SELECT * FROM dialogs WHERE account_id=? ORDER BY title COLLATE NOCASE", (account_id,)
            ).fetchall()
            return [dict(row) for row in rows]

    def get_dialog(self, account_id: str, dialog_id: str) -> dict[str, Any] | None:
        with self._read() as connection:
            return self._dict(
                connection.execute(
                    "SELECT * FROM dialogs WHERE account_id=? AND id=?", (account_id, dialog_id)
                ).fetchone()
            )

    def add_output_root(self, root_id: str, path: str) -> dict[str, Any]:
        now = utc_now()
        with self._transaction() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO output_roots(id,path,created_at) VALUES(?,?,?)",
                (root_id, path, now),
            )
            row = connection.execute("SELECT * FROM output_roots WHERE path=?", (path,)).fetchone()
            return dict(row)

    def list_output_roots(self) -> list[dict[str, Any]]:
        with self._read() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM output_roots ORDER BY created_at").fetchall()]

    def get_output_root(self, root_id: str) -> dict[str, Any] | None:
        with self._read() as connection:
            return self._dict(connection.execute("SELECT * FROM output_roots WHERE id=?", (root_id,)).fetchone())

    def create_job(self, job: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO export_jobs(
                    id,account_id,dialog_id,output_root_id,status,stage,config_json,
                    progress_json,output_path,error,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    job["id"],
                    job["account_id"],
                    job["dialog_id"],
                    job["output_root_id"],
                    job.get("status", "queued"),
                    job.get("stage", "queued"),
                    json.dumps(job["config"], ensure_ascii=False),
                    json.dumps(job["progress"], ensure_ascii=False),
                    job.get("output_path"),
                    job.get("error"),
                    now,
                    now,
                ),
            )
        return self.get_job(job["id"])  # type: ignore[return-value]

    def get_idempotency_key(self, account_id: str, key: str) -> dict[str, Any] | None:
        with self._read() as connection:
            return self._dict(
                connection.execute(
                    "SELECT * FROM idempotency_keys WHERE account_id=? AND key=?",
                    (account_id, key),
                ).fetchone()
            )

    def save_idempotency_key(
        self, account_id: str, key: str, request_hash: str, job_id: str
    ) -> None:
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO idempotency_keys(account_id,key,request_hash,job_id,created_at)
                VALUES(?,?,?,?,?)
                """,
                (account_id, key, request_hash, job_id, utc_now()),
            )

    def _job(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        value = dict(row)
        value["config"] = json.loads(value.pop("config_json"))
        value["progress"] = json.loads(value.pop("progress_json"))
        return value

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._read() as connection:
            return self._job(connection.execute("SELECT * FROM export_jobs WHERE id=?", (job_id,)).fetchone())

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._read() as connection:
            return [self._job(row) for row in connection.execute("SELECT * FROM export_jobs ORDER BY created_at DESC").fetchall()]  # type: ignore[misc]

    def delete_job(self, job_id: str) -> bool:
        with self._transaction() as connection:
            cursor = connection.execute("DELETE FROM export_jobs WHERE id=?", (job_id,))
            return cursor.rowcount > 0

    def get_active_job_for_account(self, account_id: str) -> dict[str, Any] | None:
        with self._read() as connection:
            row = connection.execute(
                """
                SELECT * FROM export_jobs
                WHERE account_id=?
                  AND status IN ('queued','running','waiting','pausing','paused','cancelling','awaiting_confirmation')
                ORDER BY created_at DESC LIMIT 1
                """,
                (account_id,),
            ).fetchone()
            return self._job(row)

    def update_job(self, job_id: str, **changes: Any) -> dict[str, Any] | None:
        allowed = {"status", "stage", "progress", "output_path", "error"}
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"Unsupported job fields: {sorted(unknown)}")
        assignments: list[str] = []
        values: list[Any] = []
        for key, value in changes.items():
            column = "progress_json" if key == "progress" else key
            if key == "progress":
                value = json.dumps(value, ensure_ascii=False)
            assignments.append(f"{column}=?")
            values.append(value)
        assignments.append("updated_at=?")
        values.append(utc_now())
        values.append(job_id)
        with self._transaction() as connection:
            connection.execute(f"UPDATE export_jobs SET {','.join(assignments)} WHERE id=?", values)
        return self.get_job(job_id)

    def recover_jobs(self) -> None:
        now = utc_now()
        with self._transaction() as connection:
            connection.execute(
                """
                UPDATE export_jobs
                SET status='paused', stage='interrupted', updated_at=?
                WHERE status IN ('queued','running','pausing','waiting','cancelling')
                """,
                (now,),
            )

    def upsert_message(self, job_id: str, dialog_id: str, message: dict[str, Any]) -> bool:
        with self._transaction() as connection:
            exists = connection.execute(
                "SELECT 1 FROM messages WHERE job_id=? AND dialog_id=? AND message_id=?",
                (job_id, dialog_id, message["id"]),
            ).fetchone()
            connection.execute(
                """
                INSERT INTO messages(
                    job_id,dialog_id,message_id,date,edit_date,sender_id,text,
                    reply_to_message_id,forward_json,media_json,raw_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(job_id,dialog_id,message_id) DO UPDATE SET
                    date=excluded.date,
                    edit_date=excluded.edit_date,
                    sender_id=excluded.sender_id,
                    text=excluded.text,
                    reply_to_message_id=excluded.reply_to_message_id,
                    forward_json=excluded.forward_json,
                    media_json=excluded.media_json,
                    raw_json=excluded.raw_json
                """,
                (
                    job_id,
                    dialog_id,
                    message["id"],
                    message["date"],
                    message.get("edit_date"),
                    message.get("sender_id"),
                    message.get("text", ""),
                    message.get("reply_to_message_id"),
                    json.dumps(message.get("forward"), ensure_ascii=False) if message.get("forward") else None,
                    json.dumps(message.get("media"), ensure_ascii=False) if message.get("media") else None,
                    json.dumps(message.get("raw"), ensure_ascii=False) if message.get("raw") else None,
                ),
            )
            return exists is None

    def upsert_messages_batch(
        self, job_id: str, dialog_id: str, messages: list[dict[str, Any]]
    ) -> None:
        if not messages:
            return
        with self._transaction() as connection:
            connection.executemany(
                """
                INSERT INTO messages(
                    job_id,dialog_id,message_id,date,edit_date,sender_id,text,
                    reply_to_message_id,forward_json,media_json,raw_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(job_id,dialog_id,message_id) DO UPDATE SET
                    date=excluded.date,
                    edit_date=excluded.edit_date,
                    sender_id=excluded.sender_id,
                    text=excluded.text,
                    reply_to_message_id=excluded.reply_to_message_id,
                    forward_json=excluded.forward_json,
                    media_json=excluded.media_json,
                    raw_json=excluded.raw_json
                """,
                [
                    (
                        job_id,
                        dialog_id,
                        message["id"],
                        message["date"],
                        message.get("edit_date"),
                        message.get("sender_id"),
                        message.get("text", ""),
                        message.get("reply_to_message_id"),
                        json.dumps(message.get("forward"), ensure_ascii=False)
                        if message.get("forward")
                        else None,
                        json.dumps(message.get("media"), ensure_ascii=False)
                        if message.get("media")
                        else None,
                        json.dumps(message.get("raw"), ensure_ascii=False)
                        if message.get("raw")
                        else None,
                    )
                    for message in messages
                ],
            )

    def upsert_asset(self, asset: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        now = utc_now()
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM assets WHERE job_id=? AND dialog_id=? AND message_id=? AND kind=?",
                (asset["job_id"], asset["dialog_id"], asset["message_id"], asset["kind"]),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO assets(
                        id,job_id,dialog_id,message_id,kind,original_name,safe_name,mime_type,
                        expected_size,status,bytes_done,relative_path,sha256,skip_reason,error,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        asset["id"],
                        asset["job_id"],
                        asset["dialog_id"],
                        asset["message_id"],
                        asset["kind"],
                        asset.get("original_name"),
                        asset["safe_name"],
                        asset.get("mime_type"),
                        asset.get("expected_size"),
                        asset.get("status", "pending"),
                        asset.get("bytes_done", 0),
                        asset.get("relative_path"),
                        asset.get("sha256"),
                        asset.get("skip_reason"),
                        asset.get("error"),
                        now,
                    ),
                )
                row = connection.execute("SELECT * FROM assets WHERE id=?", (asset["id"],)).fetchone()
                return dict(row), True
            return dict(existing), False

    def upsert_assets_batch(self, assets: list[dict[str, Any]]) -> None:
        if not assets:
            return
        now = utc_now()
        with self._transaction() as connection:
            connection.executemany(
                """
                INSERT INTO assets(
                    id,job_id,dialog_id,message_id,kind,original_name,safe_name,mime_type,
                    expected_size,status,bytes_done,relative_path,sha256,skip_reason,error,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(job_id,dialog_id,message_id,kind) DO NOTHING
                """,
                [
                    (
                        asset["id"],
                        asset["job_id"],
                        asset["dialog_id"],
                        asset["message_id"],
                        asset["kind"],
                        asset.get("original_name"),
                        asset["safe_name"],
                        asset.get("mime_type"),
                        asset.get("expected_size"),
                        asset.get("status", "pending"),
                        asset.get("bytes_done", 0),
                        asset.get("relative_path"),
                        asset.get("sha256"),
                        asset.get("skip_reason"),
                        asset.get("error"),
                        now,
                    )
                    for asset in assets
                ],
            )

    def update_asset(self, asset_id: str, **changes: Any) -> dict[str, Any]:
        allowed = {
            "status",
            "bytes_done",
            "expected_size",
            "relative_path",
            "sha256",
            "checkpoint_sha256",
            "skip_reason",
            "error",
        }
        if set(changes) - allowed:
            raise ValueError("Unsupported asset update")
        assignments = [f"{key}=?" for key in changes]
        values = list(changes.values())
        assignments.append("updated_at=?")
        values.extend([utc_now(), asset_id])
        with self._transaction() as connection:
            connection.execute(f"UPDATE assets SET {','.join(assignments)} WHERE id=?", values)
            row = connection.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
            if row is None:
                raise KeyError(asset_id)
            return dict(row)

    def list_assets(self, job_id: str) -> list[dict[str, Any]]:
        with self._read() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM assets WHERE job_id=? ORDER BY message_id", (job_id,)).fetchall()]

    def list_assets_page(
        self,
        job_id: str,
        after_message_id: int = -1,
        after_asset_id: str = "",
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        with self._read() as connection:
            rows = connection.execute(
                """
                SELECT * FROM assets
                WHERE job_id=?
                  AND (message_id>? OR (message_id=? AND id>?))
                ORDER BY message_id,id
                LIMIT ?
                """,
                (job_id, after_message_id, after_message_id, after_asset_id, limit),
            ).fetchall()
            return [dict(row) for row in rows]

    def iter_assets(self, job_id: str, batch_size: int = 500) -> Iterator[dict[str, Any]]:
        with self._read() as connection:
            cursor = connection.execute(
                "SELECT * FROM assets WHERE job_id=? ORDER BY message_id,id", (job_id,)
            )
            while rows := cursor.fetchmany(batch_size):
                for row in rows:
                    yield dict(row)

    def list_messages(self, job_id: str) -> list[dict[str, Any]]:
        with self._read() as connection:
            rows = connection.execute(
                "SELECT * FROM messages WHERE job_id=? ORDER BY date,message_id", (job_id,)
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["id"] = item.pop("message_id")
            item.pop("job_id", None)
            item.pop("dialog_id", None)
            for source, target in (("forward_json", "forward"), ("media_json", "media"), ("raw_json", "raw")):
                raw = item.pop(source)
                item[target] = json.loads(raw) if raw else None
            result.append(item)
        return result

    def iter_messages(self, job_id: str, batch_size: int = 500) -> Iterator[dict[str, Any]]:
        with self._read() as connection:
            cursor = connection.execute(
                "SELECT * FROM messages WHERE job_id=? ORDER BY date,message_id", (job_id,)
            )
            while rows := cursor.fetchmany(batch_size):
                message_ids = [int(row["message_id"]) for row in rows]
                placeholders = ",".join("?" for _ in message_ids)
                asset_by_message: dict[int, dict[str, Any]] = {}
                if message_ids:
                    asset_rows = connection.execute(
                        f"SELECT * FROM assets WHERE job_id=? AND message_id IN ({placeholders})",
                        (job_id, *message_ids),
                    ).fetchall()
                    asset_by_message = {int(row["message_id"]): dict(row) for row in asset_rows}
                for row in rows:
                    item = dict(row)
                    item["id"] = item.pop("message_id")
                    item.pop("job_id", None)
                    item.pop("dialog_id", None)
                    for source, target in (
                        ("forward_json", "forward"),
                        ("media_json", "media"),
                        ("raw_json", "raw"),
                    ):
                        raw = item.pop(source)
                        item[target] = json.loads(raw) if raw else None
                    asset = asset_by_message.get(int(item["id"]))
                    if asset and item.get("media"):
                        item["media"].update(
                            {
                                "status": asset["status"],
                                "path": asset["relative_path"],
                                "sha256": asset["sha256"],
                                "skip_reason": asset["skip_reason"],
                            }
                        )
                    yield item

    def counts(self, job_id: str) -> dict[str, int]:
        with self._read() as connection:
            messages = connection.execute("SELECT COUNT(*) FROM messages WHERE job_id=?", (job_id,)).fetchone()[0]
            row = connection.execute(
                """
                SELECT COUNT(*),
                       COALESCE(SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END),0),
                       COALESCE(SUM(bytes_done),0),
                       COALESCE(SUM(CASE WHEN status!='skipped' AND expected_size IS NOT NULL THEN expected_size ELSE 0 END),0),
                       COALESCE(SUM(CASE WHEN status='skipped' THEN 1 ELSE 0 END),0),
                       COALESCE(SUM(CASE WHEN status NOT IN ('completed','skipped') AND expected_size IS NULL THEN 1 ELSE 0 END),0),
                       COALESCE(SUM(CASE
                           WHEN status NOT IN ('completed','skipped') AND expected_size IS NOT NULL
                           THEN MAX(expected_size-bytes_done,0) ELSE 0 END),0),
                       COALESCE(SUM(CASE WHEN kind='photo' THEN 1 ELSE 0 END),0),
                       COALESCE(SUM(CASE WHEN kind='video' THEN 1 ELSE 0 END),0),
                       COALESCE(SUM(CASE WHEN kind='file' THEN 1 ELSE 0 END),0),
                       COALESCE(SUM(CASE
                           WHEN kind='photo' AND status!='skipped' AND expected_size IS NOT NULL
                           THEN expected_size ELSE 0 END),0),
                       COALESCE(SUM(CASE
                           WHEN kind='video' AND status!='skipped' AND expected_size IS NOT NULL
                           THEN expected_size ELSE 0 END),0),
                       COALESCE(SUM(CASE
                           WHEN kind='file' AND status!='skipped' AND expected_size IS NOT NULL
                           THEN expected_size ELSE 0 END),0),
                       COALESCE(SUM(CASE
                           WHEN kind='photo' AND status NOT IN ('completed','skipped') AND expected_size IS NULL
                           THEN 1 ELSE 0 END),0),
                       COALESCE(SUM(CASE
                           WHEN kind='video' AND status NOT IN ('completed','skipped') AND expected_size IS NULL
                           THEN 1 ELSE 0 END),0),
                       COALESCE(SUM(CASE
                           WHEN kind='file' AND status NOT IN ('completed','skipped') AND expected_size IS NULL
                           THEN 1 ELSE 0 END),0)
                FROM assets WHERE job_id=?
                """,
                (job_id,),
            ).fetchone()
        return {
            "messages_seen": messages,
            "messages_saved": messages,
            "files_total": row[0],
            "files_done": row[1],
            "bytes_done": row[2],
            "bytes_total": row[3],
            "files_skipped": row[4],
            "unknown_size_files": row[5],
            "bytes_remaining": row[6],
            "photos_total": row[7],
            "videos_total": row[8],
            "regular_files_total": row[9],
            "photos_bytes_total": row[10],
            "videos_bytes_total": row[11],
            "regular_files_bytes_total": row[12],
            "photos_unknown_size": row[13],
            "videos_unknown_size": row[14],
            "regular_files_unknown_size": row[15],
        }

    def add_event(self, job_id: str, event_type: str, data: dict[str, Any]) -> int:
        with self._transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO job_events(job_id,type,data_json,created_at) VALUES(?,?,?,?)",
                (job_id, event_type, json.dumps(data, ensure_ascii=False), utc_now()),
            )
            return int(cursor.lastrowid)

    def events_after(self, job_id: str, event_id: int, limit: int = 100) -> list[dict[str, Any]]:
        with self._read() as connection:
            rows = connection.execute(
                "SELECT * FROM job_events WHERE job_id=? AND id>? ORDER BY id LIMIT ?",
                (job_id, event_id, limit),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "type": row["type"],
                "data": json.loads(row["data_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def latest_event_id(self, job_id: str) -> int:
        with self._read() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(id),0) FROM job_events WHERE job_id=?", (job_id,)
            ).fetchone()
            return int(row[0])
