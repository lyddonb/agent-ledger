# Session archive

`ledger session add <transcript> --run <run-id>` redacts an agent's session transcript, stores it gzipped at `.audit/runs/<run-id>/sessions/<session-id>.jsonl.gz`, and appends a `session` entry with the raw and redacted hashes. Adding the same transcript again changes nothing. If redaction can't be confirmed, nothing is archived, the entry records where the transcript is, and the exit code is 1.

## Sub-features

- `session-archive`: a transcript with secrets is archived redacted, and the entry records `archived: true`, the path and both hashes.
- `session-many`: twelve redactions under one pattern (`url_password`) still archive. This was a real bug, fixed in e101dc0.
- `session-idempotent`: a second `session add` of the same file prints `unchanged:` and appends nothing.
- `session-scan`: the archive passes `scan`.

## How to get to it (user POV)

- `ledger session add <file> --run <run-id> --actor '<JSON>'`, optionally with `--session <id>` (by default, the file name up to its first dot). The drive uses the default id, `verify-session`.
- agent-kit's show-me-your-work skill archives sessions at the end of a governed run. Not driven here.

## Driving it with control-ledger

Preconditions:

- A fresh workspace from `$C launch`, with `$C doctor` exiting 0.
- No run ending in `-session`.

- **Start a run.** `init session`. Exit 0.
- **Write a transcript.** `scratch/verify-session.jsonl` gets 14 JSON lines: one user message, 12 tool results each holding `psql postgres://app:pw<i>x@db/main`, and one holding `export ANTHROPIC_API_KEY=<fake key>`.
- **Archive it.** `session add <workspace>/scratch/verify-session.jsonl --run <run-id> --actor '<agent JSON>'`. Exit 0, and stdout starts `archived verify-session:`. The `.jsonl.gz` exists, and decompressed it has no `pw<i>x` and no key, but has exactly 12 `[REDACTED:url_password]`. The last ledger entry is `type: session`, with `archived: true`, `path: .audit/runs/<run-id>/sessions/verify-session.jsonl.gz`, and a `redacted_sha256` equal to the sha256 of the decompressed archive.
- **Add again.** Run the same command. Exit 0, stdout starts `unchanged:`, and the ledger line count is unchanged.
- **Verify.** `verify .audit/runs/<run-id>`. Exit 0.
- **Scan.** `scan`. Exit 0.

## Gotchas

- Keep the transcript outside `.audit/`, in the workspace's `scratch/`. A raw transcript under `.audit/` fails `scan`.
- A refused archive still appends an entry, with `archived: false`, and exits 1. Read the exit code and the entry, not only whether a file exists.
- Re-adding is a no-op only while the redact patterns are the same (`patterns_sha256`). A different HOME policy archives again.
