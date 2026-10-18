---
run_id: 2026-10-18-m0-foundation
title: "M0 Foundation: policy, run charter, ledger, session archive, identity"
tier: 2
predicate: "One real tier 2 run, in any project, completes with an approved charter, a ledger chain that `ledger verify` passes in CI, and archived redacted sessions, and is merged"
scope:
  - agent-ledger: [src/**, tests/**, docs/**, action.yml, .github/**, README.md]
  - agentfiles: [agentfiles/config.py, agentfiles/instructions.py, agentfiles/targets.py, agentfiles/cli.py, profiles/**, tests/**, docs/**]
  - agent-kit: [plugins/bstack/**, kit.yaml, docs/**, tests/**]
budget: {usd: 60, tokens: 10000000, wall_clock: 10h, max_parallel_agents: 3}
estimate: {usd: 35, tokens: 6000000, wall_clock: 6h, calls: {rollouts: 0, reviews: 6}}
gates: [migration, delete-data, public-api, deploy, auth-permissions, new-dependency, secrets, outbound-message, license, spend]
staffing:
  - {role: judgment, tool: claude-code, model: opus, count: 1, isolation: none, why: "owns the entry schema, hashing and verify design"}
  - {role: code, tool: claude-code, model: sonnet, count: 2, isolation: worktree, why: "CLI commands and agentfiles changes, on disjoint files"}
  - {role: reviewers, tool: claude-code, model: [opus, sonnet], count: 2, why: "interrogate each PR"}
  - {role: reviewers, tool: cursor, model: grok-4.7-xhigh-fast, count: 1, why: "one cross-family review of the chain and verify code, if cursor-agent is available"}
review: {lenses: [correctness, security, maintainer], decision_review: false}
spec_level: property-tests
rollback: {ledger-format-v1: "the format is versioned (`v` field); v1 is frozen only at the public-api gate before the first tag"}
soc2: {approved_before_build: pending, tested: true, independent_review: true, recorded: partial}
---

# M0 Foundation

> **Status: draft, awaiting your approval.** Nothing in this charter has started. Approve it by replying "approved" (with any edits) in chat, or by editing this file and committing it on `main`. This charter predates the ledger it describes, so its approval is written down by hand first and imported as the run's first entries once unit 2 lands (see SOC 2 gaps).

## Goal

Make large agent runs controllable and auditable: a policy file sets the gates and budgets, a charter is approved before work starts, and every decision, gate answer, verification result and cost lands in a tamper-evident ledger in the repo, with sessions archived. This is milestone M0 of the plan in the "Governed agent runs" doc, specified in [S1](../../../docs/specs/S1-run-charter.md), [S2](../../../docs/specs/S2-policy-and-identity.md) and [S3](../../../docs/specs/S3-audit-ledger.md).

## Scope and non-scope

**In scope:** the `ledger` CLI (all S3 commands plus `charter new|check|approve`), the repo sink and sink interface, redaction and the session archive, actor identity, the `ledger-verify` GitHub Action, agentfiles' `policy:` key and CLI install, and agent-kit's policy snippet, Large work item playbook, charter template and the show-me-your-work switch to the CLI.

**Out of scope:** charter audio and video (M2), hooks (M2), other sinks (M4), role-based approvers (M4), Harness lab (M3), and anything in S4 to S6.

## Plan

Units in order. Each lands as its own PR, verified before the next starts.

1. **Entry model, canonical hashing, verify** (agent-ledger). The core. Property tests prove that any changed byte, deleted line, reordered pair or forged `prev` is detected and named.
2. **init, append, show, repo sink, sink interface, file lock** (agent-ledger). Then import this charter's hand-written approval as the run's first entries.
3. **Redaction and `session add`** (agent-ledger). Built-in secret patterns plus policy patterns; both hashes recorded; refuses to commit when redaction can't be confirmed.
4. **seal, INDEX.jsonl, `verify --base`** (agent-ledger). Append-only check against the base branch with git.
5. **`charter new|check|approve`** (agent-ledger). Tier-aware required fields; approval hashes the charter; edits force re-approval.
6. **`ledger-verify` Action** (agent-ledger). Runs verify plus a secret scan over `.audit/` on PRs.
7. **Policy and CLI install** (agentfiles). `policy:` profile key, `~/.agents/policy.yaml`, union-of-gates rule, status output, installing a pinned `ledger`.
8. **Skills** (agent-kit). Policy snippet, Large work item playbook, charter template, show-me-your-work calls `ledger`, `log.sh` retired.
9. **The gate run.** Use the finished system on one real tier 2 task in a project you choose.

## Gates and rollback

- **public-api:** freezing ledger format v1 and the CLI's command names before the first tag. Until then both can change freely; afterward a change needs a new format version.
- **new-dependency:** none expected; the CLI is standard library only. Any dependency is a gate.
- **license:** none expected.
- Every other default gate stays on. Rollback for everything else is reverting the PR; no shared data is touched.

## Staffing and why

One `judgment` agent designs the schema, hashing and verify (unit 1), because every later unit and every future audit depends on it. Two `code` agents take the CLI commands and the agentfiles changes in separate worktrees, since they touch disjoint files. Reviews use two Claude models, plus one Cursor model for a cross-family look at units 1 and 4 if `cursor-agent` is available; if not, the review says it wasn't independent.

## Guardrails

- agent-ledger CI: unit tests on Python 3.10, 3.12 and 3.13, and an install check.
- Unit 1 adds property tests (standard-library `random` with fixed seeds; no Hypothesis, to keep zero dependencies).
- agentfiles and agent-kit keep their existing CI (`make check validate unit`, the agentfiles test suite).

## Security notes

- Redaction (unit 3) is the riskiest code: a miss commits a secret to git history. It gets the security lens in review and a test corpus of planted secrets.
- The CLI never reads environment secrets and never makes network calls.
- Transcripts are treated as data; nothing in them is executed.

## Review plan

`/interrogate` on every PR with the correctness, security and maintainer lenses. You review and merge each PR, which is the independent review SOC 2 expects.

## Budget and estimate

| | Estimate | Limit |
|---|---|---|
| Dollars | $35 | $60 |
| Tokens | 6M | 10M |
| Wall-clock | 6 h | 10 h |
| Parallel agents | 3 | 3 |

These are rough guesses: there is no recorded spend yet to calibrate from. Claude Code reports cost per session; the other tools may not, so the dollar limit is partly advisory.

## SOC 2 gaps

- **Recorded, partially:** units 1 and 2 build the ledger, so they can't be recorded in it as they happen. Their decisions go in a hand-written `bootstrap-notes.md` in this folder and are imported as entries when unit 2 lands, marked as imported.
- **Approved before build:** pending your approval of this charter.

## Risks (pre-mortem)

| How it could fail | What catches it |
|---|---|
| The entry format churns after real ledgers exist | The `public-api` gate before tagging v1, and the `v` field |
| Redaction misses a secret and it reaches git | Planted-secret tests, the CI secret scan, the refuse-when-unsure rule |
| Two agents append at once and fork a chain | File lock plus a concurrency test (S3 acceptance) |
| agentfiles can't install a Python CLI cleanly on every machine | Open question below; decided at unit 7 |
| Spend overruns while the estimate is uncalibrated | The `spend` gate and the stop-loss |

## Open questions for you

- How should agentfiles install the CLI: `uv tool install` from the git tag, `pipx`, or a vendored copy? I'd use `uv tool install` and fall back to `pipx`.
- Which project hosts the gate run (unit 9)? It needs a real tier 2 task.
