"""Build one flat, validated CBZ from a chapter folder."""

from __future__ import annotations

import os
import threading
import zipfile
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from backlist.chapter import IMAGE_EXTENSIONS, natural_key

# ponytail: Pillow verifies each page once, in the worker. Fine for chapter-sized folders.


def list_pages(chapter_dir: Path) -> list[Path]:
    root = chapter_dir.resolve()
    pages: list[Path] = []
    for path in chapter_dir.iterdir():
        if not path.is_file() or path.is_symlink():
            continue
        if path.name.startswith(".") or path.name.startswith("._"):
            continue
        if path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        resolved = path.resolve()
        if resolved != root and root not in resolved.parents:
            continue
        pages.append(path)
    pages.sort(key=lambda p: (natural_key(p.name), p.name.casefold()))
    return pages


def check_image(path: Path) -> None:
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            image.load()
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
        raise ValueError(f"damaged image: {path.name}") from exc


def list_pages_under(folder: Path) -> list[Path]:
    """Every image below folder, in natural path order. Used when a profile packs a whole volume."""
    root = folder.resolve()
    pages: list[Path] = []
    for path in folder.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(folder)
        if any(part.startswith(".") or part.startswith("._") for part in relative.parts):
            continue
        if path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        resolved = path.resolve()
        if resolved != root and root not in resolved.parents:
            continue
        pages.append(path)
    pages.sort(key=lambda page: (natural_key(page.relative_to(folder).as_posix()), page.name.casefold()))
    return pages


def fingerprint(chapter_dir: Path) -> tuple:
    """Names, sizes and mtimes. Changes while a copy is still writing."""
    return _fingerprint(chapter_dir, chapter_dir.iterdir(), chapter_dir)


def fingerprint_tree(folder: Path) -> tuple:
    return _fingerprint(folder, folder.rglob("*"), folder)


def _fingerprint(folder: Path, paths, base: Path) -> tuple:
    items: list[tuple] = []
    for path in paths:
        if path.is_symlink() or not path.is_file():
            continue
        relative = path.relative_to(base)
        if any(part.startswith(".") for part in relative.parts[:-1]):
            continue
        stat = path.stat()
        items.append((relative.as_posix(), stat.st_size, stat.st_mtime_ns))
    return tuple(sorted(items))


def validate_cbz(path: Path, expected: list[str]) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            bad = archive.testzip()
            names = archive.namelist()
    except zipfile.BadZipFile as exc:
        raise ValueError("CBZ failed validation") from exc
    if bad is not None or names != expected:
        raise ValueError("CBZ failed validation")
    for name in names:
        if name.endswith("/") or name.startswith(("/", "\\")) or ".." in name.split("/"):
            raise ValueError("CBZ failed validation")


def write_cbz(dest: Path, pages: list[Path], base: Path | None = None) -> list[str]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f"{dest.name}.{threading.get_ident()}.partial")
    rels = [page.name if base is None else page.relative_to(base).as_posix() for page in pages]
    basenames = [Path(rel).name for rel in rels]
    use_relative = len(basenames) != len(set(basenames))
    names: list[str] = []
    used: set[str] = set()
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_STORED) as archive:
            for index, (page, rel) in enumerate(zip(pages, rels), start=1):
                name = rel if use_relative else Path(rel).name
                if name in used:
                    name = f"{index:04d}_{name}"
                used.add(name)
                names.append(name)
                archive.write(page, arcname=name)
        validate_cbz(tmp, names)
        os.replace(tmp, dest)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise ValueError(f"write failed: {exc}") from exc
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    return names
