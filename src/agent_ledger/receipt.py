"""Receipts: a `verification` entry that records one criterion's probe, the commit it ran on and
what came back (S3 "Receipt entries"). `parse_receipt` holds every rule, so `ledger append` and
`ledger receipts` can't disagree about what a valid receipt is."""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Sequence

from ._text import visible
from .entry import Entry, EntryError, EntryType
from .index import COMMIT_RE

CRITERION_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*-[A-Z](?:0|[1-9][0-9]*)")  # no leading zeros: C8 and C08 would be two rows
SHA256_RE = re.compile(r"[0-9a-f]{64}")
_ID_PARTS_RE = re.compile(r"([A-Za-z][A-Za-z0-9]*)-([A-Z])([0-9]+)")


class Verdict(Enum):
    VERIFIED = "VERIFIED"
    NOT_VERIFIED = "NOT VERIFIED"
    INCONCLUSIVE = "INCONCLUSIVE"


@dataclass(frozen=True)
class Receipt:
    verdict: Verdict
    criterion: str
    spec: str
    spec_sha256: str
    probe: str
    commit: str
    exit: int | None = None
    dirty: bool = False
    not_run: str | None = None


def _refuse(rule: str) -> EntryError:
    return EntryError("schema", rule)


def _text(obj: Mapping[str, Any], key: str) -> str:
    if key not in obj:
        raise _refuse(f"data.receipt.{key} is required")
    value = obj[key]
    if not isinstance(value, str) or not value:
        raise _refuse(f"data.receipt.{key} must be a non-empty string")
    return value


def parse_receipt(data: Mapping[str, Any]) -> Receipt | None:
    """The receipt in a verification entry's `data`, or None when `data` has no `receipt` key.
    Raises EntryError("schema", ...) naming the key and the rule when the receipt breaks one."""
    if "receipt" not in data:
        return None
    verdicts = {v.value: v for v in Verdict}
    verdict = data.get("verdict")
    if not isinstance(verdict, str) or verdict not in verdicts:
        raise _refuse("data.verdict is required beside data.receipt and must be one of "
                      + ", ".join(verdicts))
    raw = data["receipt"]
    if not isinstance(raw, Mapping):
        raise _refuse("data.receipt must be an object")

    criterion = _text(raw, "criterion")
    if not CRITERION_RE.fullmatch(criterion):
        raise _refuse("data.receipt.criterion must look like P2e-C8 (a phase, a dash, a letter, a number "
                      "with no leading zero)")
    spec = _text(raw, "spec")
    sha = _text(raw, "spec_sha256")
    if not SHA256_RE.fullmatch(sha):
        raise _refuse("data.receipt.spec_sha256 must be 64 lowercase hex characters")
    probe = _text(raw, "probe")
    commit = _text(raw, "commit")
    if not COMMIT_RE.fullmatch(commit):
        raise _refuse("data.receipt.commit must be 40 or 64 lowercase hex characters")

    code = raw.get("exit")
    if code is not None and type(code) is not int:  # bool is an int subclass; refuse it
        raise _refuse("data.receipt.exit must be an integer or null")
    dirty = raw.get("dirty", False)
    if type(dirty) is not bool:
        raise _refuse("data.receipt.dirty must be true or false")
    not_run = _text(raw, "not_run") if "not_run" in raw else None

    receipt = Receipt(verdicts[verdict], criterion, spec, sha, probe, commit, code, dirty, not_run)
    if receipt.verdict is Verdict.VERIFIED:
        if code not in (0, None):
            raise _refuse("data.verdict VERIFIED needs data.receipt.exit to be 0 or null; "
                          "record a failed probe as NOT VERIFIED")
        if not_run is not None:
            raise _refuse("data.verdict VERIFIED can't have data.receipt.not_run; record it as INCONCLUSIVE")
        if dirty:
            raise _refuse("data.verdict VERIFIED can't have data.receipt.dirty true; record it as INCONCLUSIVE")
    return receipt


@dataclass(frozen=True)
class ReceiptRow:
    receipt: Receipt
    seq: int
    earlier: int                    # valid receipts for this criterion before this one
    evidence: str | None            # the entry's first evidence pointer
    imported: bool = False          # the entry was imported from an older record, not written by a probe
    broken_later: tuple[int, ...] = ()  # seqs of later receipts for this criterion that break the rules


@dataclass(frozen=True)
class Invalid:
    seq: int
    criterion: str | None  # the criterion it names, when that much of it is readable


@dataclass(frozen=True)
class LatestReceipts:
    rows: tuple[ReceiptRow, ...]       # one per criterion, in id order
    invalid: tuple[Invalid, ...] = ()  # verification entries whose receipt breaks the rules


def _id_key(criterion: str) -> tuple[tuple[tuple[int, int | str], ...], str, int]:
    """Natural order: P2 before P10, P2 before P2e, C2 before C10."""
    prefix, letter, number = _ID_PARTS_RE.fullmatch(criterion).groups()  # type: ignore[union-attr]
    chunks = tuple((0, int(c)) if c.isdigit() else (1, c) for c in re.findall(r"[0-9]+|[^0-9]+", prefix))
    return chunks, letter, int(number)


def _named_criterion(data: Mapping[str, Any]) -> str | None:
    raw = data.get("receipt")
    criterion = raw.get("criterion") if isinstance(raw, Mapping) else None
    return criterion if isinstance(criterion, str) and CRITERION_RE.fullmatch(criterion) else None


def latest_receipts(entries: Sequence[Entry]) -> LatestReceipts:
    latest: dict[str, ReceiptRow] = {}
    invalid: list[Invalid] = []
    for e in entries:
        if e.type is not EntryType.VERIFICATION:
            continue
        try:
            receipt = parse_receipt(e.data)
        except EntryError:
            invalid.append(Invalid(e.seq, _named_criterion(e.data)))
            continue
        if receipt is None:
            continue
        before = latest.get(receipt.criterion)
        latest[receipt.criterion] = ReceiptRow(
            receipt, e.seq, 0 if before is None else before.earlier + 1, e.evidence[0] if e.evidence else None,
            imported="imported" in e.data)
    rows = tuple(
        ReceiptRow(r.receipt, r.seq, r.earlier, r.evidence, r.imported,
                   tuple(i.seq for i in invalid if i.criterion == c and i.seq > r.seq))
        for c, r in ((c, latest[c]) for c in sorted(latest, key=_id_key)))
    return LatestReceipts(rows, tuple(invalid))


HEADER = ("Criterion", "Verdict", "Probe", "Commit", "Evidence", "Entry")


def _code(text: str) -> str:
    """A Markdown code span that holds `text` and nothing else. Its fence is one backtick longer
    than any run inside, and a pipe is written \\| so it can't end the table cell: a pipe after a
    backslash is escaped in a table however many backslashes come before it."""
    body = visible(text).replace("|", "\\|")
    if not body.strip():
        body = "(blank)"
    longest = max((len(run) for run in re.findall(r"`+", body)), default=0)
    fence = "`" * (longest + 1)
    pad = " " if body.startswith("`") or body.endswith("`") else ""
    return f"{fence}{pad}{body}{pad}{fence}"


def _row(row: ReceiptRow) -> tuple[str, ...]:
    r = row.receipt
    verdict = r.verdict.value
    if r.not_run is not None:
        verdict += f" (not run: {_code(r.not_run)})"
    if r.dirty:
        verdict += " (uncommitted changes)"
    if row.broken_later:
        # the newest record for this criterion can't be read, so this row may be out of date
        verdict += f" (a later receipt breaks the rules: seq {', '.join(map(str, row.broken_later))})"
    probe = _code(r.probe) + (f" (exit {r.exit})" if r.exit is not None else "")
    entry = f"seq {row.seq}" + (f", {row.earlier} earlier" if row.earlier else "") + (", imported" if row.imported else "")
    return (r.criterion, verdict, probe, _code(r.commit[:7]),
            _code(row.evidence) if row.evidence is not None else "", entry)


def render_table(rows: Sequence[ReceiptRow]) -> str:
    """A Markdown table: two header lines, then one line per row. Receipt text is untrusted, so
    every part of it sits in a code span that can't hold a pipe, a line break, an invisible character or a backtick run
    that ends the span early; the other cells are fixed words and integers."""
    lines = [HEADER, ("---",) * len(HEADER), *(_row(r) for r in rows)]
    return "\n".join("| " + " | ".join(cells) + " |" for cells in lines)
