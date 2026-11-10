"""`ledger verify` over a whole repo: every run's chain, INDEX.jsonl, the seals, and with
`--base`, that nothing already on the base branch was changed (S3 "What verify detects").

A run's chain alone can't show a cut-off end or a rewritten suffix. These checks close that:

- A sealed run's last entry is its `seal`, and nothing follows it.
- Every sealed run has one INDEX.jsonl line, and every line names a sealed run whose seal entry
  has the line's hash and commit.
- The commit a seal names is in HEAD's history and holds the run's ledger with exactly the
  head the seal follows (the seal's `prev`).
- With `--base REF`: every ledger and INDEX.jsonl that exists at REF still starts with the
  same bytes. So merged history is append-only.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from . import _git
from .entry import EntryType
from .index import COMMIT_RE, INDEX_FILE, IndexBroken, IndexOk, verify_index_bytes
from .verify import LEDGER_FILE, Broken, Kind, Problem, Verified, verify_bytes, verify_run

AUDIT_DIR = ".audit"
RUNS_DIR = "runs"


@dataclass(frozen=True)
class Finding:
    path: str  # relative to the repo root
    problem: Problem


@dataclass
class RepoReport:
    runs: dict[str, Verified | Broken] = field(default_factory=dict)
    index_lines: int = 0
    sealed: int = 0
    base: str | None = None
    findings: list[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.findings and all(isinstance(r, Verified) for r in self.runs.values())

    def add(self, path: str, line: int, kind: Kind, message: str) -> None:
        self.findings.append(Finding(path, Problem(line, kind, message)))


def _ledger_rel(run_id: str) -> str:
    return f"{AUDIT_DIR}/{RUNS_DIR}/{run_id}/{LEDGER_FILE}"


def _first_difference(old: bytes, new: bytes) -> int:
    """The 1-based line where `new` stops starting with `old`."""
    n = 1
    for a, b in zip(old, new):
        if a != b:
            return n
        if a == 0x0A:
            n += 1
    return n


def verify_repo(root: Path, base: str | None = None) -> RepoReport:
    """Raises _git.GitError when a git check is needed and git can't answer it (no repo, a bad
    base ref); a missing commit or file is a finding, not an error."""
    report = RepoReport()
    runs_dir = root / AUDIT_DIR / RUNS_DIR
    if runs_dir.is_dir():
        for name in sorted(os.listdir(runs_dir)):
            if (runs_dir / name).is_dir():
                report.runs[name] = verify_run(runs_dir / name)

    index_rel = f"{AUDIT_DIR}/{INDEX_FILE}"
    index_path = root / index_rel
    index = IndexOk(())
    if index_path.is_symlink() or (index_path.exists() and not index_path.is_file()):
        report.add(index_rel, 0, Kind.NOT_A_FILE, f"{INDEX_FILE} is not a regular file")
    elif index_path.exists():
        checked = verify_index_bytes(index_path.read_bytes())
        if isinstance(checked, IndexBroken):
            report.findings.extend(Finding(index_rel, p) for p in checked.problems)
        else:
            index = checked
    report.index_lines = len(index.lines)

    seals = {}
    for run_id, run in report.runs.items():
        if not isinstance(run, Verified):
            continue
        for e in run.entries:
            if e.type is EntryType.SEAL:
                seals[run_id] = e
                if e is not run.entries[-1]:
                    report.add(_ledger_rel(run_id), e.seq + 1, Kind.AFTER_SEAL,
                               f"entries follow the seal at seq {e.seq}; a sealed run takes no more")
                break
    report.sealed = len(seals)

    indexed = {}
    for n, line in enumerate(index.lines, 1):
        indexed[line.run_id] = line
        seal = seals.get(line.run_id)
        run = report.runs.get(line.run_id)
        if run is None:
            report.add(index_rel, n, Kind.INDEX_MISMATCH, f"{line.run_id} has no run folder")
        elif isinstance(run, Verified) and seal is None:
            report.add(index_rel, n, Kind.INDEX_MISMATCH, f"{line.run_id} is indexed but has no seal entry")
        elif seal is not None and (seal.hash, seal.data.get("commit")) != (line.seal, line.commit):
            report.add(index_rel, n, Kind.INDEX_MISMATCH,
                       f"{line.run_id}: line names seal {line.seal[:12]} at {line.commit[:12]}, the run's "
                       f"seal is {seal.hash[:12]} at {str(seal.data.get('commit'))[:12]}")
    for run_id, seal in seals.items():
        if run_id not in indexed:
            report.add(_ledger_rel(run_id), seal.seq, Kind.UNINDEXED_SEAL,
                       f"sealed at seq {seal.seq} but not in {INDEX_FILE}; run `ledger seal {run_id}` again")

    for run_id, seal in seals.items():
        rel = _ledger_rel(run_id)
        commit = seal.data.get("commit")
        if not isinstance(commit, str) or not COMMIT_RE.fullmatch(commit) or not _git.is_ancestor(root, commit):
            report.add(rel, seal.seq, Kind.SEAL_COMMIT,
                       f"the seal names commit {str(commit)[:12]}, which is not in HEAD's history "
                       f"(a squash or rebase merge drops it; merge with a merge commit)")
            continue
        then = _git.show(root, commit, rel)
        held = verify_bytes(run_id, then) if then is not None else None
        if not isinstance(held, Verified) or held.head.link != seal.prev:
            report.add(rel, seal.seq, Kind.SEAL_COMMIT,
                       f"commit {commit[:12]} doesn't hold this run's ledger with head {seal.prev[:12]}")

    if base is not None:
        report.base = _git.resolve(root, base)
        for rel in _git.ls_tree(root, report.base, AUDIT_DIR):
            if not (rel == index_rel or (rel.startswith(f"{AUDIT_DIR}/{RUNS_DIR}/") and rel.endswith("/" + LEDGER_FILE))):
                continue
            old = _git.show(root, report.base, rel)
            path = root / rel
            if old is None:
                report.add(rel, 0, Kind.BASE_CHANGED, f"listed at {base} but git couldn't read it")
                continue
            new = path.read_bytes() if path.is_file() and not path.is_symlink() else None
            if new is None:
                report.add(rel, 0, Kind.BASE_CHANGED, f"exists at {base} but is gone or not a regular file")
            elif not new.startswith(old):
                line = _first_difference(old, new)
                report.add(rel, line, Kind.BASE_CHANGED,
                           f"line {line} differs from {base}, or lines were cut; merged lines are append-only")
    return report
