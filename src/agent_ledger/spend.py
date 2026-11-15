"""`ledger spend`: a run's recorded spend against its charter budget (S1 "Budget", S3 "Spend
entries"). Each `spend` entry records spend since the previous one, so the run's spend is the sum."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .entry import Entry, EntryType

MICROS = 1_000_000


@dataclass(frozen=True)
class SpendTotal:
    usd_micros: int
    tokens: int
    entries: int     # spend entries counted
    estimated: int   # of those, how many say estimate: true
    skipped: tuple[int, ...] = ()  # seqs of spend entries with no valid usd_micros (written before the rule)


@dataclass(frozen=True)
class Limit:
    name: str          # "usd" or "tokens", the charter's budget key
    used: int          # micros for usd
    limit: int | None  # None when the charter sets no such limit

    @property
    def reached(self) -> bool:
        return self.limit is not None and self.used >= self.limit


def _count(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def total_spend(entries: Sequence[Entry]) -> SpendTotal:
    usd = tokens = counted = estimated = 0
    skipped = []
    for e in entries:
        if e.type is not EntryType.SPEND:
            continue
        micros = _count(e.data.get("usd_micros"))
        if micros is None:
            skipped.append(e.seq)
            continue
        usd += micros
        tokens += _count(e.data.get("tokens")) or 0
        counted += 1
        estimated += e.data.get("estimate") is True
    return SpendTotal(usd, tokens, counted, estimated, tuple(skipped))


def limits(total: SpendTotal, budget: Mapping[str, Any] | None) -> list[Limit]:
    """The charter budget's usd and tokens limits beside what the run used. `charter check`
    requires both to be positive whole numbers; anything else counts as no limit."""
    budget = budget if isinstance(budget, Mapping) else {}
    usd, tokens = _count(budget.get("usd")), _count(budget.get("tokens"))
    return [Limit("usd", total.usd_micros, None if usd is None else usd * MICROS),
            Limit("tokens", total.tokens, tokens)]


def unrecorded_spend(entries: Sequence[Entry]) -> Entry | None:
    """The last `verification` entry when no `spend` entry follows it, else None. A unit ends
    with its verification, so this is spend the ledger hasn't recorded yet."""
    for e in reversed(entries):
        if e.type is EntryType.SPEND:
            return None
        if e.type is EntryType.VERIFICATION:
            return e
    return None


def usd(micros: int) -> str:
    return f"${micros // MICROS:,}.{micros % MICROS // 10_000:02d}"
