# ledger verification map

This folder is the maintained record of what a user can do with the `ledger` CLI and how to prove each thing works. Read this page, then use the matching feature file as the recipe. `control-ledger drive <feature>` runs each recipe exactly as written here.

## Baseline preconditions

- `$C` is `<repo>/.agents/skills/verify-ledger/scripts/control-ledger`, as an absolute path.
- `$C launch` has made a fresh workspace. Every other command gets it as `--ws <ws>`, or from `VERIFY_LEDGER_WS` in one persistent shell. Its `repo/` is its own git repo with one commit and an empty `.audit/`, and its `home/` is HOME for every ledger command.
- `$C doctor` exits 0, and its `code-under-test` line names this checkout's `src/`.
- Never drive the repo's own `.audit/`, or a workspace that `launch` didn't create.

## Driving conventions

- Run ledger commands through `control-ledger drive` or `control-ledger cli -- <args>`. Both run the checkout's `ci/run_ledger.py` with the workspace repo as the working directory.
- Actors are fixed test identities. The agent is `verify-ledger@control-ledger#0000`, acting for `verify-ledger@github`.
- Run ids come from `init`'s output, `started <yyyy-mm-dd>-<slug>: ...`. Never compute the date yourself, since a run can cross midnight UTC.
- Each feature uses its own run (`chain`, `redaction`, `session`, `receipts`, `seal`), so features don't depend on one another. A feature runs once per workspace.
- Fake secrets are assembled at run time from parts and never written into these files.

## Proof and skip reporting

- Proof for each command is its argv, stdout, stderr and exit code, in `<evidence>/<feature>/steps.jsonl`.
- A change on disk is proved by reading the file back (ledger lines, the gzip archive, `INDEX.jsonl`), not by trusting stdout.
- A catch, such as a tamper, a planted secret or a malformed receipt, is proved by its exit code, by the message naming the problem, and by the file being unchanged afterward.
- `<evidence>/audit-snapshot/` keeps the final `.audit/` with a sha256 manifest.
- Report an entry point you didn't drive as not verified. Don't count a pass through `control-ledger` as proof for the GitHub Action.

## Feature entry contract

Each feature file starts with an H1 title and one paragraph describing the user-visible behavior. It then has exactly four H2 sections, in this order:

1. `Sub-features`: short IDs, one line each.
2. `How to get to it (user POV)`: every entry point, and which one the drive covers.
3. `Driving it with control-ledger`: starts with `Preconditions:`, then labeled bullets, each pairing a user action with the exact command and its observable result.
4. `Gotchas`: traps that waste or invalidate a run.

## Entry points shared by every feature

- The `ledger` console script from `pip install .` (`agent_ledger.cli:main`).
- The `ledger` shim agentfiles installs, which runs `ci/run_ledger.py` from a pinned commit.
- `python -I ci/run_ledger.py <args>` from a checkout. **The drive uses this one**, from the checkout under test.
- The `ledger-verify` GitHub Action (`action.yml`), which runs only `verify` and `scan`. Nothing here drives it.

## Features

- [Run chain](./run-chain.md): init, append, show and verify, and tamper detection.
- [Redaction](./redaction.md): secrets redacted on append, and `scan` over `.audit/`.
- [Session archive](./session-archive.md): `session add` with a redacted, compressed transcript.
- [Receipts](./receipts.md): verification receipts and the `receipts` table.
- [Seal](./seal.md): `seal`, `INDEX.jsonl`, and verify's seal count.

Not mapped yet: `charter new|check|approve`, `spend`, and `verify --base` against a branch with real history.
