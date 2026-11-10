"""Run charters (S1): read and check `.audit/runs/<run-id>/charter.md`, write a new one from
the template, and record a human's approval in the run's ledger.

The front matter is YAML, and the standard library has no YAML parser. Charters use a small
subset, so this reads exactly that and refuses anything else with the line number:

- `key: value` at the top level;
- values: plain scalars (`2`, `true`, `10h`, `src/**`), "double" or 'single' quoted strings,
  flow lists `[a, b]` and flow maps `{k: v}`, nested;
- a key with nothing after it, followed by a block list of `- value` or `- key: value` items.

Refusing beats guessing here: a field read wrong could approve the wrong budget or scope.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .entry import EntryType, HumanActor, _check_run_id
from .sink import LedgerError, RepoSink

CHARTER_FILE = "charter.md"

# S2's default gates. A charter may add gates but never remove one (S2 "Files and precedence");
# policy files can add more, which unit 7 reads.
DEFAULT_GATES = ("migration", "delete-data", "public-api", "deploy", "auth-permissions",
                 "new-dependency", "secrets", "outbound-message", "license", "spend")
SPEC_LEVELS = ("types", "schemas", "property-tests", "model-check")
SOC2_KEYS = ("approved_before_build", "tested", "independent_review", "recorded")
BODY_SECTIONS = ("Goal", "Scope and non-scope", "Plan", "Gates and rollback", "Staffing and why",
                 "Guardrails", "Security notes", "Review plan", "Budget and estimate", "Risks")
PLACEHOLDER = "TODO"
_PLACEHOLDER_RE = re.compile(r"\b(?:TODO|TBD|FIXME|XXX)\b", re.IGNORECASE)
MAX_DEPTH = 16
MAX_INT_DIGITS = 18


class CharterError(ValueError):
    def __init__(self, line: int, message: str) -> None:
        super().__init__(f"line {line}: {message}" if line else message)
        self.line = line
        self.message = message


@dataclass(frozen=True)
class Problem:
    line: int  # 1-based line in charter.md; 0 means the whole file
    message: str


# --- reading the front matter -------------------------------------------------------------

_INT = re.compile(r"-?[0-9]+")
_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_\-]*")


class _Flow:
    """Recursive-descent reader for one value: a scalar, or flow lists and maps."""

    def __init__(self, text: str, line: int) -> None:
        self.s, self.i, self.line = text, 0, line

    def fail(self, message: str) -> CharterError:
        return CharterError(self.line, f"{message} (at column {self.i + 1})")

    def ws(self) -> None:
        while self.i < len(self.s) and self.s[self.i] == " ":
            self.i += 1

    def value(self, stops: str, depth: int = 0) -> Any:
        if depth > MAX_DEPTH:
            raise self.fail(f"nested more than {MAX_DEPTH} levels deep")
        self.depth = depth
        self.ws()
        if self.i >= len(self.s):
            raise self.fail("a value is missing")
        c = self.s[self.i]
        if c == "[":
            return self.seq()
        if c == "{":
            return self.mapping()
        if c == '"':
            return self.double()
        if c == "'":
            return self.single()
        return self.plain(stops)

    def seq(self) -> list[Any]:
        self.i += 1
        out: list[Any] = []
        depth = self.depth
        self.ws()
        if self.s[self.i:self.i + 1] == "]":
            self.i += 1
            return out
        while True:
            out.append(self.value(",]", depth + 1))
            self.depth = depth
            self.ws()
            c = self.s[self.i:self.i + 1]
            self.i += 1
            if c == "]":
                return out
            if c != ",":
                raise self.fail("expected , or ] in a list")

    def mapping(self) -> dict[str, Any]:
        self.i += 1
        out: dict[str, Any] = {}
        self.ws()
        if self.s[self.i:self.i + 1] == "}":
            self.i += 1
            return out
        while True:
            self.ws()
            m = _KEY.match(self.s, self.i)
            if not m or self.s[m.end():m.end() + 1] != ":":
                raise self.fail("expected key: value in a map")
            key = m.group()
            if key in out:
                raise self.fail(f"duplicate key {key!r}")
            self.i = m.end() + 1
            depth = self.depth
            out[key] = self.value(",}", depth + 1)
            self.depth = depth
            self.ws()
            c = self.s[self.i:self.i + 1]
            self.i += 1
            if c == "}":
                return out
            if c != ",":
                raise self.fail("expected , or } in a map")

    def double(self) -> str:
        m = re.compile(r'"((?:[^"\\]|\\.)*)"').match(self.s, self.i)
        if not m:
            raise self.fail("unterminated double-quoted string")
        self.i = m.end()
        try:
            return json.loads(m.group())  # YAML's double-quoted escapes, for the common ones
        except ValueError:
            raise self.fail("unsupported escape in a double-quoted string") from None

    def single(self) -> str:
        m = re.compile(r"'((?:[^']|'')*)'").match(self.s, self.i)
        if not m:
            raise self.fail("unterminated single-quoted string")
        self.i = m.end()
        return m.group(1).replace("''", "'")

    def plain(self, stops: str) -> Any:
        start = self.i
        while self.i < len(self.s) and self.s[self.i] not in stops:
            if self.s[self.i] in "[]{}" or (self.s[self.i] == ":" and self.s[self.i + 1:self.i + 2] in (" ", "")):
                raise self.fail(f"{self.s[self.i]!r} in a plain value; quote the value")
            self.i += 1
        text = self.s[start:self.i].strip()
        if not text:
            raise self.fail("a value is missing")
        if text[0] in "&*!|>%@`?":
            raise self.fail(f"YAML feature {text[0]!r} isn't supported in a charter")
        if _INT.fullmatch(text):
            digits = text.lstrip("-")
            if len(digits) > 1 and digits[0] == "0":
                raise self.fail(f"{text} has a leading zero (YAML may read it as octal); quote it or drop the zero")
            if len(digits) > MAX_INT_DIGITS:
                raise self.fail(f"a number over {MAX_INT_DIGITS} digits")
            return int(text)
        return {"true": True, "false": False, "null": None, "~": None}.get(text, text)


def _strip_comment(line: str) -> str:
    """Drop a trailing `# comment` that sits outside quotes."""
    quote, escaped = None, False
    for i, c in enumerate(line):
        if quote:
            if escaped:
                escaped = False
            elif c == "\\" and quote == '"':
                escaped = True
            elif c == quote:
                quote = None
        elif c in "\"'" and (i == 0 or line[i - 1] in " [{,:"):
            quote = c
        elif c == "#" and (i == 0 or line[i - 1] == " "):
            return line[:i].rstrip()
    return line.rstrip()


def _one(text: str, line: int) -> Any:
    reader = _Flow(text, line)
    value = reader.value("")
    reader.ws()
    if reader.i != len(reader.s):
        raise reader.fail("unexpected text after the value")
    return value


def parse_front_matter(lines: list[str], first: int) -> tuple[dict[str, Any], dict[str, int]]:
    """Fields and the line each starts on. `lines` are the front matter lines; `first` is the
    1-based file line of lines[0]."""
    fields: dict[str, Any] = {}
    where: dict[str, int] = {}
    i = 0
    while i < len(lines):
        n = first + i
        if "\t" in lines[i]:
            raise CharterError(n, "a tab in the front matter; use spaces")
        raw = _strip_comment(lines[i])
        i += 1
        if not raw.strip():
            continue
        if raw[0] == " ":
            raise CharterError(n, "unexpected indentation")
        m = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*):(.*)", raw)
        if not m:
            raise CharterError(n, "expected key: value")
        key, rest = m.group(1), m.group(2).strip()
        if key in fields:
            raise CharterError(n, f"duplicate key {key!r}")
        where[key] = n
        if rest:
            fields[key] = _one(rest, n)
            continue
        items: list[Any] = []
        while i < len(lines):
            if "\t" in lines[i]:
                raise CharterError(first + i, "a tab in the front matter; use spaces")
            item = _strip_comment(lines[i])
            if not item.strip():
                i += 1
                continue
            im = re.fullmatch(r"  - (.+)", item)
            if not im:
                break
            body = im.group(1)
            km = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_.\-]*): (.+)", body)
            items.append({km.group(1): _one(km.group(2), first + i)} if km and body[0] not in "{[\"'" else _one(body, first + i))
            i += 1
        if not items:
            raise CharterError(n, f"{key} has no value; write it on the same line or as a `  - item` list")
        fields[key] = items
    return fields, where


@dataclass
class Charter:
    path: Path
    raw: bytes
    fields: dict[str, Any]
    where: dict[str, int]
    headings: list[tuple[int, str]]  # (line, text) of each `## ` heading in the body
    body: list[tuple[int, str]] = field(default_factory=list)  # body lines outside fences and comments

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.raw).hexdigest()

    def line(self, key: str) -> int:
        return self.where.get(key, 1)


def read_charter(path: Path) -> Charter:
    """Raises CharterError for a file that can't be read as a charter at all."""
    for part in (path, *list(path.parents)[:3]):  # charter.md, the run folder, runs, .audit
        if part.is_symlink():
            raise CharterError(0, f"{part} is a symlink; a charter is read only from real files and folders")
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise CharterError(0, f"{path} not found") from None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise CharterError(0, f"not UTF-8 at byte {e.start}") from None
    lines = text.split("\n")
    if lines[0].rstrip("\r") != "---":
        raise CharterError(1, "a charter starts with a --- line, then front matter, then ---")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].rstrip("\r") == "---")
    except StopIteration:
        raise CharterError(1, "the front matter has no closing --- line") from None
    if any("\r" in l for l in lines[:end + 1]):
        raise CharterError(0, "carriage returns in the front matter; use LF line endings")
    fields, where = parse_front_matter(lines[1:end], 2)
    body: list[tuple[int, str]] = []
    fence, comment = None, False
    for i in range(end + 1, len(lines)):
        line = lines[i]
        stripped = line.lstrip()
        if fence:
            if stripped.startswith(fence):
                fence = None
            continue
        if comment:
            comment = "-->" not in line
            continue
        if stripped.startswith(("```", "~~~")):
            fence = stripped[:3]
            continue
        if stripped.startswith("<!--"):
            comment = "-->" not in stripped[4:]
            continue
        body.append((i + 1, line))
    # A heading inside a code block or HTML comment isn't a section a reader sees.
    headings = [(n, l[3:].strip()) for n, l in body if l.startswith("## ")]
    return Charter(path, raw, fields, where, headings, body)


# --- checking ------------------------------------------------------------------------------

_DURATION = re.compile(r"([0-9]+)([mhd])")


def _minutes(value: Any) -> int | None:
    m = _DURATION.fullmatch(value) if isinstance(value, str) else None
    return int(m.group(1)) * {"m": 1, "h": 60, "d": 1440}[m.group(2)] if m else None


def _has_placeholder(value: Any) -> bool:
    if isinstance(value, str):
        return _PLACEHOLDER_RE.search(value) is not None
    if isinstance(value, dict):
        return any(_has_placeholder(k) or _has_placeholder(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_has_placeholder(v) for v in value)
    return False


def _positive_int(v: Any) -> bool:
    return type(v) is int and v > 0


def check_charter(c: Charter, run_id: str) -> list[Problem]:
    """Every problem with a charter for its tier (S1), each with the line it starts on."""
    f, out = c.fields, []

    def bad(key: str, message: str) -> None:
        out.append(Problem(c.line(key), message))

    for key in ("run_id", "title", "tier", "predicate", "scope", "gates"):
        if key not in f:
            out.append(Problem(1, f"missing required field {key!r}"))
    if "run_id" in f and f["run_id"] != run_id:
        bad("run_id", f"run_id {f['run_id']!r} doesn't match the run folder {run_id!r}")
    tier = f.get("tier") if type(f.get("tier")) is int else None
    if "tier" in f and tier not in (1, 2, 3):
        bad("tier", "tier must be 1, 2 or 3")
    for key in ("title", "predicate"):
        if key in f and (not isinstance(f[key], str) or not f[key].strip()):
            bad(key, f"{key} must be a non-empty string")
    if "scope" in f and (not isinstance(f["scope"], list) or not f["scope"]):
        bad("scope", "scope must be a non-empty list")
    if "gates" in f:
        gates = f["gates"]
        if not isinstance(gates, list) or not all(isinstance(g, str) for g in gates):
            bad("gates", "gates must be a list of gate ids")
        else:
            missing = [g for g in DEFAULT_GATES if g not in gates]
            if missing:
                bad("gates", f"gates can be added but not removed; missing {', '.join(missing)}")
    for key, value in f.items():
        if _has_placeholder(value):
            bad(key, f"{key} still has a placeholder (TODO, TBD, FIXME or XXX)")
    for n, text in c.body:
        if _PLACEHOLDER_RE.search(text):
            out.append(Problem(n, "the body still has a placeholder (TODO, TBD, FIXME or XXX)"))

    if tier in (2, 3):
        out.extend(_check_significant(c))
    if tier == 3:
        review = f.get("review")
        if isinstance(review, dict):
            lenses = review.get("lenses")
            if not isinstance(lenses, list) or "security" not in lenses:
                bad("review", "tier 3 needs the security lens in review.lenses")
            if review.get("decision_review") is not True:
                bad("review", "tier 3 needs review.decision_review: true")
    return sorted(out, key=lambda p: p.line)


def _check_significant(c: Charter) -> list[Problem]:
    f, out = c.fields, []

    def bad(key: str, message: str) -> None:
        out.append(Problem(c.line(key), message))

    for key in ("budget", "estimate", "staffing", "review", "spec_level", "rollback", "soc2"):
        if key not in f:
            out.append(Problem(1, f"tier 2 and 3 need {key!r}"))
    budget, estimate = f.get("budget"), f.get("estimate")
    if "budget" in f:
        if not isinstance(budget, dict):
            bad("budget", "budget must be a map")
        else:
            for k in ("usd", "tokens", "max_parallel_agents"):
                if not _positive_int(budget.get(k)):
                    bad("budget", f"budget.{k} must be a positive whole number")
            if not _minutes(budget.get("wall_clock")):
                bad("budget", "budget.wall_clock must be a positive duration like 90m, 8h or 2d")
    if "estimate" in f:
        if not isinstance(estimate, dict):
            bad("estimate", "estimate must be a map")
        else:
            for k in ("usd", "tokens"):
                if type(estimate.get(k)) is not int or estimate[k] < 0:
                    bad("estimate", f"estimate.{k} must be a whole number")
            if _minutes(estimate.get("wall_clock")) is None:
                bad("estimate", "estimate.wall_clock must be a duration like 90m, 8h or 2d")
            if not isinstance(estimate.get("calls"), dict):
                bad("estimate", "estimate.calls must map each kind of call to a planned count")
            if isinstance(budget, dict):
                for k in ("usd", "tokens"):
                    if type(estimate.get(k)) is int and type(budget.get(k)) is int and estimate[k] > budget[k]:
                        bad("estimate", f"estimate.{k} ({estimate[k]}) is over budget.{k} ({budget[k]})")
                e, b = _minutes(estimate.get("wall_clock")), _minutes(budget.get("wall_clock"))
                if e is not None and b is not None and e > b:
                    bad("estimate", "estimate.wall_clock is over budget.wall_clock")
    staffing = f.get("staffing")
    if "staffing" in f:
        if not isinstance(staffing, list) or not staffing:
            bad("staffing", "staffing must be a non-empty list")
        else:
            for n, s in enumerate(staffing, 1):
                if not isinstance(s, dict) or not all(k in s for k in ("role", "tool", "model", "count")):
                    bad("staffing", f"staffing item {n} needs role, tool, model and count")
                elif not _positive_int(s["count"]):
                    bad("staffing", f"staffing item {n}: count must be a positive whole number")
    review = f.get("review")
    if "review" in f and (not isinstance(review, dict) or not isinstance(review.get("lenses"), list)
                          or not review["lenses"] or not isinstance(review.get("decision_review"), bool)):
        bad("review", "review needs lenses (a non-empty list) and decision_review (true or false)")
    if "spec_level" in f and f["spec_level"] not in SPEC_LEVELS:
        bad("spec_level", f"spec_level must be one of {', '.join(SPEC_LEVELS)}")
    if "rollback" in f and (not isinstance(f["rollback"], dict) or not f["rollback"]):
        bad("rollback", "rollback must map each risky change to how it is undone")
    soc2 = f.get("soc2")
    if "soc2" in f and (not isinstance(soc2, dict) or any(k not in soc2 for k in SOC2_KEYS)):
        bad("soc2", f"soc2 needs {', '.join(SOC2_KEYS)}")

    names = [h for _, h in c.headings]
    pos = -1
    for section in BODY_SECTIONS:
        found = next((i for i, h in enumerate(names) if h == section or h.startswith(section + " ")), None)
        if found is None:
            out.append(Problem(0, f"the body needs a `## {section}` section"))
        elif found < pos:
            out.append(Problem(c.headings[found][0], f"`## {section}` is out of order (S1 lists the sections in order)"))
        else:
            pos = found
    return out


# --- approval ------------------------------------------------------------------------------

_EVIDENCE_HASH = re.compile(r"charter\.md sha256 ([0-9a-f]{64})(?:,.*)?")
_SHA256 = re.compile(r"[0-9a-f]{64}")


def approved_hash(entry: Any) -> str | None:
    """The charter hash an approval entry approves: data.charter_sha256, or for approvals
    written before `ledger charter approve` existed, the `charter.md sha256 <hex>` evidence."""
    if not isinstance(entry.actor, HumanActor):
        return None
    value = entry.data.get("charter_sha256")
    if isinstance(value, str):
        return value if _SHA256.fullmatch(value) else None
    for pointer in entry.evidence:
        m = _EVIDENCE_HASH.fullmatch(pointer)
        if m:
            return m.group(1)
    return None


@dataclass(frozen=True)
class Status:
    state: str            # "approved", "stale" (edited since the last approval) or "draft"
    approval_seq: int | None
    approved_sha256: str | None


def approval_status(sink: RepoSink, run_id: str, charter_sha256: str) -> Status:
    """Approved when the latest approval in the run's ledger names this exact charter. An
    approval of an earlier version means the charter was edited after approval (S1: back to
    draft). A run without a ledger has no approval."""
    try:
        entries = sink.read(run_id).entries
    except LedgerError:
        if not (sink.run_dir(run_id) / "ledger.jsonl").exists():
            return Status("draft", None, None)
        raise
    latest = next((e for e in reversed(entries) if e.type is EntryType.APPROVAL and approved_hash(e)), None)
    if latest is None:
        return Status("draft", None, None)
    sha = approved_hash(latest)
    return Status("approved" if sha == charter_sha256 else "stale", latest.seq, sha)


TEMPLATE = """---
run_id: {run_id}
title: "{title}"
tier: {tier}                      # 1 routine, 2 significant, 3 critical
predicate: "TODO: the observable result that means the run is done"
scope: [TODO]                # paths or globs the run may change
budget: {{usd: TODO, tokens: TODO, wall_clock: TODO, max_parallel_agents: TODO}}
estimate: {{usd: TODO, tokens: TODO, wall_clock: TODO, calls: {{rollouts: 0, reviews: TODO}}}}
gates: [{gates}]
staffing:
  - {{role: code, tool: claude-code, model: TODO, count: 1}}
  - {{role: reviewers, tool: claude-code, model: [TODO], count: 2}}
review: {{lenses: [correctness, maintainer], decision_review: false}}
spec_level: TODO             # types | schemas | property-tests | model-check
rollback: {{TODO: "TODO: how each risky change is undone"}}
soc2: {{approved_before_build: true, tested: true, independent_review: true, recorded: true}}
---

# {title}

## Goal

TODO

## Scope and non-scope

TODO

## Plan

TODO: units in order, each verifiable on its own.

## Gates and rollback

TODO

## Staffing and why

TODO

## Guardrails

TODO

## Security notes

TODO

## Review plan

TODO

## Budget and estimate

TODO

## Risks

TODO: a short pre-mortem.
"""


def new_charter(runs_dir: Path, run_id: str, *, title: str, tier: int) -> Path:
    """Write a template charter for a new run. Refuses an existing charter."""
    _check_run_id(run_id)
    if tier not in (1, 2, 3):
        raise LedgerError("tier must be 1, 2 or 3")
    if "\"" in title or "\n" in title or "\\" in title:
        raise LedgerError("keep the title to one line without quotes or backslashes; edit it in the file after")
    folder = runs_dir / run_id
    for f in (runs_dir.parent, runs_dir, folder):
        if f.is_symlink():
            raise LedgerError(f"{f} is a symlink; charters only live in real folders")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / CHARTER_FILE
    text = TEMPLATE.format(run_id=run_id, title=title, tier=tier, gates=", ".join(DEFAULT_GATES))
    try:
        with open(path, "x", encoding="utf-8") as fh:
            fh.write(text)
    except FileExistsError:
        raise LedgerError(f"{path} already exists") from None
    return path


@dataclass(frozen=True)
class Approved:
    entry: Any
    new: bool


def approve_charter(sink: RepoSink, run_id: str, *, actor: HumanActor, why: str, quote: str | None,
                    channel: str | None, relayed_by: str | None, append) -> Approved:
    """Record a human's approval of the charter as it is now. The charter must pass check, and
    the run's ledger must exist. Approving the same charter twice is a no-op."""
    path = sink.run_dir(run_id) / CHARTER_FILE
    charter = read_charter(path)
    problems = check_charter(charter, run_id)
    if problems:
        raise CharterError(problems[0].line, f"the charter fails check ({len(problems)} problems); "
                                             f"run `ledger charter check {run_id}`")
    status = approval_status(sink, run_id, charter.sha256)
    if status.state == "approved":
        latest = next(e for e in sink.read(run_id).entries if e.seq == status.approval_seq)
        return Approved(latest, new=False)
    data: dict[str, Any] = {"charter_sha256": charter.sha256}
    for k, v in (("quote", quote), ("channel", channel), ("relayed_by", relayed_by)):
        if v:
            data[k] = v
    if status.approval_seq is not None:
        data["supersedes"] = status.approval_seq
    entry = append(sink, run_id, actor=actor, type=EntryType.APPROVAL,
                   summary=f"Charter approved (tier {charter.fields.get('tier')})", why=why,
                   evidence=[f"charter.md sha256 {charter.sha256}"], data=data)
    return Approved(entry, new=True)
