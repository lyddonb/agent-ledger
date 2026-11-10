"""`ledger scan`: look for secrets anywhere in `.audit/` (S3 "CI check").

Every write is redacted already (M0 unit 3), so a finding means something reached `.audit/`
without going through the CLI, or a pattern was added since. Each line is checked in every
decoded view the redactor confirms with, and a finding names the file, line and pattern, never
the matched text. Anything that can't be read and scanned (binary, oversized, a symlink) is a
finding too, since an unscanned file could hold anything.
"""
from __future__ import annotations

import gzip
import io
import os
import stat
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

from . import _git
from .redact import Redactor

AUDIT_DIR = ".audit"
# A transcript archive is decompressed as a stream with this cap, so a crafted gzip can't
# exhaust memory or time; real sessions are well under it.
MAX_DECOMPRESSED = 512 * 1024 ** 2
MAX_LINE = 64 * 1024 ** 2
MAX_LINES = 5_000_000
# Seconds one file may take; past it the file is a finding rather than a stuck CI job.
MAX_SECONDS = 600
# Seconds a whole scan may take, under the 30-minute job limit the docs set.
MAX_TOTAL_SECONDS = 1500
_LINKS = ("120000", "160000")


@dataclass(frozen=True)
class Finding:
    path: str     # relative to the repo root
    line: int     # 1-based; 0 means the whole file
    what: str     # a pattern name, or why the file couldn't be scanned


def _lines(name: str, data: bytes) -> Iterator[tuple[int, str] | str]:
    """The file's text lines, or one string saying why it can't be read."""
    if name.endswith(".gz"):
        try:
            stream = gzip.GzipFile(fileobj=io.BytesIO(data))
            total = 0
            for n, raw in enumerate(iter(lambda: stream.readline(MAX_LINE + 1), b""), 1):
                total += len(raw)
                if total > MAX_DECOMPRESSED or len(raw) > MAX_LINE or n > MAX_LINES:
                    yield "too large to scan when decompressed"
                    return
                try:
                    yield n, raw.decode("utf-8").rstrip("\n")
                except UnicodeDecodeError:
                    yield "not UTF-8 when decompressed"
                    return
        except (OSError, EOFError, zlib.error):
            yield "not a readable gzip file"
        return
    if len(data) > MAX_DECOMPRESSED:
        yield "too large to scan"
        return
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        yield "not UTF-8 text"
        return
    start = 0
    for n in range(1, MAX_LINES + 2):
        end = text.find("\n", start)
        line = text[start:] if end == -1 else text[start:end]
        if len(line) > MAX_LINE or n > MAX_LINES:
            yield "too large to scan (a line or the line count is over the cap)"
            return
        yield n, line
        if end == -1:
            return
        start = end + 1


def scan_bytes(rel: str, data: bytes, redactor: Redactor) -> list[Finding]:
    out = []
    deadline = time.monotonic() + MAX_SECONDS
    for item in _lines(rel, data):
        if isinstance(item, str):
            out.append(Finding(rel, 0, item))
            break
        n, line = item
        if not line.strip():
            continue
        if time.monotonic() > deadline:
            out.append(Finding(rel, 0, f"took over {MAX_SECONDS} s to scan"))
            break
        name = redactor.find(line)
        if name is not None:
            out.append(Finding(rel, n, name))
    return out


def _walk(root: Path) -> Iterator[tuple[str, bytes | None]]:
    """(path, bytes) for every file under .audit on disk, without following a symlink; bytes is
    None for anything that isn't a regular file."""
    top = root / AUDIT_DIR
    if top.is_symlink():
        yield AUDIT_DIR, None
        return
    if not top.is_dir():
        return
    for folder, dirs, files in os.walk(top, followlinks=False):
        for d in [d for d in dirs if (Path(folder) / d).is_symlink()]:
            dirs.remove(d)
            yield (Path(folder) / d).relative_to(root).as_posix(), None
        for f in sorted(files):
            path = Path(folder) / f
            rel = path.relative_to(root).as_posix()
            mode = path.lstat().st_mode
            yield rel, path.read_bytes() if stat.S_ISREG(mode) else None


def _changed(root: Path, base: str) -> Iterator[tuple[str, bytes | None]]:
    """(path, committed bytes) for every file under .audit at HEAD that is new or differs from
    `base`, read as git objects like `verify --base`."""
    base_id, head = _git.resolve(root, base), _git.resolve(root, "HEAD")
    before = {rel: (mode, oid) for mode, oid, rel in _git.ls_tree_entries(root, base_id, AUDIT_DIR)}
    for mode, oid, rel in _git.ls_tree_entries(root, head, AUDIT_DIR):
        if before.get(rel) == (mode, oid):  # a mode change (file to symlink) counts as changed
            continue
        yield rel, None if mode in _LINKS else _git.blob(root, oid)


def scan_repo(root: Path, redactor: Redactor, base: str | None = None) -> tuple[int, list[Finding]]:
    """(files scanned, findings). Without `base`, every file under .audit on disk; with it, the
    committed files under .audit that changed since `base`."""
    files: Iterable[tuple[str, bytes | None]] = _changed(root, base) if base else _walk(root)
    count, out = 0, []
    deadline = time.monotonic() + MAX_TOTAL_SECONDS
    for rel, data in files:
        if time.monotonic() > deadline:
            out.append(Finding(rel, 0, f"not scanned: the scan ran past its {MAX_TOTAL_SECONDS} s budget"))
            break
        count += 1
        if data is None:
            out.append(Finding(rel, 0, "a symlink, submodule or special file; .audit holds plain files"))
        else:
            out.extend(scan_bytes(rel, data, redactor))
    return count, out
