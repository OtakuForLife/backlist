"""Series browse, page and cover reads, and structure commit. Import and add-series record rows through remember_series and set_work."""

from __future__ import annotations

import re
import shutil
import threading
import time
import zipfile
from pathlib import Path

from backlist.chapter import IMAGE_EXTENSIONS, contained_path, natural_key, path_lock, safe_component
from backlist.convert import validate_cbz
from backlist.profiles import (
    chapter_parts,
    cover_relative,
    existing_cover,
    is_cover_name,
    library_entry,
    optional_int,
    parse_file_numbers,
    profile_named,
    remember_series,
    render_template,
    set_work,
)
from backlist.store import Store

_file_cache: dict = {}
_file_cache_gen = 0
_file_cache_lock = threading.Lock()


def _clear_file_cache() -> None:
    global _file_cache_gen
    with _file_cache_lock:
        _file_cache.clear()
        _file_cache_gen += 1


def add_series(store: Store, body: dict) -> dict:
    settings = store.settings()
    series = str(body.get("series") or "").strip()
    safe_component(series)
    profile_name = str(body.get("profile") or settings.get("default_profile") or "").strip()
    profile = profile_named(settings, profile_name)
    if profile is None:
        raise ValueError(f"unknown profile: {profile_name}")
    remember_series(settings, series, profile["name"])
    store.save_settings(settings)
    return store.settings()


def commit_structure(store: Store, body: dict) -> dict:
    """Rename or merge library CBZs so each path matches the profile template, and update the stored cover."""
    settings = store.settings()
    series = str(body.get("series") or "").strip()
    safe_component(series)
    entry = library_entry(settings, series)
    if entry is None:
        raise ValueError(f"unknown series: {series}")
    profile = profile_named(settings, str(body.get("profile") or "").strip())
    if profile is None:
        raise ValueError(f"unknown profile: {body.get('profile')}")
    _clear_file_cache()
    files = _series_files(settings, entry)
    output = Path(settings["output_dir"]).resolve()
    renames, merges, new_cover = _plan_structure(
        output, entry, profile, files, _assignment_map(body.get("files"), files)
    )
    locked = sorted({dest.resolve() for dest, _sources in merges} | {dest.resolve() for _src, dest, _old, _new in renames}, key=str)
    held = [path_lock(dest) for dest in locked]
    for lock in held:
        lock.acquire()
    try:
        for dest, sources in merges:
            _merge_cbzs(dest, sources)
            for src in sources:
                _prune_empty(src.parent, output)
        _apply_moves([(src, dest) for src, dest, _old, _new in renames])
        for src, _dest, _old, _new in renames:
            _prune_empty(src.parent, output)
    finally:
        for lock in reversed(held):
            lock.release()
    old_cover = entry.get("cover") or ""
    remember_series(
        settings,
        entry["series"],
        profile["name"],
        None if new_cover == old_cover else new_cover,
    )
    for work in (settings.get("works") or {}).values():
        if str(work.get("series", "")).casefold() != entry["series"].casefold():
            continue
        work["profile"] = profile["name"]
        if old_cover and work.get("cover") == old_cover:
            work["cover"] = new_cover
    store.save_settings(settings)
    if renames or merges or profile["name"] != entry["profile"]:
        store.add_log("INFO", f"Structure {entry['series']}")
    _clear_file_cache()
    return series_browse(store, entry["series"])


def series_browse(store: Store, series: str) -> dict:
    settings = store.settings()
    entry = library_entry(settings, series)
    if entry is None:
        raise ValueError(f"unknown series: {series}")
    files = _listed_files(settings, entry)
    return {
        "series": entry["series"],
        "profile": entry["profile"],
        "has_cover": cover_file(store, entry["series"]) is not None,
        "tree": _series_tree(entry["series"], files),
        "files": files,
    }


def series_pages(store: Store, series: str, rel: str) -> list[str]:
    dest = _series_file(store, series, rel)
    if dest.suffix.lower() != ".cbz":
        raise ValueError("not a cbz")
    try:
        with zipfile.ZipFile(dest) as archive:
            names = [name for name in archive.namelist() if _zip_image(name)]
    except (zipfile.BadZipFile, OSError) as exc:
        raise ValueError(f"{dest.name} is not a valid CBZ") from exc
    return sorted(names, key=natural_key)


def series_image(store: Store, series: str, rel: str, page: str | None) -> tuple[bytes, str]:
    dest = _series_file(store, series, rel)
    if dest.suffix.lower() == ".cbz":
        if not page:
            raise ValueError("page is required")
        if page not in series_pages(store, series, rel):
            raise ValueError("page is not in the cbz")
        try:
            with zipfile.ZipFile(dest) as archive:
                return archive.read(page), _media_type(page)
        except (zipfile.BadZipFile, OSError) as exc:
            raise ValueError(f"{dest.name} is not a valid CBZ") from exc
    if page:
        raise ValueError("page is only valid for a cbz")
    if dest.suffix.lower() not in IMAGE_EXTENSIONS:
        raise ValueError("not an image")
    try:
        return dest.read_bytes(), _media_type(dest.name)
    except OSError as exc:
        raise ValueError(f"{dest.name} is not a readable image") from exc


def cover_file(store: Store, series: str) -> Path | None:
    settings = store.settings()
    entry = library_entry(settings, series)
    if entry is None:
        raise ValueError(f"unknown series: {series}")
    return existing_cover(settings, entry.get("cover") or "")


def _series_file(store: Store, series: str, rel: str) -> Path:
    settings = store.settings()
    entry = library_entry(settings, series)
    if entry is None:
        raise ValueError(f"unknown series: {series}")
    text = str(rel or "").replace("\\", "/").strip()
    if not text or text.startswith("/") or any(part in {"", ".", ".."} for part in text.split("/")):
        raise ValueError("unsafe path")
    allowed = {item["path"] for item in _listed_files(settings, entry)}
    if text not in allowed:
        raise ValueError("file is outside the series")
    dest = contained_path(Path(settings["output_dir"]), text)
    if not dest.is_file():
        raise ValueError(f"file is missing: {Path(text).name}")
    return dest


def _listed_files(settings: dict, entry: dict) -> list[dict]:
    """Reuse a series listing for a few seconds. A structure commit clears it."""
    key = (settings["output_dir"], entry["profile"], entry["series"])
    now = time.time()
    with _file_cache_lock:
        generation = _file_cache_gen
        hit = _file_cache.get(key)
        if hit and now - hit[0] < 3 and hit[2] == generation:
            return hit[1]
    files = _series_files(settings, entry)
    with _file_cache_lock:
        if _file_cache_gen == generation:
            _file_cache[key] = (now, files, generation)
    return files


def _number_fields(name: str, formats: dict) -> tuple[str, str]:
    info = parse_file_numbers(name, formats)
    chapter = ""
    if info["chapter"] is not None:
        chapter = str(info["chapter"])
        if info.get("fraction"):
            chapter += f".{info['fraction']}"
    volume = "" if info["volume"] is None else str(info["volume"])
    return volume, chapter


def _assignment_map(raw, files: list[dict]) -> dict[str, dict]:
    if not isinstance(raw, list):
        raise ValueError("files must be a list")
    assignments: dict[str, dict] = {}
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("each file must be an object")
        path = str(item.get("path") or "").replace("\\", "/")
        if path in assignments:
            raise ValueError("duplicate file")
        assignments[path] = item
    expected = {item["path"] for item in files if item["kind"] == "cbz"}
    if set(assignments) != expected:
        raise ValueError("every chapter file is required")
    return assignments


def _chapter_sort_key(item: tuple[str, int | None, str]) -> tuple:
    fraction = str(item[2])
    numeric = fraction.isdigit()
    return (
        item[1] is None,
        item[1] if item[1] is not None else 0,
        numeric,
        int(fraction) if numeric else 0,
        item[0],
    )


def _plan_cbz_moves(output: Path, series: str, profile: dict, assignments: dict[str, dict]):
    groups: dict[str, list[tuple[str, int | None, str]]] = {}
    for path, fields in assignments.items():
        volume = optional_int(fields.get("volume"), "volume")
        chapter, fraction = chapter_parts(fields.get("chapter"))
        info = {"series": series, "volume": volume, "chapter": chapter, "fraction": fraction}
        dest_rel = render_template(profile["template"], profile, info)
        groups.setdefault(dest_rel, []).append((path, chapter, fraction or ""))
    for group in groups.values():
        group.sort(key=_chapter_sort_key)
    renames: list[tuple[Path, Path, str, str]] = []
    merges: list[tuple[Path, list[Path]]] = []
    merge_sources: set[Path] = set()
    for dest_rel, group in groups.items():
        dest = contained_path(output, dest_rel)
        sources = []
        for old_rel, _chapter, _fraction in group:
            src = contained_path(output, old_rel)
            if src.is_symlink() or not src.is_file():
                raise ValueError(f"file is missing: {Path(old_rel).name}")
            sources.append(src)
        if len(sources) == 1:
            if sources[0] != dest:
                renames.append((sources[0], dest, group[0][0], dest_rel))
            continue
        if dest.exists() and dest.resolve() not in {src.resolve() for src in sources}:
            raise ValueError(f"already exists: {dest.name}")
        merges.append((dest, sources))
        merge_sources.update(src.resolve() for src in sources)
    return renames, merges, merge_sources, groups


def _plan_other_moves(output: Path, entry: dict, profile: dict, files: list[dict], groups: dict):
    series = entry["series"]
    old_profile = entry["profile"]
    planned: list[tuple[str, str]] = []
    seen: dict[str, str] = {}

    def take(old_rel: str, new_rel: str) -> None:
        if new_rel in seen and seen[new_rel] != old_rel:
            raise ValueError(f"two files would become {Path(new_rel).name}")
        if new_rel in groups:
            raise ValueError(f"two files would become {Path(new_rel).name}")
        seen[new_rel] = old_rel
        if new_rel != old_rel:
            planned.append((old_rel, new_rel))

    for item in files:
        if item["kind"] == "cbz":
            continue
        new_rel = item["path"]
        if item.get("is_cover") or item["path"] == (entry.get("cover") or ""):
            new_rel = cover_relative(profile["name"], series, Path(item["name"]).suffix)
        elif profile["name"] != old_profile:
            parts = item["path"].split("/")
            if parts and parts[0] == old_profile:
                new_rel = "/".join([profile["name"], *parts[1:]])
        take(item["path"], new_rel)
    renames: list[tuple[Path, Path, str, str]] = []
    for old_rel, new_rel in planned:
        src = contained_path(output, old_rel)
        dest = contained_path(output, new_rel)
        if src.is_symlink() or not src.is_file():
            raise ValueError(f"file is missing: {Path(old_rel).name}")
        renames.append((src, dest, old_rel, new_rel))
    return renames, planned


def _plan_structure(output: Path, entry: dict, profile: dict, files: list[dict], assignments: dict[str, dict]):
    """Renames, CBZ merges, and the cover path to store. Sources stay put until apply."""
    cbz_renames, merges, merge_sources, groups = _plan_cbz_moves(output, entry["series"], profile, assignments)
    other_renames, planned = _plan_other_moves(output, entry, profile, files, groups)
    renames = cbz_renames + other_renames
    rename_sources = {src.resolve() for src, _dest, _old, _new in renames}
    merge_dests = {dest.resolve() for dest, _sources in merges}
    for _src, dest, _old, new_rel in renames:
        resolved = dest.resolve()
        blocks_merge = resolved in merge_dests
        blocks_existing = dest.exists() and resolved not in rename_sources and resolved not in merge_sources
        if blocks_merge or blocks_existing:
            raise ValueError(f"already exists: {Path(new_rel).name}")
    old_cover = entry.get("cover") or ""
    new_cover = old_cover
    for old, new in planned:
        if old_cover:
            if old == old_cover:
                new_cover = new
        elif is_cover_name(Path(old).name):
            new_cover = new
            break
    return renames, merges, new_cover


def _merge_cbzs(dest: Path, sources: list[Path]) -> None:
    """One CBZ, pages in source order, numbered so a reader keeps that order."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f"{dest.name}.{threading.get_ident()}.partial")
    try:
        listed: list[tuple[Path, str]] = []
        for source in sources:
            with zipfile.ZipFile(source) as archive:
                pages = [name for name in archive.namelist() if _zip_image(name)]
                pages.sort(key=natural_key)
                listed.extend((source, page) for page in pages)
        if not listed:
            raise ValueError(f"no pages to merge into {dest.name}")
        width = max(4, len(str(len(listed))))
        names: list[str] = []
        opened: dict[Path, zipfile.ZipFile] = {}
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_STORED) as out:
            try:
                for number, (source, page) in enumerate(listed, start=1):
                    archive = opened.get(source)
                    if archive is None:
                        archive = zipfile.ZipFile(source)
                        opened[source] = archive
                    name = f"{number:0{width}d}{Path(page).suffix.lower() or '.jpg'}"
                    names.append(name)
                    out.writestr(name, archive.read(page))
            finally:
                for archive in opened.values():
                    archive.close()
        validate_cbz(tmp, names)
        tmp.replace(dest)
    except ValueError:
        tmp.unlink(missing_ok=True)
        raise
    except (OSError, zipfile.BadZipFile) as exc:
        tmp.unlink(missing_ok=True)
        raise ValueError(f"could not merge into {dest.name}: {exc}") from exc
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    for source in sources:
        if source.resolve() == dest.resolve():
            continue
        source.unlink(missing_ok=True)


def _same_bytes(left: Path, right: Path) -> bool:
    if left.stat().st_size != right.stat().st_size:
        return False
    with left.open("rb") as src, right.open("rb") as dest:
        while True:
            a = src.read(1024 * 1024)
            b = dest.read(1024 * 1024)
            if a != b:
                return False
            if not a:
                return True


def _present(path: Path) -> bool:
    """Directory listing, not exists(). A bind mount can report a leftover name as gone."""
    try:
        return path.name in {item.name for item in path.parent.iterdir()}
    except OSError:
        return False


def _ensure_gone(path: Path) -> None:
    # ponytail: a few short retries. Upgrade path is an OS rename that cannot copy.
    last: OSError | None = None
    for _ in range(6):
        if not _present(path):
            return
        try:
            path.unlink()
        except OSError as exc:
            last = exc
        if not _present(path):
            return
        time.sleep(0.05)
    raise OSError(f"could not remove {path.name}") from last


def _rename(src: Path, dest: Path) -> None:
    """Copy, then delete the source.

    Docker Desktop bind mounts can rename by copying and leave the old name
    even when exists() says it is gone. If the old name will not go, the new
    copy is removed so a commit cannot keep both.
    """
    if src.resolve() == dest.resolve():
        return
    created = not _present(dest)
    if not created and not _same_bytes(src, dest):
        raise OSError(f"could not rename {src.name}")
    if created:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        if not _same_bytes(src, dest):
            dest.unlink(missing_ok=True)
            raise OSError(f"could not rename {src.name}")
    try:
        _ensure_gone(src)
    except OSError:
        if created:
            dest.unlink(missing_ok=True)
        raise


def _apply_moves(pairs: list[tuple[Path, Path]]) -> None:
    staged: list[tuple[Path, Path, Path]] = []
    try:
        for index, (src, dest) in enumerate(pairs):
            temp = src.with_name(f".backlist-move-{index}{src.suffix}")
            if temp.exists():
                raise ValueError(f"temporary name is in use: {temp.name}")
            _rename(src, temp)
            staged.append((src, temp, dest))
        for _src, temp, dest in staged:
            dest.parent.mkdir(parents=True, exist_ok=True)
            _rename(temp, dest)
    except Exception as exc:
        rollback_error: OSError | None = None
        failed_temp = ""
        for src, temp, dest in reversed(staged):
            try:
                if dest.is_file() and not src.exists() and not temp.exists():
                    _rename(dest, src)
                elif temp.exists() and not src.exists():
                    _rename(temp, src)
            except OSError as err:
                rollback_error = err
                failed_temp = temp.name
        if rollback_error is not None:
            raise ValueError(f"{exc}; rollback failed for {failed_temp}") from exc
        if isinstance(exc, ValueError):
            raise
        raise ValueError(str(exc)) from exc


def _prune_empty(start: Path, stop: Path) -> None:
    current = start
    while current != stop and stop in current.parents:
        try:
            if not current.is_dir() or any(current.iterdir()):
                return
            current.rmdir()
        except OSError:
            return
        current = current.parent



def _series_files(settings: dict, entry: dict) -> list[dict]:
    output = Path(settings["output_dir"]).resolve()
    profile = entry["profile"]
    series = entry["series"]
    safe_component(profile)
    safe_component(series)
    found: list[dict] = []
    seen: set[str] = set()

    def add(path: Path) -> None:
        resolved = path.resolve()
        if output not in resolved.parents or not resolved.is_file() or path.is_symlink():
            return
        rel = resolved.relative_to(output).as_posix()
        if any(part.startswith(".") for part in rel.split("/")):
            return
        if rel in seen:
            return
        seen.add(rel)
        suffix = resolved.suffix.lower()
        if suffix == ".cbz":
            kind = "cbz"
        elif suffix in IMAGE_EXTENSIONS:
            kind = "image"
        else:
            kind = "file"
        volume, chapter = _number_fields(resolved.name, settings["formats"])
        found.append(
            {
                "name": resolved.name,
                "path": rel,
                "kind": kind,
                "size": resolved.stat().st_size,
                "volume": volume,
                "chapter": chapter,
                "is_cover": is_cover_name(resolved.name),
            }
        )

    try:
        series_dir = (output / profile / series).resolve()
        if output in series_dir.parents and series_dir.is_dir():
            for path in series_dir.rglob("*"):
                if path.is_file():
                    add(path)
        profile_dir = (output / profile).resolve()
        if output in profile_dir.parents and profile_dir.is_dir():
            row = profile_named(settings, profile)
            template = row["template"] if row else "{series}.cbz"
            for path in profile_dir.iterdir():
                if path.is_file() and _flat_name_matches(template, series, path.name):
                    add(path)
    except OSError as exc:
        raise ValueError(f"could not list {series}: {exc}") from exc
    found.sort(key=lambda item: natural_key(item["path"]))
    return found


def _series_tree(series: str, files: list[dict]) -> dict:
    root = {"name": series, "kind": "dir", "children": []}
    for item in files:
        parts = item["path"].split("/")
        if len(parts) > 1:
            parts = parts[1:]
        if parts and parts[0].casefold() == series.casefold():
            parts = parts[1:]
        if not parts:
            continue
        node = root
        for folder in parts[:-1]:
            child = next((one for one in node["children"] if one["kind"] == "dir" and one["name"] == folder), None)
            if child is None:
                child = {"name": folder, "kind": "dir", "children": []}
                node["children"].append(child)
            node = child
        node["children"].append(
            {"name": parts[-1], "kind": item["kind"], "path": item["path"], "size": item["size"]}
        )
    return root


def _zip_image(name: str) -> bool:
    if not name or name.endswith("/") or "\\" in name or name.startswith("/"):
        return False
    parts = name.split("/")
    if any(part in {"", ".", ".."} or part.startswith(".") for part in parts):
        return False
    return Path(name).suffix.lower() in IMAGE_EXTENSIONS


def _media_type(name: str) -> str:
    suffix = Path(name).suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".png":
        return "image/png"
    if suffix == ".webp":
        return "image/webp"
    if suffix == ".gif":
        return "image/gif"
    return "application/octet-stream"


def _flat_name_matches(template: str, series: str, filename: str) -> bool:
    """True when filename is that template's file for this series, not a longer title."""
    pattern = template.replace("\\", "/").rsplit("/", 1)[-1]
    head, sep, tail = pattern.partition("{series}")
    if not sep:
        return filename.casefold() == series.casefold()
    literal = tail.split("{", 1)[0]
    prefix = head + series + literal
    if not filename.casefold().startswith(prefix.casefold()):
        return False
    rest = filename[len(prefix) :]
    remainder = tail[len(literal) :]
    escaped = re.escape(remainder).replace(re.escape("{volume}"), r"\d+").replace(
        re.escape("{chapter}"), r"\d+(?:\.\d+)?"
    )
    return re.fullmatch(escaped, rest, re.IGNORECASE) is not None

