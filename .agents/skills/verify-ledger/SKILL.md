---
name: verify-ledger
description: Prove agent-ledger's `ledger` CLI works by driving the real command from an empty .audit/ in a throwaway git repo, and keep the evidence. Covers init, append, show, verify and tamper detection, redaction and scan, session archive, receipts, and seal. Use when verifying a change to agent-ledger, before saying a ledger feature works, or when verify-work asks for a live run.
---

# verify-ledger

agent-ledger's product is the `ledger` command line. This skill runs that command the way a user does, in a throwaway repo that starts with an empty `.audit/`, checks exit codes and the files it writes, and keeps every command, its output and a copy of the final `.audit/` as evidence.

Everything goes through one helper, `control-ledger`. Run it from the repo root as shown below. It needs Python 3.10 or newer. If `python3` is older (macOS ships 3.9), it re-runs itself on the newest `python3.10` to `python3.14` it finds on PATH. You don't need to install anything.

**Don't use the `ledger` on PATH.** On Beau's machines that is a shim pinned to an older commit. `control-ledger` always runs this checkout's code (`ci/run_ledger.py`, the same entry point the shim and the ledger-verify Action use), and `doctor` prints which code ran.

## Cold start: the whole sequence

From the agent-ledger repo root:

```bash
C=.agents/skills/verify-ledger/scripts/control-ledger
$C launch                      # prints: workspace <dir>, evidence <dir>, then "ready: export VERIFY_LEDGER_WS=<dir>"
export VERIFY_LEDGER_WS=<the workspace dir it printed>
$C doctor                      # every line "ok", last line "doctor: ready to drive", exit 0
$C drive all                   # one PASS line per feature, last line "PASSED: 5/5 features; evidence in <dir>", exit 0
$C cleanup                     # "removed <workspace>; evidence kept in <dir>"
```

To drive one feature instead of all of them, run `$C drive <feature>` with a feature name from the map below. Each feature runs once per workspace. To drive it again, run `cleanup`, then `launch` a new workspace.

Pass looks like this: `drive` exits 0, every feature line says `PASS`, and `<evidence>/drive-all.json` has `"ok": true`.

Fail looks like this: `drive` exits 1, and the failing feature prints `FAIL`, the failed step, and each failed check with what it saw. For example:

```text
FAIL receipts         7 steps, 0.65s
     failed step: receipts table
     - one row per criterion, in natural order (P2 before P10): ['P10-C1', 'P2-C1']
```

Exit 2 means the harness itself couldn't run, for example a missing workspace, a target that isn't an agent-ledger checkout, or `drive all` on a workspace whose `.audit/` isn't empty. The message says what to do.

## Launch

`control-ledger launch` creates `$TMPDIR/verify-ledger-XXXX/`, which holds:

- `repo/`: a fresh `git init` with one commit (a README) and an empty `.audit/`. `ledger` runs with this as its working directory, so it finds `.audit/` from the nearest `.git`, as it does in a real project.
- `home/`: the `HOME` for every ledger command, so `~/.agents/policy.yaml` can't add redact patterns to the run.
- `scratch/`: inputs such as transcripts, kept outside `.audit/`.
- `control-ledger.json`: the record of this workspace (target checkout and commit, Python, evidence path). Every other command reads it, and `cleanup` refuses a folder without it.

It's ready once `launch` prints `ready:`. There is no server, and every ledger command is a short-lived process.

Options: `--target <checkout>` runs another agent-ledger checkout's code (use this to drive an old commit), `--evidence-root <dir>` moves the evidence, and `--parent <dir>` moves the workspace. Two workspaces never share anything, so parallel runs are safe.

## Doctor

`control-ledger doctor` is read-only. Run it after launch, and again whenever something looks off. Each line is `ok` or `FAIL`:

| Check | What it proves |
|---|---|
| `python` | The interpreter is 3.10 or newer. |
| `target` | The checkout under test, and that its commit hasn't moved since launch. |
| `code-under-test` | `agent_ledger` is imported from `<target>/src`, not from an installed copy. |
| `ledger-runs` | `ledger --version` runs from the target. |
| `isolated-root` | The workspace repo is its own git root, outside the checkout, so nothing writes to the repo's real `.audit/`. |
| `isolated-home` | HOME is the workspace's, with no user policy. |
| `audit-dir` | `.audit/` exists, and lists the runs in it (`empty` right after launch). |
| `evidence-dir` | The evidence folder exists and is writable. |

`doctor --json` prints the same as JSON. Exit 0 means every line is ok.

## Drive

`control-ledger drive <feature>` runs the journey written in `features/<feature>.md`. Each step runs one real `ledger` command, then checks its exit code and the files it changed. The first failed step stops that feature, and the other features still run. Every step is written to `<evidence>/<feature>/steps.jsonl` as it runs.

| Feature | Map | What the drive proves |
|---|---|---|
| `run-chain` | [features/run-chain.md](features/run-chain.md) | init, append, show and verify; a hand edit is caught, and append refuses a broken chain |
| `redaction` | [features/redaction.md](features/redaction.md) | secrets in a summary or data are redacted on append; scan finds a secret planted under `.audit/` |
| `session-archive` | [features/session-archive.md](features/session-archive.md) | `session add` archives a redacted transcript and records both hashes; adding it again is a no-op |
| `receipts` | [features/receipts.md](features/receipts.md) | receipts print as a Markdown table, one row per criterion in natural order; a malformed one is refused |
| `seal` | [features/seal.md](features/seal.md) | seal refuses an uncommitted ledger, anchors a committed one in `INDEX.jsonl`, and verify counts it |

For a command the map doesn't cover, use `control-ledger cli -- <ledger args>`, for example `$C cli -- show <run-id>` or `$C cli -- verify`. It runs that one command in the workspace, prints its output, exits with its code, and records it in `<evidence>/cli/NNN.json`. Run ids come from `init`'s output (`started <run-id>: ...`) or `$C doctor` (the `audit-dir` line).

Not mapped yet: `charter new|check|approve`, `spend`, `verify --base` against a real base branch, and the `ledger-verify` Action. Drive them with `cli` and add a feature file when one becomes routine.

## Evidence

The evidence for a workspace is at `<repo>/.verify/ledger/<workspace name>/`, which is git-ignored. `launch` prints the path, and `--evidence-root` changes it. It holds:

- `launch.json` and `doctor.json`: the workspace record and the doctor result.
- `<feature>/steps.jsonl`: one line per step, with argv, exit code, stdout, stderr, seconds and each check (`{check, ok, detail}`).
- `<feature>/result.json`: pass or fail, and the failed step and checks.
- `drive-<feature or all>.json`: the summary, with the target commit.
- `audit-snapshot/.audit/` and `audit-snapshot/MANIFEST.sha256`: the workspace's `.audit/` after the drive, with a sha256 per file.
- `cli/NNN.json`: one per `cli` command.
- `cleanup.json`: written when the workspace is removed.

Proof standards:

- Drive the CLI as a user does. Don't call the Python API.
- Check the side effects on disk (ledger lines, archives, `INDEX.jsonl`), not only stdout. stdout wording can change between versions, so checks match only the stable parts (the run id, `ok`, `FAILED`, `refusing`).
- A tamper or a planted secret is written by hand, as an attacker or a careless commit would do it, and the drive then shows that `ledger` catches it.
- The fake secrets are assembled at run time from parts, so no secret-shaped string is committed in this skill. They are obviously fake but still match the built-in patterns.

## Cleanup

`control-ledger cleanup` removes the workspace folder, and only one that has `control-ledger.json` in it. It never touches the evidence. `--dry-run` prints what it would remove. No process outlives a command, so there's nothing to kill. Run cleanup after a failed run too.

## Helpers

Both scripts are executable and standard library only. Each has `--help` with an example.

- `scripts/control-ledger`: `launch`, `doctor`, `drive`, `cli`, `snapshot`, `cleanup`.
- `scripts/stress-ledger --runs 20 --evidence-root <dir>`: runs the cold-start sequence (launch, doctor, drive all, cleanup) N times in a row with no retries. A run passes only when every command exits 0, `drive-all.json` survives cleanup with `"ok": true`, the workspace is gone, and this checkout's own `.audit/` is unchanged. It prints each run's time and the pass count, and writes `stress.jsonl` and `stress-summary.json` to the evidence root.

To show the drive goes red on a real past bug, extract an old commit and point `--target` at it:

```bash
mkdir /tmp/ledger-at-0e7fc56 && git archive e101dc0^ | tar -x -C /tmp/ledger-at-0e7fc56
$C launch --target /tmp/ledger-at-0e7fc56   # then doctor, drive session-archive, cleanup as above
rm -rf /tmp/ledger-at-0e7fc56
```

At `e101dc0^`, `session-archive` fails at `session add` with exit 1, and at `e101dc0` it passes. At `95c8b1e^`, `receipts` fails on row order (`P10-C1` before `P2-C1`), and at `95c8b1e` it passes.
