"""Verify one run's ledger.jsonl using its hash chain alone.

What a chain-only check proves, and what it can't, is listed in docs/specs/S3-audit-ledger.md
("What verify detects"). In short: every changed byte, deleted line (except at the end),
reordered pair and forged `prev` is reported on the line where it shows; truncating the end or
rewriting a suffix with recomputed hashes is not, until seals and `verify --base` (M0 unit 4).
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Union

from ._canon import CanonError, parse_canonical
from .entry import GENESIS_PREFIX, RUN_ID_RE, ChainHead, Entry, EntryError, decode_entry

LEDGER_FILE = "ledger.jsonl"
MAX_PROBLEMS = 50


class Kind(str, enum.Enum):
    """Every way a ledger can fail. The values are the stable codes the CLI prints."""

    # whole file (line 0)
    MISSING_LEDGER = "missing-ledger"
    EMPTY_LEDGER = "empty-ledger"
    BAD_RUN_FOLDER = "bad-run-folder"
    # framing
    TORN_LINE = "torn-line"          # the last line has no LF: a crashed or cut-off write
    BAD_FRAMING = "bad-framing"      # a blank line, a CR, or a byte-order mark
    # one line's content; the first failing stage is reported
    BAD_UTF8 = "bad-utf8"
    BAD_JSON = "bad-json"            # syntax, duplicate key, float, NaN, out-of-range int, bad key
    NON_CANONICAL = "non-canonical"  # valid ledger JSON, but not the canonical bytes for it
    UNSUPPORTED_VERSION = "unsupported-version"
    SCHEMA = "schema"
    HASH_MISMATCH = "hash-mismatch"  # the stored hash isn't the hash of the line's content
    # links between a line and the one before it
    SEQ_MISMATCH = "seq-mismatch"
    PREV_MISMATCH = "prev-mismatch"
    RUN_ID_MISMATCH = "run-id-mismatch"


@dataclass(frozen=True)
class Problem:
    line: int  # 1-based line in ledger.jsonl; 0 means the whole file
    kind: Kind
    message: str  # expected vs found; never quotes the entry's free text


@dataclass(frozen=True)
class Verified:
    """Every line is valid and the chain is unbroken. Only a Verified has a head, so unit 2
    can't append to a broken chain without going around the type."""

    run_id: str
    entries: tuple[Entry, ...]  # never empty

    @property
    def head(self) -> ChainHead:
        return self.entries[-1].head


@dataclass(frozen=True)
class Broken:
    run_id: str
    problems: tuple[Problem, ...]  # never empty; ordered by line; at most MAX_PROBLEMS
    omitted: int  # problems past the cap


Report = Union[Verified, Broken]


def _int(value: Any) -> int | None:
    return value if type(value) is int else None


def verify_bytes(run_id: str, data: bytes) -> Report:
    """Check one run's ledger bytes. Pure. Reports every problem, each on the line where it shows.

    Each line is linked to the previous line's *stored* seq and hash, even when that line's own
    hash is wrong. So an edited line is reported once, on that line, instead of breaking every
    line after it; an edit with a recomputed hash shows up as a prev-mismatch on the next line;
    and a deleted line shows up only on the line after the gap. When a line can't be parsed at
    all, the next line's links are unchecked, not passed: the run already fails.
    """
    problems: list[Problem] = []
    entries: list[Entry] = []
    if not data:
        return Broken(run_id, (Problem(0, Kind.EMPTY_LEDGER, "ledger.jsonl is empty; a run always starts with one entry"),), 0)

    bodies = data.split(b"\n")
    tail = bodies.pop()  # b"" when the file ends with LF
    if tail:
        bodies.append(tail)
        problems.append(Problem(len(bodies), Kind.TORN_LINE, "last line has no newline (a write was cut off)"))

    link: tuple[int, str] | None = (0, GENESIS_PREFIX + run_id)
    for n, body in enumerate(bodies, 1):
        framing = None
        if not body:
            framing = "blank line"
        elif b"\r" in body:
            framing = "carriage return (CRLF line endings?)"
        elif n == 1 and body.startswith(b"\xef\xbb\xbf"):
            framing = "byte-order mark"
        if framing:
            problems.append(Problem(n, Kind.BAD_FRAMING, framing))
            link = None
            continue
        try:
            obj = parse_canonical(body)
        except CanonError as e:
            problems.append(Problem(n, Kind(e.kind), str(e)))
            link = None
            continue

        seq, prev, stored = _int(obj.get("seq")), obj.get("prev"), obj.get("hash")
        if link is not None:
            want_seq, want_prev = link[0] + 1, link[1]
            if seq != want_seq:
                problems.append(Problem(n, Kind.SEQ_MISMATCH, f"seq is {obj.get('seq')!r}, expected {want_seq}"))
            if prev != want_prev:
                where = "the genesis link" if want_prev.startswith(GENESIS_PREFIX) else f"line {n - 1}'s hash"
                problems.append(Problem(n, Kind.PREV_MISMATCH, f"prev {_short(prev)} does not match {where} {_short(want_prev)}"))
        if obj.get("run_id") != run_id:
            problems.append(Problem(n, Kind.RUN_ID_MISMATCH, f"run_id {_short(obj.get('run_id'))} is not this run ({run_id})"))
        try:
            entries.append(decode_entry(obj))
        except EntryError as e:
            problems.append(Problem(n, Kind(e.kind), str(e)))
        link = (seq, stored) if seq is not None and isinstance(stored, str) else None

    if not problems:
        return Verified(run_id, tuple(entries))
    problems.sort(key=lambda p: p.line)
    return Broken(run_id, tuple(problems[:MAX_PROBLEMS]), max(0, len(problems) - MAX_PROBLEMS))


def verify_run(run_dir: Path) -> Report:
    """Verify `<run_dir>/ledger.jsonl`. The run id is the folder name (S3 layout).

    Raises OSError when the folder or file can't be read for a reason other than a missing
    ledger; a missing ledger in a run folder is a failed check, not an I/O error.
    """
    run_id = run_dir.name
    if not run_dir.is_dir():
        raise NotADirectoryError(f"{run_dir} is not a directory")
    if not RUN_ID_RE.fullmatch(run_id):
        return Broken(run_id, (Problem(0, Kind.BAD_RUN_FOLDER, f"folder name {run_id!r} is not a run id (<yyyy-mm-dd>-<slug>)"),), 0)
    path = run_dir / LEDGER_FILE
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return Broken(run_id, (Problem(0, Kind.MISSING_LEDGER, f"{LEDGER_FILE} not found"),), 0)
    return verify_bytes(run_id, data)


def _short(value: Any) -> str:
    if isinstance(value, str) and len(value) > 20:
        return value[:16] + "..."
    return repr(value) if not isinstance(value, str) else value
