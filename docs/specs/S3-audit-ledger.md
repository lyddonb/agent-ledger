# S3. Audit ledger and session archive

> **Status: draft.** Mirrors S3 in the "Governed agent runs" plan doc, which stays canonical until you approve this spec. After approval, this file is canonical.

Each run keeps a hash-chained ledger in the repo, so any later edit to its history is detectable. Session transcripts are archived beside it, redacted. Merging into protected `main` anchors the chain in the project's git history.

**Lands in:** agent-ledger (the `ledger` CLI, sinks, redaction, the `ledger-verify` CI action), agent-kit (show-me-your-work and the playbooks call the CLI), each project (the `.audit/` files). **Milestone:** M0 (external sink: M4). **Requirements:** R4 to R8, R19.

## Layout

```text
.audit/
  INDEX.jsonl                     # one seal per merged run, itself chained
  runs/<run-id>/
    charter.md                    # S1
    ledger.jsonl                  # this run's chain
    sessions/<session-id>.jsonl.gz  # redacted transcripts
```

## Entry schema

One JSON object per line:

| Field | Meaning |
|---|---|
| `seq` | 1, 2, 3 within the run |
| `ts` | UTC time, ISO 8601 |
| `run_id` | The run this belongs to |
| `actor` | Agent or human, per S2 |
| `type` | `charter`, `approval`, `state`, `decision`, `gate`, `gate-answer`, `verification`, `review`, `spend`, `lever`, `lesson`, `session`, `seal` |
| `summary` | One line: what happened |
| `why` | One line: the reason |
| `evidence` | Pointers: commit SHAs, PR numbers, file paths with line, artifact hashes. Never pasted content |
| `data` | Type-specific fields, such as `{usd, tokens, wall_s}` for `spend` or `{verdict}` for `verification` |
| `prev` | The previous entry's hash, or `genesis:<run-id>` for the first |
| `hash` | SHA-256 of this entry's canonical JSON (sorted keys, no spaces) without `hash` |

## The ledger CLI

`ledger` is a standard-library Python CLI in this repo, installed once per machine (agentfiles installs it) and pinned by version in CI. bstack's show-me-your-work keeps its review steps and calls the CLI instead of `log.sh`.

| Command | Does |
|---|---|
| `ledger init <slug>` | Creates the run folder and the genesis entry |
| `ledger append --type T --summary S --why W --evidence E` | Redacts, chains and writes one entry through each configured sink |
| `ledger verify [--base origin/main]` | Checks every chain, that existing lines are unchanged relative to the base, and that each seal names a commit in history |
| `ledger show <run-id>` | Prints a readable table, newest last |
| `ledger session add <file>` | Redacts, compresses and stores a transcript, then appends a `session` entry with both hashes |
| `ledger seal <run-id>` | Writes the `seal` entry (final hash and head commit) and appends it to `INDEX.jsonl` |
| `ledger charter ...` | Charter commands from S1 |
| `ledger import-tsv <file>` | Converts an old `decisions.tsv` trail into a run |

Parallel subagents append through the orchestrating agent, or take a file lock, so a chain never forks.

## Sinks

Every write goes through a sink interface with two calls, `write(entry)` and `seal(run_id, hash, commit)`. M0 ships the `repo` sink. An `s3-object-lock` sink and an `external` ledger sink come in M4, configured in policy `audit.sinks`, with no change to any skill.

## Session archive

- Every session that worked on the run is archived when the run closes, and on pause.
- Before writing, the transcript is redacted with built-in secret patterns plus the policy's `redact_patterns`. The entry stores the hash of the raw transcript and of the redacted file, so integrity can be proven later without keeping the raw copy in git.
- Files over the policy's `lfs_over_mb` go to Git LFS.
- If redaction can't be confirmed (the scanner errors, or a pattern match survives), the transcript stays out of git and the entry records where it is.

## CI check

This repo ships a GitHub Action, `ledger-verify`, that runs `ledger verify --base <target branch>` and a secret scan over `.audit/` on every PR. It fails when a chain breaks, a past line changed, or a secret appears. With branch protection that forbids force-pushes to `main`, the merged history is the project-level chain.

## Migration

Existing `decisions.tsv` trails stay readable. `ledger import-tsv` converts one into a run, marked as imported, with chaining starting at import.

## Acceptance criteria

- [ ] Editing any past line of a ledger makes `ledger verify` fail and names the line.
- [ ] Deleting a line or reordering two lines fails verification.
- [ ] A planted fake secret in a transcript is redacted before it reaches git; the CI scan finds nothing.
- [ ] Two subagents appending at once produce one valid chain.
- [ ] A sealed run's `INDEX.jsonl` entry names a commit that exists on `main`.
- [ ] Swapping the repo sink for a test sink needs only a policy change.
