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
