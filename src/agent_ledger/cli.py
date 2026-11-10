"""The `ledger` command line.

Every planned subcommand is declared so `--help` shows the whole surface. Commands in HANDLERS
work; the rest exit with code 2 and point at the spec that defines them (M0 charter,
.audit/runs/2026-10-18-m0-foundation/charter.md).
"""
from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from datetime import date
from pathlib import Path
from typing import Callable, Mapping

from . import __version__
from ._canon import CanonError, parse_value
from .charter import (CHARTER_FILE, CharterError, approval_status, approve_charter, check_charter,
                      new_charter, read_charter)
from .entry import Actor, EntryError, EntryType, HumanActor, _actor_from_wire
from .redact import PolicyError, Redactor, policy_patterns
from .run import IMPORTED_KEY, append, find_root, init_run, repo_sink, run_id_for, seal_run, utc_now
from ._git import GitError
from .repo_verify import verify_repo
from .session import add_session
from .sink import ChainBroken, IndexBrokenError, LedgerError
from .verify import Broken, Verified, verify_run

EXIT_OK = 0
EXIT_FAILED = 1  # the check ran and the ledger failed it
EXIT_ERROR = 2   # usage error, unreadable input, or a command that isn't built yet

# subcommand -> (one-line help, spec that defines it)
COMMANDS: dict[str, tuple[str, str]] = {
    "init": ("start a run: create its folder and genesis entry", "docs/specs/S3-audit-ledger.md"),
    "append": ("redact, chain and write one ledger entry", "docs/specs/S3-audit-ledger.md"),
    "verify": ("check every run, the index and seals, or one run's chain", "docs/specs/S3-audit-ledger.md"),
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
    p.add_argument("run_folder", metavar="RUN_FOLDER", type=Path, nargs="?", default=None,
                   help="check only this run folder's chain, such as .audit/runs/2026-10-18-m0-foundation "
                        "(default: every run in the repo, INDEX.jsonl and the seals)")
    p.add_argument("--base", metavar="REF", default=None,
                   help="also check that every ledger and INDEX.jsonl at REF (such as origin/main) "
                        "is unchanged here apart from lines added at the end")
    _add_root_arg(p)


def _verify(ns: argparse.Namespace) -> int:
    if ns.run_folder is None:
        return _verify_repo(ns)
    if ns.base is not None:
        print("ledger verify: --base checks the whole repo; leave out RUN_FOLDER", file=sys.stderr)
        return EXIT_ERROR
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


def _verify_repo(ns: argparse.Namespace) -> int:
    try:
        root = _root(ns)
        report = verify_repo(root, ns.base)
    except (GitError, LedgerError, OSError) as e:
        print(f"ledger verify: {e}", file=sys.stderr)
        return EXIT_ERROR
    for run_id, run in report.runs.items():
        if isinstance(run, Broken):
            _print_problems(run, Path(".audit/runs") / run_id / "ledger.jsonl")
    for f in report.findings:
        where = f"{f.path}:{f.problem.line}" if f.problem.line else f.path
        print(f"{where}: {f.problem.kind.value}: {f.problem.message}")
    broken = sum(isinstance(r, Broken) for r in report.runs.values())
    if not report.ok:
        print(f"FAILED: {broken} broken run{'s' if broken != 1 else ''}, {len(report.findings)} other problems")
        return EXIT_FAILED
    since = f"; merged lines unchanged since {ns.base} ({report.base[:12]})" if report.base else ""
    print(f"ok: {len(report.runs)} runs, {report.sealed} sealed, {report.index_lines} indexed{since}")
    if not report.base:
        print("note: without --base, lines cut from or rewritten in merged ledgers aren't checked")
    return EXIT_OK


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


def _root(ns: argparse.Namespace) -> Path:
    return ns.root if ns.root is not None else find_root(Path.cwd())


def _sink(ns: argparse.Namespace):
    return repo_sink(_root(ns))


def _add_redact_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument("--redact-pattern", action="append", default=[], metavar="REGEX",
                   help="a Python regex to redact, added to the built-in secret patterns and the "
                        "policy's security.redact_patterns; repeat for more")


def _redactor(ns: argparse.Namespace) -> Redactor:
    """Built-in patterns, plus ~/.agents/policy.yaml and the repo's .agents/policy.yaml, plus
    --redact-pattern. An unreadable policy is an error, never skipped."""
    try:
        return Redactor([*policy_patterns(_root(ns)), *ns.redact_pattern])
    except PolicyError as e:
        raise LedgerError(str(e)) from None


def _guard(name: str, ns: argparse.Namespace, body: Callable[[], int]) -> int:
    """Run a write or read command, turning ledger errors into the CLI's exit codes."""
    try:
        return body()
    except ChainBroken as e:
        print(f"ledger {name}: refusing: the ledger fails verify", file=sys.stderr)
        _print_problems(e.report, _sink(ns).run_dir(e.report.run_id) / "ledger.jsonl")
        return EXIT_FAILED
    except IndexBrokenError as e:
        print(f"ledger {name}: refusing: .audit/INDEX.jsonl fails verify", file=sys.stderr)
        for p in e.report.problems:
            print(f".audit/INDEX.jsonl:{p.line}: {p.kind.value}: {p.message}")
        return EXIT_FAILED
    except (LedgerError, EntryError, OSError) as e:
        print(f"ledger {name}: {e}", file=sys.stderr)
        return EXIT_ERROR


def _add_init_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("slug", help="the run's slug; the run id is <date>-<slug>")
    p.add_argument("--date", type=_day, default=None, help="the run id's date (default: today in UTC, matching ts)")
    p.add_argument("--actor", type=_actor, required=True, help=ACTOR_HELP)
    p.add_argument("--why", default="Run started with `ledger init`", help="one line: why the run exists")
    _add_redact_arg(p)
    _add_root_arg(p)


def _init(ns: argparse.Namespace) -> int:
    def body() -> int:
        sink = _sink(ns)
        run_id = run_id_for(ns.slug, ns.date or utc_now().date())
        entry = init_run(sink, run_id, actor=ns.actor, why=ns.why, redactor=_redactor(ns))
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
    _add_redact_arg(p)
    _add_root_arg(p)


def _append(ns: argparse.Namespace) -> int:
    def body() -> int:
        entry = append(_sink(ns), ns.run, actor=ns.actor, type=EntryType(ns.type),
                       summary=ns.summary, why=ns.why, evidence=ns.evidence, data=ns.data,
                       redactor=_redactor(ns))
        print(f"appended {entry.run_id} seq {entry.seq} {entry.hash}")
        return EXIT_OK
    return _guard("append", ns, body)


# Characters that print as nothing, or that change how neighbors print: format and combining
# marks, unassigned and private-use code points, odd spaces, and the Hangul fillers (letters
# by category, blank on screen).
_INVISIBLE_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Mn", "Me", "Zs", "Zl", "Zp"})
_FILLERS = frozenset("\u115f\u1160\u3164\uffa0")


def _visible(text: str) -> str:
    """Summary and why as a reader should see them: invisible and bidi-affecting characters are
    written as \\uXXXX, so one row can't hide text or pass for another."""
    return "".join(
        f"\\u{ord(ch):04x}" if ch != " " and (unicodedata.category(ch) in _INVISIBLE_CATEGORIES
                                             or ch in _FILLERS) else ch
        for ch in text)


def _actor_label(actor: Actor) -> str:
    """kind:id with every non-ASCII character escaped. Ids are logins and agent names, so an id
    that differs from another only in a look-alike or invisible character shows the difference."""
    return actor.to_wire()["kind"] + ":" + ascii(actor.id)[1:-1]


def _add_show_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("run_id", metavar="RUN_ID", help="the run to print")
    _add_root_arg(p)


def _show(ns: argparse.Namespace) -> int:
    def body() -> int:
        report = _sink(ns).read(ns.run_id)
        rows = [(str(e.seq), e.ts, e.type.value, _actor_label(e.actor),
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


def _add_session_args(p: argparse.ArgumentParser) -> None:
    sub = p.add_subparsers(dest="session_command", metavar="ACTION", required=True)
    a = sub.add_parser("add", help="redact, compress and archive a transcript, then record it",
                       description="Redact a session transcript, store it as "
                                   ".audit/runs/<run>/sessions/<session>.jsonl.gz and append a "
                                   "`session` entry with the raw and redacted hashes. If redaction "
                                   "can't be confirmed, nothing is stored, the entry records where "
                                   "the transcript is, and the exit code is 1.")
    a.add_argument("transcript", type=Path, metavar="FILE", help="the transcript (JSON lines, UTF-8)")
    a.add_argument("--run", required=True, metavar="RUN_ID", help="the run the session worked on")
    a.add_argument("--actor", type=_actor, required=True, help=ACTOR_HELP)
    a.add_argument("--session", default=None, metavar="ID",
                   help="the session id (default: the file name up to its first dot)")
    _add_redact_arg(a)
    _add_root_arg(a)


def _session(ns: argparse.Namespace) -> int:
    def body() -> int:
        root = _root(ns)
        r = add_session(repo_sink(root), ns.run, ns.transcript, actor=ns.actor,
                        redactor=_redactor(ns), session_id=ns.session, root=root)
        d = r.entry.data
        if not r.new:
            print(f"unchanged: {d['session_id']} already recorded at seq {r.entry.seq}")
        if r.warning:
            print(f"warning: {r.warning}", file=sys.stderr)
        if not r.archived:
            print(f"ledger session add: not archived: {d['reason']}; recorded its location at seq "
                  f"{r.entry.seq} (the transcript stays out of git)", file=sys.stderr)
            return EXIT_FAILED
        if r.new:
            counts = ", ".join(f"{k} {v}" for k, v in d["redactions"].items()) or "none"
            print(f"archived {d['session_id']}: {d['path']} (redactions: {counts}); "
                  f"seq {r.entry.seq} {r.entry.hash}")
        return EXIT_OK
    return _guard("session add", ns, body)


def _add_seal_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("run_id", metavar="RUN_ID", help="the run to seal")
    p.add_argument("--actor", type=_actor, required=True, help=ACTOR_HELP)
    p.add_argument("--why", default="", help="one line: why the run is done (default: a standard reason)")
    _add_root_arg(p)


def _seal(ns: argparse.Namespace) -> int:
    def body() -> int:
        root = _root(ns)
        try:
            r = seal_run(repo_sink(root), root, ns.run_id, actor=ns.actor, why=ns.why)
        except GitError as e:
            raise LedgerError(str(e)) from None
        if r.new:
            print(f"sealed {ns.run_id} at seq {r.entry.seq}, commit {r.entry.data['commit'][:12]}; "
                  f"indexed in .audit/INDEX.jsonl. Commit both files.")
        elif r.indexed:
            print(f"{ns.run_id} was already sealed (seq {r.entry.seq}); added its line to .audit/INDEX.jsonl")
        else:
            print(f"{ns.run_id} is already sealed (seq {r.entry.seq}) and indexed; nothing to do")
        return EXIT_OK
    return _guard("seal", ns, body)


def _human(text: str) -> HumanActor:
    actor = _actor(text)
    if not isinstance(actor, HumanActor):
        raise argparse.ArgumentTypeError("only a human approves a charter (S2); pass a human actor")
    return actor


def _add_charter_args(p: argparse.ArgumentParser) -> None:
    sub = p.add_subparsers(dest="charter_command", metavar="ACTION", required=True)
    n = sub.add_parser("new", help="write a template charter for a new run",
                       description="Write .audit/runs/<date>-<slug>/charter.md from the S1 template. Its "
                                   "TODO placeholders fail `check` until they are filled in.")
    n.add_argument("slug", help="the run's slug; the run id is <date>-<slug>")
    n.add_argument("--title", required=True, help="one line: what the run does")
    n.add_argument("--tier", type=int, choices=[1, 2, 3], default=2, help="1 routine, 2 significant, 3 critical")
    n.add_argument("--date", type=_day, default=None, help="the run id's date (default: today in UTC)")
    _add_root_arg(n)
    c = sub.add_parser("check", help="check a charter against its tier, and show its approval",
                       description="Check charter.md for its tier's required fields and sections (S1), "
                                   "and show whether the ledger approves this exact version. Exit 0 valid, "
                                   "1 invalid (or, with --require-approved, not approved), 2 unreadable.")
    c.add_argument("run_id", metavar="RUN_ID")
    c.add_argument("--require-approved", action="store_true",
                   help="also fail unless the latest approval in the ledger is for this exact charter")
    _add_root_arg(c)
    a = sub.add_parser("approve", help="record a human's approval of the charter as it is now",
                       description="Append an approval entry naming the charter's sha256. The charter must "
                                   "pass check and the run must be started (`ledger init`). Editing the "
                                   "charter afterward puts it back to draft until approved again.")
    a.add_argument("run_id", metavar="RUN_ID")
    a.add_argument("--actor", type=_human, required=True, help="the approving human, as S2 JSON")
    a.add_argument("--sha256", required=True, metavar="HEX",
                   help="the charter hash (at least 12 characters) that `ledger charter check` printed for "
                        "the version the human reviewed; a charter edited since is refused")
    a.add_argument("--why", default="The charter was reviewed and approved before work starts (S1)",
                   help="one line: how and why it was approved")
    a.add_argument("--quote", default=None, help="the approver's words, verbatim")
    a.add_argument("--channel", default=None, help="where the approval was given, such as 'claude-code chat'")
    a.add_argument("--relayed-by", default=None, help="the agent id that typed this approval in, if any")
    _add_redact_arg(a)
    _add_root_arg(a)
    r = sub.add_parser("render", help="read the charter aloud or as video (M2, not built)")
    r.add_argument("args", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)


def _charter(ns: argparse.Namespace) -> int:
    if ns.charter_command == "render":
        print("ledger charter render: not implemented yet (milestone M2). Specified in "
              "docs/specs/S1-run-charter.md.", file=sys.stderr)
        return EXIT_ERROR

    def body() -> int:
        root = _root(ns)
        sink = repo_sink(root)
        if ns.charter_command == "new":
            run_id = run_id_for(ns.slug, ns.date or utc_now().date())
            path = new_charter(sink.runs_dir, run_id, title=ns.title, tier=ns.tier)
            print(f"wrote {path.relative_to(root)}; fill in the TODOs, then `ledger charter check {run_id}`")
            return EXIT_OK
        path = sink.run_dir(ns.run_id) / CHARTER_FILE
        rel = path.relative_to(root)
        charter = read_charter(path)
        if ns.charter_command == "approve":
            r = approve_charter(sink, ns.run_id, actor=ns.actor, sha256=ns.sha256, why=ns.why, quote=ns.quote,
                                channel=ns.channel, relayed_by=ns.relayed_by,
                                append=lambda *a, **k: append(*a, redactor=_redactor(ns), **k))
            if r.new:
                print(f"approved {ns.run_id}: charter sha256 {r.entry.data['charter_sha256'][:16]} at seq {r.entry.seq}")
            else:
                print(f"{ns.run_id}: this charter is already approved at seq {r.entry.seq}; nothing to do")
            return EXIT_OK
        problems = check_charter(charter, ns.run_id)
        # What the tools will enforce, as they read it, so the approver reviews exactly that.
        print(f"charter {rel} sha256 {charter.sha256}")
        for key in ("tier", "predicate", "scope", "budget", "estimate", "gates", "staffing", "review",
                    "spec_level", "rollback", "soc2"):
            if key in charter.fields:
                print(f"  {key}: {json.dumps(charter.fields[key], ensure_ascii=True, separators=(', ', ': '))}")
        for p in problems:
            print(f"{rel}:{p.line}: {p.message}" if p.line else f"{rel}: {p.message}")
        status = approval_status(sink, ns.run_id, charter.sha256)
        if status.state == "approved":
            print(f"approved at seq {status.approval_seq} (sha256 {charter.sha256[:12]})")
        elif status.state == "stale":
            print(f"draft: edited since the approval at seq {status.approval_seq}; approve it again")
        else:
            print("draft: not approved")
        if problems:
            print(f"FAILED {ns.run_id}: {len(problems)} problems")
            return EXIT_FAILED
        print(f"ok {ns.run_id}: tier {charter.fields.get('tier')} charter is complete")
        if ns.require_approved and status.state != "approved":
            return EXIT_FAILED
        return EXIT_OK

    try:
        return _guard(f"charter {ns.charter_command}", ns, body)
    except RecursionError:
        print(f"ledger charter {ns.charter_command}: the charter is nested too deeply to read", file=sys.stderr)
        return EXIT_ERROR
    except CharterError as e:
        print(f"ledger charter {ns.charter_command}: {e}", file=sys.stderr)
        return EXIT_FAILED if ns.charter_command == "approve" else EXIT_ERROR


# subcommand -> (add its arguments, run it). A command is implemented iff it is here.
HANDLERS: dict[str, tuple[Callable[[argparse.ArgumentParser], None], Callable[[argparse.Namespace], int]]] = {
    "init": (_add_init_args, _init),
    "append": (_add_append_args, _append),
    "verify": (_add_verify_args, _verify),
    "show": (_add_show_args, _show),
    "session": (_add_session_args, _session),
    "seal": (_add_seal_args, _seal),
    "charter": (_add_charter_args, _charter),
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
