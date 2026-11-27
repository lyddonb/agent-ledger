# S2. Policy, gates and identity

> **Status: draft.** Mirrors S2 in the "Governed agent runs" plan doc, which stays canonical until you approve this spec. Most of S2 lands in agentfiles; it's here because the ledger reads policy and writes identity.

One policy file tells every agent which actions are gates, what budgets to use and which security rules apply. agentfiles writes it the way it already writes model roles. Each actor in a run has an identity that names both the agent and the person it acts for.

**Lands in:** agentfiles (policy file, identity config, installing the CLI), agent-kit (a generated policy snippet that skills read), agent-ledger (reads policy, writes actors). **Milestone:** M0 (role-based approvers: M4). **Requirements:** R2, R3, R6, R18.

## Files and precedence

| File | Written by | Committed | Holds |
|---|---|---|---|
| `~/.agents/policy.yaml` | agentfiles, from profiles' `policy:` key | No | Your defaults |
| `.agents/policy.yaml` in a project | A person, by hand or with agentfiles | Yes | The team's rules for that project |
| The charter's front matter | The agent, approved by a human | Yes | This run's additions |

Stricter always wins. Gates and the security rules that add protection (sandboxed tiers, redact patterns, audit sinks) are the union of all three, so a project or charter can add a gate but never remove one. `network_allow` is the opposite kind of rule, since allowing a host loosens the policy, so it is the intersection: a later level can narrow it, never widen it. A budget can be lowered at each level but not raised past the project's ceiling, session archiving stays on once any level turns it on, and a check's mode can be raised at each level but never lowered (enforce over observe over off). agentfiles applies these rules across profiles and `local.yaml`, and `agentfiles policy check` applies them to a project's file (M0 unit 7).

## Schema

```yaml
version: 1
gates:                         # ids; the default list below
  - migration
  - delete-data
  - public-api
  - deploy
  - auth-permissions
  - new-dependency
  - secrets
  - outbound-message
  - license
  - spend
budgets:
  tier2: {usd: 50, tokens: 8000000, wall_clock: 8h, max_parallel_agents: 4}
  tier3: {usd: 150, tokens: 20000000, wall_clock: 24h, max_parallel_agents: 6}
security:
  sandbox_unattended: [2, 3]    # tiers that must run in a sandbox when unattended
  network_allow: [github.com, registry.npmjs.org, pypi.org]
  redact_patterns: []           # added to the built-in secret patterns
audit:
  sinks: [repo]                 # later: s3-object-lock, external
  sessions: {archive: true, lfs_over_mb: 20}
identity:
  human: lyddonb@github         # who you are in ledger entries
  scheduled_owner: creator      # or a named person
approvers: {}                   # reserved for M4: gate id -> roles
checks:                         # verification checks by id: off | observe | enforce
  commit-gate: observe
  stop-checks: observe
```

The budget numbers are placeholders until the ledger has real spend to calibrate from.

## Check modes

A gate waits for a person. A check is a machine verdict: a hook or script that judges the agent's work and, in `enforce`, stops it. `checks:` gives each check a mode.

| Mode | What the check does |
|---|---|
| `off` | Nothing |
| `observe` | Logs what it would have stopped, and stops nothing |
| `enforce` | Stops the action, with the reason |

- A check id missing from every file is `observe`. A new check ships logging, not blocking, and a person moves it to `enforce` once its log shows no false stop.
- Stricter wins across files, like gates: the highest mode any file sets is the mode.
- Write it as a block map of plain `id: mode` lines under a top-level `checks:` key, so readers without a YAML parser (bstack's hooks) can read it.
- Known ids: `commit-gate` (a commit in a session that ran no verification or review), `stop-checks` (the repo's declared cheap checks when the agent stops), `spec-drift` (agent-kit R22), `receipts`, `test-guard` (agent-kit R29). An unknown id is an error, so a typo can't leave a check in the default mode.
- An enforced check may let an action through after it has stopped it twice in one session, and record why, so a check that is wrong can't stall the work. A gate never does this.
- A would-stop in `observe` is a `lesson` entry with `data.kind: "would-block"` in the machine's weekly flags run (S3 "Flag entries" says where that run lives). Its `data` names the check, the repo, the reason and the session.

## Default gates

| Gate | Fires when |
|---|---|
| `migration` | A schema or data migration touches shared data |
| `delete-data` | Data, branches or files outside the run's scope would be deleted |
| `public-api` | A public API, protocol or CLI contract changes |
| `deploy` | Anything deploys, or infrastructure changes |
| `auth-permissions` | Auth, permissions or access control code changes |
| `new-dependency` | A package or service dependency is added |
| `secrets` | A secret, credential or key would be read, created or rotated |
| `outbound-message` | A message goes to a person outside the session |
| `license` | A license changes, or code under a new license comes in |
| `spend` | Any budget limit, or the stop-loss, is reached |

## Identity

Every ledger entry carries an `actor`:

```json
{"kind": "agent", "id": "beau-agent@claude-code#7f3c", "on_behalf_of": "lyddonb@github",
 "tool": "claude-code", "model": "sonnet", "session": "26586e96"}
```

- An agent's id is `<agent name>@<tool>#<session prefix>`, unique per session. Subagents get their own ids with the same `on_behalf_of`.
- A human's id is their GitHub login. Approvals and gate answers have `kind: human`.
- A scheduled or background agent acts for whoever created the schedule, unless `identity.scheduled_owner` names someone else.
- Commits carry the same attribution: agent commits add `Agent-Id:` and `On-Behalf-Of:` trailers. Signed commits are recommended, not required, in M0.

## agentfiles changes

- A `policy:` key in profiles, merged like `roles:`; `local.yaml` can lower budgets per machine.
- `agentfiles sync` writes `~/.agents/policy.yaml`; `status` and `list` show the active gates and budgets.
- A check that a project's `.agents/policy.yaml` parses and doesn't try to remove a gate.
- agentfiles installs the `ledger` CLI at a pinned version.
- `checks:` in profiles and `local.yaml`, merged so the highest mode wins; `policy check` refuses a project file that names an unknown check id or mode. A project that sets a check lower than the user's policy parses, and the user's mode still applies.

## Acceptance criteria

- [x] `agentfiles sync builder` writes a valid `~/.agents/policy.yaml` with the default gates (agentfiles `tests/test_policy.py`, M0 unit 7).
- [x] A project policy that omits a user gate still enforces it; a test proves the union (agentfiles `policy check`, `tests/test_policy.py`).
- [ ] A charter budget above the project ceiling fails `ledger charter check`.
- [ ] Every ledger entry from a test run has an actor with `on_behalf_of` set.
- [ ] A scheduled test run's entries name the schedule's creator.
- [ ] A profile that sets `commit-gate: enforce` and a `local.yaml` that sets it to `off` sync to `enforce`; a project file that sets it to `observe` leaves it at `enforce`, and one that names an unknown id fails `agentfiles policy check` (agentfiles `tests/test_policy.py`, agent-kit R32).
