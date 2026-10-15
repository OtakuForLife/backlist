"""SQLite job history and the JSON config in /appdata."""

from __future__ import annotations

import copy
import json
import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from backlist.profiles import (
    DEFAULT_FORMATS,
    DEFAULT_PROFILES,
    coerce_default_profile,
    coerce_formats,
    coerce_library,
    coerce_profiles,
    coerce_works,
)

DEFAULTS = {
    "input_dir": "/input",
    "output_dir": "/output",
    "watcher_enabled": True,
    "stability_seconds": 10,
    "worker_count": 1,
    "existing_behaviour": "skip",
    "delete_source": False,
    "profiles": DEFAULT_PROFILES,
    "formats": DEFAULT_FORMATS,
    "default_profile": "Manga",
    "works": {},
    "library": [],
}
_BEHAVIOURS = {"skip", "overwrite", "rename"}
_INTS = {
    "stability_seconds": (0, 3600),
    "worker_count": (1, 8),
}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
    raise ValueError(f"expected yes or no, got: {value}")


def _abs_path(value) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError("invalid path")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("path must be absolute")
    return str(path)


def coerce_settings(raw: dict) -> dict:
    data = copy.deepcopy(DEFAULTS)
    skip = {"profiles", "formats", "works", "library", "default_profile"}
    data.update({key: raw[key] for key in DEFAULTS if key in raw and key not in skip})
    data["input_dir"] = _abs_path(data["input_dir"])
    data["output_dir"] = _abs_path(data["output_dir"])
    if Path(data["input_dir"]).resolve() == Path(data["output_dir"]).resolve():
        raise ValueError("input and output must be different folders")
    data["watcher_enabled"] = as_bool(data["watcher_enabled"])
    data["delete_source"] = as_bool(data["delete_source"])
    behaviour = str(data["existing_behaviour"]).strip().lower()
    if behaviour not in _BEHAVIOURS:
        raise ValueError("existing file must be skip, overwrite, or rename")
    data["existing_behaviour"] = behaviour
    for key, (low, high) in _INTS.items():
        try:
            number = int(data[key])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} must be a number") from exc
        if not low <= number <= high:
            raise ValueError(f"{key} must be between {low} and {high}")
        data[key] = number
    data["profiles"] = coerce_profiles(raw.get("profiles", data["profiles"]))
    data["formats"] = coerce_formats(raw.get("formats", data["formats"]))
    data["works"] = coerce_works(raw.get("works", data["works"]), data["profiles"])
    if "library" in raw:
        data["library"] = coerce_library(raw.get("library"), data["profiles"])
    else:
        seeded = []
        seen: set[str] = set()
        for item in data["works"].values():
            folded = item["series"].casefold()
            if folded in seen:
                continue
            seen.add(folded)
            seeded.append(item)
        data["library"] = coerce_library(seeded, data["profiles"])
    data["default_profile"] = coerce_default_profile(
        raw.get("default_profile", data["default_profile"]), data["profiles"]
    )
    return data


class Store:
    def __init__(self, appdata: Path):
        self.appdata = Path(appdata)
        self.appdata.mkdir(parents=True, exist_ok=True)
        self.config_path = self.appdata / "config.json"
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.appdata / "jobs.sqlite", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init_db()

    def _init_db(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY,
                series TEXT NOT NULL,
                source_name TEXT NOT NULL,
                source_rel TEXT NOT NULL,
                status TEXT NOT NULL,
                output_name TEXT,
                page_count INTEGER,
                error TEXT,
                fingerprint TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY,
                ts TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL
            );
            """
        )
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(jobs)")}
        if "chapter_name" in columns and "source_name" not in columns:
            self.conn.execute("ALTER TABLE jobs RENAME COLUMN chapter_name TO source_name")
        if "chapter_dir" in columns and "source_rel" not in columns:
            self.conn.execute("ALTER TABLE jobs RENAME COLUMN chapter_dir TO source_rel")
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(jobs)")}
        if "mapping" not in columns:
            self.conn.execute("ALTER TABLE jobs ADD COLUMN mapping TEXT")
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(jobs)")}
        if "chapter_label" in columns:
            self.conn.execute("ALTER TABLE jobs DROP COLUMN chapter_label")
        self.conn.execute("DROP INDEX IF EXISTS idx_jobs_chapter")
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_source_rel ON jobs(source_rel, id DESC)"
        )
        self.conn.commit()

    def settings(self) -> dict:
        stored = None
        if self.config_path.exists():
            try:
                stored = json.loads(self.config_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise ValueError("config.json is not valid JSON") from exc
            if not isinstance(stored, dict):
                raise ValueError("config.json must be an object")
        data = dict(DEFAULTS)
        data["input_dir"] = os.environ.get("INPUT_DIR", data["input_dir"])
        data["output_dir"] = os.environ.get("OUTPUT_DIR", data["output_dir"])
        self._apply_env(data, stored)
        if stored is not None:
            data.update({key: stored[key] for key in DEFAULTS if key in stored})
        return coerce_settings(data)

    @staticmethod
    def _apply_env(data: dict, stored: dict | None) -> None:
        """Env fills keys the saved file does not set. A bad env value cannot hide that file."""

        def take(key: str, raw: str, parse):
            if stored is not None and key in stored:
                return
            try:
                data[key] = parse(raw)
            except ValueError as exc:
                raise ValueError(f"{key} from the environment is invalid") from exc

        if "WATCHER_ENABLED" in os.environ:
            take("watcher_enabled", os.environ["WATCHER_ENABLED"], as_bool)
        if "STABILITY_SECONDS" in os.environ:
            take("stability_seconds", os.environ["STABILITY_SECONDS"], int)
        if "WORKER_COUNT" in os.environ:
            take("worker_count", os.environ["WORKER_COUNT"], int)
        if "EXISTING_FILE_BEHAVIOUR" in os.environ:
            take("existing_behaviour", os.environ["EXISTING_FILE_BEHAVIOUR"], lambda value: value.strip().lower())
        if "DELETE_SOURCE" in os.environ:
            take("delete_source", os.environ["DELETE_SOURCE"], as_bool)

    def save_settings(self, raw: dict) -> dict:
        clean = coerce_settings(raw)
        tmp = self.config_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(clean, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.config_path)
        return clean

    def add_log(self, level: str, message: str) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO logs (ts, level, message) VALUES (?, ?, ?)",
                (_now(), level, message),
            )
            self.conn.execute(
                "DELETE FROM logs WHERE id < (SELECT COALESCE(MAX(id), 0) - 2000 FROM logs)"
            )
            self.conn.commit()

    def logs(self, limit: int = 200) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT ts, level, message FROM logs ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def counts(self) -> dict:
        counts = {key: 0 for key in ("WAITING", "PROCESSING", "SUCCESS", "FAILED", "SKIPPED")}
        with self._lock:
            rows = self.conn.execute("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status")
            for row in rows:
                if row["status"] in counts:
                    counts[row["status"]] = row["n"]
        return counts

    def recent_jobs(self, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM jobs ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def enqueue(
        self,
        series: str,
        source_name: str,
        source_rel: str,
        fingerprint: tuple,
        behaviour: str,
        output_exists: bool,
        mapping: dict,
        force: bool = False,
    ) -> str:
        rel = source_rel
        fp = json.dumps(fingerprint)
        with self._lock:
            if self._has_active(rel):
                return "ignored"
            last = self._latest(rel)
            if output_exists and behaviour == "skip":
                if last and last["status"] in ("SUCCESS", "SKIPPED"):
                    return "ignored"
                self._insert(series, source_name, rel, "SKIPPED", fp, None, mapping)
                return "skipped"
            if (
                not force
                and last
                and last["fingerprint"] == fp
                and last["status"] == "FAILED"
            ):
                return "ignored"
            if (
                not force
                and last
                and last["fingerprint"] == fp
                and last["status"] == "SUCCESS"
                and output_exists
            ):
                return "ignored"
            self._insert(series, source_name, rel, "WAITING", fp, None, mapping)
            return "queued"

    def claim(self) -> dict | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM jobs WHERE status = 'WAITING' ORDER BY id LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            cur = self.conn.execute(
                """UPDATE jobs SET status = 'PROCESSING', updated_at = ?
                   WHERE id = ? AND status = 'WAITING'""",
                (_now(), row["id"]),
            )
            self.conn.commit()
            if cur.rowcount != 1:
                return None
            job = dict(row)
            job["status"] = "PROCESSING"
            return job

    def finish(
        self,
        job_id: int,
        status: str,
        *,
        error: str | None = None,
        output_name: str | None = None,
        page_count: int | None = None,
    ) -> None:
        with self._lock:
            self.conn.execute(
                """UPDATE jobs
                   SET status = ?, error = ?, output_name = COALESCE(?, output_name),
                       page_count = COALESCE(?, page_count), updated_at = ?
                   WHERE id = ?""",
                (status, error, output_name, page_count, _now(), job_id),
            )
            self.conn.commit()

    def retry(self, job_id: int) -> bool:
        with self._lock:
            cur = self.conn.execute(
                """UPDATE jobs SET status = 'WAITING', error = NULL, updated_at = ?
                   WHERE id = ? AND status IN ('FAILED', 'SKIPPED', 'SUCCESS')""",
                (_now(), job_id),
            )
            self.conn.commit()
            return cur.rowcount == 1

    def _has_active(self, source_rel: str) -> bool:
        row = self.conn.execute(
            """SELECT 1 FROM jobs
               WHERE source_rel = ? AND status IN ('WAITING', 'PROCESSING') LIMIT 1""",
            (source_rel,),
        ).fetchone()
        return row is not None

    def _latest(self, source_rel: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM jobs WHERE source_rel = ? ORDER BY id DESC LIMIT 1",
            (source_rel,),
        ).fetchone()
        return dict(row) if row else None

    def _insert(
        self,
        series: str,
        source_name: str,
        source_rel: str,
        status: str,
        fingerprint: str,
        error: str | None,
        mapping: dict | None = None,
    ) -> None:
        ts = _now()
        self.conn.execute(
            """INSERT INTO jobs (
                   series, source_name, source_rel, status,
                   output_name, page_count, error, fingerprint, created_at, updated_at, mapping
               ) VALUES (?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?)""",
            (
                series,
                source_name,
                source_rel,
                status,
                error,
                fingerprint,
                ts,
                ts,
                None if mapping is None else json.dumps(mapping),
            ),
        )
        self.conn.commit()
