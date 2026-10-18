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

Body sections, in order: Goal, Scope and non-scope, Plan (units in order), Gates and rollback, Staffing and why, Guardrails, Security notes, Review plan, Budget and estimate, SOC 2 gaps (if any), Risks (from a short pre-mortem).

## Lifecycle

A run moves through fixed states, and every move is a ledger entry (S3):

1. **draft**: the agent grounds itself cheaply (how, why, recall; no fan-outs) and writes the charter. `ledger charter check` validates it.
2. **approved**: a human approves. The ledger records who and the charter's hash. Editing the charter after this returns it to draft.
3. **running**: the agent works without asking about reversible, in-budget work.
4. **gated**: a gate was reached. Only that thread stops; independent units continue. The human's answer is a ledger entry, then the run returns to running.
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
- The estimate lists planned calls (executions, reviews, panels). At close, the ledger CLI compares the plan with recorded calls and flags any difference, as Harness² does with its budget identity.
- Reaching any limit is the `spend` gate. A stop-loss applies too: if the predicate hasn't moved in N units and half the budget is spent, stop and write up.
- Where a tool reports no spend, the budget is advisory and the charter says so.

## Rendering (M2)

`ledger charter render --audio` reads the charter aloud as a two-minute briefing; `--video` narrates generated slides. Both come from the Markdown file on demand and are not committed.

## Acceptance criteria

- [ ] beau-mode routes a multi-PR or unattended task to the Large work item playbook.
- [ ] `ledger charter check` rejects a charter missing any required field for its tier.
- [ ] Approving writes a ledger entry with the approver and the charter hash; editing afterward forces re-approval.
- [ ] A test run reaches a gate, stops only that thread, and resumes after an approval entry.
- [ ] Close reports planned versus recorded calls and spend.
- [ ] One real tier 2 run completes end to end with an approved charter.
