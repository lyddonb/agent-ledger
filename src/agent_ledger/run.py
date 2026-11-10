"""Starting a run and appending to it: the operations behind `ledger init` and `ledger append`."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from . import _git
from .index import COMMIT_RE, INDEX_FILE
from .redact import Redactor, Unconfirmed
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


SPEND_COUNTS = ("usd_micros", "tokens", "cache_read_tokens", "wall_s")


def check_spend(entry_type: EntryType, data: Mapping[str, Any] | None) -> None:
    """S3 "Spend entries": usd_micros is required; the counts are non-negative integers and
    estimate is a boolean. Other keys are allowed."""
    if entry_type is not EntryType.SPEND:
        return
    data = data or {}
    if "usd_micros" not in data:
        raise EntryError("schema", "a spend entry needs data.usd_micros (dollars in millionths: $4.25 is 4250000)")
    for key in SPEND_COUNTS:
        value = data.get(key, 0)
        if type(value) is not int or value < 0:  # bool is an int subclass; refuse it
            raise EntryError("schema", f"data.{key} must be a non-negative integer")
    if "estimate" in data and type(data["estimate"]) is not bool:
        raise EntryError("schema", "data.estimate must be true or false")



def run_id_for(slug: str, day: date) -> str:
    return f"{day.isoformat()}-{slug}"


def redact_fields(redactor: Redactor, fields: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, int]]:
    """Redact the free-text fields of an entry (summary, why, evidence, data) before chaining.
    Raises LedgerError when redaction can't be confirmed, so nothing unconfirmed is written."""
    try:
        clean, counts = redactor.redact_value(dict(fields))
        redactor.confirm(json.dumps(clean, ensure_ascii=False))
    except Unconfirmed as e:
        raise LedgerError(f"refusing: {e}") from None
    return clean, counts


def init_run(sink: RepoSink, run_id: str, *, actor: Actor, why: str, clock: Clock = utc_now,
             redactor: Redactor | None = None) -> Entry:
    """Write a run's first entry, a `state` entry with data {"state": "started"}."""
    why = redact_fields(redactor or Redactor(), {"why": why})[0]["why"]
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
    redactor: Redactor | None = None,
    _sealing: bool = False,
    _expect_link: str | None = None,
) -> Entry:
    """Redact, then chain one entry onto the run's current head and write it.

    The sink rejects an entry chained to a stale head, so on a race this re-reads the head
    and re-chains (with a fresh timestamp) instead of forking the chain.
    """
    fields, _ = redact_fields(redactor or Redactor(), {
        "summary": summary, "why": why, "evidence": list(evidence), "data": dict(data or {})})
    summary, why, evidence, data = fields["summary"], fields["why"], fields["evidence"], fields["data"]
    check_imported(data)
    check_spend(type, data)
    if type is EntryType.SEAL and not _sealing:
        raise LedgerError("a seal entry is written by `ledger seal`, which also indexes it")
    for _ in range(APPEND_ATTEMPTS):
        last = sink.read(run_id).entries[-1]
        if last.type is EntryType.SEAL:
            raise LedgerError(f"{run_id} is sealed (seq {last.seq}); a sealed run takes no more entries")
        if _expect_link is not None and last.head.link != _expect_link:
            raise LedgerError(f"{run_id}: another entry was appended while sealing; commit it, then seal again")
        entry = next_entry(last.head, now=clock(), actor=actor, type=type,
                           summary=summary, why=why, evidence=evidence, data=data)
        try:
            sink.write(entry)
            return entry
        except StaleHead:
            if _expect_link is not None:  # the head moved: chaining onto it would seal a head the commit lacks
                raise LedgerError(f"{run_id}: another entry was appended while sealing; commit it, then seal again") from None
            continue
    raise LedgerError(f"{run_id}: gave up after {APPEND_ATTEMPTS} attempts; other writers kept appending first")


@dataclass(frozen=True)
class Sealed:
    entry: Entry
    new: bool  # False when the run was already sealed; `indexed` says if this call indexed it
    indexed: bool


def seal_run(sink: RepoSink, root: Path, run_id: str, *, actor: Actor, why: str = "", clock: Clock = utc_now) -> Sealed:
    """Seal a run: write its `seal` entry, chained onto the head committed at HEAD and naming
    that commit, then add the run to INDEX.jsonl.

    Everything that could stop the index write is checked before the seal entry is written:
    the index must verify and must not name the run already. The ledger must be committed at
    HEAD exactly as it is on disk, and the seal is chained onto that committed head or not at
    all, so the commit always holds the head the seal follows. If the index write still fails
    (a crash, a full disk), running seal again indexes the sealed run. That is also how a
    sealed run gets indexed after taking main's INDEX.jsonl in a merge conflict."""
    last = sink.read(run_id).entries[-1]
    index = sink.read_index()  # raises when the index fails verify
    line = next((l for l in index.lines if l.run_id == run_id), None)
    if last.type is EntryType.SEAL:
        commit = last.data.get("commit")
        if not isinstance(commit, str) or not COMMIT_RE.fullmatch(commit):
            raise LedgerError(f"{run_id}'s seal entry (seq {last.seq}) names no valid commit; it can't be indexed")
        if line is None:
            sink.seal(run_id, last.hash, commit, now=clock())
            return Sealed(last, new=False, indexed=True)
        if (line.seal, line.commit) != (last.hash, commit):
            raise LedgerError(f"{INDEX_FILE} line {line.seq} names another seal for {run_id}; take the base "
                              f"branch's {INDEX_FILE}, then seal again")
        return Sealed(last, new=False, indexed=False)
    if line is not None:
        raise LedgerError(f"{INDEX_FILE} line {line.seq} already names {run_id}, which isn't sealed; take the "
                          f"base branch's {INDEX_FILE}, then seal again")
    ledger = sink.run_dir(run_id) / "ledger.jsonl"
    rel = ledger.relative_to(root).as_posix()
    commit = _git.resolve(root, "HEAD")
    committed = _git.show(root, commit, rel)
    if committed is None or committed != ledger.read_bytes():
        raise LedgerError(f"{rel} has changes that aren't committed at HEAD; commit the ledger, then seal, "
                          f"so the seal's commit holds the head it names")
    # The seal's prev is the committed head (the run's final hash); its data names the commit.
    entry = append(sink, run_id, actor=actor, type=EntryType.SEAL, summary="Run sealed",
                   why=why or "The run is finished; sealing anchors its head in git history (S3)",
                   evidence=[commit], data={"commit": commit}, clock=clock, _sealing=True,
                   _expect_link=last.head.link)
    sink.seal(run_id, entry.hash, commit, now=clock())
    return Sealed(entry, new=True, indexed=True)
