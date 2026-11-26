"""Starting a run and appending to it: the operations behind `ledger init` and `ledger append`."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from . import _git
from .index import COMMIT_RE, INDEX_FILE
from .receipt import parse_receipt
from .redact import Redactor, Unconfirmed
from .entry import (HUMAN_ONLY_TYPES, TS_FORMAT, TS_RE, Actor, ChainHead, Entry, EntryError, EntryType,
                    HumanActor, next_entry)
from .sink import LedgerError, RepoSink, StaleHead

AUDIT_DIR = ".audit"
RUNS_DIR = "runs"
# Entries copied in from a hand-written record carry data["imported"] (S3 "Migration").
IMPORTED_KEY = "imported"
# S3 "Who typed a human's entry": a human approval or gate answer says who typed it in.
RELAYED_BY_KEY, QUOTE_KEY, SELF_TYPED_KEY = "relayed_by", "quote", "self_typed"
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


def check_typed_by(actor: Actor, entry_type: EntryType, data: Mapping[str, Any] | None) -> None:
    """S3 "Who typed a human's entry": an approval or gate answer naming a human must say who
    typed it. Relayed by an agent: data.relayed_by and data.quote, both non-empty text. Typed
    by the human: data.self_typed is true. Copied from an older record: data.imported, whose
    `by` names the importer. Without one of these, an agent's entry would pass for the human's."""
    if entry_type not in HUMAN_ONLY_TYPES or not isinstance(actor, HumanActor):
        return
    data = data or {}
    relayed = RELAYED_BY_KEY in data
    self_typed = SELF_TYPED_KEY in data
    if self_typed and data[SELF_TYPED_KEY] is not True:
        raise EntryError("schema", "data.self_typed must be true when present")
    if self_typed and RELAYED_BY_KEY in data:
        raise EntryError("schema", "an entry can't be both typed by the human (self_typed) and relayed (relayed_by)")
    if relayed:
        for key in (RELAYED_BY_KEY, QUOTE_KEY):
            if not isinstance(data.get(key), str) or not data[key].strip():
                raise EntryError("schema", f"a relayed {entry_type.value} needs data.relayed_by (the agent that typed "
                                           f"it) and data.quote (the human's words, verbatim), both non-empty")
        return
    if self_typed or IMPORTED_KEY in data:
        return
    raise EntryError("schema", f"a human's {entry_type.value} must say who typed it: an agent relaying it records "
                               f"data.relayed_by and data.quote (--relayed-by and --quote on `charter approve`); "
                               f"a human typing their own passes --typed-by-me")


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


def check_receipt(entry_type: EntryType, data: Mapping[str, Any] | None) -> None:
    """S3 "Receipt entries": a verification entry's data.receipt, when present, follows the
    receipt rules. An entry with no receipt is unchanged."""
    if entry_type is EntryType.VERIFICATION:
        parse_receipt(data or {})


FLAG_KIND = "flag"
FLAG_TEXT_KEYS = ("target", "did_instead", "suggested_fix")


def check_flag(entry_type: EntryType, data: Mapping[str, Any], evidence: Sequence[str]) -> None:
    """S3 "Flag entries": a lesson whose data.kind is "flag" names its target, what was done
    instead and a suggested fix, each non-empty text, and has at least one evidence item.
    Other keys are allowed, and any other lesson is unchanged."""
    if entry_type is not EntryType.LESSON or data.get("kind") != FLAG_KIND:
        return
    for key in FLAG_TEXT_KEYS:
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise EntryError("schema", f"a flag needs data.{key}, a non-empty string "
                                       f"(a flag has data.target, data.did_instead and data.suggested_fix)")
    if not evidence:
        raise EntryError("schema", "a flag needs at least one --evidence item: the command that showed the fault, "
                                   "and its output")


class RedactionRefused(LedgerError):
    """An entry's own text couldn't be confirmed clean after redaction, so it wasn't written."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"refusing: {detail}")
        self.detail = detail


def run_id_for(slug: str, day: date) -> str:
    return f"{day.isoformat()}-{slug}"


def redact_fields(redactor: Redactor, fields: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, int]]:
    """Redact the free-text fields of an entry (summary, why, evidence, data) before chaining.
    Raises RedactionRefused when redaction can't be confirmed, so nothing unconfirmed is written."""
    try:
        clean, counts = redactor.redact_value(dict(fields))
        redactor.confirm(json.dumps(clean, ensure_ascii=False))
    except Unconfirmed as e:
        raise RedactionRefused(str(e)) from None
    except RecursionError:  # brackets nested past what the redactor can walk
        raise RedactionRefused("the text is nested too deeply to check for secrets") from None
    return clean, counts


def init_run(sink: RepoSink, run_id: str, *, actor: Actor, why: str, clock: Clock = utc_now,
             redactor: Redactor | None = None) -> Entry:
    """Write a run's first entry, a `state` entry with data {"state": "started"}."""
    why = redact_fields(redactor or Redactor(), {"why": why})[0]["why"]
    first = next_entry(ChainHead.genesis(run_id), now=clock(), actor=actor, type=EntryType.STATE,
                       summary="Run started", why=why, data={"state": "started"})
    sink.create(first)
    return first


@dataclass(frozen=True)
class Prepared:
    """An entry's text after redaction and the write-time rules, ready to chain."""

    summary: str
    why: str
    evidence: list[str]
    data: dict[str, Any]
    redactions: dict[str, int]  # pattern name -> spans redacted


def prepare(redactor: Redactor, *, actor: Actor, type: EntryType, summary: str, why: str,
            evidence: Sequence[str] = (), data: Mapping[str, Any] | None = None) -> Prepared:
    """Redact an entry's text, then apply every rule checked at write time. `append` and
    `import-tsv` both write through this, so neither can skip a rule the other follows."""
    fields, counts = redact_fields(redactor, {
        "summary": summary, "why": why, "evidence": list(evidence), "data": dict(data or {})})
    check_receipt(type, data)  # the caller's own mistake, reported before redaction can change the text
    clean = fields["data"]
    check_imported(clean)
    check_spend(type, clean)
    check_flag(type, clean, fields["evidence"])
    try:
        check_receipt(type, clean)
    except EntryError as e:
        raise EntryError("schema", f"redaction changed the receipt, so it no longer follows the rules ({e}); "
                                   f"a redaction pattern matches a value the receipt needs") from None
    check_typed_by(actor, type, clean)
    return Prepared(fields["summary"], fields["why"], fields["evidence"], clean, counts)


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
    p = prepare(redactor or Redactor(), actor=actor, type=type, summary=summary, why=why,
                evidence=evidence, data=data)
    summary, why, evidence, data = p.summary, p.why, p.evidence, p.data
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
    committed = _git.file_at(root, commit, rel)
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
