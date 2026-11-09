"""The `ledger` command line.

Every planned subcommand is declared so `--help` shows the whole surface. Commands in HANDLERS
work; the rest exit with code 2 and point at the spec that defines them (M0 charter,
.audit/runs/2026-10-18-m0-foundation/charter.md).
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from typing import Callable, Mapping

from . import __version__
from ._canon import CanonError, parse_value
from .entry import Actor, EntryError, EntryType, _actor_from_wire
from .run import IMPORTED_KEY, append, find_root, init_run, repo_sink, run_id_for, utc_now
from .sink import ChainBroken, LedgerError
from .verify import Broken, Verified, verify_run

EXIT_OK = 0
EXIT_FAILED = 1  # the check ran and the ledger failed it
EXIT_ERROR = 2   # usage error, unreadable input, or a command that isn't built yet

# subcommand -> (one-line help, spec that defines it)
COMMANDS: dict[str, tuple[str, str]] = {
    "init": ("start a run: create its folder and genesis entry", "docs/specs/S3-audit-ledger.md"),
    "append": ("chain and write one ledger entry (redaction comes in M0 unit 3)", "docs/specs/S3-audit-ledger.md"),
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
    _print_problems(report, path)
    return EXIT_FAILED


def _print_problems(report: Broken, path: Path) -> None:
    for p in report.problems:
        where = f"{path}:{p.line}" if p.line else str(path)
        print(f"{where}: {p.kind.value}: {p.message}")
    if report.omitted:
        print(f"... and {report.omitted} more problems")
    print(f"FAILED {report.run_id}: {len(report.problems) + report.omitted} problems")


ACTOR_HELP = ("who is acting, as S2 JSON: an agent is "
              '{"kind":"agent","id":"<name>@<tool>#<session prefix>","on_behalf_of":"<login>@github",'
              '"tool":"...","model":"...","session":"..."}; a human is '
              '{"kind":"human","id":"<github login>","on_behalf_of":"<login>@github"}')


def _actor(text: str) -> Actor:
    try:
        return _actor_from_wire(parse_value(text))
    except (CanonError, EntryError) as e:
        raise argparse.ArgumentTypeError(f"bad actor: {e}") from None


def _data(text: str) -> dict:
    try:
        value = parse_value(text)
    except CanonError as e:
        raise argparse.ArgumentTypeError(f"bad data: {e}") from None
    if not isinstance(value, Mapping):
        raise argparse.ArgumentTypeError("data must be a JSON object")
    return dict(value)


def _day(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a date (YYYY-MM-DD): {text!r}") from None


def _add_root_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument("--root", type=Path, default=None,
                   help="the repo root holding .audit/ (default: the nearest folder with .git)")


def _sink(ns: argparse.Namespace):
    return repo_sink(ns.root if ns.root is not None else find_root(Path.cwd()))


def _guard(name: str, ns: argparse.Namespace, body: Callable[[], int]) -> int:
    """Run a write or read command, turning ledger errors into the CLI's exit codes."""
    try:
        return body()
    except ChainBroken as e:
        print(f"ledger {name}: refusing: the ledger fails verify", file=sys.stderr)
        _print_problems(e.report, _sink(ns).run_dir(e.report.run_id) / "ledger.jsonl")
        return EXIT_FAILED
    except (LedgerError, EntryError, OSError) as e:
        print(f"ledger {name}: {e}", file=sys.stderr)
        return EXIT_ERROR


def _add_init_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("slug", help="the run's slug; the run id is <date>-<slug>")
    p.add_argument("--date", type=_day, default=None, help="the run id's date (default: today in UTC, matching ts)")
    p.add_argument("--actor", type=_actor, required=True, help=ACTOR_HELP)
    p.add_argument("--why", default="Run started with `ledger init`", help="one line: why the run exists")
    _add_root_arg(p)


def _init(ns: argparse.Namespace) -> int:
    def body() -> int:
        sink = _sink(ns)
        run_id = run_id_for(ns.slug, ns.date or utc_now().date())
        entry = init_run(sink, run_id, actor=ns.actor, why=ns.why)
        print(f"started {run_id}: {sink.run_dir(run_id) / 'ledger.jsonl'} seq {entry.seq} {entry.hash}")
        return EXIT_OK
    return _guard("init", ns, body)


def _add_append_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--run", required=True, metavar="RUN_ID", help="the run to append to")
    p.add_argument("--actor", type=_actor, required=True, help=ACTOR_HELP)
    p.add_argument("--type", required=True, choices=[t.value for t in EntryType], help="the entry type")
    p.add_argument("--summary", required=True, help="one line: what happened")
    p.add_argument("--why", required=True, help="one line: the reason")
    p.add_argument("--evidence", action="append", default=[],
                   help="a pointer (commit, PR, path:line, hash); repeat for more")
    p.add_argument("--data", type=_data, default=None, help="type-specific fields as a JSON object")
    _add_root_arg(p)


def _append(ns: argparse.Namespace) -> int:
    def body() -> int:
        entry = append(_sink(ns), ns.run, actor=ns.actor, type=EntryType(ns.type),
                       summary=ns.summary, why=ns.why, evidence=ns.evidence, data=ns.data)
        print(f"appended {entry.run_id} seq {entry.seq} {entry.hash}")
        return EXIT_OK
    return _guard("append", ns, body)


def _visible(text: str) -> str:
    """Text as a reader should see it: format characters (bidi overrides, zero-width marks)
    and other invisibles are written as \\uXXXX, so one row can't impersonate another."""
    return "".join(ch if ch == " " or ch.isprintable() else f"\\u{ord(ch):04x}" for ch in text)


def _actor_label(actor: Actor) -> str:
    return actor.to_wire()["kind"] + ":" + actor.id


def _add_show_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("run_id", metavar="RUN_ID", help="the run to print")
    _add_root_arg(p)


def _show(ns: argparse.Namespace) -> int:
    def body() -> int:
        report = _sink(ns).read(ns.run_id)
        rows = [(str(e.seq), e.ts, e.type.value, _visible(_actor_label(e.actor)),
                 ("[imported] " if IMPORTED_KEY in e.data else "") + _visible(e.summary),
                 _visible(e.why))
                for e in report.entries]
        header = ("seq", "ts", "type", "actor", "summary")
        widths = [max(len(r[i]) for r in [header, *rows]) for i in range(4)]
        indent = " " * (sum(widths) + 2 * 4)
        print("  ".join([*(h.ljust(w) for h, w in zip(header, widths)), header[4]]))
        for *cells, summary, why in rows:
            print("  ".join([*(c.ljust(w) for c, w in zip(cells, widths)), summary]))
            print(f"{indent}why: {why}")
        print(f"{report.run_id}: {len(report.entries)} entries, verified; head {report.head.link}")
        return EXIT_OK
    return _guard("show", ns, body)


# subcommand -> (add its arguments, run it). A command is implemented iff it is here.
HANDLERS: dict[str, tuple[Callable[[argparse.ArgumentParser], None], Callable[[argparse.Namespace], int]]] = {
    "init": (_add_init_args, _init),
    "append": (_add_append_args, _append),
    "verify": (_add_verify_args, _verify),
    "show": (_add_show_args, _show),
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
