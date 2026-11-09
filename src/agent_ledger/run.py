"""Starting a run and appending to it: the operations behind `ledger init` and `ledger append`."""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .entry import TS_FORMAT, TS_RE, Actor, ChainHead, Entry, EntryError, EntryType, next_entry
from .sink import LedgerError, RepoSink, StaleHead

AUDIT_DIR = ".audit"
RUNS_DIR = "runs"
# Entries copied in from a hand-written record carry data["imported"] (S3 "Migration").
IMPORTED_KEY = "imported"
# Each failed attempt means another writer appended, so this only runs out under a flood.
APPEND_ATTEMPTS = 100

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def find_root(start: Path) -> Path:
    """The repo root: the nearest folder at or above `start` holding `.git` (a folder, or a file
    in a worktree). Outside a repo there is no sensible default, so that is an error."""
    start = start.resolve()
    for folder in (start, *start.parents):
        if (folder / ".git").exists():
            return folder
    raise LedgerError(f"no .git folder at or above {start}; run inside a repo or pass --root")


def repo_sink(root: Path) -> RepoSink:
    return RepoSink(root / AUDIT_DIR / RUNS_DIR)


def check_imported(data: Mapping[str, Any] | None) -> None:
    """S3 "Migration": data.imported, when present, is {source, source_ts, by}, all text."""
    if not data or IMPORTED_KEY not in data:
        return
    marker = data[IMPORTED_KEY]
    if not isinstance(marker, Mapping) or set(marker) != {"source", "source_ts", "by"} or not all(
            isinstance(v, str) and v for v in marker.values()):
        raise EntryError("schema", 'data.imported must be {"source", "source_ts", "by"}, each a non-empty string')
    try:
        format_ok = TS_RE.fullmatch(marker["source_ts"]) and datetime.strptime(marker["source_ts"], TS_FORMAT)
    except ValueError:
        format_ok = False
    if not format_ok:
        raise EntryError("schema", "data.imported.source_ts must be a real ts, like 2026-11-08T19:51:55Z")


def run_id_for(slug: str, day: date) -> str:
    return f"{day.isoformat()}-{slug}"


def init_run(sink: RepoSink, run_id: str, *, actor: Actor, why: str, clock: Clock = utc_now) -> Entry:
    """Write a run's first entry, a `state` entry with data {"state": "started"}."""
    first = next_entry(ChainHead.genesis(run_id), now=clock(), actor=actor, type=EntryType.STATE,
                       summary="Run started", why=why, data={"state": "started"})
    sink.create(first)
    return first


def append(
    sink: RepoSink,
    run_id: str,
    *,
    actor: Actor,
    type: EntryType,
    summary: str,
    why: str,
    evidence: Sequence[str] = (),
    data: Mapping[str, Any] | None = None,
    clock: Clock = utc_now,
) -> Entry:
    """Chain one entry onto the run's current head and write it.

    The sink rejects an entry chained to a stale head, so on a race this re-reads the head
    and re-chains (with a fresh timestamp) instead of forking the chain.
    """
    check_imported(data)
    for _ in range(APPEND_ATTEMPTS):
        entry = next_entry(sink.head(run_id), now=clock(), actor=actor, type=type,
                           summary=summary, why=why, evidence=evidence, data=data)
        try:
            sink.write(entry)
            return entry
        except StaleHead:
            continue
    raise LedgerError(f"{run_id}: gave up after {APPEND_ATTEMPTS} attempts; other writers kept appending first")
