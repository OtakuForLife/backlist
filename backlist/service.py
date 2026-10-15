"""Watch the inbox and queue CBZ jobs."""

from __future__ import annotations

import json
import shutil
from datetime import datetime
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers.polling import PollingObserver

from backlist.chapter import alternate_path, contained_path, path_lock, safe_component
from backlist.convert import (
    check_image,
    fingerprint,
    fingerprint_tree,
    list_pages,
    list_pages_under,
    validate_cbz,
    write_cbz,
)
from backlist.profiles import (
    Unit,
    can_auto,
    cover_relative,
    discover,
    is_cover_name,
    output_from_template,
    chapter_parts,
    existing_cover,
    library_entry,
    optional_int,
    previous_cover,
    profile_named,
    remember_series,
    render_template,
    set_work,
    work_entry,
)
from backlist.store import Store


class _Wake(FileSystemEventHandler):
    def __init__(self, event: threading.Event):
        self._event = event

    def on_any_event(self, event):  # noqa: ANN001 - watchdog signature
        self._event.set()


class Stability:
    def __init__(self):
        self._seen: dict[str, tuple] = {}

    def observe(self, key: str, fp: tuple, now: float, delay: float) -> tuple[str, bool]:
        prev = self._seen.get(key)
        fresh = prev is None or prev[0] != fp
        if fresh:
            self._seen[key] = (fp, now)
            if delay > 0:
                return "changed", True
            return "ready", True
        if now - prev[1] >= delay:
            return "ready", False
        return "waiting", False


class Service:
    def __init__(self, appdata: Path):
        self.store = Store(appdata)
        self.stability = Stability()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._force_scan = False
        self._flag_lock = threading.Lock()
        self._inflight_lock = threading.Lock()
        self._inflight = 0
        self._observer = None
        self._observed: str | None = None
        self._input: str | None = None
        self._units_lock = threading.Lock()
        self._units_cache: list[Unit] | None = None
        self._units_at = 0.0
        self._units_key: str | None = None
        self._units_gen = 0
        self._units_warming = False
        self._watcher_on: bool | None = None
        self._missing_logged: str | None = None
        self._pool: ThreadPoolExecutor | None = None
        self._threads: list[threading.Thread] = []
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._stop.clear()
        self._pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="cbz")
        self._threads = [
            threading.Thread(target=self._dispatcher_loop, name="cbz-dispatch", daemon=True),
            threading.Thread(target=self._scan_loop, name="cbz-scan", daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        self._stop_observer()
        for thread in self._threads:
            thread.join(timeout=5)
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
        self._started = False

    def log(self, level: str, message: str) -> None:
        print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {level:<4}  {message}", flush=True)
        try:
            self.store.add_log(level, message)
        except Exception as exc:  # noqa: BLE001 - logging must not kill a job
            print(f"log persist failed: {exc}", flush=True)

    def request_scan(self) -> None:
        with self._flag_lock:
            self._force_scan = True
        self._wake.set()

    def update_settings(self, raw: dict) -> dict:
        saved = self.store.save_settings(raw)
        self._drop_units()
        self._wake.set()
        return saved

    def _drop_units(self) -> None:
        with self._units_lock:
            self._units_at = 0.0
            self._units_gen += 1

    def _units(self) -> list[Unit]:
        """Inbox walk. A fresh result is reused for two seconds. The walk itself does not hold the lock."""
        settings = self.store.settings()
        key = settings["input_dir"]
        now = time.time()
        with self._units_lock:
            if self._units_cache is not None and self._units_key == key and now - self._units_at < 2:
                return self._units_cache
            self._units_gen += 1
            generation = self._units_gen
        root = Path(key)
        units = discover(root, settings) if root.is_dir() else []
        with self._units_lock:
            if generation == self._units_gen:
                self._units_cache = units
                self._units_at = time.time()
                self._units_key = key
        return units

    def set_watcher(self, enabled: object) -> dict:
        settings = self.store.settings()
        settings["watcher_enabled"] = enabled
        return self.update_settings(settings)

    def state(self) -> dict:
        """Shelf data for the page. Does not walk the inbox; the scanner fills that cache."""
        settings = self.store.settings()
        imports, ready = self._cached_imports(settings)
        return {
            "watcher": "RUNNING" if settings["watcher_enabled"] else "PAUSED",
            "counts": self.store.counts(),
            "settings": settings,
            "jobs": self.store.recent_jobs(),
            "logs": self.store.logs(),
            "imports": imports,
            "imports_ready": ready,
        }

    def _cached_imports(self, settings: dict) -> tuple[list[dict], bool]:
        with self._units_lock:
            units = self._units_cache if self._units_key == settings["input_dir"] else None
            age = time.time() - self._units_at
        paused = not settings.get("watcher_enabled")
        if units is None or (paused and age > 15):
            self._warm_units()
        if units is None:
            return [], False
        return _import_rows(settings, units), True

    def _warm_units(self) -> None:
        """Refresh the inbox cache off the request. The page must not wait for the walk."""
        with self._units_lock:
            if self._units_warming:
                return
            self._units_warming = True

        def run() -> None:
            try:
                self._units()
            except OSError as exc:
                self.log("ERROR", f"input is not readable: {exc}")
            finally:
                with self._units_lock:
                    self._units_warming = False

        threading.Thread(target=run, name="backlist-inbox", daemon=True).start()

    def list_imports(self) -> list[dict]:
        return _import_rows(self.store.settings(), self._units())

    def import_files(self, body: dict) -> str:
        settings = self.store.settings()
        rel, source, root = _source_in_input(settings["input_dir"], str(body.get("path") or ""))
        self._drop_units()
        unit = next((item for item in self._units() if item.source_rel == rel), None)
        profile_name = str(body.get("profile") or (unit.profile_name if unit else "")).strip()
        profile = profile_named(settings, profile_name)
        if profile is None:
            raise ValueError(f"unknown profile: {profile_name}")
        if "series" in body:
            series = str(body.get("series") or "").strip()
        elif unit is not None:
            series = unit.series
        else:
            series = ""
        safe_component(series)
        if source.is_file() and is_cover_name(source.name):
            return self._import_cover(settings, source, root, profile, series)
        if "volume" in body:
            volume = optional_int(body.get("volume"), "volume")
        elif unit is not None:
            volume = unit.volume
        else:
            volume = None
        if "chapter" in body:
            chapter, fraction = chapter_parts(body.get("chapter"))
        elif unit is None:
            chapter, fraction = None, None
        else:
            chapter, fraction = unit.chapter, unit.fraction
        info = {"series": series, "volume": volume, "chapter": chapter, "fraction": fraction}
        dest_rel = render_template(profile["template"], profile, info)
        output_from_template(Path(settings["output_dir"]), dest_rel)
        work = _work_folder(root, source)
        if work:
            set_work(settings, work, profile["name"], series, previous_cover(settings, work))
            remember_series(settings, series, profile["name"])
            self.store.save_settings(settings)
            self._drop_units()
            settings = self.store.settings()
        single = source.is_file()
        recursive = source.is_dir() and "{chapter}" not in profile["template"]
        mapping = {
            "profile": profile["name"],
            "series": series,
            "volume": volume,
            "chapter": chapter,
            "fraction": fraction,
            "dest_rel": dest_rel,
            "recursive": recursive,
            "single": single,
        }
        output_exists = output_from_template(Path(settings["output_dir"]), dest_rel).exists()
        return self.store.enqueue(
            series,
            source.name,
            rel,
            _fingerprint_source(source, recursive),
            settings["existing_behaviour"],
            output_exists,
            mapping,
            force=True,
        )

    def _import_cover(self, settings: dict, source: Path, root: Path, profile: dict, series: str) -> str:
        work = _work_folder(root, source)
        if not work:
            raise ValueError("a cover must sit inside a work folder")
        check_image(source)
        rel = cover_relative(profile["name"], series, source.suffix)
        dest = contained_path(Path(settings["output_dir"]), rel)
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, dest)
        except OSError as exc:
            raise ValueError(f"cover write failed: {exc}") from exc
        check_image(dest)
        set_work(settings, work, profile["name"], series, rel)
        remember_series(settings, series, profile["name"], rel)
        self.store.save_settings(settings)
        self._drop_units()
        try:
            _take_source(source, root)
        except OSError as exc:
            self.log("ERROR", f"could not remove source: {exc}")
        self.store.add_log("INFO", f"Cover {work}")
        return "cover"

    def retry(self, job_id: int) -> bool:
        return self.store.retry(job_id)

    def scan(self, now: float | None = None) -> None:
        settings = self.store.settings()
        root = Path(settings["input_dir"])
        if str(root) != self._input:
            self.stability = Stability()
            self._input = str(root)
        if not root.is_dir():
            message = f"input path is missing: {root}"
            if message != self._missing_logged:
                self.log("ERROR", message)
                self._missing_logged = message
            with self._units_lock:
                self._units_cache = []
                self._units_key = str(root)
                self._units_at = time.time()
            return
        self._missing_logged = None
        moment = time.time() if now is None else now
        try:
            found = self._units()
        except OSError as exc:
            self.log("ERROR", f"input is not readable: {exc}")
            return
        for unit in found:
            if not can_auto(unit):
                continue
            try:
                fp = _fingerprint_source(unit.source, unit.recursive)
            except OSError as exc:
                self.log("ERROR", f"{unit.source_rel}: {exc}")
                continue
            phase, fresh = self.stability.observe(
                unit.source_rel, fp, moment, float(settings["stability_seconds"])
            )
            if fresh:
                self.log("INFO", f"Detected chapter: {unit.source_rel}")
            if phase != "ready":
                continue
            action = self._enqueue_unit(unit, settings, force=False, fp=fp)
            if action == "queued":
                self.log("INFO", f"Chapter is stable: {unit.source_rel}")
            elif action == "skipped":
                self.log("INFO", f"Skip existing: {unit.label}")

    def process_pending(self) -> None:
        while True:
            job = self.store.claim()
            if job is None:
                return
            self.run_job(job)

    def run_job(self, job: dict) -> None:
        try:
            self._convert(job)
        except Exception as exc:  # noqa: BLE001 - a bad chapter must not kill the worker
            self.store.finish(job["id"], "FAILED", error=str(exc))
            self.log("ERROR", f"{job['source_rel']}: {exc}")

    def _enqueue_unit(self, unit: Unit, settings: dict, force: bool, fp: tuple | None = None) -> str:
        output_exists = False
        if unit.dest_rel and not unit.error:
            try:
                output_exists = output_from_template(Path(settings["output_dir"]), unit.dest_rel).exists()
            except ValueError as exc:
                self.log("ERROR", f"{unit.source_rel}: {exc}")
        if fp is None:
            fp = _fingerprint_source(unit.source, unit.recursive)
        return self.store.enqueue(
            unit.series,
            unit.source_name,
            unit.source_rel,
            fp,
            settings["existing_behaviour"],
            output_exists,
            _mapping(unit),
            force=force,
        )

    def _convert(self, job: dict) -> None:
        settings = self.store.settings()
        _rel, source, _root = _source_in_input(settings["input_dir"], job["source_rel"])
        if not source.exists():
            raise ValueError(f"source is missing: {job['source_rel']}")
        mapping = json.loads(job["mapping"]) if job.get("mapping") else None
        if not mapping or not mapping.get("dest_rel"):
            raise ValueError("import mapping is missing")
        if mapping.get("single"):
            pages = [source] if source.is_file() else list_pages(source)
        elif mapping.get("recursive"):
            pages = list_pages_under(source)
        else:
            pages = list_pages(source)
        if not pages:
            raise ValueError("no images found")
        for page in pages:
            check_image(page)
        output_root = Path(settings["output_dir"])
        if not output_root.exists():
            try:
                output_root.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise ValueError(f"output path is missing: {exc}") from exc
        dest = output_from_template(output_root, mapping["dest_rel"])
        behaviour = settings["existing_behaviour"]
        with path_lock(dest):
            if dest.exists() and behaviour == "skip":
                self.store.finish(job["id"], "SKIPPED", output_name=dest.name)
                self.log("INFO", f"Skip existing: {dest.name}")
                return
            if dest.exists() and behaviour == "rename":
                dest = alternate_path(dest)
            self.log("INFO", f"Creating: {dest.name}")
            names = write_cbz(dest, pages, source if mapping.get("recursive") else None)
        self.log("INFO", f"Added {len(pages)} pages")
        self.log("INFO", "CBZ validation successful")
        validate_cbz(dest, names)
        try:
            _take_source(source, Path(settings["input_dir"]))
        except OSError as exc:
            self.log("ERROR", f"could not remove source: {exc}")
        else:
            self.log("INFO", f"Moved source: {job['source_rel']}")
        self.store.finish(
            job["id"],
            "SUCCESS",
            error=None,
            output_name=dest.name,
            page_count=len(pages),
        )
        self.log("INFO", "Job completed")

    def _scan_loop(self) -> None:
        while not self._stop.is_set():
            try:
                settings = self.store.settings()
                enabled = settings["watcher_enabled"]
                if enabled != self._watcher_on:
                    self.log("INFO", "Watcher running" if enabled else "Watcher paused")
                    self._watcher_on = enabled
                self._ensure_observer(settings)
                with self._flag_lock:
                    force = self._force_scan
                    self._force_scan = False
                if enabled or force:
                    self.scan()
            except Exception as exc:  # noqa: BLE001
                self.log("ERROR", f"scan failed: {exc}")
            if self._stop.is_set():
                return
            try:
                settings = self.store.settings()
                timeout = 2 if settings["watcher_enabled"] else None
            except (ValueError, TypeError, sqlite3.Error, OSError) as exc:
                self.log("ERROR", f"settings failed: {exc}")
                timeout = 2
            self._wake.wait(timeout)
            self._wake.clear()

    def _dispatcher_loop(self) -> None:
        while not self._stop.is_set():
            try:
                limit = int(self.store.settings()["worker_count"])
            except (ValueError, TypeError, sqlite3.Error, OSError) as exc:
                self.log("ERROR", f"settings failed: {exc}")
                limit = 1
            with self._inflight_lock:
                busy = self._inflight >= limit
            if busy:
                self._stop.wait(0.2)
                continue
            job = self.store.claim()
            if job is None:
                self._stop.wait(0.3)
                continue
            with self._inflight_lock:
                self._inflight += 1
            pool = self._pool
            if pool is None:
                raise RuntimeError("worker pool is not running")
            pool.submit(self._run, job)

    def _run(self, job: dict) -> None:
        try:
            self.run_job(job)
        finally:
            with self._inflight_lock:
                self._inflight -= 1

    def _ensure_observer(self, settings: dict) -> None:
        # Unraid /mnt/user is FUSE and does not emit inotify. Polling still sees copies.
        if not settings["watcher_enabled"]:
            self._stop_observer()
            return
        path = settings["input_dir"]
        if self._observer is not None and self._observed == path:
            return
        self._stop_observer()
        if not Path(path).is_dir():
            return
        observer = PollingObserver(timeout=2)
        observer.schedule(_Wake(self._wake), path, recursive=True)
        observer.start()
        self._observer = observer
        self._observed = path

    def _stop_observer(self) -> None:
        observer = self._observer
        self._observer = None
        self._observed = None
        if observer is None:
            return
        observer.stop()
        observer.join(timeout=5)


def _mapping(unit: Unit) -> dict:
    return {
        "profile": unit.profile_name,
        "series": unit.series,
        "volume": unit.volume,
        "chapter": unit.chapter,
        "fraction": unit.fraction,
        "dest_rel": unit.dest_rel,
        "recursive": unit.recursive,
        "single": unit.single,
    }


def _fingerprint_source(source: Path, recursive: bool) -> tuple:
    if source.is_file():
        stat = source.stat()
        return ((source.name, stat.st_size, stat.st_mtime_ns),)
    if recursive:
        return fingerprint_tree(source)
    return fingerprint(source)


def _import_rows(settings: dict, units: list[Unit]) -> list[dict]:
    rows = []
    for unit in units:
        if can_auto(unit) or _cover_imported(settings, unit):
            continue
        chapter = "" if unit.chapter is None else str(unit.chapter)
        if unit.fraction:
            chapter += f".{unit.fraction}"
        dest = unit.dest_rel
        error = unit.error
        if unit.cover:
            error = None
            dest = ""
            if unit.profile_name and unit.series:
                dest = cover_relative(unit.profile_name, unit.series, Path(unit.source_name).suffix)
        rows.append(
            {
                "path": unit.source_rel,
                "work": unit.work,
                "series": unit.series,
                "volume": "" if unit.volume is None else str(unit.volume),
                "chapter": chapter,
                "profile": unit.profile_name,
                "dest": dest,
                "single": unit.single,
                "is_cover": unit.cover,
                "error": error,
            }
        )
    return rows



def _cover_imported(settings: dict, unit: Unit) -> bool:
    if not unit.cover:
        return False
    cover = ""
    entry = work_entry(settings, unit.work) if unit.work else None
    if entry and entry.get("cover"):
        cover = entry["cover"]
    if not cover:
        library = library_entry(settings, unit.series or "")
        cover = (library or {}).get("cover") or ""
    return existing_cover(settings, cover) is not None



def _take_source(source: Path, root: Path) -> None:
    """Remove an imported file and any folders it leaves empty. The input root stays."""
    if source.is_file():
        source.unlink()
    elif source.is_dir():
        shutil.rmtree(source)
    else:
        return
    current = source.parent.resolve()
    stop = root.resolve()
    while current != stop and stop in current.parents:
        try:
            if any(current.iterdir()):
                return
            current.rmdir()
        except OSError:
            return
        current = current.parent


def _source_in_input(input_dir: str, path: str) -> tuple[str, Path, Path]:
    root = Path(input_dir).resolve()
    source = contained_path(root, path)
    if not source.exists():
        raise ValueError(f"source is missing: {path}")
    return source.relative_to(root).as_posix(), source, root


def _work_folder(root: Path, source: Path) -> str:
    if source == root or root not in source.parents:
        return ""
    folder = root / source.relative_to(root).parts[0]
    if folder.is_dir() and (source == folder or folder in source.parents):
        return folder.name
    return ""

