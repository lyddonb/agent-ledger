"""The few local git reads that seals and `verify --base` need. Never fetches or pushes."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .index import COMMIT_RE


class GitError(Exception):
    """git isn't installed, the folder isn't a repo, or a ref doesn't resolve."""


def _run(root: Path, *args: str, ok_codes: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess:
    # A fixed environment: no pager, no prompts, no user config that could rewrite output.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_TERMINAL_PROMPT="0", GIT_CONFIG_NOSYSTEM="1", LC_ALL="C")
    try:
        proc = subprocess.run(["git", "-C", str(root), "--no-pager", *args], capture_output=True, env=env,
                              timeout=120)
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


def show(root: Path, commit: str, path: str) -> bytes | None:
    """The bytes of `path` (relative to `root`) at `commit`, or None when it isn't there."""
    _object_id(commit)
    proc = _run(root, "show", f"{commit}:./{path}", ok_codes=(0, 128))
    return proc.stdout if proc.returncode == 0 else None


def ls_tree(root: Path, commit: str, path: str) -> list[str]:
    """Files under `path` at `commit`, relative to `root`."""
    _object_id(commit)
    # -z: names come back raw and NUL-separated, never quoted, so an odd name can't slip past.
    proc = _run(root, "ls-tree", "-r", "-z", "--name-only", "--full-name", commit, "--", path)
    prefix = _run(root, "rev-parse", "--show-prefix").stdout.decode().strip()
    names = [n.decode("utf-8", "surrogateescape") for n in proc.stdout.split(b"\0") if n]
    return [n[len(prefix):] for n in names if n.startswith(prefix)]
