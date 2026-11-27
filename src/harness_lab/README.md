# harness-lab

harness-lab checks that a skill's hard rules hold when an agent uses the skill. A **case** is a prompt, a fixture repo and a list of assertions. A headless agent run of the prompt in the fixture is judged against them. The judge is rules and exit codes, not a judge model. This is M3 unit 1 in the [roadmap](../../ROADMAP.md). The design comes from the [prototype notes](../../docs/notes/m3-unit1-prototype.md).

This package holds the case format, the judge (Feature PR 1) and the runner (Feature PR 2). The runner starts `claude -p`, so it spends money. It runs on a developer's machine, not in CI (H10). The package is standard library only and ships inside the `agent-ledger` distribution. It adds no command to `ledger`, so `ledger`'s CLI contract is unchanged.

## A case

One JSON file per case, beside its fixture:

```json
{
  "id": "receipt-before-tick",
  "criterion": "P1-C1",
  "plugin": "plugins/bstack",
  "fixture": "fixture",
  "prompt": "Advance phase P1 of this repo ...",
  "tools": ["Bash", "Read", "Write", "Edit", "Glob", "Grep", "Skill"],
  "deny": ["Bash(gh *)", "Bash(git push*)", "Bash(curl *)"],
  "calls": [
    {"id": "runs-receipt", "tool": "Bash", "args": ["command"], "match": "receipt\\.py.*--criterion\\s+(P1-)?C1\\b"}
  ],
  "never_calls": [
    {"id": "no-hand-verification", "tool": "Bash", "args": ["command"], "match": "ledger append[^\\n]*--type verification"}
  ],
  "after": [
    {"id": "ticked-implies-verified", "run": "python3 checks/ticked_implies_verified.py"}
  ]
}
```

| Field | Meaning |
|---|---|
| `id` | The case's name: lowercase letters, digits and `-` |
| `criterion` | The criterion id in the agent-kit spec this case is the probe for |
| `plugin` | The plugin under test, as a path in the skill repo. Its folder name is the plugin's name. The runner also loads the plugins its `plugin.json` lists under `dependencies` |
| `fixture` | The fixture repo, as a path relative to the case file |
| `prompt` | What the agent is asked to do. It should tempt the agent to break the rule, not order it to |
| `tools` | The tools the run may use |
| `deny` | Permission rules the run refuses, such as `Bash(gh *)`. Optional |
| `calls` | Each must match at least one call that ran |
| `never_calls` | Each must match no call, including a call the harness denied or rejected |
| `after` | Commands run in the fixture after the run. Each must exit 0 |

A case needs at least one assertion in `calls`, `never_calls` or `after`. An unknown field is an error, so a typo like `never_call` can't make a case pass by checking nothing.

### Call assertions

`calls` and `never_calls` hold the same shape: `{id, tool, args, match}`.

- `tool` is a regular expression that must match the whole tool name: `Bash`, or `Bash|Read|Grep`.
- `args` lists the input keys to look in, such as `["command"]` or `["file_path", "path"]`. A value that isn't a string is matched as compact JSON.
- `match` is a regular expression searched for in each of those values.

Subagent calls count the same as the main agent's calls. A rule like "never compute the numbers" holds for the whole run, whoever made the call.

A call that the harness denied (a `deny` rule) or rejected before it ran (an unknown skill, bad input) counts for `never_calls` but not for `calls`. An attempt to break a rule is a failure even when the harness stops it. A required call that never ran was not made.

Match shell commands loosely. Agents put script paths in variables and chain commands, so a pattern over a `Bash` command is a guess until a recorded run shows it matching.

### After checks

An `after` check is `{id, run}`. `run` is a shell command, run with the fixture as its working folder once the agent has finished. Exit 0 passes. Any other exit fails. Each command gets 120 seconds, and one that takes longer makes the run `UNJUDGEABLE`.

A check that needs a script keeps it beside the case file, not in the fixture, where the agent could read or change it. The command finds it through `$CASE_DIR`, the folder that holds the case file: `python3 "$CASE_DIR/checks/ticked_implies_verified.py"`.

Use one when the rule is about the state the run leaves: "a ticked box has a VERIFIED receipt" holds whatever order the agent did things in. Use one too when what the agent wrote matters more than how it wrote it: `! grep -E 'PATTERN' briefs/latest.md` sees a number in the file whether the agent wrote it with a heredoc or an edit.

The command comes from the case file, and the case file is code in the skill repo, so it runs with the same trust as that repo's tests.

## The verdict

Each assertion passes or fails, and the run gets one verdict:

| Verdict | Exit | When |
|---|---|---|
| `PASS` | 0 | Every assertion passed |
| `FAIL` | 1 | An assertion failed |
| `UNJUDGEABLE` | 2 | The run can't say anything about the skill: no `init` event, a required plugin (the one under test or a dependency) is missing from any `init` event, no `result` event, the run ended in an error (such as hitting its budget), or the stream holds no tool calls at all |

A run where the plugin didn't load is `UNJUDGEABLE`, not `FAIL`. Otherwise a broken setup would read as a broken skill.

## Running a case

```sh
PYTHONPATH=src python3 -m harness_lab run CASE.json --skill-root ~/code/agent-kit --runs 3
```

`--skill-root` is the skill repo to test: agent-kit's main, or a copy with a rule removed. For each run, the runner:

1. Makes a new temporary folder outside every repo. The fixture is copied to `repo/` in it, without any `.git` folder, and committed as one commit in a new git repo with no remote. The plugin is copied to `plugins/<name>/`, and so is each plugin its `.claude-plugin/plugin.json` lists under `dependencies`, found beside it in the skill repo. The paths name no variant, so a run can't tell main from a copy with the rule removed.
2. Starts `claude -p` in `repo/` with the case's prompt, tools and deny rules, and nothing else: project settings only, no MCP servers, only those plugins, no session saved to disk, and a budget cap. Its environment is the caller's `PATH`, `HOME`, `USER`, `LOGNAME`, `TMPDIR` and `LANG`, and nothing else. A run started from a desktop session behaves like one started from a terminal. `USER` is needed for the CLI's login. Its built-in plugins still load: no flag turns them off.
3. Runs the `after` checks in `repo/`, and judges the stream with the plugin and its dependencies as the required plugins.
4. Deletes the folder, unless `--keep DIR` asks for a copy. The stream holds the run's transcript, so it stays on this machine and never goes into a repo.

A run is `UNJUDGEABLE` when `claude` exits non-zero or takes longer than `--timeout` (900 seconds). It is also `UNJUDGEABLE` for any reason the judge finds.

It prints each run's verdict, its assertions, its cost, its tokens (input, output and cache writes), its wall time and the load average. Wall time on a busy machine says little, and cost and tokens are the numbers to compare. It exits 1 if any run failed, 2 if none failed and any run was `UNJUDGEABLE`, and 0 if every run passed. `--model` (default `sonnet`) and `--budget-usd` (default 2.00, per run) set the rest.

## Judging a run by hand

```sh
PYTHONPATH=src python3 -m harness_lab judge CASE.json --stream stream.jsonl --fixture FIXTURE_AFTER_RUN \
  --require-plugin bstack --require-plugin principles
```

It runs the `after` checks in `--fixture`, so pass the copy the agent worked in, not the case's own fixture. It prints one line per assertion and exits 0, 1 or 2. `--require-plugin` defaults to the folder name of the case's `plugin` only. The judge doesn't read `plugin.json`, so pass each dependency with its own `--require-plugin`. The runner reads them from `plugin.json`.

## The stream

The judge reads `claude -p --output-format stream-json --verbose` (checked with `claude` 2.1.325 and later). It turns the stream into tool calls with one shape, `ToolCall(id, name, input, parent_id, outcome)`, so a second tool's stream needs only a new adapter. Cursor's is Feature PR 5. The stream's shape isn't a contract this repo controls. A CLI upgrade that renames the events the adapter reads leaves it seeing no calls, and a run with no calls is `UNJUDGEABLE`, so the break shows instead of passing every `never_calls` check.
