"""Chapter-name patterns, natural sort, and safe path pieces."""

from __future__ import annotations

import re
import threading
from pathlib import Path

IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
    ".bmp",
    ".tif",
    ".tiff",
}

# Prefer an explicit "chapter", then "ch", then a lone "c".
# These strings are the central default; profiles.py stores the same patterns in config.
CHAPTER_PATTERNS = (
    r"(?i)(?<![a-z])chapter[\s._\-]*(\d+)(?:\.(\d+))?",
    r"(?i)(?<![a-z])ch(?!apter)[\s._\-]*(\d+)(?:\.(\d+))?",
    r"(?i)(?<![a-z])c(?!h)[\s._\-]*(\d+)(?:\.(\d+))?",
)
VOLUME_PATTERN = r"(?i)(?<![a-z])vol(?:ume)?[\s._\-]*(\d+)"


def natural_key(name: str) -> tuple:
    parts = re.split(r"(\d+)", name)
    key: list[tuple] = []
    for part in parts:
        if part.isdigit():
            key.append((0, int(part)))
        else:
            key.append((1, part.casefold()))
    return tuple(key)


def contained_path(root: Path, relative: str) -> Path:
    """A file under root. Symlinks and `..` are rejected."""
    parts = [part for part in relative.replace("\\", "/").split("/") if part]
    if not parts or any(part in {".", ".."} for part in parts):
        raise ValueError("unsafe path")
    for part in parts:
        safe_component(part)
    root_abs = root.resolve()
    unresolved = root_abs.joinpath(*parts)
    if unresolved.is_symlink():
        raise ValueError("unsafe path")
    dest = unresolved.resolve()
    if dest != root_abs and root_abs not in dest.parents:
        raise ValueError("unsafe path")
    return dest


_path_guard = threading.Lock()
_path_locks: dict[str, threading.Lock] = {}


def path_lock(dest: Path) -> threading.Lock:
    """One lock per destination. The map lock is not held while the file is written."""
    key = str(dest.resolve())
    with _path_guard:
        lock = _path_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _path_locks[key] = lock
        return lock


def safe_component(name: str) -> str:
    if not name or name in {".", ".."}:
        raise ValueError("unsafe name")
    if any(c in name for c in "/\\\x00") or any(ord(c) < 32 for c in name):
        raise ValueError("unsafe name")
    return name


def alternate_path(path: Path) -> Path:
    stem, suffix, parent = path.stem, path.suffix, path.parent
    for n in range(2, 1000):
        candidate = parent / f"{stem} ({n}){suffix}"
        if not candidate.exists():
            return candidate
    raise ValueError("too many existing files")
