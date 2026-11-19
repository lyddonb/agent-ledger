# Redaction

Every ledger write is redacted before it lands. A secret in an entry's summary, why, evidence or data is replaced by `[REDACTED:<pattern name>]`, and the chain still verifies. `ledger scan` checks every file under `.audit/` for secrets, so a secret that got there some other way, such as a hand-written file, fails the scan and is named.

## Sub-features

- `redact-append`: secrets in `--summary` and `--data` are replaced on append, and the entry still verifies.
- `scan-clean`: `scan` on a clean `.audit/` exits 0 and says `ok`.
- `scan-catch`: `scan` exits 1 and names a file under `.audit/` that holds a secret.

## How to get to it (user POV)

- `ledger append` (and every other write: `init`, `session add`, `charter approve`) redacts with the built-in patterns, plus `~/.agents/policy.yaml`, `.agents/policy.yaml` and `--redact-pattern`. The drive covers `append` with the built-in patterns only, because HOME is isolated and the workspace has no policy file.
- `ledger scan` from any shared entry point. The drive uses `ci/run_ledger.py`.
- `ledger scan --base <ref>` in the `ledger-verify` Action. Not driven here.

## Driving it with control-ledger

Preconditions:

- A fresh workspace from `$C launch`, with `$C doctor` exiting 0 (`isolated-home` ok, so no user policy is read).
- No run ending in `-redaction`.

- **Start a run.** `init redaction`. Exit 0.
- **Append with secrets.** `append --type lesson --summary "Rotated <fake Anthropic key> after it showed up in a log" --data '{"note": "old token <fake GitHub token>"}'`. Exit 0. `ledger.jsonl` contains neither fake value, and does contain `[REDACTED:anthropic_key]` and `[REDACTED:github_token]`.
- **Verify.** `verify .audit/runs/<run-id>`. Exit 0.
- **Scan, clean.** `scan`. Exit 0, and stdout starts `ok:`.
- **Plant a secret.** Write `.audit/runs/<run-id>/planted-notes.md` by hand, containing `GITHUB_TOKEN=<fake GitHub token>`.
- **Scan, catch.** `scan`. Exit 1. stdout names `planted-notes.md` and says `FAILED`.
- **Remove it.** Delete the file. `scan` exits 0 again.

## Gotchas

- Fake secrets have to match the real patterns or nothing is tested. The drive builds them at run time (`sk-` + `ant-` + filler, `gh` + `p_` + filler), so the repo never holds a secret-shaped literal. Don't paste one into this file.
- A user `~/.agents/policy.yaml` adds patterns and changes what gets redacted. Drive only with the workspace's HOME, and check `doctor`'s `isolated-home` line.
- `scan` reads files on disk, including uncommitted ones. `scan --base` reads only committed changes, so a planted file that isn't committed is invisible to it.
