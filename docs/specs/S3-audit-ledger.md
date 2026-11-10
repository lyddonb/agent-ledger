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
| `data` | Type-specific fields, such as `{usd_micros, tokens, wall_s}` for `spend` (see Spend entries) or `{verdict}` for `verification` |
| `prev` | The previous entry's hash, or `genesis:<run-id>` for the first |
| `hash` | SHA-256 of this entry's canonical JSON (sorted keys, no spaces) without `hash` |

Every field is always present, and no other keys are allowed. The v1 rules, exactly:

- `seq`: an integer from 1 to 2^53−1. `prev` is `genesis:<run_id>` when `seq` is 1, and otherwise 64 lowercase hex characters, as is `hash`.
- `ts`: matches `[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z` and is a real Gregorian date and time with seconds 00 to 59 (no leap second).
- `run_id`: matches `[0-9]{4}-[0-9]{2}-[0-9]{2}-[a-z0-9]+(-[a-z0-9]+)*` (S1's `<yyyy-mm-dd>-<slug>`), the date is a real date, and it equals the run folder's name.
- Text: `summary`, `why`, each `evidence` item and each actor field are non-empty strings containing no U+0000 to U+001F, no U+007F to U+009F, and no U+2028 or U+2029.
- `evidence` is a list of text strings and may be empty. `data` is an object and may be empty.
- `actor`: an agent has exactly `kind` (`"agent"`), `id`, `on_behalf_of`, `tool`, `model` and `session`; a human has exactly `kind` (`"human"`), `id` and `on_behalf_of`. `approval` and `gate-answer` entries have a human actor (S2).

### Spend entries

Format v1 has no floating-point numbers, so dollars are integer millionths: `usd_micros: 4250000` is $4.25. Micros keep one input token on the cheapest model above zero, and 2^53 micros is about $9 billion. A `spend` entry's `data` has:

| Key | Required | Meaning |
|---|---|---|
| `usd_micros` | yes | Dollars spent, in millionths |
| `tokens` | no | Input, output and cache-write tokens. Cache reads are not counted (S1 "Budget") |
| `cache_read_tokens` | no | Cache-read tokens, reported separately |
| `wall_s` | no | Wall-clock seconds |
| `estimate` | no | `true` when the figures are estimated, not billed |

Each is a non-negative integer except `estimate`, a boolean. Other keys are allowed. `ledger append` refuses a `spend` entry that breaks these rules; `verify` doesn't check them, because they are a convention inside `data`, not part of format v1. Decided by Beau on 2026-11-09 (the M0 run's ledger records it as a `gate-answer`).

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

### Seals and INDEX.jsonl

- **Seal entry.** `ledger seal <run-id>` appends a `seal` entry. Its `prev` is the run's final hash, and `data.commit` (also its one evidence pointer) is HEAD's full commit id. It refuses unless the ledger at HEAD is byte for byte the file on disk, so the commit really holds the head the seal follows. A sealed run takes no more entries; `append` refuses, and a hand-made `seal` entry is refused too.
- **INDEX.jsonl.** Then the run gets one line in `.audit/INDEX.jsonl`: the canonical JSON of `{v, seq, ts, run_id, seal, commit, prev, hash}`, where `seal` is the seal entry's hash and `prev` chains from `genesis:index`, with the same canonical form, framing and hashing as a ledger. The line is appended under the same `flock` rule as a ledger, so concurrent seals make one chain. A run appears at most once (`duplicate-seal`).
- **Merges.** A seal names a branch commit, so it survives only a merge commit. A squash or rebase merge drops that commit and verify reports `seal-commit`; protect `main` to allow merge commits only. Two branches that each seal a run conflict on `INDEX.jsonl`: take the base branch's copy, run `ledger seal <run-id>` again (it indexes a sealed run that has no line), and commit.

### What `ledger verify` over the repo detects

`ledger verify` with no run folder checks every run under `.audit/runs`, `INDEX.jsonl`, and how they agree. `--base REF` adds the append-only check. Exit 0 verified, 1 failed, 2 a usage, read or git error (such as a bad ref).

| Change | Reported | Needs |
|---|---|---|
| Any break in one run's chain | As in the table above | |
| An entry after a seal | `after-seal` on that line | |
| A sealed run with no index line | `unindexed-seal` | |
| An index line for a run that isn't sealed, or naming another seal or commit | `index-mismatch` on that line | |
| A seal whose commit isn't in HEAD's history, or doesn't hold that ledger with the head the seal follows | `seal-commit` | git |
| A changed or cut line, or a deleted ledger, in a file that exists at the base | `base-changed` on the first differing line | `--base` |
| Entries cut from the end of a merged run, or a merged suffix rewritten with recomputed hashes | `base-changed` | `--base` |
| A merged ledger replaced by another valid chain | `base-changed` | `--base` |
| A symlink or submodule anywhere in `.audit` (at HEAD, at the base, or on disk), or a symlinked run folder | `not-a-file` | |
| A ledger or `INDEX.jsonl` on disk that isn't the committed file (an uncommitted edit, or a `.gitattributes` conversion) | `uncommitted` | `--base` |

The base check covers every `ledger.jsonl` under `.audit/runs` and `.audit/INDEX.jsonl` that git lists at the base, and it reads both sides as git objects (the base commit's blobs against HEAD's, by `ls-tree` with modes and `cat-file`), never the working tree. So a PR can't show the check one set of bytes and merge another: not by swapping a folder for a symlink, which would hide merged files from the next PR's check, and not by a `.gitattributes` encoding or line-ending conversion, which changes the committed bytes while the checkout looks the same. With `--base`, verify therefore checks committed work: commit first. Without `--base`, verify says so, since merged lines can then be cut or rewritten unnoticed. In CI the checkout needs full history (`fetch-depth: 0`) for the seal and base checks.

## The ledger CLI

`ledger` is a standard-library Python CLI in this repo, installed once per machine (agentfiles installs it) and pinned by version in CI. bstack's show-me-your-work keeps its review steps and calls the CLI instead of `log.sh`.

| Command | Does |
|---|---|
| `ledger init <slug> --actor A [--date D] [--why W]` | Creates the run folder (or uses an existing one) and the first entry, a `state` entry with `data` `{"state": "started"}`, chained to `genesis:<run-id>`. The run id is `<date>-<slug>`; the date defaults to today. Refuses a run that already has a ledger (built in M0 unit 2) |
| `ledger append --run R --actor A --type T --summary S --why W [--evidence E]... [--data JSON]` | Redacts summary, why, evidence and data (built in M0 unit 3), chains and writes one entry through each configured sink. Refuses a run whose ledger fails verify: exit 1 with the problems, as `verify` prints them (built in M0 unit 2) |
| `ledger verify [--base origin/main]` | Checks every chain, `INDEX.jsonl` and the seals, and with `--base` that lines at the base are unchanged (built in M0 unit 4) |
| `ledger verify RUN_FOLDER` | Checks one run's chain (built in M0 unit 1). Exit 0 verified, 1 failed, 2 usage or read error |
| `ledger show <run-id>` | Prints a readable table, newest last, after verifying the run; a run that fails verify prints the problems instead and exits 1 (built in M0 unit 2) |
| `ledger session add <file> --run R --actor A [--session ID] [--redact-pattern P]...` | Redacts, compresses and stores a transcript, then appends a `session` entry with both hashes. Exit 0 archived (or unchanged since the last add), 1 not archived because redaction couldn't be confirmed, 2 usage or read error (built in M0 unit 3) |
| `ledger seal <run-id> --actor A [--why W]` | Writes the `seal` entry (its `prev` is the final hash; it names the HEAD commit) and appends the run to `INDEX.jsonl`. Re-running it indexes a sealed run that has no line (built in M0 unit 4) |
| `ledger charter ...` | Charter commands from S1 |
| `ledger import-tsv <file>` | Converts an old `decisions.tsv` trail into a run |

`--actor` is the S2 actor as JSON. It is required on every write: the CLI never guesses who is acting, so a subagent can't inherit its parent's identity by accident. The run folder is `.audit/runs/<run-id>` under `--root`, which defaults to the nearest folder holding `.git`.

Parallel subagents append through the orchestrating agent, or take a file lock, so a chain never forks. The repo sink takes the lock itself: `write` holds `flock` on `ledger.jsonl` (exclusive to write, shared to read), re-verifies the file, and accepts an entry only if its `seq` and `prev` extend the current head. `append` re-reads the head and re-chains when it loses that race. So any writer, CLI or library, gets one chain. After taking the lock, `write` checks that the file it locked is still the one at `ledger.jsonl` (a `git checkout` replaces the file by rename) and retries if not. Rename doesn't take the lock, so `write` checks again after writing: if the file was replaced mid-write, the entry is reported as not recorded (exit 2), never as success. A write that fails partway is truncated back under the lock. On macOS, `fsync` doesn't force the drive cache (that needs `F_FULLFSYNC`), so an append can still be lost to a power cut. The lock needs `fcntl` (Linux, macOS), so `append` and `show` do too; `init` and `verify` don't. A process killed mid-write still leaves a torn last line, which verify reports and every later append refuses until a person repairs it. `ledger verify` reads without the lock, so run it when no one is appending.

The writer refuses a symlink at `.audit`, `.audit/runs` or the run folder, as verify refuses one at `ledger.jsonl`. `verify` itself still follows a symlinked run folder (M0 unit 1's choice); the `ledger-verify` Action (unit 6) has to refuse one in an untrusted PR. Outside a git repo, `--root` is required.

The CLI does not authenticate `--actor`: anyone who can run `ledger append` can write an entry naming any actor, including a human approval. In M0 the protection is review of the PR that adds the line; signing is not in M0. `ledger show` prints each actor as `kind:id` with non-ASCII escaped, and escapes invisible and combining characters in the text, so one actor can't pass for another on screen.

## Sinks

Every write goes through a sink interface with two calls, `write(entry)` and `seal(run_id, hash, commit)` (`agent_ledger.sink.Sink`). M0 ships the `repo` sink, which is the chain's source of truth: other sinks mirror entries it has accepted. Its `seal(run_id, hash, commit)` appends the run's line to `INDEX.jsonl` (M0 unit 4). An `s3-object-lock` sink and an `external` ledger sink come in M4, configured in policy `audit.sinks`, with no change to any skill.

## Session archive

- Every session that worked on the run is archived when the run closes, and on pause.
- Before writing, the transcript is redacted with built-in secret patterns plus the policy's `redact_patterns`. The entry stores the hash of the raw transcript and of the redacted file, so integrity can be proven later without keeping the raw copy in git.
- Files over the policy's `lfs_over_mb` go to Git LFS.
- If redaction can't be confirmed (the scanner errors, or a pattern match survives), the transcript stays out of git and the entry records where it is.

How it works (M0 unit 3, `agent_ledger.redact`):

- **Patterns.** Built-in patterns cover private keys, vendor keys and tokens (Anthropic, OpenAI, GitHub, GitLab, AWS, Google, Slack, Stripe, npm), JWTs, bearer and Basic auth headers, passwords in URLs, and `password=`/`api_key:`/`token=`-style assignments (a quoted value is taken to its closing quote, so short or spaced passwords go too). They lean wide: over-redacting a transcript is cheap, a miss can't be undone. A replaced secret reads `[REDACTED:<pattern>]`; policy patterns are named `policy_1`, `policy_2` and so on.
- **Policy patterns.** `security.redact_patterns` is read from `~/.agents/policy.yaml` and the repo's `.agents/policy.yaml`, and the two lists are unioned, plus any `--redact-pattern` flags. The standard library has no YAML parser, so the reader takes only `redact_patterns: []` or a block list of single-quoted strings (single quotes keep regex backslashes literal). Any other shape, a second `redact_patterns` anywhere in the file, or an invalid regex stops the command with exit 2 rather than dropping a pattern.
- **Confirmation.** Spans from every pattern are found in the original text and merged, so overlapping patterns leave no fragment. Every line is then checked in each view a reader could decode it to: as written, with escapes collapsed at any depth (`\uXXXX`, and backslashes before quotes and slashes), and for JSON, every string and key decoded, with strings that hold JSON decoded again. A JSON line that still matches, or that no longer parses, is rebuilt from the original line's value with every string and key redacted (recursing into nested JSON), then checked again. A match that survives, a non-JSON line that still matches, or a transcript that isn't UTF-8 means unconfirmed. `append` and `init` redact summary, why, evidence and data (keys included) the same way and refuse anything unconfirmed; a redacted data key then fails the schema, so the entry is refused.
- **The `session` entry.** `data` holds `session_id` (by default the file name without its last suffix), `raw_sha256`, `raw_bytes` and `patterns_sha256` (a hash of the pattern set used), then either `archived: true` with `path` (repo-relative), `redacted_sha256` (of the redacted text before compression, so `gunzip -c <path> | shasum -a 256` checks it) and `redactions` (pattern name to count), or `archived: false` with `location` (the transcript's path, written `~/...` under the home folder so the entry doesn't carry the user name) and `reason`. Archives are gzip with no name or time in the header, so the same text gives the same bytes.
- **Re-adding.** A transcript is archived on pause and again at close. Adding one writes nothing only when the latest entry for that session archived it, with the same raw hash and pattern set, and the archive file exists. Anything else is redone: a changed transcript, new patterns, a missing file, or an earlier refusal. A redo replaces the archive file and appends a new entry, so every version's hashes stay in the chain. The entry is appended before the new file is moved into place, so a failed append leaves the previous archive as it was. A transcript inside the repo gets a warning: the raw copy must not be committed.
- **Not yet.** Routing archives over `lfs_over_mb` to Git LFS needs policy reading for `audit.sessions` (M0 unit 7); until then an archive over 20 MB compressed only prints a warning.

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
- [x] A sealed run's `INDEX.jsonl` entry names a commit that exists on `main` (`tests/test_seal.py`; it needs merge commits, see "Seals and INDEX.jsonl").
- [ ] Swapping the repo sink for a test sink needs only a policy change.
