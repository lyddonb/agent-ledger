"""`ledger import-tsv`: an old show-me-your-work decisions.tsv as a new run (S3 "Migration").

The file is read whole, every row becomes one imported `decision` entry, and the run is written
in one step. One bad row means nothing is written, because a ledger only grows: a half
imported trail could not be taken back once merged.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping, Sequence

from .entry import TS_FORMAT, Actor, ChainHead, Entry, EntryError, EntryType, next_entry
from .redact import Redactor
from .run import IMPORTED_KEY, Clock, RedactionRefused, prepare, utc_now
from .sink import LedgerError, RepoSink

DEFAULT_SLUG = "imported-decisions"
DEFAULT_WHY = "An older decisions.tsv trail is copied into the ledger with `ledger import-tsv`"

# Header name -> the field it fills. log.sh wrote `ts phase decision why evidence result`;
# hand-kept trails use `when what why evidence result`.
COLUMNS = {"ts": "when", "when": "when", "decision": "summary", "what": "summary", "why": "why",
           "evidence": "evidence", "result": "result", "phase": "phase"}
REQUIRED = ("when", "summary", "why")
LOG_SH_HEADER = ["ts", "phase", "decision", "why", "evidence", "result"]
# A date, with an optional time to the minute or the second, and an optional Z.
_WHEN_RE = re.compile(r"([0-9]{4}-[0-9]{2}-[0-9]{2})(?:T([0-9]{2}:[0-9]{2})(?::([0-9]{2}))?Z?)?")
WHEN_EXAMPLES = "2026-10-24, 2026-10-24T10:34 or 2026-10-24T10:34:00Z"


class TsvError(LedgerError):
    """The file can't be imported as it is. Every problem is listed, and nothing was written."""

    def __init__(self, problems: Sequence[str]) -> None:
        count = f"{len(problems)} problem{'s' if len(problems) != 1 else ''}"
        super().__init__("\n  ".join([f"nothing imported, {count}:", *problems]))
        self.problems = tuple(problems)


@dataclass(frozen=True)
class Row:
    """One record of the file. `line` is its line number there; the header is line 1."""

    line: int
    when: str       # the time cell as written
    source_ts: str  # that time in ts form
    summary: str
    why: str
    evidence: str   # "" when the cell is empty
    result: str
    phase: str


@dataclass(frozen=True)
class Imported:
    entries: tuple[Entry, ...]                         # the whole run, seq 1 first
    redactions: tuple[tuple[int, dict[str, int]], ...]  # (file line, pattern name -> count); line 0 is --why


def source_ts(when: str) -> str | None:
    """The time a record gives, in ts form, or None when the cell isn't a date or time. A part
    the record leaves out (the time of day, the seconds) is zero. The digits are kept as
    written: a record that names no zone is read as UTC, which is what log.sh wrote."""
    m = _WHEN_RE.fullmatch(when)
    if not m:
        return None
    ts = f"{m[1]}T{m[2] or '00:00'}:{m[3] or '00'}Z"
    try:
        datetime.strptime(ts, TS_FORMAT)
    except ValueError:
        return None
    return ts


def _unguard(text: str) -> str:
    """A log.sh cell as its writer meant it: log.sh put a `'` before a cell starting with `=`,
    `+`, `-` or `@`, so a spreadsheet wouldn't run it as a formula."""
    return text[1:] if len(text) > 1 and text[0] == "'" and text[1] in "=+-@" else text


def parse_tsv(raw: bytes) -> list[Row]:
    """The file's records, or TsvError naming every line that isn't one."""
    rows, problems = _read(raw)
    if problems:
        raise TsvError([f"line {line}: {what}" for line, what in problems])
    return rows


def _read(raw: bytes) -> tuple[list[Row], list[tuple[int, str]]]:
    """The records that parse, and (line, problem) for each line that doesn't. A file with no
    usable header raises instead. Cells are split on tabs and lines on LF (or CRLF) and nothing
    else: there is no quoting in this format."""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise TsvError(["the file is not UTF-8"]) from None
    lines = [line[:-1] if line.endswith("\r") else line for line in text.split("\n")]
    if lines and lines[-1] == "":
        lines.pop()
    if not lines:
        raise TsvError(["the file is empty; it needs a header line and at least one record"])
    names = lines[0].split("\t")
    # The name isn't printed: when the header is missing, line 1 is a record and may hold a secret.
    problems = [f"line 1: cell {i} is not a column name; the columns are {', '.join(COLUMNS)}"
                for i, n in enumerate(names, 1) if n not in COLUMNS]
    fields = [COLUMNS.get(n) for n in names]
    problems += [f"line 1: two columns hold the {f} ({', '.join(n for n in names if COLUMNS.get(n) == f)})"
                 for f in dict.fromkeys(fields) if f and fields.count(f) > 1]
    problems += [f"line 1: no column for the {f}; name one {' or '.join(n for n, v in COLUMNS.items() if v == f)}"
                 for f in REQUIRED if f not in fields]
    if problems:
        raise TsvError(problems)
    rows, bad = [], []
    by_log_sh = names == LOG_SH_HEADER
    for number, line in enumerate(lines[1:], 2):
        if line == lines[0]:
            continue  # log.sh could write its header again on a network mount; it is not a record
        cells = line.split("\t")
        if len(cells) != len(names):
            bad.append((number, f"{len(cells)} cell{'s' if len(cells) != 1 else ''}, "
                                f"but the header names {len(names)} columns"))
            continue
        cell = {f: _unguard(c) if by_log_sh else c for f, c in zip(fields, cells)}
        ts = source_ts(cell["when"])
        if ts is None:
            bad.append((number, f"the {names[fields.index('when')]} cell is not a date or time "
                                f"like {WHEN_EXAMPLES}"))
            continue
        rows.append(Row(number, cell["when"], ts, cell["summary"], cell["why"], cell.get("evidence", ""),
                        cell.get("result", ""), cell.get("phase", "")))
    if not rows and not bad:
        raise TsvError(["the file has a header and no records"])
    return rows, bad


def source_path(file: Path, root: Path) -> str:
    """How entries name the file: its path in the repo, or just its name when it is outside,
    so an entry never carries a home folder."""
    try:
        return file.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return file.name


def already_imported(sink: RepoSink, sha256: str) -> str | None:
    """The run that imported a file with this hash, if one did. An import's first entry records
    it. A run that can't be read isn't consulted: `ledger verify` reports those."""
    if not sink.runs_dir.is_dir():
        return None
    for folder in sorted(sink.runs_dir.iterdir()):
        try:
            first = sink.read(folder.name).entries[0]
        except (LedgerError, EntryError, OSError):
            continue
        source = first.data.get("source")
        if isinstance(source, Mapping) and source.get("sha256") == sha256:
            return first.run_id
    return None


def import_tsv(sink: RepoSink, run_id: str, raw: bytes, path: str, *, actor: Actor, record_actor: Actor,
               why: str = DEFAULT_WHY, clock: Clock = utc_now, redactor: Redactor | None = None) -> Imported:
    """Write the file `raw` (named `path` in the entries) as the new run `run_id`.

    Seq 1 is the importer's own `state` entry, naming the file and its hash. Each record
    follows as a `decision` entry by `record_actor`, marked `data.imported`. Raises TsvError
    when any record can't be an entry, and LedgerError when this file was imported before.
    """
    redactor = redactor or Redactor()
    rows, problems = _read(raw)
    sha256 = hashlib.sha256(raw).hexdigest()
    earlier = already_imported(sink, sha256)
    if earlier is not None:
        raise LedgerError(f"this file was already imported as {earlier}, whose first entry records its "
                          f"sha256; nothing imported")
    source = {"path": path, "sha256": sha256, "rows": len(rows)}
    start = prepare(redactor, actor=actor, type=EntryType.STATE, summary="Run started", why=why,
                    data={"state": "started", "source": source})
    if start.data["source"] != source:
        # The hash is how a second import is recognized, and the path is what every entry cites.
        raise LedgerError("a redaction pattern matches the file's name or its sha256, so the run couldn't "
                          "name what it imported; import a copy under another name, or without that "
                          "pattern; nothing imported")
    first = next_entry(ChainHead.genesis(run_id), now=clock(), actor=actor, type=EntryType.STATE,
                       summary=start.summary, why=start.why, data=start.data)
    entries, redactions = [first], ([(0, start.redactions)] if start.redactions else [])
    for row in rows:
        data = {IMPORTED_KEY: {"source": f"{path}#{row.line}", "source_ts": row.source_ts, "by": actor.id}}
        if row.when != row.source_ts:
            data["when"] = row.when
        data.update({k: v for k, v in (("result", row.result), ("phase", row.phase)) if v})
        try:
            p = prepare(redactor, actor=record_actor, type=EntryType.DECISION, summary=row.summary,
                        why=row.why, evidence=[row.evidence] if row.evidence else [], data=data)
            if p.data[IMPORTED_KEY] != data[IMPORTED_KEY]:
                raise EntryError("schema", "a redaction pattern matches the import marker (the file's name, "
                                           "the record's time or the importer's id)")
            entries.append(next_entry(entries[-1].head, now=clock(), actor=record_actor, type=EntryType.DECISION,
                                      summary=p.summary, why=p.why, evidence=p.evidence, data=p.data))
        except (EntryError, RedactionRefused) as e:
            problems.append((row.line, str(e)))
            continue
        if p.redactions:
            redactions.append((row.line, p.redactions))
    if problems:
        raise TsvError([f"line {line}: {what}" for line, what in sorted(problems)])
    sink.create(first, entries[1:])
    return Imported(tuple(entries), tuple(redactions))
