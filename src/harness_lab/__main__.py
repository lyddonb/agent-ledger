"""`python -m harness_lab judge`: judge a run that already happened."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from .calls import StreamError, read_claude_stream
from .case import CaseError, load_case
from .judge import Verdict, judge, run_after_checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m harness_lab")
    sub = parser.add_subparsers(dest="command", required=True)
    j = sub.add_parser("judge", help="judge one run's stream and fixture against a case")
    j.add_argument("case", type=Path)
    j.add_argument("--stream", type=Path, required=True, help="the run's stream-json output")
    j.add_argument("--fixture", type=Path, required=True, help="the fixture as the run left it")
    j.add_argument("--require-plugin", action="append", default=None,
                   help="a plugin the run must have loaded (default: the case's plugin)")
    args = parser.parse_args(argv)

    try:
        case = load_case(args.case)
        with args.stream.open(encoding="utf-8") as f:
            run = read_claude_stream(f)
        exits = run_after_checks(case, args.fixture)
    except CaseError as e:
        print(f"error: {args.case}:", *e.problems, sep="\n  ", file=sys.stderr)
        return Verdict.UNJUDGEABLE.value
    except (OSError, StreamError, subprocess.TimeoutExpired) as e:
        print(f"error: {e}", file=sys.stderr)
        return Verdict.UNJUDGEABLE.value

    result = judge(case, run, exits, args.require_plugin or [case.plugin_name])
    for r in result.results:
        print(f"{'pass' if r.passed else 'FAIL'}  {r.kind.value:<11} {r.id}: {r.evidence}")
    print(f"{result.verdict.name}  {case.id}: {result.reason}")
    return result.verdict.value


if __name__ == "__main__":
    sys.exit(main())
