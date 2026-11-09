"""Shared test fixtures: random but seeded chains, and byte-level ledger surgery."""
import hashlib
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent_ledger._canon import canonical_bytes  # noqa: E402
from agent_ledger.entry import (  # noqa: E402
    HUMAN_ONLY_TYPES, AgentActor, ChainHead, EntryType, HumanActor, encode_line, next_entry,
)

RUN_ID = "2026-11-08-property"
T0 = datetime(2026, 11, 8, 12, 0, 0, tzinfo=timezone.utc)

# Characters that stress the canonical form: JSON escapes, non-ASCII, astral plane, U+2028, DEL.
_TEXT_POOL = list("abcxyz019 _-./:#@") + ['"', "\\", "/", "é", "中", "😀", "ß"]
_DATA_POOL = _TEXT_POOL + ["\u2028", "\n", "\t", "\x00", "\x1b", "\x7f"]


def rand_text(rng: random.Random, pool=_TEXT_POOL) -> str:
    return "".join(rng.choice(pool) for _ in range(rng.randint(1, 24)))


def rand_value(rng: random.Random, depth: int = 0):
    roll = rng.random()
    if depth >= 3 or roll < 0.5:
        return rng.choice([
            None, True, False, 0, -1, rng.randint(-(2**53 - 1), 2**53 - 1), rand_text(rng, _DATA_POOL),
        ])
    if roll < 0.75:
        return [rand_value(rng, depth + 1) for _ in range(rng.randint(0, 3))]
    return {rand_key(rng): rand_value(rng, depth + 1) for _ in range(rng.randint(0, 3))}


def rand_key(rng: random.Random) -> str:
    return rng.choice("abcdefghijklmnopqrstuvwxyz") + "".join(
        rng.choice("abcz019_") for _ in range(rng.randint(0, 8)))


def rand_actor(rng: random.Random, human: bool):
    if human:
        return HumanActor(rng.choice(["lyddonb", "a-reviewer"]), "lyddonb@github")
    session = "".join(rng.choice("0123456789abcdef") for _ in range(8))
    return AgentActor(f"beau-agent@claude-code#{session[:4]}", "lyddonb@github", "claude-code",
                      rng.choice(["opus", "sonnet"]), session)


def build_chain(rng: random.Random, n: int, run_id: str = RUN_ID) -> list[bytes]:
    """n valid, chained ledger lines (each ending in LF)."""
    head = ChainHead.genesis(run_id)
    lines = []
    for i in range(n):
        etype = rng.choice(list(EntryType))
        human = etype in HUMAN_ONLY_TYPES or rng.random() < 0.2
        entry = next_entry(
            head, now=T0 + timedelta(seconds=i), actor=rand_actor(rng, human), type=etype,
            summary=rand_text(rng), why=rand_text(rng),
            evidence=[rand_text(rng) for _ in range(rng.randint(0, 3))],
            data={rand_key(rng): rand_value(rng) for _ in range(rng.randint(0, 3))},
        )
        lines.append(encode_line(entry))
        head = entry.head
    return lines


def resign(line: bytes, **changes) -> bytes:
    """Apply `changes` to a line's fields and recompute its hash, as a forger with write access would."""
    import json
    obj = json.loads(line)
    obj.update(changes)
    obj.pop("hash")
    obj["hash"] = hashlib.sha256(canonical_bytes(obj)).hexdigest()
    return canonical_bytes(obj) + b"\n"
