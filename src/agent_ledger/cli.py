"""The `ledger` command line.

Every planned subcommand is declared so `--help` shows the whole surface. Commands in HANDLERS
work; the rest exit with code 2 and point at the spec that defines them (M0 charter,
.audit/runs/2026-10-18-m0-foundation/charter.md).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable

from . import __version__
from .verify import Broken, Verified, verify_run

EXIT_OK = 0
EXIT_FAILED = 1  # the check ran and the ledger failed it
EXIT_ERROR = 2   # usage error, unreadable input, or a command that isn't built yet

# subcommand -> (one-line help, spec that defines it)
COMMANDS: dict[str, tuple[str, str]] = {
    "init": ("start a run: create its folder and genesis entry", "docs/specs/S3-audit-ledger.md"),
    "append": ("redact, chain and write one ledger entry", "docs/specs/S3-audit-ledger.md"),
    "verify": ("check one run's hash chain", "docs/specs/S3-audit-ledger.md"),
    "show": ("print a run's ledger as a table", "docs/specs/S3-audit-ledger.md"),
    "seal": ("close a run's chain and anchor it in INDEX.jsonl", "docs/specs/S3-audit-ledger.md"),
    "session": ("archive a redacted session transcript", "docs/specs/S3-audit-ledger.md"),
    "charter": ("write, check, approve or render a run charter", "docs/specs/S1-run-charter.md"),
    "import-tsv": ("convert a show-me-your-work decisions.tsv into a run", "docs/specs/S3-audit-ledger.md"),
}

CHAIN_ONLY_NOTE = (
    "note: chain-only check. It can't detect entries cut from the end or a rewritten suffix "
    "with recomputed hashes; seals and `verify --base` close that (M0 unit 4)."
)


def _add_verify_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("run_folder", metavar="RUN_FOLDER", type=Path,
                   help="a run folder, such as .audit/runs/2026-10-18-m0-foundation")


def _verify(ns: argparse.Namespace) -> int:
    try:
        report = verify_run(ns.run_folder)
    except OSError as e:
        print(f"ledger verify: {e}", file=sys.stderr)
        return EXIT_ERROR
    path = ns.run_folder / "ledger.jsonl"
    if isinstance(report, Verified):
        head = report.head
        print(f"ok {report.run_id}: {len(report.entries)} entries, head seq {head.seq} {head.link}")
        print(CHAIN_ONLY_NOTE)
        return EXIT_OK
    assert isinstance(report, Broken)
    for p in report.problems:
        where = f"{path}:{p.line}" if p.line else str(path)
        print(f"{where}: {p.kind.value}: {p.message}")
    if report.omitted:
        print(f"... and {report.omitted} more problems")
    print(f"FAILED {report.run_id}: {len(report.problems) + report.omitted} problems")
    return EXIT_FAILED


# subcommand -> (add its arguments, run it). A command is implemented iff it is here.
HANDLERS: dict[str, tuple[Callable[[argparse.ArgumentParser], None], Callable[[argparse.Namespace], int]]] = {
    "verify": (_add_verify_args, _verify),
}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ledger", description="Tamper-evident records of agent runs.")
    p.add_argument("--version", action="version", version=f"ledger {__version__}")
    sub = p.add_subparsers(dest="command", metavar="COMMAND")
    for name, (help_text, _spec) in COMMANDS.items():
        s = sub.add_parser(name, help=help_text, description=help_text)
        if name in HANDLERS:
            HANDLERS[name][0](s)
        else:
            s.add_argument("args", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    ns = parser.parse_args(argv)
    if not ns.command:
        parser.print_help()
        return EXIT_OK
    if ns.command in HANDLERS:
        return HANDLERS[ns.command][1](ns)
    _help, spec = COMMANDS[ns.command]
    print(f"ledger {ns.command}: not implemented yet (milestone M0). Specified in {spec}.",
          file=sys.stderr)
    return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
