"""The `ledger` command line.

Pre-M0 stub: every subcommand is declared so `--help` shows the planned surface, and each one
exits with code 2 and points at the spec that defines it. Implementation starts after the M0
charter (.audit/runs/2026-10-18-m0-foundation/charter.md) is approved.
"""
from __future__ import annotations

import argparse
import sys

from . import __version__

# subcommand -> (one-line help, spec that defines it)
COMMANDS: dict[str, tuple[str, str]] = {
    "init": ("start a run: create its folder and genesis entry", "docs/specs/S3-audit-ledger.md"),
    "append": ("redact, chain and write one ledger entry", "docs/specs/S3-audit-ledger.md"),
    "verify": ("check chains, append-only history and seals", "docs/specs/S3-audit-ledger.md"),
    "show": ("print a run's ledger as a table", "docs/specs/S3-audit-ledger.md"),
    "seal": ("close a run's chain and anchor it in INDEX.jsonl", "docs/specs/S3-audit-ledger.md"),
    "session": ("archive a redacted session transcript", "docs/specs/S3-audit-ledger.md"),
    "charter": ("write, check, approve or render a run charter", "docs/specs/S1-run-charter.md"),
    "import-tsv": ("convert a show-me-your-work decisions.tsv into a run", "docs/specs/S3-audit-ledger.md"),
}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ledger",
        description="Tamper-evident records of agent runs (pre-M0: commands are not implemented yet).",
    )
    p.add_argument("--version", action="version", version=f"ledger {__version__}")
    sub = p.add_subparsers(dest="command", metavar="COMMAND")
    for name, (help_text, _spec) in COMMANDS.items():
        s = sub.add_parser(name, help=help_text, description=help_text)
        s.add_argument("args", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    ns = parser.parse_args(argv)
    if not ns.command:
        parser.print_help()
        return 0
    _help, spec = COMMANDS[ns.command]
    print(f"ledger {ns.command}: not implemented yet (milestone M0). Specified in {spec}.",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
