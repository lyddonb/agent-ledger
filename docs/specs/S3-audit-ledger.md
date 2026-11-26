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

**Spend entries add up.** Each `spend` entry records spend since the previous `spend` entry, never a running total, so the run's spend is the sum of its entries. Parallel agents can then each record their own spend without reading anyone else's. Record one when each unit is verified. `ledger spend <run-id>` adds up `usd_micros` and `tokens` and compares them with the charter's `budget.usd` and `budget.tokens`. It exits 0 under both limits and 1 at or over either one (the `spend` gate, S1 "Budget"). An entry with no valid `usd_micros` (written before the rule above) isn't counted, and the command names its seq. The M0 run wrote running totals before this rule existed, so `ledger spend` overstates it: $138.50 against about $99.67 really spent. Decided by Beau on 2026-11-15 in chat ("approve the additive spend line in S3"); no run was open to record it in.

### Receipt entries

A receipt records that one criterion's probe ran: which criterion, which probe, on which commit, and what came back. It is a `verification` entry whose `data` has a `receipt` object beside `verdict`. A `verification` entry with no `receipt` is unchanged by this section.

| Key | Required | Meaning |
|---|---|---|
| `verdict` | yes | `VERIFIED`, `NOT VERIFIED` or `INCONCLUSIVE`, beside `receipt`, not inside it |
| `receipt.criterion` | yes | The criterion's id, qualified by its spec: `P2e-C8` is C8 in phase P2e. It matches `[A-Za-z][A-Za-z0-9]*-[A-Z](0\|[1-9][0-9]*)`: the number has no leading zero, so `C8` and `C08` can't be two criteria |
| `receipt.spec` | yes | The repo path of the file that states the criterion |
| `receipt.spec_sha256` | yes | That file's SHA-256 when the probe ran, as 64 lowercase hex characters, so the receipt still names the text it was checked against after the spec is edited |
| `receipt.probe` | yes | The command that ran, or for a probe that is not a command, its name |
| `receipt.commit` | yes | The commit the probe ran on: 40 or 64 lowercase hex characters |
| `receipt.exit` | no | The probe's exit code, an integer, or `null` for a probe with none |
| `receipt.dirty` | no | `true` when tracked files differed from `commit` while the probe ran |
| `receipt.not_run` | no | The part of the criterion's probe that did not run |

`spec`, `probe` and `not_run` are non-empty strings; `dirty` is a boolean. Other keys are allowed in `receipt`. A probe that never ran has no receipt: there is no fourth verdict.

`VERIFIED` is a claim about the whole probe on the named commit, so three combinations are refused: `VERIFIED` with an `exit` other than `0` or `null`, `VERIFIED` with `not_run`, and `VERIFIED` with `dirty: true`. Record those as `INCONCLUSIVE`, or `NOT VERIFIED` when the probe failed.

`ledger append` refuses a `verification` entry whose `receipt` breaks these rules; `verify` doesn't check them, because like spend entries they are a convention inside `data`, not part of format v1. The rules are checked again after redaction. When a redaction pattern matches a value the receipt needs, such as the commit, the entry is refused and the error says redaction changed it.

**A receipt is a specific claim, not a proof.** The ledger shows the entry was not edited afterward. It cannot show the probe ran. A receipt names the command and the commit so that anyone can run it again, and the evidence pointer (a path and a hash) is to output that may not be reproducible byte for byte. Have a script write the receipt from the probe's exit code, not the agent from memory.

**The commit is never the one that holds the receipt.** Committing the ledger makes a new commit. A check that a receipt is current compares the tree at `receipt.commit` with the tree at the head, ignoring `.audit/` and the spec file.

`ledger receipts <run-id>` prints the run's receipts as a Markdown table for a pull request body, one row per criterion: criterion, verdict, probe and exit code, commit, the entry's first evidence pointer, and the entry's seq.

- **Order.** Rows are in natural id order: `P2` before `P2e` before `P10`, and `C2` before `C10`.
- **Several receipts for one criterion.** The last one is shown, with a count of the earlier ones.
- **A receipt that breaks the rules** (written by a version before them) isn't shown. The command names its seq, and its criterion when that is readable, on stderr: a line printed straight after a Markdown table would render as one more row. When such a receipt is later than the row shown for its criterion, the row says so, because the newest record for that criterion could not be read.
- **An imported receipt** (`data.imported`) is marked `imported` in the entry cell. It was transcribed from an older record, not written when a probe ran.
- **Text from a receipt is data.** Each piece sits in a code span. A pipe is escaped, and line breaks and invisible or bidi-affecting characters are written as `\uXXXX`, as `ledger show` writes them. Receipt text cannot add a row, a column or markup, or display as something other than what it says.

### Flag entries

A flag records that an agent, mid-task, found a skill, reference or pointer wrong or missing, and what it did instead. A later pass reads the week's flags and proposes the edits. A flag is a `lesson` entry whose `data` has `kind: "flag"`. A `lesson` without it is unchanged by this section.

| Key | Required | Meaning |
|---|---|---|
| `kind` | yes | `"flag"` |
| `target` | yes | The skill, reference or pointer at fault, by name: `bstack:beau-mode`, or a file path and line |
| `did_instead` | yes | What the agent did instead, so the task went on |
| `suggested_fix` | yes | The edit the agent would make to the target |

Each is a non-empty string. Other keys are allowed. `summary` says what was wrong in one line and `why` says how the agent found it. The entry's `evidence` holds the command that showed the fault and its output, one item per line, at least one item. This is the one place S3 allows pasted content in `evidence`: a flag's proof is usually in a session the reader can't open, and each item is still one line with no control characters, so paste the lines that show the fault, not the whole output. Redaction runs on it as on any entry.

`ledger append` refuses a flag that breaks these rules; `verify` doesn't check them, because like spend entries and receipts they are a convention inside `data`, not part of format v1. There is no format bump: format v1 freezes entry keys, not conventions inside `data`. The rules are checked after redaction.

**A flag is never edited.** The ledger is append-only, so a wrong flag stays wrong. Whatever fixes it, a commit, a pull request or a later entry, cites the flag entry's `hash`, and that citation closes it. A flag nobody cites is open.

**Where flags go.** A flag raised inside a governed run is appended to that run. Outside one, flags go into one rolling run per machine and week, in the repo `--root` names, so where flags are kept is that repo's choice. Flags can name private context, so keep them in a private repo.

- **The run.** `<machine>` names the machine and fits the slug rule, `[a-z0-9]+(-[a-z0-9]+)*`: `mac`, `devbox-2`, not a raw hostname. `ledger init flags-<machine>` prefixes the day, in UTC like every run id, so the run is named for the day of the week's first flag.
- **This week's run** is the `flags-<machine>` run whose date is on or after this week's Monday in UTC. If there is none, the first flag of the week starts it with `ledger init`, which needs no charter. If there are two (two inits raced), append to the later one; both verify.
- **It is never sealed.** A rolling run grows across commits and pull requests and stays append-only under `verify --base`, as any merged run does. An unsealed run with no charter verifies.

Writing a flag, from the repo that keeps them, with `ACTOR` set to the S2 actor JSON:

```bash
machine=mac
set -- $(python3 -c 'import datetime as d; t = d.datetime.now(d.timezone.utc).date(); print(t, t - d.timedelta(days=t.weekday()))')
today=$1 monday=$2
run=$(ls .audit/runs 2>/dev/null | grep -E "^[0-9]{4}-[0-9]{2}-[0-9]{2}-flags-$machine\$" | awk -v m="$monday" '$0 >= m' | sort | tail -n 1)
if [ -z "$run" ]; then
  run="$today-flags-$machine"
  ledger init "flags-$machine" --date "$today" --actor "$ACTOR" --why "the first flag this week"
fi
ledger append --run "$run" --actor "$ACTOR" --type lesson \
  --summary "beau-mode names playbooks/review.md, which does not exist" \
  --why "opening the playbook the skill named failed" \
  --evidence '$ cat playbooks/review.md' \
  --evidence 'cat: playbooks/review.md: No such file or directory' \
  --data '{"kind": "flag", "target": "bstack:beau-mode", "did_instead": "used playbooks/opening-a-pr.md", "suggested_fix": "name playbooks/opening-a-pr.md"}'
```

Then commit `.audit/runs/<run>/ledger.jsonl` with the work, as any ledger change. `ledger show <run>` lists the week's flags.

### Who typed a human's entry

An `approval` or `gate-answer` names a human actor, but the human often doesn't type it: an agent relays their words from a chat. `ledger append` (and so `ledger charter approve`) refuses a human-actor `approval` or `gate-answer` unless its `data` says who typed it, in one of three ways:

| Who typed it | `data` | CLI |
|---|---|---|
| An agent, relaying the human | `relayed_by` (the agent's id) and `quote` (the human's words, verbatim), both non-empty | `--data '{"relayed_by": ..., "quote": ...}'`; `--relayed-by` and `--quote` on `charter approve` |
| The human, in their own terminal | `self_typed: true` | `--typed-by-me` |
| An importer, copying an older record | `imported` (see Migration), whose `by` names the importer | `--data '{"imported": ...}'` |

`self_typed` with `relayed_by` is refused, as is `self_typed` set to anything but `true`. A `quote` alone, without `relayed_by` or `self_typed`, doesn't say who typed it, so it is refused. Agent-actor entries need no marker. Like spend entries, this is a convention inside `data` checked at write time; `verify` doesn't check it, so entries written before the rule still verify.

The actor isn't authenticated (see "The ledger CLI"), so an agent can still pass `--typed-by-me`. The rule makes that a stated claim in the entry, which PR review and the session archive can check, rather than a silent default.

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
- **Merges.** A seal names a branch commit, so it survives only a merge commit. A squash or rebase merge drops that commit and verify reports `seal-commit`; protect `main` to allow merge commits only. A process killed while appending to `INDEX.jsonl` leaves a torn last line that every later seal refuses, as with a ledger; delete that partial line (it was never committed), then run `ledger seal` again. Two branches that each seal a run conflict on `INDEX.jsonl`: take the base branch's copy, run `ledger seal <run-id>` again (it indexes a sealed run that has no line), and commit.

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
| `ledger session add <file> --run R --actor A [--session ID] [--redact-pattern P]...` | Redacts, compresses and stores a transcript, then appends a `session` entry with both hashes. Exit 0 archived (or unchanged since the last add), 1 not archived because redaction couldn't be confirmed (a `session` entry still records where the transcript is), 2 usage or read error (built in M0 unit 3) |
| `ledger seal <run-id> --actor A [--why W]` | Writes the `seal` entry (its `prev` is the final hash; it names the HEAD commit) and appends the run to `INDEX.jsonl`. Re-running it indexes a sealed run that has no line (built in M0 unit 4). It warns when no `spend` entry follows the run's last `verification`, since the final unit's spend is then missing; it still seals |
| `ledger spend <run-id>` | Adds up the run's `spend` entries and prints used, limit and what is left for the charter's `budget.usd` and `budget.tokens`, after verifying the run. Exit 0 under both limits, 1 at or over either (or the run fails verify), 2 unreadable (see Spend entries) |
| `ledger receipts <run-id>` | Prints the run's receipts as a Markdown table, one row per criterion with its latest verdict, after verifying the run (see Receipt entries). Exit 0 printed, 1 the run fails verify, 2 unreadable |
| `ledger charter ...` | Charter commands from S1 |
| `ledger import-tsv <file> --actor A --record-actor A [--slug S] [--date D] [--why W] [--redact-pattern P]...` | Copies an old `decisions.tsv` trail into a new run, one imported `decision` entry per record, written whole or not at all (see Migration). Exit 0 imported, 2 not imported |

`--actor` is the S2 actor as JSON. It is required on every write: the CLI never guesses who is acting, so a subagent can't inherit its parent's identity by accident. The run folder is `.audit/runs/<run-id>` under `--root`, which defaults to the nearest folder holding `.git`.

Parallel subagents append through the orchestrating agent, or take a file lock, so a chain never forks. The repo sink takes the lock itself: `write` holds `flock` on `ledger.jsonl` (exclusive to write, shared to read), re-verifies the file, and accepts an entry only if its `seq` and `prev` extend the current head. `append` re-reads the head and re-chains when it loses that race. So any writer, CLI or library, gets one chain. After taking the lock, `write` checks that the file it locked is still the one at `ledger.jsonl` (a `git checkout` replaces the file by rename) and retries if not. Rename doesn't take the lock, so `write` checks again after writing: if the file was replaced mid-write, the entry is reported as not recorded (exit 2), never as success. A write that fails partway is truncated back under the lock. On macOS, `fsync` doesn't force the drive cache (that needs `F_FULLFSYNC`), so an append can still be lost to a power cut. The lock needs `fcntl` (Linux, macOS), so `append` and `show` do too; `init` and `verify` don't. A process killed mid-write still leaves a torn last line, which verify reports and every later append refuses until a person repairs it. `ledger verify` reads without the lock, so run it when no one is appending.

The writer refuses a symlink at `.audit`, `.audit/runs` or the run folder, as verify refuses one at `ledger.jsonl`. `ledger verify RUN_FOLDER` still follows a symlinked run folder (M0 unit 1's choice); `ledger verify` over the repo, which the `ledger-verify` Action (unit 6) runs, refuses any symlink in `.audit`. Outside a git repo, `--root` is required.

The CLI does not authenticate `--actor`: anyone who can run `ledger append` can write an entry naming any actor, including a human approval, though a human's approval or gate answer must say who typed it (see "Who typed a human's entry"). In M0 the protection is review of the PR that adds the line; signing is not in M0. `ledger show` prints each actor as `kind:id` with non-ASCII escaped, and escapes invisible and combining characters in the text, so one actor can't pass for another on screen.

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
- **The `session` entry.** `data` holds `session_id` (by default the file name without its last suffix), `raw_sha256`, `raw_bytes` and `patterns_sha256` (a hash of the pattern set used), then either `archived: true` with `path` (repo-relative), `redacted_sha256` (of the redacted text before compression, so `gunzip -c <path> | shasum -a 256` checks it) and `redactions` (a list of `[pattern name, count]` pairs, sorted by name), or `archived: false` with `location` (the transcript's path, written `~/...` under the home folder so the entry doesn't carry the user name) and `reason`. The `archived: false` entry is written on every refusal: a transcript that can't be confirmed, an archive path that looks like a secret, and an archived entry that `append` refuses. The archive file is then discarded. `redactions` is a list of pairs, not a name-to-count object, because `"url_password":16` reads to the redactor as a `password=` assignment, so the entry would be refused. Entries written before 2026-11-15 hold the object form. Archives are gzip with no name or time in the header, so the same text gives the same bytes.
- **Re-adding.** A transcript is archived on pause and again at close. Adding one writes nothing only when the latest entry for that session archived it, with the same raw hash and pattern set, and the archive file exists. Anything else is redone: a changed transcript, new patterns, a missing file, or an earlier refusal. A redo replaces the archive file and appends a new entry, so every version's hashes stay in the chain. The entry is appended before the new file is moved into place, so a failed append leaves the previous archive as it was. A transcript inside the repo gets a warning: the raw copy must not be committed.
- **Not yet.** Routing archives over `lfs_over_mb` to Git LFS needs policy reading for `audit.sessions` (M0 unit 7); until then an archive over 20 MB compressed only prints a warning.

## CI check

This repo ships a GitHub Action, `ledger-verify`, that runs `ledger verify --base <target branch>` and a secret scan over `.audit/` on every PR. It fails when a chain breaks, a past line changed, or a secret appears. With branch protection that forbids force-pushes to `main`, the merged history is the project-level chain.

Built in M0 unit 6. A pull request controls any workflow that runs from it (`pull_request` runs the merge commit's own workflow files and local actions), so the check to require runs from `pull_request_target`: the workflow and the Action come from the base branch, and the pull request's merge commit is checked out as data and never executed. Add it to a project like this:

```yaml
on:
  push: {branches: [main]}
  pull_request_target:
  merge_group:
permissions: {contents: read}
jobs:
  ledger-verify:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ github.event_name == 'pull_request_target' && format('refs/pull/{0}/merge', github.event.pull_request.number) || github.sha }}
          path: checked
          fetch-depth: 0              # seals and --base need the full history
          persist-credentials: false
      - uses: lyddonb/agent-ledger@<commit or tag>
        with: {root: checked}
```

Never add a step that runs code from `checked` (a build, a test, a script): under `pull_request_target` that would run the pull request's code with the base branch's trust. Then require `ledger-verify` on the default branch.

- **Base.** For a pull request, the merge commit's first parent: the base branch as GitHub merged it, so a base branch that moved on since can't fail an honest change. The Action checks that parent is on `origin/<base>` and refuses a checkout that isn't a merge commit. In a GitHub stack, `github.base_ref` names the stack's base while the merge commit sits on the parent pull request's branch, so the parent isn't on `origin/<base>`; then the base is their merge base, the newest commit of `origin/<base>` the pull request contains, and the Action prints a notice saying so. A parent with no history in common with `origin/<base>` still fails (`ci/pr_base.py`). For a merge queue, the queue's base; for a push, the commit before it. A push that creates a branch has no base: verify still checks every chain and seal, with a warning. Any other event with no base fails; pass the `base` input.
- **What runs.** `ledger verify --base` (chains, index, seals, append-only) and `ledger scan --base` (secrets in the `.audit` files the change touches). Both run, and either failing fails the job.
- **`ledger scan`.** Every line of every file under `.audit` is checked in each decoded view the redactor confirms with. The patterns are the built-ins plus the project's `.agents/policy.yaml` both at the base and in the change, so a change can't remove a pattern and leak what it matched in the same pull request. Only placeholders naming a real pattern count as redacted, so a token can't hide inside a fake `[REDACTED:...]`. A finding names the file, line and pattern, never the text. A file that can't be read, is binary, is a symlink or submodule, or is too large (512 MB, 5 million lines or a 64 MB line, archives decompressed as a capped stream) is a finding, since it can't be shown clean. One file may take 10 minutes and the whole scan 25; past that the rest is a finding, so a hostile change fails the check instead of holding a runner. Without `--base` it scans every file under `.audit` on disk.
- **The runner.** The Action runs the CLI from its own copy with `python -I`, so even the checked repo's own `agent_ledger` package or `sitecustomize.py` on `PYTHONPATH` can't replace it. Every workflow expression reaches the shell through `env`, and the job needs only `contents: read`.
- **This repo.** `ledger-verify` runs the base branch's Action as above. A second job, `ledger-verify-self`, runs a pull request's own Action under `pull_request` so changes to the Action are exercised before they merge; it can be edited by the pull request it checks, so it is never the required check. A pull request that adds or changes the workflow is checked by the base branch's version until it merges.

## Migration

Existing `decisions.tsv` trails stay readable. `ledger import-tsv` converts one into a run, marked as imported, with chaining starting at import.

An imported entry is marked by `data.imported`, an object with `source` (where the record was, such as `bootstrap-notes.md#3`), `source_ts` (the time the record gives, in `ts` form) and `by` (the id of the actor who imported it). Its `ts` is when it was chained, never the earlier time, so `ts` always means "written to the ledger at". Its `actor` is the record's original actor, which `data.imported.by` qualifies: an imported human approval was typed in by the importer, not signed by the human. `ledger show` tags these entries `[imported]`. This is a convention inside `data`, not a format change.

### `ledger import-tsv`

```text
ledger import-tsv <file> --actor A --record-actor A [--slug S] [--date D] [--why W] [--redact-pattern P]... [--root R]
```

It reads one tab-separated file and writes one new run. It never adds to a ledger that exists.

- **The run.** The run id is `<date>-<slug>`. The date is the day of the import, not of the records, and defaults to today in UTC. The slug defaults to `imported-decisions`. A run that already has a ledger is refused. As with `ledger init`, a run folder that holds only a charter is used.
- **The first entry** is the importer's own `state` entry, as `ledger init` writes it, not an imported one. Its `data` is `{"state": "started", "source": {"path", "sha256", "rows"}}`: the file, the SHA-256 of its bytes, and how many records it held.
- **The records.** Each record becomes one `decision` entry, in file order, marked `data.imported`. `source` is `<path>#<line>`, where the line is the record's line number in the file and the header is line 1. `by` is the id of `--actor`.
- **The path** in `source` is the file's path inside the repo, or only its name when the file is outside the repo. An entry never carries a home folder.
- **Two actors.** `--actor` is who runs the import. `--record-actor` is who wrote the records, and it is the imported entries' `actor`. A `decisions.tsv` has no actor column, and the CLI never guesses who acted, so the flag is required. Where nobody knows a part of the identity, such as the session or the model, write `unknown` there. One file has one record actor. A trail written by several goes in as one file per actor.

Columns are found by the header's names, in any order:

| Column | Becomes | Required |
|---|---|---|
| `ts` or `when` | `data.imported.source_ts` | yes |
| `decision` or `what` | `summary` | yes |
| `why` | `why` | yes |
| `evidence` | one `evidence` pointer holding the whole cell. An empty cell gives no pointer | no |
| `result` | `data.result`, left out when the cell is empty | no |
| `phase` | `data.phase`, left out when the cell is empty | no |

show-me-your-work's `log.sh` wrote `ts phase decision why evidence result`. Trails kept by hand use `when what why evidence result`. A header that names any other column, names one field twice, or lacks a required one is refused. The message gives the cell's position, not its text: when the header is missing, line 1 is a record.

- **Lines and cells.** The file is UTF-8, and a byte order mark is dropped. Lines end in LF or CRLF, and cells are split on tabs. Nothing is quoted, so a `"` is ordinary text. `log.sh` put a `'` before a cell that starts with `=`, `+`, `-` or `@`, so a spreadsheet wouldn't run it as a formula. When the header is exactly `log.sh`'s six columns in its order, the import takes that `'` off. In any other file a leading `'` is the writer's own and stays. A later line that repeats the header exactly is skipped: `log.sh` could write its header again on a network mount.
- **The evidence cell is not split.** Old trails separate pointers with semicolons, commas or prose, and no rule splits them all correctly.
- **The time.** The cell is a date (`2026-10-24`), or a date and time to the minute or the second (`2026-10-26T10:34`, `2026-11-08T19:51:55`), with or without a final `Z`. A part the record leaves out is zero, so `2026-10-24` gives a `source_ts` of `2026-10-24T00:00:00Z`. The digits are kept as written. A record names no zone, so it is read as UTC, which is what `log.sh` wrote. When the cell is not already in `ts` form, `data.when` keeps it as written, so a reader can tell a date from midnight. A time with an offset, or any other text, is a malformed record.
- **A malformed record stops the import.** A record with the wrong number of cells, a time that can't be read, an empty summary or why, or text that format v1 refuses (a control character or a Unicode line break in `summary`, `why` or `evidence`) is a problem, and so is an empty line. The command lists every problem by line number, writes nothing, and exits 2. The ledger file is written in one step, so it appears whole or not at all. A ledger only grows, so half an imported trail could not be taken back after a merge. Fix a copy of the file and import the copy.
- **Redaction.** Every cell goes through the redaction and the write-time checks that `ledger append` uses, with the same patterns (built-in, policy and `--redact-pattern`). A redacted cell is imported with its `[REDACTED:<pattern>]` placeholder, and the command prints the line number and the pattern name, never the text. A record whose redaction can't be confirmed is a problem like any other, so nothing is written. The import is also refused when a pattern matches what the run needs to name its source: the file's path or `sha256` in the first entry, or a record's `data.imported`. A redacted hash could not stop a second import, and a redacted path would cut the line number from `source`.
- **A file is imported once.** The import is refused when the first entry of any run in the repo records the same `sha256`. A copy with any byte changed is a different file. A run that fails verify isn't consulted.

The run is then an ordinary open run: commit it, and `ledger seal` it when nothing more will be added.

## Acceptance criteria

- [ ] Editing any past line of a ledger makes `ledger verify` fail and names the line.
- [ ] Deleting a line or reordering two lines fails verification.
- [ ] A planted fake secret in a transcript is redacted before it reaches git; the CI scan finds nothing.
- [x] Two subagents appending at once produce one valid chain (`tests/test_sink.py` `ConcurrentAppendTest`, M0 unit 2).
- [x] A sealed run's `INDEX.jsonl` entry names a commit that exists on `main` (`tests/test_seal.py`; it needs merge commits, see "Seals and INDEX.jsonl").
- [ ] Swapping the repo sink for a test sink needs only a policy change.
