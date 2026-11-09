"""The ledger entry: its typed model, its hash, and the one way to make the next entry.

Format v1 is specified in docs/specs/S3-audit-ledger.md. Pure: no I/O, no clock.
"""
from __future__ import annotations

import enum
import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Mapping, Sequence, Union

from ._canon import MAX_SAFE_INT, CanonError, JsonValue, canonical_bytes, freeze, show

FORMAT_VERSION: Literal[1] = 1
GENESIS_PREFIX = "genesis:"

HASH_RE = re.compile(r"[0-9a-f]{64}")
RUN_ID_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}-[a-z0-9]+(?:-[a-z0-9]+)*")  # S1: <yyyy-mm-dd>-<slug>
TS_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
# Control characters and Unicode line breaks: summary, why and evidence pointers are one line each.
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")

ENTRY_KEYS = frozenset(
    {"v", "seq", "ts", "run_id", "actor", "type", "summary", "why", "evidence", "data", "prev", "hash"}
)


class EntryType(str, enum.Enum):
    """S3's closed list of entry types. Adding one is a format change."""

    CHARTER = "charter"
    APPROVAL = "approval"
    STATE = "state"
    DECISION = "decision"
    GATE = "gate"
    GATE_ANSWER = "gate-answer"
    VERIFICATION = "verification"
    REVIEW = "review"
    SPEND = "spend"
    LEVER = "lever"
    LESSON = "lesson"
    SESSION = "session"
    SEAL = "seal"


# S2: "Approvals and gate answers have kind: human."
HUMAN_ONLY_TYPES = frozenset({EntryType.APPROVAL, EntryType.GATE_ANSWER})


class EntryError(ValueError):
    """An entry that breaks the v1 schema. `kind` is "schema", "unsupported-version" or
    "hash-mismatch", matching verify's problem kinds."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


def _schema(cond: bool, message: str) -> None:
    if not cond:
        raise EntryError("schema", message)


def _text(value: Any, name: str) -> str:
    _schema(isinstance(value, str) and value != "", f"{name} must be a non-empty string")
    _schema(not _CONTROL_RE.search(value), f"{name} must be one line with no control characters")
    return value


@dataclass(frozen=True)
class AgentActor:
    """S2 agent identity: {"kind": "agent", "id", "on_behalf_of", "tool", "model", "session"}."""

    id: str
    on_behalf_of: str
    tool: str
    model: str
    session: str

    def __post_init__(self) -> None:
        for name in ("id", "on_behalf_of", "tool", "model", "session"):
            _text(getattr(self, name), f"actor.{name}")

    def to_wire(self) -> dict[str, JsonValue]:
        return {"kind": "agent", "id": self.id, "on_behalf_of": self.on_behalf_of,
                "tool": self.tool, "model": self.model, "session": self.session}


@dataclass(frozen=True)
class HumanActor:
    """S2 human identity: {"kind": "human", "id", "on_behalf_of"}. id is the GitHub login."""

    id: str
    on_behalf_of: str

    def __post_init__(self) -> None:
        _text(self.id, "actor.id")
        _text(self.on_behalf_of, "actor.on_behalf_of")

    def to_wire(self) -> dict[str, JsonValue]:
        return {"kind": "human", "id": self.id, "on_behalf_of": self.on_behalf_of}


Actor = Union[AgentActor, HumanActor]


def _actor_from_wire(obj: Any) -> Actor:
    _schema(isinstance(obj, Mapping), "actor must be an object")
    kind = obj.get("kind")
    if kind == "agent":
        keys = {"kind", "id", "on_behalf_of", "tool", "model", "session"}
        _schema(set(obj) == keys, f"agent actor must have exactly the keys {sorted(keys)}")
        return AgentActor(obj["id"], obj["on_behalf_of"], obj["tool"], obj["model"], obj["session"])
    if kind == "human":
        keys = {"kind", "id", "on_behalf_of"}
        _schema(set(obj) == keys, f"human actor must have exactly the keys {sorted(keys)}")
        return HumanActor(obj["id"], obj["on_behalf_of"])
    raise EntryError("schema", 'actor.kind must be "agent" or "human"')


def _check_run_id(run_id: Any) -> None:
    _schema(isinstance(run_id, str) and RUN_ID_RE.fullmatch(run_id) is not None,
            f"run id {show(run_id)} must be <yyyy-mm-dd>-<slug>")
    try:
        datetime.strptime(run_id[:10], "%Y-%m-%d")
    except ValueError:
        raise EntryError("schema", f"run id {show(run_id)} does not start with a real date") from None


@dataclass(frozen=True)
class ChainHead:
    """The tip of one run's chain: what the next entry links to.

    An empty run's head is `ChainHead.genesis(run_id)`: seq 0, link "genesis:<run-id>". So the
    first entry needs no special case: next seq is head.seq + 1 and next prev is head.link.
    """

    run_id: str
    seq: int
    link: str

    @classmethod
    def genesis(cls, run_id: str) -> "ChainHead":
        _check_run_id(run_id)
        return cls(run_id, 0, GENESIS_PREFIX + run_id)


@dataclass(frozen=True)
class Entry:
    """One ledger line. An Entry that exists is valid v1 AND its hash matches its content:
    __post_init__ checks both and deep-freezes `data`, so a stale or forged hash can't be held
    in this type, however it was built."""

    seq: int
    ts: str
    run_id: str
    actor: Actor
    type: EntryType
    summary: str
    why: str
    evidence: tuple[str, ...]
    data: Mapping[str, JsonValue]
    prev: str
    hash: str
    v: Literal[1] = FORMAT_VERSION

    def __post_init__(self) -> None:
        _schema(self.v == FORMAT_VERSION and type(self.v) is int, "v must be 1")
        _schema(type(self.seq) is int and 1 <= self.seq <= MAX_SAFE_INT, "seq must be an integer from 1 to 2**53-1")
        _schema(isinstance(self.ts, str) and TS_RE.fullmatch(self.ts) is not None,
                "ts must be UTC to the second, like 2026-11-08T19:51:55Z")
        try:
            datetime.strptime(self.ts, TS_FORMAT)
        except ValueError:
            raise EntryError("schema", f"ts {show(self.ts)} is not a real calendar time") from None
        _check_run_id(self.run_id)
        _schema(isinstance(self.actor, (AgentActor, HumanActor)), "actor must be an agent or a human")
        _schema(isinstance(self.type, EntryType), "type must be one of " +
                ", ".join(t.value for t in EntryType))
        if self.type in HUMAN_ONLY_TYPES:
            _schema(isinstance(self.actor, HumanActor), f"a {self.type.value} entry must come from a human")
        _text(self.summary, "summary")
        _text(self.why, "why")
        _schema(isinstance(self.evidence, tuple), "evidence must be a list of strings")
        for i, item in enumerate(self.evidence):
            _text(item, f"evidence[{i}]")
        _schema(isinstance(self.data, Mapping), "data must be an object")
        try:
            canonical_bytes(self.data)
        except CanonError as e:
            raise EntryError("schema", f"data: {e}") from None
        object.__setattr__(self, "data", freeze(self.data))
        if self.seq == 1:
            _schema(self.prev == GENESIS_PREFIX + self.run_id, f'seq 1 must have prev "genesis:{self.run_id}"')
        else:
            _schema(isinstance(self.prev, str) and HASH_RE.fullmatch(self.prev) is not None,
                    "prev must be 64 lowercase hex characters")
        _schema(isinstance(self.hash, str) and HASH_RE.fullmatch(self.hash) is not None,
                "hash must be 64 lowercase hex characters")
        actual = _sha256(self.to_wire(with_hash=False))
        if actual != self.hash:
            raise EntryError("hash-mismatch", f"stored hash {self.hash[:12]}... but content hashes to {actual[:12]}...")

    @property
    def head(self) -> ChainHead:
        """The chain head after this entry."""
        return ChainHead(self.run_id, self.seq, self.hash)

    def to_wire(self, *, with_hash: bool = True) -> dict[str, JsonValue]:
        obj = _wire(self.v, self.seq, self.ts, self.run_id, self.actor, self.type, self.summary,
                    self.why, self.evidence, self.data, self.prev)
        if with_hash:
            obj["hash"] = self.hash
        return obj


def _wire(v: int, seq: int, ts: str, run_id: str, actor: Actor, type: EntryType, summary: str,
          why: str, evidence: tuple[str, ...], data: Mapping[str, JsonValue], prev: str,
          ) -> dict[str, JsonValue]:
    """The hash preimage: the wire object without "hash". The single field-to-key mapping."""
    return {"v": v, "seq": seq, "ts": ts, "run_id": run_id, "actor": actor.to_wire(),
            "type": type.value, "summary": summary, "why": why, "evidence": evidence,
            "data": data, "prev": prev}


def _sha256(obj: Mapping[str, JsonValue]) -> str:
    return hashlib.sha256(canonical_bytes(obj)).hexdigest()


def format_ts(now: datetime) -> str:
    """A timezone-aware datetime as the one legal ts spelling (UTC, whole seconds)."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(timezone.utc).strftime(TS_FORMAT)


def next_entry(
    head: ChainHead,
    *,
    now: datetime,
    actor: Actor,
    type: EntryType,
    summary: str,
    why: str,
    evidence: Sequence[str] = (),
    data: Mapping[str, Any] | None = None,
) -> Entry:
    """The one way to make a new entry. Pure: same inputs, same entry.

    The chain decides v, seq, run_id, prev and hash; a caller can't supply them, so it can't
    mis-number or fork a chain except by passing a stale head. Unit 2's append must therefore
    take `head` from a fresh verify under its lock.
    """
    if isinstance(evidence, str):
        raise TypeError("evidence must be a sequence of strings, not one string")
    _schema(isinstance(head.seq, int) and not isinstance(head.seq, bool) and 0 <= head.seq < MAX_SAFE_INT, "the chain is full: seq would pass 2**53-1")
    _schema(isinstance(actor, (AgentActor, HumanActor)), "actor must be an AgentActor or HumanActor")
    _schema(isinstance(type, EntryType), "type must be an EntryType")
    try:
        canonical_bytes(data or {})
    except CanonError as e:
        raise EntryError("schema", f"data: {e}") from None
    seq, ts, prev = head.seq + 1, format_ts(now), head.link
    evidence, frozen = tuple(evidence), freeze(dict(data or {}))
    _schema(all(isinstance(item, str) for item in evidence), "evidence must be strings")
    digest = _sha256(_wire(FORMAT_VERSION, seq, ts, head.run_id, actor, type, summary, why,
                           evidence, frozen, prev))
    return Entry(seq=seq, ts=ts, run_id=head.run_id, actor=actor, type=type, summary=summary,
                 why=why, evidence=evidence, data=frozen, prev=prev, hash=digest)


def encode_line(entry: Entry) -> bytes:
    """The exact bytes a writer appends: CANON(entry) + LF."""
    return canonical_bytes(entry.to_wire()) + b"\n"


def decode_entry(obj: Mapping[str, Any]) -> Entry:
    """A parsed wire object (for example from json.loads) to an Entry, or EntryError.

    `v` is checked first: the other field rules belong to the version. The result is
    deep-frozen, so it never shares mutable state with `obj`.
    """
    _schema(isinstance(obj, Mapping), "an entry must be a JSON object")
    try:
        canonical_bytes(obj)
    except CanonError as e:
        raise EntryError("schema", str(e)) from None
    obj = freeze(obj)  # type: ignore[assignment]
    if obj.get("v") != FORMAT_VERSION or type(obj.get("v")) is not int:
        raise EntryError("unsupported-version", f"v is {show(obj.get('v'))}; this ledger reads v{FORMAT_VERSION}")
    missing, extra = ENTRY_KEYS - set(obj), set(obj) - ENTRY_KEYS
    _schema(not missing, f"missing keys: {', '.join(sorted(missing))}")
    _schema(not extra, f"unknown keys: {show(sorted(extra))}")
    try:
        entry_type = EntryType(obj["type"])
    except ValueError:
        raise EntryError("schema", f"unknown type {show(obj['type'])}") from None
    evidence = obj["evidence"]
    _schema(isinstance(evidence, tuple), "evidence must be a list of strings")
    return Entry(
        seq=obj["seq"], ts=obj["ts"], run_id=obj["run_id"], actor=_actor_from_wire(obj["actor"]),
        type=entry_type, summary=obj["summary"], why=obj["why"], evidence=evidence,
        data=obj["data"], prev=obj["prev"], hash=obj["hash"], v=obj["v"],
    )
