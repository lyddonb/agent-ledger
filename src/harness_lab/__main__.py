"""`python -m harness_lab run` runs a case; `python -m harness_lab judge` judges a run that already happened."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from .calls import StreamError, read_claude_stream
from .case import CaseError, load_case
from .judge import Judgment, Verdict, judge, run_after_checks
from .runner import RunReport, Settings, SetupError, overall, run_case


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m harness_lab")
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run", help="run a case with claude -p and judge each run")
    r.add_argument("case", type=Path)
    r.add_argument("--skill-root", type=Path, required=True, help="the skill repo to test, such as agent-kit")
    r.add_argument("--runs", type=int, default=1)
    r.add_argument("--model", default=Settings.model)
    r.add_argument("--budget-usd", type=float, default=Settings.budget_usd, help="the cap for each run")
    r.add_argument("--timeout", type=float, default=Settings.timeout_s, help="seconds each run may take")
    r.add_argument("--claude", default=Settings.claude, help="the claude command")
    r.add_argument("--keep", type=Path, help="copy each run's folder (stream and repo) to DIR/run-N")
    j = sub.add_parser("judge", help="judge one run's stream and fixture against a case")
    j.add_argument("case", type=Path)
    j.add_argument("--stream", type=Path, required=True, help="the run's stream-json output")
    j.add_argument("--fixture", type=Path, required=True, help="the fixture as the run left it")
    j.add_argument("--require-plugin", action="append", default=None,
                   help="a plugin the run must have loaded (default: the case's plugin)")
    args = parser.parse_args(argv)
    try:
        case = load_case(args.case)
    except CaseError as e:
        print(f"error: {args.case}:", *e.problems, sep="\n  ", file=sys.stderr)
        return Verdict.UNJUDGEABLE.value
    return _run(case, args) if args.command == "run" else _judge(case, args)


def _run(case, args) -> int:
    if args.runs < 1:
        print("error: --runs must be at least 1", file=sys.stderr)
        return Verdict.UNJUDGEABLE.value
    settings = Settings(claude=args.claude, model=args.model, budget_usd=args.budget_usd, timeout_s=args.timeout)
    reports: list[RunReport] = []
    for n in range(1, args.runs + 1):
        try:
            report = run_case(case, args.skill_root, settings, args.keep / f"run-{n}" if args.keep else None)
        except (SetupError, OSError, subprocess.CalledProcessError) as e:
            print(f"error: {e}", file=sys.stderr)
            return Verdict.UNJUDGEABLE.value
        reports.append(report)
        cost = f"${report.cost_usd:.3f}" if report.cost_usd is not None else "$?"
        print(f"run {n}/{args.runs}  ", end="")
        _print(case.id, report.judgment)
        print(f"  {cost}  {report.tokens:,} tokens  {report.wall_s:.1f} s  load {report.load[0]:.0f} to {report.load[1]:.0f}")
    verdict = overall(reports)
    counts = {v: sum(r.judgment.verdict is v for r in reports) for v in Verdict}
    spent = sum(r.cost_usd or 0 for r in reports)
    print(f"{verdict.name}  {case.id}: {counts[Verdict.PASS]} passed, {counts[Verdict.FAIL]} failed, "
          f"{counts[Verdict.UNJUDGEABLE]} unjudgeable of {len(reports)}; ${spent:.3f} in all")
    return verdict.value


def _judge(case, args) -> int:
    try:
        with args.stream.open(encoding="utf-8") as f:
            run = read_claude_stream(f)
        exits = run_after_checks(case, args.fixture)
    except (OSError, StreamError, subprocess.TimeoutExpired) as e:
        print(f"error: {e}", file=sys.stderr)
        return Verdict.UNJUDGEABLE.value
    result = judge(case, run, exits, args.require_plugin or [case.plugin_name])
    _print(case.id, result)
    return result.verdict.value


def _print(case_id: str, result: Judgment) -> None:
    print(f"{result.verdict.name}  {case_id}: {result.reason}")
    for r in result.results:
        print(f"  {'pass' if r.passed else 'FAIL'}  {r.kind.value:<11} {r.id}: {r.evidence}")


if __name__ == "__main__":
    sys.exit(main())
