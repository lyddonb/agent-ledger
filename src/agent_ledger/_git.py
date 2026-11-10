"""The few local git reads that seals and `verify --base` need. Never fetches or pushes."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .index import COMMIT_RE


class GitError(Exception):
    """git isn't installed, the folder isn't a repo, or a ref doesn't resolve."""


def _run(root: Path, *args: str, ok_codes: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess:
    # A fixed environment: no system or global config, no replace refs, no prompts, no lazy
    # fetches, no fsmonitor hook. The repo's own .git/config still applies; a pull request
    # can't change it, since git never commits a .git path.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_TERMINAL_PROMPT="0", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_NO_REPLACE_OBJECTS="1", GIT_NO_LAZY_FETCH="1", LC_ALL="C")
    try:
        proc = subprocess.run(["git", "-C", str(root), "--no-pager", "-c", "core.fsmonitor=false", *args],
                              capture_output=True, env=env, timeout=120)
    except FileNotFoundError:
        raise GitError("git is not installed") from None
    except subprocess.TimeoutExpired:
        raise GitError(f"git {args[0]} took over 120 s") from None
    if proc.returncode not in ok_codes:
        raise GitError(f"git {' '.join(args)}: {proc.stderr.decode(errors='replace').strip()}")
    return proc


def resolve(root: Path, ref: str) -> str:
    """The full commit id `ref` names."""
    if ref.startswith("-"):
        raise GitError(f"{ref!r} is not a ref")
    return _run(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").stdout.decode().strip()


def _object_id(value: str) -> str:
    """Refuse anything but a full lowercase object id, so a value read from a ledger can never
    reach git as an option or a revision expression."""
    if not COMMIT_RE.fullmatch(value):
        raise GitError(f"{value[:20]!r} is not a full commit id")
    return value


def is_ancestor(root: Path, commit: str, of: str = "HEAD") -> bool:
    """Whether `commit` exists and is in the history of `of`."""
    _object_id(commit)
    proc = _run(root, "merge-base", "--is-ancestor", commit, of, ok_codes=(0, 1, 128))
    return proc.returncode == 0


def file_at(root: Path, commit: str, path: str) -> bytes | None:
    """The stored bytes of `path` (relative to `root`) at `commit`: None unless it is a plain
    file there. Read the same way as the base check (ls-tree, then cat-file), so a seal is
    judged on exactly the bytes that merge."""
    for mode, oid, rel in ls_tree_entries(root, commit, path):
        if rel == path:
            return blob(root, oid) if mode in ("100644", "100755") else None
    return None


def ls_tree_entries(root: Path, commit: str, path: str) -> list[tuple[str, str, str]]:
    """(mode, object id, path relative to `root`) for every entry under `path` at `commit`,
    including symlinks (120000) and submodules (160000), never following either."""
    _object_id(commit)
    proc = _run(root, "ls-tree", "-r", "-z", "--full-name", commit, "--", path)
    prefix = _run(root, "rev-parse", "--show-prefix").stdout.decode().strip()
    out = []
    for record in proc.stdout.split(b"\0"):
        if not record:
            continue
        meta, _, name = record.partition(b"\t")
        mode, _kind, oid = meta.decode().split(" ")
        name_s = name.decode("utf-8", "surrogateescape")
        if name_s.startswith(prefix):
            out.append((mode, oid, name_s[len(prefix):]))
    return out


def blob(root: Path, oid: str) -> bytes:
    """A blob's stored bytes, with no filters, attributes or line-ending conversion."""
    _object_id(oid)
    return _run(root, "cat-file", "blob", oid).stdout
