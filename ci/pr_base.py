"""Print the base a pull request's ledgers are checked against.

usage: python -I pr_base.py <root> <base branch>

<root> is a checkout of the pull request's merge commit (refs/pull/<n>/merge). The base is that
commit's first parent: the base branch as GitHub merged it, so a base branch that moved on since
can't fail an honest change. In a GitHub stack, `github.base_ref` names the stack's base (main)
while the merge commit sits on the parent pull request's branch, so its first parent isn't on
origin/<base>. Then the base is the newest commit of origin/<base> the pull request contains:
every ledger main had there must still be append-only, which is the rule the check is for.

The script exits 1 with a GitHub `::error::` line when there is no sound base.
"""

from __future__ import annotations

import subprocess
import sys


class NoBase(Exception):
    pass


def _git(root: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", root, *args], capture_output=True, text=True)


def pr_base(root: str, base_branch: str) -> tuple[str, str | None]:
    """The base commit, and a notice to print when it isn't the merge commit's first parent."""
    if _git(root, "rev-parse", "--verify", "--quiet", "HEAD^2").returncode != 0:
        raise NoBase("check out the pull request's merge commit (refs/pull/<n>/merge), not its head")
    first_parent = _git(root, "rev-parse", "HEAD^1").stdout.strip()
    target = f"refs/remotes/origin/{base_branch}"
    if _git(root, "rev-parse", "--verify", "--quiet", target).returncode != 0:
        raise NoBase(f"origin/{base_branch} isn't in the checkout; fetch it with fetch-depth: 0")
    if _git(root, "merge-base", "--is-ancestor", first_parent, target).returncode == 0:
        return first_parent, None
    fork = _git(root, "merge-base", first_parent, target)
    if fork.returncode != 0 or not fork.stdout.strip():
        raise NoBase(f"the merge commit's first parent shares no history with origin/{base_branch}")
    base = fork.stdout.strip()
    return base, (
        f"the merge commit's first parent {first_parent[:12]} isn't on origin/{base_branch} "
        f"(a stacked pull request?); checking against {base[:12]}, the newest commit of "
        f"origin/{base_branch} it contains"
    )


def main(argv: list[str]) -> int:
    root, base_branch = argv
    try:
        base, notice = pr_base(root, base_branch)
    except NoBase as e:
        print(f"::error::{e}")
        return 1
    if notice:
        print(f"::notice::{notice}", file=sys.stderr)
    print(base)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
