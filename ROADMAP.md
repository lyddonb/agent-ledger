# Roadmap

The full plan, with all milestones and the reasoning, is in the "Governed agent runs" planning doc. This file tracks what lands in **this** repo.

| Milestone | This repo's part | Starts when |
|---|---|---|
| **M0. Foundation** (done: v1.0.0, 2026-11-15) | The `ledger` CLI (init, append, verify, show, seal, spend, session add, charter, import-tsv, scan), the repo sink and sink interface, redaction and the session archive, actor identity, the `ledger-verify` Action | You approve the [M0 charter](.audit/runs/2026-10-18-m0-foundation/charter.md) |
| **Receipts** (v1.1.0, 2026-11-16) | The receipt convention for `verification` entries and `ledger receipts`, from the run `2026-11-17-criterion-receipts` | Done |
| **import-tsv** (v1.2.0, 2026-11-21) | `ledger import-tsv`, the one M0 command that shipped as a stub: an old `decisions.tsv` trail becomes a run of imported entries (S3 "Migration") | Done |
| M1. Quality and security | Charter fields for the spec ladder and security lens; review entries | M0's gate passes |
| M2. Enforcement | `ledger charter render --audio/--video`; spend calibration from recorded runs | M1's gate passes |
| M3. Harness lab | A second package, `harness-lab`: test agent-setup changes on a project's own tasks (spec S6 in the plan doc). Score human cost (interventions, review time) as well as pass rate, so it doesn't optimize for autonomy alone ([notes](docs/notes/steering-beyond-automation.md)). Its first slice is M3 unit 1, below | One app has a verification skill: met 2026-11-18 by `verify-ledger` (pull request 23). Unit 1 opens before M1 and M2 (H8, answered 2026-11-21) |
| **Flags** | A convention for agent-raised flags: a `lesson` entry an agent appends mid-task when a skill or reference was wrong, kept in one rolling run per machine and week (S3 "Flag entries"), from [pull request 45](https://github.com/lyddonb/agent-ledger/pull/45) | Done |
| M4. Collaboration and compliance | `s3-object-lock` and `external` sinks; role-based approvers | A teammate or an audit needs it |

## M0 units

See the charter's Plan section. In short: (1) entry model, hashing and verify; (2) init, append, show and the repo sink; (3) redaction and sessions; (4) seal and `verify --base`; (5) charter commands; (6) the Action. Units 7 and 8 land in agentfiles and agent-kit; unit 9 is the gate run.

## M3 units

### M3 unit 1. Hard rules as tool-call checks
**Status:** Done, 2026-11-27. Feature PRs 1 to 4 are merged: the case format, the judge, the runner and a
receipt from a case, in [`src/harness_lab`](src/harness_lab/README.md), and the three first cases in agent-kit
(its pull requests 136, 137 and 140, criteria P1-C1 to P1-C3). The Done when was checked on agent-kit's main at
`a07636f`: each case passed 3 of 3 runs, and each failed on a copy with its rule removed
([record](.changes/2026-11-27-m3-unit1-done.json)). receipt-before-tick's patch had stopped applying to main;
agent-kit pull request 153 rebuilds it and adds a test that every case's patch applies. Feature PR 5, a Cursor
adapter, stays out of the unit until a Cursor run can be made hermetic. The prototype ran on 2026-11-21 and
2026-11-26 ([notes](docs/notes/m3-unit1-prototype.md)). The prototype found tool-call assertions alone too narrow: two of the three
first cases need a check on the fixture after the run. Beau accepted the `after` assertion kind
on 2026-11-26 ("Accept after"), and the What line below includes it. The Feature lives in the
`harness-lab` package, not a `ledger eval` subcommand, so `ledger`'s CLI contract stays as
tagged (Beau, 2026-11-26: "Yes, harness-lab"). The cases run locally (H10, 2026-11-26: "stay
local"), until Feature PR 2's runner has 20 or more runs per case and a flake rate. Nothing is
open for Beau. The proposed Feature PRs are in the notes, under "Proposed Feature PRs".

The first slice of Harness lab. It comes from Akshay's "[Our Claude skills and guidance, six months later](https://akshay.co/posts/our-claude-skills-and-guidance-six-months-later/)"
(read 2026-11-19), where skills that passed lint and review still did badly in use. The plan
across repos is in [agent-kit's roadmap](https://github.com/lyddonb/agent-kit/blob/main/ROADMAP.md),
under Skill ops. This is the deterministic half of its behavioral evals.

**What:** A case is a prompt, a fixture repo and assertions on a headless agent run: `calls`,
`never_calls` and argument matches on its tool calls, and `after` checks on the fixture once the
run ends. An `after` check is a command run in the fixture whose exit code is the verdict, so it
stays rules and exit codes. Each hard rule is a criterion in a spec
file in agent-kit, and the eval command is that criterion's probe. A run then writes an
ordinary receipt (criterion id, spec path, commit), and `ledger receipts` prints the results.
There is no separate eval store and no judge model. The first cases are three of agent-kit's
hard rules: `qualitydirector` takes brief numbers from `summary --json` and never computes
them; the Product phase playbook never ticks a box without `receipt.py`; no flaky test is
re-run. The cases run locally (H10). If a later ask allows it, agent-kit's CI runs only the cases for plugins its
`check_versions.py` reports as changed. Until then they run locally.
**Depends on:** agent-kit R38, so cases come from sessions that happened. H8 and H9 are
answered (2026-11-21): the unit opens early, and its cases run locally, not in CI, until the
Prototype step gives a cost per run.
**Playbook:** Prototype first, to settle how to capture tool calls from `claude -p` and from
one other tool. Then Feature, split into PRs with the Multi-phase plan playbook.
**Done when:** each first case, judged on its tool-call assertions and its `after` checks, fails
on a skill edit that removes the rule it guards, and passes on agent-kit's main three times in a
row.
**Why not done:** agent-kit's R3 and R13 waited on real prompts. Its R38 finds them.
**Prioritize when:** agent-kit R38 has four weeks of rows, or before the next edit to
beau-mode's non-negotiables. agent-kit's R3 and R13 point here, and its R25 uses these evals to
show a cut loses no rule.

## Flags unit

### Flags unit 1. The flag convention and a weekly flags run
From Harrison Chase's "[The Knowledge Factory](https://www.langchain.com/blog/knowledge-factory)"
(read 2026-11-23), with evidence from Uber's
"[How Uber Evals 1 Million Agent Skill Invocations a Month](https://www.uber.com/us/en/blog/optimizing-ubers-agent-skills/)"
(read 2026-11-25), whose always-on papercut skill is the same idea at scale. The plan across
repos is in [agent-kit's roadmap](https://github.com/lyddonb/agent-kit/blob/main/ROADMAP.md),
under Knowledge factory.

**What:** S3 gains a "Flag entries" section beside Receipt entries. A flag is a `lesson` entry
whose `data` has `kind: "flag"`, `target` (the skill, reference or pointer at fault, by name),
`did_instead` and `suggested_fix`. The command and its output that showed the fault go in the
entry's `evidence`. A flag is never edited. Whatever fixes it cites the flag entry's hash, and
that citation closes it. `ledger append` writes one today. Add a `--flag` shorthand only if the
prototype shows `append` is too awkward for an agent mid-task. Outside a governed run, flags go
into one rolling run per machine and week, created with `ledger init` the first time a week's
first flag is written. `ledger init flags-<machine>` prefixes the day it runs, so the run is
named for the day of the week's first flag; the convention finds this week's run by looking for
a `flags-<machine>` run started since Monday. It lives in the repo `--root` names.

What the code already settles (read 2026-11-25 at 64a0d17):

- Tried in a scratch repo on 2026-11-25: `ledger init flags-mac` then `ledger append --type
  lesson` with the flag's `data` gave a run that `ledger verify` passed, with a planted GitHub
  token in the evidence redacted.
- `ledger init` needs no charter. `init_run` writes a `state` entry and checks nothing else, so
  a flags run starts without an approval.
- `ledger verify` accepts an unsealed run with no charter. A rolling run grows across PRs and
  stays append-only under `--base`, as any merged run does.
- Runs live under `.audit/runs/` of the repo `--root` names, so where flags are kept is that
  repo's choice. Flags can name private context, so keep them in a private repo.

**Format:** no format bump. Format v1 freezes entry keys, not conventions inside `data`, the same
way receipts shipped in 1.1.0. When `rejection` forces format v2, promote `flag` to its own type
in the same bump (IDEAS).
**Depends on:** nothing.
**Playbook:** Feature.
**Done when:** a flag written with the documented command lands in a weekly flags run, passes
`ledger verify --base origin/main`, has a planted secret in its evidence redacted, and
`ledger show` prints it.
**Status:** Done, 2026-11-25, in [pull request 45](https://github.com/lyddonb/agent-ledger/pull/45). `ledger append` writes flags; no `--flag` shorthand (IDEAS, `ledger flag`). agent-kit R45 can start.
