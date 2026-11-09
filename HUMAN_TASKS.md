# Human tasks

Things an agent cannot or should not do. **🚧 BLOCKER** items block other work.

| # | Task | Status |
|---|---|---|
| H1 | Confirm the name `agent-ledger` (CLI `ledger`) | To do |
| H2 | Create the GitHub repo and push | Done (private for now; see below) |
| H3 | Turn on Actions and protect `main` (no force-push) | Done 2026-11-08: Actions on; main requires a PR and the three test checks, admins included, no force-push or deletion. |
| H4 | Approve the M0 charter | Done 2026-11-08 |
| H5 | Pick the project for the M0 gate run | Needed by M0 unit 9 |

### H1. Confirm the name
Renaming is cheap until the repo is public and `uses: lyddonb/agent-ledger@v1` appears in other repos' CI. Rename the folder, `pyproject.toml`, the README and the specs if you want something else.

### H2. Create the GitHub repo and push — Done
The repo exists as `lyddonb/agent-ledger` but is **private**. The plan below says public; making it public is still your call.

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
