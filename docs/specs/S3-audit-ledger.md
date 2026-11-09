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
| `v` | Format version. `1` for everything below. A verifier rejects a version it doesn't know |
| `seq` | 1, 2, 3 within the run |
| `ts` | UTC time, ISO 8601, to the second: `2026-11-08T19:51:55Z` |
| `run_id` | The run this belongs to |
| `actor` | Agent or human, per S2 |
| `type` | `charter`, `approval`, `state`, `decision`, `gate`, `gate-answer`, `verification`, `review`, `spend`, `lever`, `lesson`, `session`, `seal` |
| `summary` | One line: what happened |
| `why` | One line: the reason |
| `evidence` | Pointers: commit SHAs, PR numbers, file paths with line, artifact hashes. Never pasted content |
| `data` | Type-specific fields, such as `{usd, tokens, wall_s}` for `spend` or `{verdict}` for `verification` |
| `prev` | The previous entry's hash, or `genesis:<run-id>` for the first |
| `hash` | SHA-256 of this entry's canonical JSON (sorted keys, no spaces) without `hash` |

Every field is always present, and no other keys are allowed. The v1 rules, exactly:

- `seq`: an integer from 1 to 2^53−1. `prev` is `genesis:<run_id>` when `seq` is 1, and otherwise 64 lowercase hex characters, as is `hash`.
- `ts`: matches `[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z` and is a real Gregorian date and time with seconds 00 to 59 (no leap second).
- `run_id`: matches `[0-9]{4}-[0-9]{2}-[0-9]{2}-[a-z0-9]+(-[a-z0-9]+)*` (S1's `<yyyy-mm-dd>-<slug>`), the date is a real date, and it equals the run folder's name.
- Text: `summary`, `why`, each `evidence` item and each actor field are non-empty strings containing no U+0000 to U+001F, no U+007F to U+009F, and no U+2028 or U+2029.
- `evidence` is a list of text strings and may be empty. `data` is an object and may be empty.
- `actor`: an agent has exactly `kind` (`"agent"`), `id`, `on_behalf_of`, `tool`, `model` and `session`; a human has exactly `kind` (`"human"`), `id` and `on_behalf_of`. `approval` and `gate-answer` entries have a human actor (S2).

> **Open (spend amounts):** format v1 has no floating-point numbers (see below), so `spend` can't carry `usd: 4.25`. The proposal is integer `usd_micros`. Decide before v1 is frozen at the `public-api` gate.

### Format v1: canonical form

This section is normative. A verifier in any language must agree with it byte for byte; `tests/vectors/` holds golden vectors and a golden ledger.

- **Values.** A strict subset of JSON: `null`, `true`, `false`, integers, strings, arrays and objects. Integers have no fraction or exponent and lie within ±(2^53−1). There are no floats, `NaN` or `Infinity`. Object keys match `[a-z][a-z0-9_]{0,63}` and are unique. Strings are Unicode scalar values: a lone surrogate, raw or escaped, is invalid. Arrays and objects nest at most 32 deep, counting the line's top-level object as 1. No Unicode normalization is applied anywhere.
- **Encoding.** RFC 8785 (JCS) for these values: no whitespace; object members sorted by key (keys are ASCII, so byte order equals code-point order); integers in shortest decimal form, with no `-0`; strings escape `"` and `\` and use `\b \f \n \r \t`, write every other U+0000 to U+001F as `\u00xx` in lowercase hex, and write everything else, including `/`, DEL and non-ASCII, as raw UTF-8.
- **Hash.** `hash` is the lowercase hex SHA-256 of the canonical encoding of the entry without its `hash` member.
- **Lines.** Each line of `ledger.jsonl` is the canonical encoding of the whole entry, `hash` included, followed by one LF. The file is UTF-8 with no BOM, no CR and no blank lines, and ends with LF. A verifier compares the line's bytes with the canonical encoding of what it parsed, so a whitespace, escape or key-order edit is an error, never normalized away.

### What `ledger verify` detects

Each line is linked to the previous line's stored `seq` and `hash`, so a problem is reported first on the line where it shows, not on every line after it. When a line can't be parsed, the next line's links are unchecked; the run fails, but a deletion right after an unreadable line isn't reported on its own. A `ledger.jsonl` that is a symlink or not a regular file fails (`not-a-file`). Messages quote found values ASCII-escaped and truncated.

| Change to the file | Reported |
|---|---|
| Any changed byte | First on the changed line (`non-canonical`, `bad-json`, `schema` or `hash-mismatch`); a change to `seq` or `hash` also shows on the next line |
| A line edited and its hash recomputed | On the next line (`prev-mismatch`) |
| A deleted line, other than the last | On the line after the gap (`seq-mismatch`, `prev-mismatch`) |
| Two lines swapped | On the first moved line and the lines around each move |
| A forged `prev` | On that line (`prev-mismatch`, or `schema` for a genesis link out of place) |
| A line copied in from another run | On that line (`run-id-mismatch`) |
| A write cut off mid-line | On the last line (`torn-line`) |

A chain on its own can't detect entries cut from the end, a suffix rewritten with recomputed hashes, or a whole file replaced by another valid chain: the hash has no secret. Seals, `INDEX.jsonl` and `verify --base` (M0 unit 4) close those gaps by anchoring the head in git history.

## The ledger CLI

`ledger` is a standard-library Python CLI in this repo, installed once per machine (agentfiles installs it) and pinned by version in CI. bstack's show-me-your-work keeps its review steps and calls the CLI instead of `log.sh`.

| Command | Does |
|---|---|
| `ledger init <slug> --actor A [--date D] [--why W]` | Creates the run folder (or uses an existing one) and the first entry, a `state` entry with `data` `{"state": "started"}`, chained to `genesis:<run-id>`. The run id is `<date>-<slug>`; the date defaults to today. Refuses a run that already has a ledger (built in M0 unit 2) |
| `ledger append --run R --actor A --type T --summary S --why W [--evidence E]... [--data JSON]` | Redacts (from M0 unit 3), chains and writes one entry through each configured sink. Refuses a run whose ledger fails verify: exit 1 with the problems, as `verify` prints them (built in M0 unit 2) |
| `ledger verify [--base origin/main]` | Checks every chain, that existing lines are unchanged relative to the base, and that each seal names a commit in history |
| `ledger verify RUN_FOLDER` | Checks one run's chain (built in M0 unit 1). Exit 0 verified, 1 failed, 2 usage or read error |
| `ledger show <run-id>` | Prints a readable table, newest last, after verifying the run; a run that fails verify prints the problems instead and exits 1 (built in M0 unit 2) |
| `ledger session add <file>` | Redacts, compresses and stores a transcript, then appends a `session` entry with both hashes |
| `ledger seal <run-id>` | Writes the `seal` entry (final hash and head commit) and appends it to `INDEX.jsonl` |
| `ledger charter ...` | Charter commands from S1 |
| `ledger import-tsv <file>` | Converts an old `decisions.tsv` trail into a run |

`--actor` is the S2 actor as JSON. It is required on every write: the CLI never guesses who is acting, so a subagent can't inherit its parent's identity by accident. The run folder is `.audit/runs/<run-id>` under `--root`, which defaults to the nearest folder holding `.git`.

Parallel subagents append through the orchestrating agent, or take a file lock, so a chain never forks. The repo sink takes the lock itself: `write` holds `flock` on `ledger.jsonl` (exclusive to write, shared to read), re-verifies the file, and accepts an entry only if its `seq` and `prev` extend the current head. `append` re-reads the head and re-chains when it loses that race. So any writer, CLI or library, gets one chain. After taking the lock, `write` checks that the file it locked is still the one at `ledger.jsonl` (a `git checkout` replaces the file by rename) and retries if not. A write that fails partway is truncated back under the lock. The lock needs `fcntl` (Linux, macOS), so `append` and `show` do too; `init` and `verify` don't. A process killed mid-write still leaves a torn last line, which verify reports and every later append refuses until a person repairs it. `ledger verify` reads without the lock, so run it when no one is appending.

The writer refuses a symlink at `.audit`, `.audit/runs` or the run folder, as verify refuses one at `ledger.jsonl`. Outside a git repo, `--root` is required.

The CLI does not authenticate `--actor`: anyone who can run `ledger append` can write an entry naming any actor, including a human approval. In M0 the protection is review of the PR that adds the line; signing is not in M0. `ledger show` prints each actor as `kind:id` and escapes invisible and bidi characters, so one actor can't pass for another on screen.

## Sinks

Every write goes through a sink interface with two calls, `write(entry)` and `seal(run_id, hash, commit)` (`agent_ledger.sink.Sink`). M0 ships the `repo` sink, which is the chain's source of truth: other sinks mirror entries it has accepted. Its `seal` is built in M0 unit 4. An `s3-object-lock` sink and an `external` ledger sink come in M4, configured in policy `audit.sinks`, with no change to any skill.

## Session archive

- Every session that worked on the run is archived when the run closes, and on pause.
- Before writing, the transcript is redacted with built-in secret patterns plus the policy's `redact_patterns`. The entry stores the hash of the raw transcript and of the redacted file, so integrity can be proven later without keeping the raw copy in git.
- Files over the policy's `lfs_over_mb` go to Git LFS.
- If redaction can't be confirmed (the scanner errors, or a pattern match survives), the transcript stays out of git and the entry records where it is.

## CI check

This repo ships a GitHub Action, `ledger-verify`, that runs `ledger verify --base <target branch>` and a secret scan over `.audit/` on every PR. It fails when a chain breaks, a past line changed, or a secret appears. With branch protection that forbids force-pushes to `main`, the merged history is the project-level chain.

## Migration

Existing `decisions.tsv` trails stay readable. `ledger import-tsv` converts one into a run, marked as imported, with chaining starting at import.

An imported entry is marked by `data.imported`, an object with `source` (where the record was, such as `bootstrap-notes.md#3`), `source_ts` (the time the record gives, in `ts` form) and `by` (the id of the actor who imported it). Its `ts` is when it was chained, never the earlier time, so `ts` always means "written to the ledger at". Its `actor` is the record's original actor, which `data.imported.by` qualifies: an imported human approval was typed in by the importer, not signed by the human. `ledger show` tags these entries `[imported]`. This is a convention inside `data`, not a format change.

## Acceptance criteria

- [ ] Editing any past line of a ledger makes `ledger verify` fail and names the line.
- [ ] Deleting a line or reordering two lines fails verification.
- [ ] A planted fake secret in a transcript is redacted before it reaches git; the CI scan finds nothing.
- [x] Two subagents appending at once produce one valid chain (`tests/test_sink.py` `ConcurrentAppendTest`, M0 unit 2).
- [ ] A sealed run's `INDEX.jsonl` entry names a commit that exists on `main`.
- [ ] Swapping the repo sink for a test sink needs only a policy change.
