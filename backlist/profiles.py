"""Folder profiles and the output-name templates stored in config."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from backlist.chapter import (
    CHAPTER_PATTERNS,
    IMAGE_EXTENSIONS,
    VOLUME_PATTERN,
    contained_path,
    natural_key,
    safe_component,
)
_TOKENS = {"profile", "series", "volume", "chapter"}
_TOKEN = re.compile(r"\{([a-z_]+)\}")

DEFAULT_PROFILES = [
    {
        "name": "Manga",
        "template": "{profile}/{series}/{series} Volume {volume} Chapter {chapter}.cbz",
        "volume_padding": 1,
        "chapter_padding": 4,
        "default_volume": "1",
    },
    {
        "name": "Novel",
        "template": "{profile}/{series} Volume {volume}.cbz",
        "volume_padding": 1,
        "chapter_padding": 4,
        "default_volume": "",
    },
]

DEFAULT_FORMATS = {
    "volume_pattern": VOLUME_PATTERN,
    "chapter_patterns": list(CHAPTER_PATTERNS),
}


@dataclass
class Unit:
    source_rel: str
    series: str
    source_name: str
    source: Path
    dest_rel: str
    label: str
    recursive: bool
    error: str | None = None
    work: str = ""
    volume: int | None = None
    chapter: int | None = None
    fraction: str | None = None
    profile_name: str = ""
    single: bool = False
    known: bool = False
    cover: bool = False


_COVER_STEMS = {"cover", "folder", "poster"}


def is_cover_name(name: str) -> bool:
    path = Path(name)
    return path.stem.casefold() in _COVER_STEMS and path.suffix.lower() in IMAGE_EXTENSIONS


def cover_relative(profile: str, series: str, suffix: str) -> str:
    """``{profile}/{series}/cover{suffix}`` inside the output root."""
    safe_component(profile)
    safe_component(series)
    ext = suffix.lower()
    if ext not in IMAGE_EXTENSIONS:
        raise ValueError("cover must be an image")
    return f"{profile}/{series}/cover{ext}"


def clean_cover(raw: object) -> str:
    text = str(raw or "").strip().replace("\\", "/")
    if not text:
        return ""
    parts = [part for part in text.split("/") if part and part != "."]
    if not parts or any(part == ".." for part in parts):
        raise ValueError("cover path is unsafe")
    for part in parts:
        safe_component(part)
    if Path(parts[-1]).stem.casefold() != "cover" or Path(parts[-1]).suffix.lower() not in IMAGE_EXTENSIONS:
        raise ValueError("cover path is unsafe")
    return "/".join(parts)


def compiled_formats(formats: dict) -> tuple[re.Pattern[str], tuple[re.Pattern[str], ...]]:
    try:
        volume = re.compile(formats["volume_pattern"])
        chapters = tuple(re.compile(pattern) for pattern in formats["chapter_patterns"])
    except re.error as exc:
        raise ValueError(f"invalid detection pattern: {exc}") from exc
    if volume.groups < 1 or any(pattern.groups < 1 for pattern in chapters):
        raise ValueError("volume and chapter patterns need one number group")
    return volume, chapters


def coerce_formats(raw: object) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("formats must be an object")
    data = {
        "volume_pattern": DEFAULT_FORMATS["volume_pattern"],
        "chapter_patterns": list(DEFAULT_FORMATS["chapter_patterns"]),
    }
    if "volume_pattern" in raw:
        data["volume_pattern"] = str(raw["volume_pattern"]).strip()
    if "chapter_patterns" in raw:
        patterns = raw["chapter_patterns"]
        if isinstance(patterns, str):
            patterns = [line.strip() for line in patterns.splitlines() if line.strip()]
        if not isinstance(patterns, list) or not patterns:
            raise ValueError("chapter patterns need at least one pattern")
        data["chapter_patterns"] = [str(pattern).strip() for pattern in patterns]
    compiled_formats(data)
    return data


def coerce_profiles(raw: object) -> list[dict]:
    if not isinstance(raw, list):
        raise ValueError("profiles must be a list")
    cleaned = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("a profile must be an object")
        name = str(item.get("name", "")).strip()
        safe_component(name)
        key = name.casefold()
        if key in seen:
            raise ValueError(f"duplicate profile: {name}")
        seen.add(key)
        template = str(item.get("template", "")).strip().replace("\\", "/")
        if not template.lower().endswith(".cbz") or "{series}" not in template:
            raise ValueError(f"{name}: template needs {{series}} and must end in .cbz")
        if template.startswith("/") or ".." in template.split("/"):
            raise ValueError(f"{name}: template leaves the output folder")
        unknown = set(_TOKEN.findall(template)) - _TOKENS
        if unknown:
            raise ValueError(f"{name}: unknown placeholder {{{sorted(unknown)[0]}}}")
        try:
            volume_padding = int(item.get("volume_padding", 1))
            chapter_padding = int(item.get("chapter_padding", 4))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name}: padding must be a number") from exc
        if not 0 <= volume_padding <= 8 or not 1 <= chapter_padding <= 8:
            raise ValueError(f"{name}: volume padding is 0-8, chapter padding is 1-8")
        default_volume = str(item.get("default_volume", "") or "").strip()
        if default_volume and not default_volume.isdigit():
            raise ValueError(f"{name}: default volume must be a number")
        cleaned.append(
            {
                "name": name,
                "template": template,
                "volume_padding": volume_padding,
                "chapter_padding": chapter_padding,
                "default_volume": default_volume,
            }
        )
    return cleaned


def coerce_works(raw: object, profiles: list[dict]) -> dict:
    names = {profile["name"].casefold(): profile["name"] for profile in profiles}
    if not isinstance(raw, dict):
        raise ValueError("works must be an object")
    cleaned = {}
    seen: set[str] = set()
    for key, item in raw.items():
        folder = str(key).strip()
        safe_component(folder)
        folded = folder.casefold()
        if folded in seen:
            raise ValueError(f"duplicate work: {folder}")
        seen.add(folded)
        if not isinstance(item, dict):
            raise ValueError("a work must be an object")
        profile = str(item.get("profile", "")).strip()
        if profile.casefold() not in names:
            raise ValueError(f"unknown profile: {profile}")
        series = str(item.get("series") or folder).strip()
        safe_component(series)
        cleaned[folder] = {
            "profile": names[profile.casefold()],
            "series": series,
            "cover": clean_cover(item.get("cover")),
        }
    return cleaned


def coerce_library(raw: object, profiles: list[dict]) -> list[dict]:
    names = {profile["name"].casefold(): profile["name"] for profile in profiles}
    if not isinstance(raw, list):
        raise ValueError("library must be a list")
    cleaned = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("a library entry must be an object")
        series = str(item.get("series") or "").strip()
        safe_component(series)
        folded = series.casefold()
        if folded in seen:
            raise ValueError(f"duplicate series: {series}")
        seen.add(folded)
        profile = str(item.get("profile", "")).strip()
        if profile.casefold() not in names:
            raise ValueError(f"unknown profile: {profile}")
        cleaned.append(
            {
                "series": series,
                "profile": names[profile.casefold()],
                "cover": clean_cover(item.get("cover")),
            }
        )
    return cleaned


def coerce_default_profile(raw: object, profiles: list[dict]) -> str:
    if not profiles:
        return ""
    names = {profile["name"].casefold(): profile["name"] for profile in profiles}
    text = str(raw or "").strip()
    if text.casefold() in names:
        return names[text.casefold()]
    return profiles[0]["name"]


def profile_named(settings: dict, name: str) -> dict | None:
    folded = str(name or "").casefold()
    for profile in settings.get("profiles") or []:
        if profile["name"].casefold() == folded:
            return profile
    return None


def default_profile(settings: dict) -> dict | None:
    found = profile_named(settings, settings.get("default_profile") or "")
    if found is not None:
        return found
    profiles = settings.get("profiles") or []
    return profiles[0] if profiles else None


def work_entry(settings: dict, folder: str) -> dict | None:
    works = settings.get("works") or {}
    if folder in works:
        return works[folder]
    folded = folder.casefold()
    for key, value in works.items():
        if str(key).casefold() == folded:
            return value
    return None


def can_auto(unit: Unit) -> bool:
    """A known work's complete chapter or volume. Single files always wait."""
    return bool(unit.known and not unit.single and unit.dest_rel and not unit.error)


def _parse_part(name: str, volume_re: re.Pattern[str], chapter_res: tuple[re.Pattern[str], ...], *, strict: bool):
    volumes = list(volume_re.finditer(name))
    if len(volumes) > 1:
        return None
    working = name
    volume = None
    if volumes:
        volume = int(volumes[0].group(1))
        working = f"{name[: volumes[0].start()]} {name[volumes[0].end() :]}"
    match = None
    for pattern in chapter_res:
        found = list(pattern.finditer(working))
        if not found:
            continue
        if len(found) != 1:
            return None
        match = found[0]
        break
    chapter = fraction = None
    if match is not None:
        rest = working[: match.start()] + working[match.end() :]
        if strict and re.search(r"\d", rest):
            return None
        chapter = int(match.group(1))
        fraction = match.group(2) if match.re.groups >= 2 else None
        working = rest
    elif strict and re.search(r"\d", working):
        return None
    title = re.sub(r"[\s._\-]+", " ", working).strip(" .-_")
    return {"volume": volume, "chapter": chapter, "fraction": fraction, "title": title}


def parse_path(parts: list[str], formats: dict) -> dict | None:
    volume_re, chapter_res = compiled_formats(formats)
    volume = chapter = fraction = None
    titles: list[str] = []
    for part in parts:
        info = _parse_part(part, volume_re, chapter_res, strict=True)
        if info is None:
            return None
        if info["volume"] is not None:
            if volume is not None:
                return None
            volume = info["volume"]
        if info["chapter"] is not None:
            if chapter is not None:
                return None
            chapter = info["chapter"]
            fraction = info["fraction"]
        if info["title"]:
            titles.append(info["title"])
    return {
        "series": " ".join(titles).strip() or None,
        "volume": volume,
        "chapter": chapter,
        "fraction": fraction,
    }


def _format_number(value: int | None, fraction: str | None, padding: int, default: str) -> str | None:
    if value is None:
        return default or None
    text = f"{value:0{padding}d}" if padding else str(value)
    if fraction:
        text += f".{fraction}"
    return text


def render_template(template: str, profile: dict, info: dict) -> str:
    fields = {
        "profile": profile["name"],
        "series": info.get("series") or "",
        "volume": _format_number(
            info.get("volume"), None, int(profile["volume_padding"]), str(profile.get("default_volume") or "")
        )
        or "",
        "chapter": _format_number(
            info.get("chapter"), info.get("fraction"), int(profile["chapter_padding"]), ""
        )
        or "",
    }
    missing = [token for token in _TOKEN.findall(template) if not fields.get(token)]
    if missing:
        raise ValueError(f"{missing[0]} was not detected")
    return _TOKEN.sub(lambda match: fields[match.group(1)], template)


def output_from_template(root: Path, relative: str) -> Path:
    dest = contained_path(root, relative)
    if dest.suffix.lower() != ".cbz":
        raise ValueError("template must end in .cbz")
    return dest


class _DirListing(NamedTuple):
    pages: list[Path]
    folders: list[Path]


def _split(path: Path) -> _DirListing:
    """One directory listing: image files and child folders."""
    pages: list[Path] = []
    folders: list[Path] = []
    children = list(path.iterdir())
    root = path.resolve()
    for child in children:
        if child.is_symlink() or child.name.startswith(".") or child.name.startswith("._"):
            continue
        try:
            directory = child.is_dir()
        except OSError as exc:
            logging.getLogger(__name__).warning("skipped unreadable %s: %s", child, exc)
            continue
        if directory:
            if _inside(root, child):
                folders.append(child)
            continue
        if child.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        resolved = child.resolve()
        if resolved != root and root not in resolved.parents:
            continue
        pages.append(child)
    pages.sort(key=lambda item: (natural_key(item.name), item.name.casefold()))
    folders.sort(key=lambda item: natural_key(item.name))
    return _DirListing(pages, folders)


def _inside(parent: Path, child: Path) -> bool:
    resolved = child.resolve()
    return parent == resolved or parent in resolved.parents


def _leaves(base: Path) -> list[Path]:
    listing = _split(base)
    leaves = [base] if listing.pages or not listing.folders else []
    for child in listing.folders:
        leaves.extend(_leaves(child))
    return leaves


def _fill_from_filenames(info: dict, folder: Path, formats: dict) -> dict | None:
    """Use a filename only when every page agrees. Page numbers alone do not count."""
    if info.get("chapter") is not None and info.get("volume") is not None:
        return info
    volume_re, chapter_res = compiled_formats(formats)
    volumes: set[int] = set()
    chapters: set[tuple[int, str | None]] = set()
    for page in _split(folder).pages:
        parsed = _parse_part(page.stem, volume_re, chapter_res, strict=False)
        if parsed is None:
            return None
        if parsed["volume"] is not None:
            volumes.add(parsed["volume"])
        if parsed["chapter"] is not None:
            chapters.add((parsed["chapter"], parsed["fraction"]))
    if len(volumes) > 1 or len(chapters) > 1:
        return None
    filled = dict(info)
    if filled.get("volume") is None and len(volumes) == 1:
        filled["volume"] = next(iter(volumes))
    if filled.get("chapter") is None and len(chapters) == 1:
        filled["chapter"], filled["fraction"] = next(iter(chapters))
    return filled


def parse_file_numbers(name: str, formats: dict) -> dict:
    volume_re, chapter_res = compiled_formats(formats)
    parsed = _parse_part(Path(name).stem, volume_re, chapter_res, strict=False)
    if parsed is None:
        return {"volume": None, "chapter": None, "fraction": None}
    return {"volume": parsed["volume"], "chapter": parsed["chapter"], "fraction": parsed["fraction"]}


def _file_unit(
    root: Path,
    page: Path,
    work: str,
    series: str,
    profile: dict | None,
    known: bool,
    formats: dict,
    output: Path,
) -> Unit:
    if is_cover_name(page.name):
        return Unit(
            source_rel=page.relative_to(root).as_posix(),
            series=series,
            source_name=page.name,
            source=page,
            dest_rel="",
            label=page.name,
            recursive=False,
            work=work,
            profile_name=profile["name"] if profile else "",
            single=True,
            known=known,
            cover=True,
        )
    hinted = parse_file_numbers(page.name, formats)
    info = {"series": series, "volume": hinted["volume"], "chapter": hinted["chapter"], "fraction": hinted["fraction"]}
    return _planned_unit(
        root,
        page,
        page.relative_to(root).as_posix(),
        info,
        profile,
        recursive=False,
        single=True,
        known=known,
        work=work,
        output=output,
    )


def _tree_units(
    root: Path,
    work_dir: Path,
    series: str,
    profile: dict | None,
    known: bool,
    formats: dict,
    output: Path,
) -> list[Unit]:
    units: list[Unit] = []
    grouped: dict[tuple, list[tuple[Path, dict]]] = {}
    needs_chapter = profile is not None and "{chapter}" in profile["template"]
    for child in _split(work_dir).folders:
        for leaf in _leaves(child):
            if not _inside(work_dir.resolve(), leaf):
                continue
            rel = leaf.relative_to(root).as_posix()
            parts = list(leaf.relative_to(work_dir).parts)
            info = parse_path(parts, formats) if parts else None
            if info is None:
                units.append(_hold(root, leaf, rel, series, profile, known, "chapter number is ambiguous", output))
                continue
            volume_ready = info.get("volume") is not None or bool(profile and profile.get("default_volume"))
            chapter_ready = info.get("chapter") is not None or not needs_chapter
            if not (volume_ready and chapter_ready):
                info = _fill_from_filenames(info, leaf, formats)
            if info is None:
                units.append(
                    _hold(root, leaf, rel, series, profile, known, "filenames contain more than one chapter or volume", output)
                )
                continue
            info["series"] = series
            if needs_chapter and info.get("chapter") is None:
                units.append(
                    _hold(root, leaf, rel, series, profile, known, f"chapter number is ambiguous: {leaf.name}", output)
                )
                continue
            if needs_chapter:
                units.append(
                    _planned_unit(root, leaf, rel, info, profile, recursive=False, single=False, known=known, work=work_dir.name, output=output)
                )
                continue
            grouped.setdefault((info.get("volume"),), []).append((leaf, info))
    for bucket in grouped.values():
        leaves = [leaf for leaf, _info in bucket]
        info = bucket[0][1]
        source = Path(*_shared_parts(leaves))
        rel = source.relative_to(root).as_posix()
        units.append(
            _planned_unit(root, source, rel, info, profile, recursive=True, single=False, known=known, work=work_dir.name, output=output)
        )
    return units


def _shared_parts(paths: list[Path]) -> list[str]:
    lists = [path.parts for path in paths]
    shared: list[str] = []
    for bits in zip(*lists):
        if len(set(bits)) != 1:
            break
        shared.append(bits[0])
    return shared


def _planned_unit(
    root: Path,
    source: Path,
    rel: str,
    info: dict,
    profile: dict | None,
    *,
    recursive: bool,
    single: bool,
    known: bool,
    work: str,
    output: Path,
    error: str | None = None,
) -> Unit:
    dest_rel = ""
    label = source.name
    if error is None and profile is not None:
        try:
            dest_rel = render_template(profile["template"], profile, info)
            output_from_template(output, dest_rel)
            label = Path(dest_rel).name
        except ValueError as exc:
            error = str(exc)
    elif error is None:
        error = "no profile"
    return Unit(
        source_rel=rel,
        series=info.get("series") or "",
        source_name=source.name,
        source=source,
        dest_rel=dest_rel,
        label=label,
        recursive=recursive,
        error=error,
        work=work,
        volume=info.get("volume"),
        chapter=info.get("chapter"),
        fraction=info.get("fraction"),
        profile_name="" if profile is None else profile["name"],
        single=single,
        known=known,
    )


def _hold(
    root: Path, source: Path, rel: str, series: str, profile: dict | None, known: bool, message: str, output: Path
) -> Unit:
    work = rel.split("/", 1)[0] if "/" in rel else ""
    return _planned_unit(
        root,
        source,
        rel,
        {"series": series, "volume": None, "chapter": None, "fraction": None},
        profile,
        recursive=False,
        single=False,
        known=known,
        work=work,
        output=output,
        error=message,
    )


def discover(root: Path, settings: dict) -> list[Unit]:
    root = root.resolve()
    if not root.is_dir():
        return []
    formats = settings["formats"]
    output = Path(settings["output_dir"])
    fallback = default_profile(settings)
    units: list[Unit] = []
    root_listing = _split(root)
    for page in root_listing.pages:
        units.append(_file_unit(root, page, "", "", fallback, False, formats, output))
    for folder in root_listing.folders:
        saved = work_entry(settings, folder.name)
        profile = profile_named(settings, saved["profile"]) if saved else fallback
        series = (saved or {}).get("series") or folder.name
        known = saved is not None and profile is not None
        for page in _split(folder).pages:
            units.append(_file_unit(root, page, folder.name, series, profile, False, formats, output))
        units.extend(_tree_units(root, folder, series, profile, known, formats, output))
    return units


def library_entry(settings: dict, series: str) -> dict | None:
    wanted = series.casefold()
    return next(
        (item for item in settings.get("library") or [] if item["series"].casefold() == wanted),
        None,
    )


def existing_cover(settings: dict, cover: str) -> Path | None:
    if not cover:
        return None
    try:
        dest = contained_path(Path(settings["output_dir"]), cover)
    except ValueError:
        return None
    return dest if dest.is_file() else None


def set_work(settings: dict, folder: str, profile_name: str, series: str, cover: str) -> None:
    works = {
        key: value
        for key, value in settings["works"].items()
        if str(key).casefold() != folder.casefold()
    }
    works[folder] = {"profile": profile_name, "series": series, "cover": cover}
    settings["works"] = works


def remember_series(settings: dict, series: str, profile_name: str, cover: str | None = None) -> None:
    library = []
    previous = None
    for item in settings.get("library") or []:
        if str(item.get("series", "")).casefold() == series.casefold():
            previous = item
        else:
            library.append(item)
    library.append(
        {
            "series": series,
            "profile": profile_name,
            "cover": (previous or {}).get("cover") or "" if cover is None else cover,
        }
    )
    settings["library"] = library


def previous_cover(settings: dict, folder: str) -> str:
    entry = work_entry(settings, folder)
    return (entry or {}).get("cover") or ""


def optional_int(value: object, label: str) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if not text.isdigit():
        raise ValueError(f"{label} must be a number")
    return int(text)


def chapter_parts(value: object) -> tuple[int | None, str | None]:
    if value is None:
        return None, None
    text = str(value).strip()
    if not text:
        return None, None
    head, sep, tail = text.partition(".")
    if not head.isdigit() or (sep and not tail.isdigit()):
        raise ValueError("chapter must be a number")
    return int(head), (tail if sep else None)
