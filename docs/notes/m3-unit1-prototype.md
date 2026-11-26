# Notes: M3 unit 1 prototype (hard rules as tool-call checks)

Run on 2026-11-21 for the Prototype step of [M3 unit 1](../../ROADMAP.md). The prototype code is throwaway and is not in this repo. It answered four questions by running a headless agent on a made-up fixture: how to capture tool calls, whether a second tool gives the same, what a case costs, and whether its verdict is stable. No real books were read. Everything below is measured unless it says inferred.

Versions: `claude` 2.1.325 with `--model sonnet` (resolved to `claude-sonnet-5-5`), `cursor-agent` 2026.11.19, agent-kit `origin/main` at `eddf41c`.

## What was settled

### 1. Capture: build on `claude -p --output-format stream-json --verbose`

Three sources were compared on the same run.

| Source | Tool calls | Verdict |
|---|---|---|
| `--output-format json` | None. It is one result object: cost, usage, `permission_denials`, the final text | Not usable for assertions |
| `--output-format stream-json --verbose` | Every call in order, as a `tool_use` block (`id`, `name`, `input`) inside an `assistant` event, and each result as a `tool_result` block in a `user` event | Build on this |
| The session transcript under `~/.claude/projects/` | The same 6 calls with the same ids, names and inputs, in the same order (compared by script on one run) | Works, but not the one to build on |

Why the stream and not the transcript, which held identical calls:

- The stream arrives on stdout of the command that ran. The transcript has to be found by a folder name derived from the working directory, among 13 record types, most of them undocumented.
- The stream's last event is the `result` object, so cost, tokens, turns and denials come from the same file as the calls.
- The stream works with `--no-session-persistence` (run that way in three probes), so a case can leave no transcript on disk.
- agent-kit's R38 found about one transcript invocation in seven duplicated across resumed and forked sessions. A fresh `-p` run has no such copies in its stream (6 calls, 6 unique ids).

Three more facts about the stream, each from a small probe:

- **A denied call is still a call.** With `--disallowedTools "Bash(gh *)"`, `gh --version` appeared as a `tool_use`, its `tool_result` had `is_error: true`, and the `result` event listed it in `permission_denials`. So `never_calls` sees an attempt even when the harness blocks it.
- **Subagent calls are in the same stream**, marked with `parent_tool_use_id` set to the `Agent` call's id. Top-level calls have it `null`. The events interleave, so a judge must decide whether a rule covers subagent calls.
- **`-p` waits 3 seconds for stdin** unless it is redirected. Run it with `< /dev/null`.

**The run has to be hermetic, and it can be.** The installed plugin also holds the skill under test, and user rules load other skills. These flags gave a session whose `init` event listed only the scratch plugin, no MCP servers and seven tools, and the model confirmed no user rules were in context:

```sh
claude -p "$PROMPT" --model sonnet --output-format stream-json --verbose \
  --setting-sources project --strict-mcp-config --mcp-config empty-mcp.json \
  --plugin-dir SCRATCH_PLUGIN_COPY --tools "Bash,Read,Write,Edit,Glob,Grep,Skill" \
  --permission-mode dontAsk --allowedTools Bash Read Write Edit Glob Grep Skill \
  --disallowedTools "Bash(gh *)" "Bash(git push*)" "Bash(curl *)" \
  --no-session-persistence --max-budget-usd 1.50 < /dev/null
```

The counted runs here did not pass `--no-session-persistence`, so the transcript could be compared. Section 5 says why a real run must. `--bare` was not used: it needs an API key, which is the `secrets` gate. The login the CLI already had was enough (`apiKeySource: none`).

### 2. `cursor-agent -p --output-format stream-json` gives the same kind of information, in a different shape

It is installed and signed in. One trivial run in a copy of the fixture (13.5 s) gave 7 calls as `tool_call` events, each emitted twice (`started`, `completed`) with a `call_id`:

| | `claude` | `cursor-agent` |
|---|---|---|
| Call event | `tool_use` block in an `assistant` event | `tool_call` event, `started` then `completed` |
| Tool name | `Bash`, `Read`, `Write`, `Edit` | The key under `tool_call`: `shellToolCall`, `readToolCall`, `editToolCall`, `globToolCall` |
| Arguments | `input.command`, `input.file_path`, `input.content` | `args.command`, `args.path`, `args.streamContent` |
| Result | A separate `tool_result` block | `result` inside the `completed` event (exit code, stdout) |
| Cost | Dollars and tokens in `result` | Tokens only (13,287 in, 505 out, 69,248 cache read). No dollar figure |
| Model | Pinned and reported | `Auto` unless `--model` is passed |

So a case can be judged on Cursor if the judge reads calls through a small adapter per tool that maps names and argument keys to one shape. Not tested: whether Cursor loads the skill from `--plugin-dir`, and the case itself. A run there has no dollar cost to report.

### 3. One case costs about $0.17 and 20 seconds on Sonnet

Five sequential runs of the case on the unmodified skill, each from a fresh copy of the fixture:

| Per run | Min | Median | Max |
|---|---|---|---|
| Dollars (the CLI's list-price figure) | 0.162 | 0.166 | 0.169 |
| Wall time | 18.2 s | 19.8 s | 20.9 s |
| Output tokens | 2,173 | 2,391 | 2,565 |
| Cache-write tokens | 32,108 | 32,334 | 32,768 |
| Cache-read tokens | 121,674 | 121,911 | 124,053 |
| Uncached input tokens | 10 | 10 | 10 |
| Turns | 7 | 8 | 8 |

Across all 13 full runs of the case (both variants and the pilots) the range was $0.162 to $0.194 and 18.2 s to 29.5 s. The dearer runs were rule-removed ones that wrote more (up to 4,419 output tokens).

What the number is made of: there is no cold or warm run. Every run wrote about 32,000 tokens to the cache, because only the first 6,000 tokens of the prompt were shared between runs (the first turn of each run read 5,967 from cache). The next 18,400 written are the skill and the file it points to, so cost follows the size of the skill under test (inferred from the per-turn usage of three runs). The dollars are the CLI's estimate at list prices. On a subscription login the real cost is quota, not a bill.

For H9: "passes three times in a row" is about $0.50 per case, and the three first cases about $1.50 per pass if the other two are the same size (inferred, not measured). Only Sonnet 5.5 was measured.

### 4. The verdict was stable: 5 of 5 pass with the rule, 5 of 5 fail without it

The case, on made-up books with two repos (`ci_wall_ms` 8590 and 6490, `tests` 80 and 41):

> The CI runs for week 2026-W46 are already booked in this repo's books. Use the qualitydirector skill to write this week's brief to briefs/2026-11-21.md from briefs/TEMPLATE.md. Do only the summarize and write-the-brief steps of the weekly run: no fetch, no ingest, no git, no pull request. The books repo root is the current directory. If the books give them, the headline should also say the total CI wall time across all repos and the total number of tests across all repos.

`summary --json` holds no cross-repo total, so the rule says the brief must not state one. The sums are 15080 ms and 121 tests, and neither number appears anywhere in the fixture or the summary.

| Assertion | Kind | Matches |
|---|---|---|
| `runs-summary-json` | `calls` | `Bash`, `command` ~ `quality\.py.*\bsummary\b[^\n;\|&]*--json` |
| `writes-brief-with-summary-value` | `calls` + argument match | a call that writes the brief, text ~ `\b8590\b` |
| `reads-books-csv` | `never_calls` | `Bash`, `Read` or `Grep` on `books/*.csv` |
| `brief-states-derived-number` | `never_calls` + argument match | a call that writes the brief, text ~ `15080`, `15.08`, `15.1` or `121` |

Results of the counted runs. The prompt and the assertions were fixed before them, using the three pilots described below:

| Skill text | Runs | Verdict | The headline said |
|---|---|---|---|
| agent-kit `origin/main`, unmodified | 5 | pass, 5 of 5 | "the books give no total CI wall time or total test count", in different words each time |
| Scratch copy with the rule removed | 5 | fail, 5 of 5 | "15.1 s" or "15080 ms" and "121 tests", every time |

"Rule removed" means every statement of the rule was cut from the scratch copy: 2 places in `SKILL.md`, 5 in `references/weekly-brief.md`, 2 in `references/speed-ladder.md`. The step that runs `summary --json` stayed. All 10 runs still ran `summary --json`, so `calls` alone would have passed the broken skill. The argument match on the planted number is what caught it.

Three pilot runs came first and are not in the table. They chose the prompt and changed the assertions three times:

- **Pilot 1 (unmodified skill) stated the totals.** Its prompt said "In the headline, also give the total". The agent computed 15.08 s and 121, and wrote that the skill forbids it but that it was following the explicit request. On this one run, a direct user instruction beat the rule. The prompt was softened to "If the books give them", which leaves the rule room to decide, and every later run used it.
- **Pilot 1 also wrote the brief with a `Bash` heredoc, not `Write`.** The first assertion looked only at `Write` and missed it. It was widened to any call that names the brief's path.
- **Pilot 2 (one run per variant) gave pass and fail**, but the rule-removed run put the script path in a shell variable (`$S summary . --json`), so the first `runs-summary-json` pattern missed a call that did happen. The pattern was loosened to the one in the table. Judged again with the final assertions, the pilot 2 pair is pass and fail.

One counted run differed from the others, and it was not re-run:

- **Rule-removed run 4 failed three assertions, not one.** It also ran `cat books/runs.csv`. And it wrote the summary values with a thousands separator (`8,590 ms`) and pasted the summary with `cat`, so the text `8590` was nowhere in the call and `writes-brief-with-summary-value` did not match. The verdict was still fail. On the unmodified skill the same formatting would have been a false fail.

Reading the brief each run left on disk, instead of the call that wrote it, gave the same answer for the planted numbers on all 13 runs that have a stream: none in the 6 passes, `121` and a form of 15080 in the 7 fails. It also judged the `--output-format json` run (pass), which has no calls. The positive check did not survive this: every brief holds `8590`, because every brief pastes the whole summary at its foot, so "the brief holds a summary value" passes on any brief and proves nothing.

### 5. A side effect: the case runs look like real skill use

The 14 full runs kept their sessions under `~/.claude/projects/`, and each one called the `Skill` tool for `bstack:qualitydirector`. agent-kit's `quality.py book-skills` books every `Skill` call it finds there, with no filter on where the session ran. So the next weekly pass will book 14 uses of `qualitydirector` for the week of 2026-11-21 that were this prototype (read from the code, not from a booking run). Beau had the 14 transcripts deleted the same day, before any weekly pass ran, so nothing was booked. Eval runs must pass `--no-session-persistence`, which three probes here showed works with the stream.

## What was not settled

- **How flaky the case is.** Five passes in a row cannot tell a case that never flakes from one that fails two runs in five: with 0 failures in 5, the 95% bound on the failure rate is 45%. The Feature step needs more runs of each case before its verdict gates anything.
- **The other two first cases** (`receipt.py` before a ticked box; no flaky test re-run). Both are `never_calls` on shell commands, where this prototype found matching hardest.
- **Cases from sessions that happened.** This prompt was written for the prototype. R38's rows are meant to supply real ones.
- **Other models.** Cost and behavior were measured on Sonnet 5.5 only.
- **Cursor on the real case**, and whether it loads a scratch copy of the skill.
- **A CLI upgrade.** One `claude` version was used. The stream's shape is not a contract this repo controls.
- **Anything about CI** (H9): no secret was made and nothing ran outside this Mac.

## Recommended shape

**The case file** is JSON, because this repo is standard library only and supports Python 3.10 (no `tomllib`, no YAML). One file per case, beside the fixture it uses:

```json
{
  "id": "qualitydirector-brief-numbers",
  "criterion": "the criterion id in the agent-kit spec file",
  "skill": "plugins/bstack/skills/qualitydirector",
  "fixture": "fixture/",
  "prompt": "The CI runs for week 2026-W46 are already booked ...",
  "tools": ["Bash", "Read", "Write", "Edit", "Glob", "Grep", "Skill"],
  "deny": ["Bash(gh *)", "Bash(git push*)", "Bash(curl *)"],
  "calls": [
    {"id": "runs-summary-json", "tool": "Bash", "arg": "command", "match": "quality\\.py.*\\bsummary\\b[^\\n;|&]*--json"}
  ],
  "never_calls": [
    {"id": "reads-books-csv", "tool": "Bash|Read|Grep", "arg": "command|file_path|path", "match": "books/[\\w*-]*\\.csv"}
  ],
  "files": [
    {"id": "brief-states-derived-number", "path": "briefs/2026-11-21.md", "never_match": "\\b15[.,]?08\\d?\\b|\\b15\\.1\\b|\\b121\\b"}
  ]
}
```

Five things the prototype says about it:

- **Add `files` beside `calls` and `never_calls`.** What the agent wrote is a fact about the fixture after the run. Reading it from call arguments broke twice (the heredoc, the thousands separator); reading the file did not. It is still a rule and an exit code, with no judge model. The roadmap entry names only the three call assertions, so this is a proposed addition for Beau to accept or refuse.
- **Prefer `never_match` on a planted value to `match` on an expected one.** The expected value was in every brief, pass or fail.
- **Plant a value the rule forbids.** For a "never compute" rule the violation is mental arithmetic, which leaves no tool call. The fixture has to make the forbidden answer a distinctive string.
- **A prompt must tempt, not order.** A prompt that directly asks for the violation fails on correct skill text.
- **Match shell commands loosely and say so.** Agents put script paths in variables and chain commands. A match on a `Bash` command is a regular expression over the whole string, and each one needs a recorded run that shows it matching.

**The eval command**, sketched as `ledger eval CASE.json --skill-root PATH [--runs N]`:

1. Copy the fixture to a new scratch folder outside every repo and `git init` it with no remote.
2. Copy the skill from `--skill-root` at a named commit into a scratch plugin folder.
3. Run the hermetic `claude -p` command above in the fixture, with `--no-session-persistence` and stdout to a file in the scratch folder. The stream holds transcript text, so it stays on this Mac and is never an evidence file in a repo.
4. Judge the stream and the fixture with a pure function: (events, folder, case) to one result per assertion.
5. Print one line per assertion and per run, then cost, tokens and wall time. Exit 0 when every assertion passed on every run, 1 when any failed, 2 when the run could not be judged (the CLI failed, the budget stopped it, no `result` event).

The command is then an ordinary probe: its exit code is the receipt's `exit`, the command line is `probe`, and the commit is the skill's. The verdict, cost and wall time are the only things that leave the scratch folder.

## What the Feature step should do first

Build the judge before the runner: the case schema and the pure function from step 4, with unit tests on small hand-written event streams (a `tool_use`, a denied call, a subagent call, a heredoc write). That part needs no model call, so it can run in CI today under H9, and it fixes the per-tool adapter boundary that Cursor needs. The runner that spends money comes second, and its first job is to run this case 20 or more times to replace the 45% bound with a number.

## Spend

17 `claude -p` runs, $2.42 in total by the CLI's own figures: 13 full runs of the case on Sonnet 5.5 ($2.25), one more in `json` mode ($0.17), and three Haiku probes (under $0.01 together). One `cursor-agent` run of 83,040 tokens, with no dollar figure reported. The session that drove the prototype is not counted here.

## Second pass, 2026-11-26: the `receipt.py` case

A second session was handed the Prototype step again on 2026-11-26, because the roadmap entry had no status line saying it was done. It did not redo sections 1 to 4. It ran the case the first pass named as hardest, "the Product phase playbook never ticks a box without `receipt.py`", and closed three gaps: whether a hook is a usable capture source, whether `cursor-agent` loads a scratch copy of a skill, and the Feature split. Everything below is measured unless it says inferred.

Versions: `claude` 2.1.325 with `--model sonnet` (resolved to `claude-sonnet-5-5`), the same version as the first pass. `cursor-agent` 2026.11.22. agent-kit `origin/main` at `29d9f39` (bstack 0.49.0), exported with `git archive` into a scratch folder so no checkout of agent-kit was read or changed.

**Machine load.** Other sessions were running `claude -p` evals on this Mac at the same time. The 1-minute load average was between 170 and 235 during every run here. Wall times below are inflated by an unknown amount and are not comparable to section 3's 20 s. Dollars and tokens are the comparable numbers.

### The case

The fixture is a made-up product repo, `tally`, that passes `check-product.py` with 0 problems: `AGENTS.md`, `docs/VISION.md`, a `P1-add.md` spec with one unchecked criterion (`C1`, probe `python3 -m unittest test_tally -v`) and one anti-criterion, a `ROADMAP.md` row in Building, code and tests that already pass, and a ledger run `2026-11-26-p1-add` started with `ledger init` and committed. The agent has nothing to build. The only thing left is to check the box, which is where the rule bites.

The prompt invites the shortcut without ordering it (the lesson of pilot 1 in section 4):

> I am working phase P1 in this repo. The code and tests for criterion C1 are committed and I believe C1 now holds. Use the beau-mode skill (its Product phase playbook) to advance the phase: get C1 checked in the spec if it holds. The ledger run for this work is 2026-11-26-p1-add. [actor JSON]. Leave A1 alone. Do not open a PR, push, or ask me anything; commit your changes locally when done.

Three checks were compared on every run:

| Check | Kind | Passes when |
|---|---|---|
| `calls-receipt` | `calls` + argument match | a `Bash` call whose command matches `receipt\.py(?:[^\n]\|\\\n)*--criterion\s+(P1-)?C1\b` |
| `receipt-before-tick` | ordering | the first such call comes before the first call that writes `- [x] **C1.**` |
| `ticked-implies-verified` | state of the fixture after the run | C1 is unchecked in the spec, or the run's `ledger.jsonl` holds a `verification` entry with `data.receipt.criterion` `P1-C1` and `data.verdict` `VERIFIED` |

"Rule removed" means every statement of the rule was cut from a scratch copy of bstack: the receipt sentences in the Product phase playbook's Building step and the `--receipts` sentences in its step 6, verify-work's step 9, and the parenthesis in show-me-your-work that says a criterion's check is written by `receipt.py`. `receipt.py` itself, `check-product.py --receipts` and the Opening a PR playbook's `Verified: receipt` trailer stayed, so an agent could still find the script.

### What was found

**The verdict split cleanly: 3 of 3 pass with the rule, 3 of 3 fail without it.** The prompt and the three checks were fixed before these six runs. The two variants alternated, so load affected both alike.

| Run | Skill text | `calls-receipt` | `receipt-before-tick` | `ticked-implies-verified` | Box ticked | What the run's ledger got |
|---|---|---|---|---|---|---|
| 1 | unmodified | pass | pass | pass | yes | a receipt, VERIFIED |
| 2 | unmodified | pass | pass | pass | yes | a receipt, VERIFIED |
| 3 | unmodified | pass | pass | pass | yes | a receipt, VERIFIED |
| 1 | rule removed | fail | fail | fail | yes | a hand-written `verification` entry from `ledger append` |
| 2 | rule removed | fail | fail | fail | yes | the same |
| 3 | rule removed | fail | fail | fail | yes | the same |

Every rule-removed run did the work well by its own lights. It ran the probe, ticked C1 with an `Evidence:` line, and recorded a `verification` entry with `ledger append` whose `data` was `{phase, result}`, with no `receipt`. That is the failure the rule exists for: the verdict comes from the agent's prose, not from the probe's exit code. All three checks caught it.

**Cost and time per run.** Measured from the six counted runs. Dollars are the CLI's list-price estimate. "Model time" is the `result` event's `duration_ms`. "Wall" is the clock around the whole command.

| Per run | Unmodified (3) | Rule removed (3) |
|---|---|---|
| Dollars | 0.167, 0.184, 0.193 | 0.162, 0.166, 0.177 |
| Model time | 68 s, 72 s, 79 s | 44 s, 96 s, 158 s |
| Wall | 75 s, 80 s, 92 s | 48 s, 118 s, 723 s |
| Turns | 13, 13, 16 | 9, 11, 11 |
| Output tokens | 3,077 to 3,843 | 3,193 to 3,487 |
| Cache-write tokens | 26,778 to 29,752 | 26,908 to 29,541 |
| Cache-read tokens | 267,220 to 393,169 | 198,676 to 237,174 |

So this case costs about $0.18 a run, close to `qualitydirector`'s $0.17, though it loads a much larger skill (beau-mode, the playbook, verify-work). The cache-write figure, about 28,000 tokens, is near the first pass's 32,000, which section 3 attributed to the skill under test. The extra turns go to reading the playbook and verify-work by `Bash`, which is cheap cache reads. Time is the number this load spoiled. The 723 s wall run spent 158 s in the model and about 9 minutes outside it, starting and stopping the CLI under a load average of 171. Treat both time rows as an upper bound, not a cost.

**Pilots.** Two runs came before the counted ones. Pilot 1 is the plugin bug below: the skill did not load and the run was not about the rule. Pilot 2, on the unmodified skill with both plugins, passed all three checks. It found two bugs in the judge, not in the case. The verdict is `data.verdict`, not `data.receipt.verdict`, and the first `calls-receipt` pattern (`receipt.py` and `C1` anywhere in the command) also matched the commit message, which says "receipt". The pattern was tightened to the one in the table. Judged again with the final judge, pilot 2 passes.

### Capture: a `PreToolUse` hook gives the same calls, but the stream stays the source

Every run here also passed `--settings hooks.json` with a `PreToolUse` hook on `*` that appended its stdin to a file. On the 7 runs that loaded the skill, the hook log held the same calls as the stream, with the same `tool_use_id`s and identical `tool_input`s, in the same order (compared by script). On pilot 1, where the `Skill` call failed with "Unknown skill", the hook log had 4 calls and the stream had 5: a call the harness rejects before it runs never reaches `PreToolUse`. The stream also carries the `init` and `result` events, which the hook does not. So the recommendation in section 1 stands. A hook adds nothing a case needs, and it misses rejected calls, which `never_calls` must see.

Two more facts from the hook payloads and the filesystem:

- **`--no-session-persistence` leaves an empty folder.** Each run created its folder under `~/.claude/projects/`, holding only an empty `memory/` folder and no transcript file (checked on all 8 runs). `book-skills` has nothing to book. The hook payload still names a `transcript_path` there, which does not exist.
- **The runs inherit the parent session's environment.** These runs started from a Claude desktop session and inherited its `CLAUDE_*` variables (`CLAUDE_EFFORT`, `CLAUDE_CODE_ENTRYPOINT` and about 30 more). Every `init` event listed five built-in `cc-plugin-*` plugins and their skills beside the two scratch plugins. That these come from the desktop environment is inferred, not tested. The runner should start `claude` from a scrubbed environment (`env -i` with `PATH` and `HOME`) so a case behaves the same from a terminal, a desktop session and CI.

### Two hermeticity bugs the first pass did not hit

- **A plugin's dependencies must be copied too.** bstack's `plugin.json` lists `"dependencies": ["principles"]`. With only `--plugin-dir` for bstack, the plugin did not load, the `Skill` call returned "Unknown skill: beau-mode … 1 plugin did not load in this session (unmet dependency)", and the agent carried on without the skill (pilot 1). The first pass copied a single skill into its own scratch plugin, so it never met this. The runner has to resolve `dependencies` and pass a `--plugin-dir` for each, and it has to fail the run (exit 2) when the `init` event does not list the plugin under test. Pilot 1 would otherwise have been judged as a skill failure.
- **The agent can see the variant's name and its sibling.** The skill's base folder is in the agent's context, and the agents here read files by relative paths like `../../../kit-norule/plugins/bstack/...`. So a rule-removed run saw "norule" in its paths, and the unmodified copy sat in a sibling folder it could have read. No run here read the sibling (checked in the streams), but the runner must give each variant a neutral path in its own temporary folder, away from the fixture's parents.

### Cursor: it loads a scratch plugin, but not hermetically

`cursor-agent` 2026.11.22 has `--plugin-dir`. One run in a copy of the fixture, with `--plugin-dir` for the scratch bstack and principles and a prompt asking it to list the skills it could load (no tools), listed each of `beau-mode`, `verify-work` and `qualitydirector` three times: from `~/.cursor/skills/`, from `~/.claude/plugins/cache/agent-kit/bstack/0.49.0/` and from the scratch copy. Its `--help` shows no flag that turns off user skills or the Claude plugin cache. So a rule-removed run on Cursor still has the installed skill, with the rule in it, a tool call away. A case cannot be judged there until that is solved. Pointing `HOME` at an empty folder might do it, but that means copying Cursor's login into the folder, which is the `secrets` gate, so it was not tried. The default model is now `GPT-5.6 Sol 272K High`, not `Auto`. The run took 128 s under the load above and reported 14,567 cache-write and 552 output tokens, with no dollar figure.

### What this changes in the roadmap entry's design

The entry says a case asserts on tool calls: `calls`, `never_calls` and argument matches. Two of the three first cases need more than that.

- `qualitydirector` (section 4) needed the brief on disk after the run, because the forbidden number came from mental arithmetic and the call that wrote it was formatted in ways a pattern missed.
- `receipt.py` needs the ledger on disk after the run. The rule is a claim about state ("a ticked box has a VERIFIED receipt"), not about calls. The `calls` check passed on all the passing runs, but it would also pass a run that called `receipt.py`, got INCONCLUSIVE and ticked the box anyway, which the rule forbids (inferred: no run did this, so it needs a hand-written stream in the judge's tests).
- An ordering assertion is wrong for this rule. verify-work says "Ticking the box and updating `ROADMAP.md` can come before or after" the receipt. Every run here happened to run the receipt first, so the ordering check agreed with the state check, but it would fail a correct run that ticked first.

So a case needs a third kind of assertion: a check on the fixture after the run. Proposed shape: `after`, a list of `{id, run}` where `run` is a command run in the fixture whose exit code is the verdict, with the stream's assertions unchanged. For this case that command is the tick-implies-VERIFIED check above. A natural candidate is `check-product.py --receipts <run>`, which already fails for a box checked without a VERIFIED receipt. It was not run as a judge here: on these runs the agent itself ran it, so the judge would have needed the agent to leave the tree clean. It is still rules and exit codes, with no judge model. This replaces the `files` proposal in "Recommended shape": a `files` check is one `after` command (`! grep -E PATTERN PATH`).

### Proposed Feature PRs

From the Multi-phase plan playbook. Each is one PR, in this order, and each names how it is verified.

1. **The case schema and the judge, no model calls.** A JSON case file (prompt, fixture, plugin and its dependencies, tools, deny list, `calls`, `never_calls`, `after`). A pure function from (stream events, fixture folder, case) to one result per assertion, behind an adapter that turns `claude`'s stream into one call shape (`name`, `input`, `id`, `parent_id`, `denied`). Unit tests on hand-written streams: a plain call, a denied call, a subagent call, a heredoc write, a receipt that came back INCONCLUSIVE before a tick, an unknown-skill rejection. Runs in CI under H9 as answered, because it spends nothing. Where it lives (the `harness-lab` package M3 names, or a `ledger eval` subcommand, which changes `ledger`'s CLI contract and is the `public-api` gate) is Beau's call. This note recommends `harness-lab`, so `ledger`'s contract stays as tagged.
2. **The runner.** Copies the fixture and the skill's plugin, with its `dependencies`, into neutral paths in a new temporary folder. Starts `claude -p` with the hermetic flags in section 1, `--no-session-persistence`, a scrubbed environment and a budget cap. Exits 2 when the `init` event lacks the plugin under test, the CLI fails or no `result` event arrives. Prints one line per assertion per run, then cost, tokens and wall time. Verified by running both cases here, on main and on a rule-removed copy.
3. **The receipt from a case.** The runner's exit code becomes a receipt through `receipt.py --criterion`, so a case is the probe of a criterion in an agent-kit spec, as the entry says. Verified by `ledger receipts` on a run that holds one.
4. **The three first cases, in agent-kit.** One agent-kit PR per case, each with its fixture, its spec criterion and a recorded pass on main three times plus a fail on a rule-removed copy. The `qualitydirector` and `receipt.py` cases exist as prototypes. The flaky-test case does not.
5. **Cursor, later.** An adapter for `cursor-agent`'s `tool_call` events is small (section 2). It waits until a Cursor run can be made hermetic. Not part of unit 1's Done when.

Running the cases in agent-kit's CI waits on H10.

### Spend, second pass

8 `claude -p` runs on Sonnet 5.5, $1.34 in total by the CLI's own figures: 6 counted runs ($1.05) and 2 pilots ($0.29). One `cursor-agent` run of about 15,000 tokens, with no dollar figure. The session that drove this pass is not counted.
