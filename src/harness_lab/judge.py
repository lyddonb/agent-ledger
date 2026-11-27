"""Judge one run of a case: each assertion passes or fails, and the run gets one verdict."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Collection, Mapping

from .calls import Outcome, Run, ToolCall
from .case import AfterCheck, CallMatch, Case


class Verdict(Enum):
    PASS = 0
    FAIL = 1
    UNJUDGEABLE = 2


class Kind(Enum):
    CALLS = "calls"
    NEVER_CALLS = "never_calls"
    AFTER = "after"


@dataclass(frozen=True)
class AssertionResult:
    id: str
    kind: Kind
    passed: bool
    evidence: str


@dataclass(frozen=True)
class Judgment:
    verdict: Verdict
    results: tuple[AssertionResult, ...]
    reason: str


def judge(case: Case, run: Run, after_exits: Mapping[str, int], required_plugins: Collection[str]) -> Judgment:
    """Pure: the run's calls and the `after` exit codes in, one result per assertion out."""
    problem = _unjudgeable(run, required_plugins)
    if problem:
        return Judgment(Verdict.UNJUDGEABLE, (), problem)
    results = (
        *(_check_calls(m, run.calls) for m in case.calls),
        *(_check_never_calls(m, run.calls) for m in case.never_calls),
        *(_check_after(a, after_exits) for a in case.after),
    )
    failed = [r.id for r in results if not r.passed]
    if failed:
        return Judgment(Verdict.FAIL, results, "failed: " + ", ".join(failed))
    return Judgment(Verdict.PASS, results, f"{len(results)} assertions passed")


def run_after_checks(case: Case, fixture: Path, timeout: float = 120) -> dict[str, int]:
    """Run each `after` command with `fixture` as its working folder and return its exit code."""
    exits = {}
    for check in case.after:
        done = subprocess.run(check.run, shell=True, cwd=fixture, stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout)
        exits[check.id] = done.returncode
    return exits


def _unjudgeable(run: Run, required_plugins: Collection[str]) -> str:
    if run.plugins is None:
        return "the stream has no init event"
    missing = sorted(set(required_plugins) - run.plugins)
    if missing:
        return "the plugins under test did not load: " + ", ".join(missing)
    if run.ended is None:
        return "the stream has no result event"
    if run.ended != "success":
        return f"the run ended with {run.ended!r}"
    if not run.calls:
        return "the stream has no tool calls"
    return ""


def _matches(m: CallMatch, call: ToolCall) -> bool:
    if not m.tool.fullmatch(call.name):
        return False
    for key in m.args:
        if key in call.input:
            value = call.input[key]
            text = value if isinstance(value, str) else json.dumps(value, separators=(",", ":"), sort_keys=True)
            if m.match.search(text):
                return True
    return False


def _check_calls(m: CallMatch, calls: tuple[ToolCall, ...]) -> AssertionResult:
    hits = [c for c in calls if _matches(m, c)]
    ran = [c.id for c in hits if c.outcome is Outcome.RAN]
    if ran:
        return AssertionResult(m.id, Kind.CALLS, True, "ran: " + ", ".join(ran))
    if hits:
        stopped = ", ".join(f"{c.id} ({c.outcome.value})" for c in hits)
        return AssertionResult(m.id, Kind.CALLS, False, "matched but never ran: " + stopped)
    return AssertionResult(m.id, Kind.CALLS, False, "no call matched")


def _check_never_calls(m: CallMatch, calls: tuple[ToolCall, ...]) -> AssertionResult:
    hits = [c for c in calls if _matches(m, c)]
    if hits:
        found = ", ".join(f"{c.id} ({c.outcome.value})" for c in hits)
        return AssertionResult(m.id, Kind.NEVER_CALLS, False, "matched: " + found)
    return AssertionResult(m.id, Kind.NEVER_CALLS, True, "no call matched")


def _check_after(check: AfterCheck, exits: Mapping[str, int]) -> AssertionResult:
    code = exits[check.id]
    return AssertionResult(check.id, Kind.AFTER, code == 0, f"exit {code}")
