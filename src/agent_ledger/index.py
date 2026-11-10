"""`.audit/INDEX.jsonl`: one line per sealed run, itself hash-chained (S3 "Seals and INDEX").

Each line is the canonical JSON of
    {"v": 1, "seq": n, "ts": ..., "run_id": ..., "seal": <the seal entry's hash>,
     "commit": <the commit the seal names>, "prev": <previous line's hash or "genesis:index">,
     "hash": <sha256 of the line without "hash">}
with the same canonical form, framing and hashing rules as a run ledger. Pure: no I/O.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Union

from ._canon import CanonError, canonical_bytes, parse_canonical, show
from .entry import FORMAT_VERSION, HASH_RE, TS_RE, _check_run_id, format_ts
from .verify import Kind, Problem

INDEX_FILE = "INDEX.jsonl"
INDEX_GENESIS = "genesis:index"
INDEX_KEYS = frozenset({"v", "seq", "ts", "run_id", "seal", "commit", "prev", "hash"})
COMMIT_RE = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")  # SHA-1, or SHA-256 repos


@dataclass(frozen=True)
class IndexLine:
    seq: int
    ts: str
    run_id: str
    seal: str
    commit: str
    prev: str
    hash: str


@dataclass(frozen=True)
class IndexOk:
    lines: tuple[IndexLine, ...]  # may be empty: no run sealed yet

    @property
    def head(self) -> tuple[int, str]:
        return (self.lines[-1].seq, self.lines[-1].hash) if self.lines else (0, INDEX_GENESIS)


@dataclass(frozen=True)
class IndexBroken:
    problems: tuple[Problem, ...]


IndexReport = Union[IndexOk, IndexBroken]


def _hash(obj: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(obj)).hexdigest()


def next_line(head: tuple[int, str], *, now: datetime, run_id: str, seal: str, commit: str) -> bytes:
    """The encoded line that extends an index whose head is `head` (seq, hash)."""
    _check_run_id(run_id)
    if not HASH_RE.fullmatch(seal) or not COMMIT_RE.fullmatch(commit):
        raise ValueError("seal must be a 64-hex entry hash and commit a full lowercase commit id")
    obj: dict[str, Any] = {"v": FORMAT_VERSION, "seq": head[0] + 1, "ts": format_ts(now), "run_id": run_id,
                           "seal": seal, "commit": commit, "prev": head[1]}
    obj["hash"] = _hash(obj)
    return canonical_bytes(obj) + b"\n"


def _check(obj: Any) -> str | None:
    """The first schema problem with a parsed index line, or None."""
    if set(obj) != INDEX_KEYS:
        return f"keys must be exactly {sorted(INDEX_KEYS)}, found {sorted(obj)}"
    if obj["v"] != FORMAT_VERSION:
        return f"v is {show(obj['v'])}, expected {FORMAT_VERSION}"
    if type(obj["seq"]) is not int or obj["seq"] < 1:
        return "seq must be a positive integer"
    if not isinstance(obj["ts"], str) or not TS_RE.fullmatch(obj["ts"]):
        return f"ts {show(obj['ts'])} is not a UTC time like 2026-11-09T19:51:55Z"
    try:
        _check_run_id(obj["run_id"])
    except ValueError as e:
        return str(e)
    if not isinstance(obj["seal"], str) or not HASH_RE.fullmatch(obj["seal"]):
        return f"seal {show(obj['seal'])} is not an entry hash"
    if not isinstance(obj["commit"], str) or not COMMIT_RE.fullmatch(obj["commit"]):
        return f"commit {show(obj['commit'])} is not a full commit id"
    return None


def verify_index_bytes(data: bytes) -> IndexReport:
    """Check INDEX.jsonl's framing, canonical form, schema and chain. An empty file is an
    empty index; cutting lines off a merged index is caught by `verify --base`, not here."""
    problems: list[Problem] = []
    lines: list[IndexLine] = []
    if not data:
        return IndexOk(())
    bodies = data.split(b"\n")
    if bodies[-1]:
        problems.append(Problem(len(bodies), Kind.TORN_LINE, "last line has no newline (a write was cut off)"))
    else:
        bodies.pop()
    link: tuple[int, str] | None = (0, INDEX_GENESIS)
    seen: dict[str, int] = {}
    for n, body in enumerate(bodies, 1):
        if not body or b"\r" in body or (n == 1 and body.startswith(b"\xef\xbb\xbf")):
            problems.append(Problem(n, Kind.BAD_FRAMING, "blank line, carriage return or byte-order mark"))
            link = None
            continue
        try:
            obj = parse_canonical(body)
        except CanonError as e:
            problems.append(Problem(n, Kind(e.kind), str(e)))
            link = None
            continue
        bad = _check(obj)
        if bad:
            problems.append(Problem(n, Kind.SCHEMA, bad))
            link = None
            continue
        if link is not None and (obj["seq"], obj["prev"]) != (link[0] + 1, link[1]):
            kind = Kind.SEQ_MISMATCH if obj["seq"] != link[0] + 1 else Kind.PREV_MISMATCH
            problems.append(Problem(n, kind, f"seq {obj['seq']} prev {show(obj['prev'])} does not extend "
                                             f"seq {link[0]} {show(link[1])}"))
        stored = obj["hash"]
        body_obj = {k: v for k, v in obj.items() if k != "hash"}
        if not isinstance(stored, str) or _hash(body_obj) != stored:
            problems.append(Problem(n, Kind.HASH_MISMATCH, f"hash {show(stored)} is not the hash of this line"))
        if obj["run_id"] in seen:
            problems.append(Problem(n, Kind.DUPLICATE_SEAL, f"{obj['run_id']} is already indexed on line {seen[obj['run_id']]}"))
        seen.setdefault(obj["run_id"], n)
        lines.append(IndexLine(obj["seq"], obj["ts"], obj["run_id"], obj["seal"], obj["commit"], obj["prev"], stored))
        link = (obj["seq"], stored) if isinstance(stored, str) else None
    if problems:
        return IndexBroken(tuple(sorted(problems, key=lambda p: p.line)))
    return IndexOk(tuple(lines))
