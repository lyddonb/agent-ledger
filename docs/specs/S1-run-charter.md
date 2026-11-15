# S1. Run charter, rigor tiers and budget

> **Status: draft.** Mirrors S1 in the "Governed agent runs" plan doc, which stays canonical until you approve this spec. After approval, this file is canonical.

A run charter is the one document a human approves before a large run starts. It fixes scope, gates, staffing, rigor and budget, and everything the run does afterward is checked against it.

**Lands in:** agent-ledger (the `ledger charter` commands) and agent-kit (the playbook and template, in the bstack plugin). **Milestone:** M0 (rendering to audio and video: M2). **Requirements:** R1, R2, R8, R9, R15.

## What ships

- agent-kit: a new beau-mode playbook, `large-work-item.md`. beau-mode routes to it when the work spans several PRs, runs unattended, or touches a default gate. figure-it-out calls it as its framing phase.
- agent-kit: a charter template, `references/charter-template.md`, in beau-mode.
- agent-ledger: `ledger charter` commands: `new`, `check`, `approve`, `render`.

## The charter file

Path: `.audit/runs/<run-id>/charter.md`. The run id is `<yyyy-mm-dd>-<slug>`. Front matter carries the fields tools read; the body carries the prose a person reads.

```yaml
---
run_id: 2026-10-20-export-retries
title: Make export retries idempotent
tier: 2                      # 1 routine, 2 significant, 3 critical
predicate: "60k-row export writes 60,000 rows after a mid-run retry"
scope: [src/export/**, tests/export/**]
budget: {usd: 40, tokens: 6000000, wall_clock: 6h, max_parallel_agents: 4}
estimate: {usd: 25, tokens: 3500000, wall_clock: 3h, calls: {rollouts: 0, reviews: 4}}
gates: [migration, new-dependency, public-api, spend]   # from policy, plus any added here
staffing:
  - {role: code, tool: claude-code, model: sonnet, count: 2, isolation: worktree}
  - {role: reviewers, tool: claude-code, model: [opus, sonnet], count: 2}
review: {lenses: [correctness, operability, maintainer], decision_review: false}
spec_level: property-tests   # types | schemas | property-tests | model-check
rollback: {migration: "expand then contract; old column kept until PR 3"}
soc2: {approved_before_build: true, tested: true, independent_review: true, recorded: true}
---
```

Body sections, in order: Goal, Scope and non-scope, Plan (units in order), Gates and rollback, Staffing and why, Guardrails, Security notes, Review plan, Budget and estimate, SOC 2 gaps (if any), Risks (from a short pre-mortem). A heading may add words after the name, such as `## Risks (pre-mortem)`.

### The `ledger charter` commands (M0 unit 5)

| Command | Does |
|---|---|
| `ledger charter new <slug> --title T [--tier N] [--date D]` | Writes the template to `.audit/runs/<run-id>/charter.md`; its `TODO` placeholders fail `check` until filled in. Refuses an existing charter |
| `ledger charter check <run-id> [--require-approved]` | Prints the charter's full sha256 and every value the tools enforce, as they read it; then every problem for its tier with its line, and the approval state. Exit 0 complete, 1 problems (or not approved, with `--require-approved`), 2 unreadable |
| `ledger charter approve <run-id> --actor HUMAN --sha256 HEX (--relayed-by AGENT --quote Q \| --typed-by-me) [--channel C]` | `--sha256` is the hash (at least 12 hex characters) that `check` printed for the version the human reviewed; a charter edited since is refused. The approval must say who typed it (S3 "Who typed a human's entry"): an agent relaying it passes `--relayed-by` and `--quote`, and `--quote` must contain that prefix, so the archived session shows which version the human approved; the human approving in their own terminal passes `--typed-by-me`, recorded as `data.self_typed`. With neither, or both, it is refused. Appends an `approval` entry whose `data.charter_sha256` (and evidence) is the charter's sha256, plus `supersedes` when it replaces an earlier approval. The charter must pass `check` and the run must be started (`ledger init`); the actor must be human. Approving the same bytes again is a no-op |
| `ledger charter render` | M2 |

**What `check` requires.** Every tier: `run_id` (equal to the folder), `title`, `tier` (1 to 3), `predicate`, a non-empty `scope`, and `gates` holding every default gate from S2 (a charter adds gates, never removes one). Tier 2 and 3 add `budget` (positive `usd`, `tokens`, `max_parallel_agents`, and a `wall_clock` like `90m`, `8h` or `2d`), `estimate` (with `calls`, and no figure over the budget), `staffing` (each with role, tool, model and a positive count), `review` (`lenses` and `decision_review`), `spec_level` (types, schemas, property-tests or model-check), `rollback`, `soc2` (its four keys), and the body sections in order. Tier 3 adds `security` in `review.lenses` and `decision_review: true`. At every tier: no unknown field at the top level or in `budget`, `estimate`, `review` or `soc2` (a field nothing enforces would mislead a reviewer); `budget` and `estimate` are checked whenever present; `estimate.calls` counts are whole numbers; `scope` holds paths or `repo: [paths]` items; a `soc2` value other than `true` needs a `## SOC 2 gaps` section; no placeholder (`TODO`, `TBD`, `FIXME`, `XXX`, any case) in a key, value or the body.

**Approval state.** The ledger is the source of truth: the charter is approved when the latest approval entry names its exact sha256, stale when that approval names an earlier version (the charter was edited, so it is back to draft), and draft otherwise. Only a human entry counts. Approvals written before these commands carry the hash only in evidence, as exactly `charter.md sha256 <hex>` or `charter.md sha256 <hex>, as committed in <commit>`; `check` reads that form too.

**Binding approval to what was read.** The approving human reads `check`'s output (the enforced values and the sha256) and gives the hash prefix with the approval, such as "Approved c37ae9b1a466". The agent relays it with `--sha256`, `--quote` and `--relayed-by`; a human approving in their own terminal passes `--typed-by-me` instead, so no approval is recorded without saying who typed it. In M0 the actor is not authenticated, so this binds the approval to the transcript that the session archive keeps; signed approvals come with M4.

**The front matter subset.** Charters are read without a YAML library, so the front matter is limited to `key: value` lines; plain scalars (whole numbers, `true`, `false`, `null`, and unquoted text); double- or single-quoted strings; flow lists `[a, b]` and maps `{k: v}`, nested; and a key followed by `  - item` lines, where an item is a value or one `key: value` pair. Comments start with ` #` outside quotes. Anchors, block scalars, nested block maps and tabs are refused with the line, so a field is never read wrong. So are plain values a YAML parser would type differently (`0x1f`, `1e3`, `.5`, `2026-11-10`, `10:30`, `yes`, `On`, `-`, a leading `#`, a flow `?` or `key:`): quote them. Unicode line breaks (U+0085, U+2028, U+2029), other control characters and bidi overrides are refused anywhere in the charter, and zero-width characters in the front matter, because each can make the text a person sees differ from what is parsed. `tests/vectors/charter-yaml.json` holds PyYAML's reading of 800 generated values, and the reader must agree wherever it accepts one.

## Lifecycle

A run moves through fixed states, and every move is a ledger entry (S3):

1. **draft**: the agent grounds itself cheaply (how, why, recall; no fan-outs) and writes the charter. `ledger charter check` validates it.
2. **approved**: a human approves. The ledger records who and the charter's hash. Editing the charter after this returns it to draft.
3. **running**: the agent works without asking about reversible, in-budget work.
4. **gated**: a gate was reached. Only that thread stops; independent units continue. The human's answer is a `gate-answer` entry that says who typed it (S3), then the run returns to running.
5. **closing**: verify against the predicate, run the tier's reviews, propose levers and lessons.
6. **sealed**: the chain is sealed into the PR (S3). Or **abandoned**, with a reason entry.

## Rigor tiers

| Tier | When | What runs |
|---|---|---|
| 1. Routine | Reversible, small blast radius | Playbook, verify-work, one reviewer. No charter needed unless unattended |
| 2. Significant | Several PRs, shared code, user-facing changes | Charter, ledger, interrogate with two model families, live verification lanes |
| 3. Critical | One-way doors, money, permissions, customer data, migrations | Tier 2 plus architect with arena, a security lens, decision review, formal or property specs where feasible, a human review per PR |

## Budget

- Three limits, all required for tier 2 and 3: dollars, tokens, wall-clock time. Plus a cap on parallel agents.
- Dollars are the main measure, since they weight each kind of token by its price. Tokens count input, output and cache writes, but not cache reads. An agent re-reads its cached context on every turn, so counting cache reads would trip the token limit on any long run while adding little cost. The ledger reports cache reads separately (S3 "Spend entries").
- The estimate lists planned calls (executions, reviews, panels). At close, the ledger CLI compares the plan with recorded calls and flags any difference, as Harness² does with its budget identity.
- Reaching any limit is the `spend` gate. A stop-loss applies too: if the predicate hasn't moved in N units and half the budget is spent, stop and write up.
- Where a tool reports no spend, the budget is advisory and the charter says so.

## Rendering (M2)

`ledger charter render --audio` reads the charter aloud as a two-minute briefing; `--video` narrates generated slides. Both come from the Markdown file on demand and are not committed.

## Acceptance criteria

- [ ] beau-mode routes a multi-PR or unattended task to the Large work item playbook.
- [x] `ledger charter check` rejects a charter missing any required field for its tier (`tests/test_charter.py`).
- [x] Approving writes a ledger entry with the approver and the charter hash; editing afterward forces re-approval (`tests/test_charter.py`).
- [ ] A test run reaches a gate, stops only that thread, and resumes after an approval entry.
- [ ] Close reports planned versus recorded calls and spend.
- [ ] One real tier 2 run completes end to end with an approved charter.
