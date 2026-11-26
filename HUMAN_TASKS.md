# Human tasks

Things an agent cannot or should not do. **🚧 BLOCKER** items block other work.

| # | Task | Status |
|---|---|---|
| H1 | Confirm the name `agent-ledger` (CLI `ledger`) | To do |
| H2 | Create the GitHub repo and push | Done; public since 2026-11-14 |
| H3 | Turn on Actions and protect `main` (no force-push) | Done 2026-11-08: Actions on; main requires a PR and the three test checks, admins included, no force-push or deletion. |
| H4 | Approve the M0 charter | Done 2026-11-08 |
| H5 | Pick the project for the M0 gate run | Needed by M0 unit 9 |
| H6 | Allow only merge commits on `main` (turn off squash and rebase merging) | Done 2026-11-10: squash and rebase merging off, merge commits on |
| H7 | Require the `ledger-verify` check on `main` | Done 2026-11-14: required with the three test checks, strict |
| H8 | Decide whether M3 unit 1 opens before M1 and M2 | Done 2026-11-21: yes, it opens early |
| H9 | Decide whether evals may spend model calls in CI | Done 2026-11-21: local only for now |
| H10 | H9 again, with a cost per case run: may agent-kit's CI run the M3 unit 1 cases? | To do; nothing waits on it until Feature PR 2 lands |

### H1. Confirm the name
Renaming is cheap until the repo is public and `uses: lyddonb/agent-ledger@v1` appears in other repos' CI. Rename the folder, `pyproject.toml`, the README and the specs if you want something else.

### H2. Create the GitHub repo and push — Done
The repo is `lyddonb/agent-ledger`, public since 2026-11-14, when agentfiles started installing `ledger` from it.

```bash
cd ~/code/agent-ledger
gh repo create lyddonb/agent-ledger --public --source . --push
```
Public and generic, like agent-kit and agentfiles. Nothing employer-specific goes here.

### H3. Actions and branch protection — Done
Enable Actions, then protect `main`: require the `test` check, require a PR, and block force-pushes. Blocking force-pushes is what makes merged history the project-level chain (spec S3).

### H4. Approve the M0 charter — Done 2026-11-08
Approved as written. Recorded in [bootstrap-notes.md](.audit/runs/2026-10-18-m0-foundation/bootstrap-notes.md) with the charter's hash; imported into the ledger when unit 2 lands.

Read [.audit/runs/2026-10-18-m0-foundation/charter.md](.audit/runs/2026-10-18-m0-foundation/charter.md). Edit anything (budget, staffing, units, open questions), then say "approved" in chat or commit the edited charter on `main`. Work starts only after that.

### H5. Pick the gate-run project
M0's gate is one real tier 2 run in a real project. Choose which project and task, ideally one with a `verify-<app>` skill.

### H6. Allow only merge commits — Done 2026-11-10
A seal names a commit on the PR branch. A squash or rebase merge rewrites the branch, so that commit never reaches `main`, and `ledger verify` reports `seal-commit` on every sealed run merged that way. In the repo's Settings, under Pull Requests, untick "Allow squash merging" and "Allow rebase merging", or run:

```bash
gh api -X PATCH repos/lyddonb/agent-ledger -f allow_squash_merge=false -f allow_rebase_merge=false
```
Every project that keeps `.audit/` needs the same setting; unit 7's agentfiles status can check it later.

### H7. Require the `ledger-verify` check — Done 2026-11-14
Once the `ledger` workflow is on `main` and `ledger-verify` has run on a pull request (it runs from `pull_request_target`, so it first appears on the pull request after this one), add `ledger-verify` to `main`'s required status checks. Don't require `ledger-verify-self`: a pull request can edit it next to the three test checks, so a PR that breaks a chain, rewrites merged lines or adds a secret can't merge:

```bash
gh api -X POST repos/lyddonb/agent-ledger/branches/main/protection/required_status_checks/contexts -f 'contexts[]=ledger-verify'
```
Every project that adopts the Action needs the same rule.

### H8. Does M3 unit 1 open before M1 and M2? — Done
**Answer (2026-11-21):** Yes. Beau's words: "H8 agree, yes." It opens before M1 and M2.
Not settled by this answer: what "four weeks of rows" in agent-kit R38 means. The books hold
rows for 2026-W41 and W43 to W46, booked in one run on 2026-11-21, and 109 of the 141 are in
W46. Counting those, the wait is over. Counting only weeks the installed job books as they
close (W47 to W50), it ends about 2026-12-20. The Prototype step (how to capture tool calls
from `claude -p`) reads no rows and can start under either reading.

### H9. May evals spend model calls in CI? — Done
**Answer (2026-11-21):** Local only for now. Beau's words: "H9 agree, local only for now."
agent-kit's CI gets no secret and no budget line. The cases run on a developer's machine until
the Prototype step gives a cost per run; ask again with that number.

### H10. May agent-kit's CI run the M3 unit 1 cases, now that a run has a cost?
H9 said to ask again once the Prototype step gave a cost per run. Measured on Sonnet 5.5 with
the CLI's list-price figures ([notes](docs/notes/m3-unit1-prototype.md)): the `qualitydirector`
case is about $0.17 a run, and the `receipt.py` case about $0.18 a run. "Passes three times in a
row" on the three first cases is then about $1.50 to $2 per pass (inferred from two measured
cases). With only the cases for plugins `check_versions.py` reports as changed, a PR that
changes no plugin with a case runs none. A yes needs a secret with a budget cap in agent-kit's
CI, which is the `secrets` gate there. Nothing waits on this until the runner (Feature PR 2 in
the notes) exists; until then cases run locally either way.
