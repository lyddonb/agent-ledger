---
run_id: 2026-11-17-criterion-receipts
title: "Criterion receipts: every checked criterion carries a ledger receipt"
tier: 2                      # 1 routine, 2 significant, 3 critical
predicate: "One merged curator PR that checks a phase criterion carries a receipt table in its body, printed by `ledger receipts` from that PR's own ledger run, with one row per criterion the PR moved; `ledger verify --base origin/main` passes in curator; and the Product phase playbook refuses to check a box that has no passing receipt"
scope: ["docs/specs/S3-audit-ledger.md", "src/agent_ledger/**", "tests/**", "README.md", "ROADMAP.md", "IDEAS.md", ".audit/runs/2026-11-17-criterion-receipts/**", "agent-kit:plugins/bstack/**", "agent-kit:tests/**", "agent-kit:docs/**", "agent-kit:changes.yaml", "agent-kit:kit.yaml", "agent-kit:ROADMAP.md", "agentfiles:agentfiles.lock", "curator:.audit/**", "curator:.github/workflows/**", "curator:.claude/skills/verify-curator/**", "curator:.agents/skills/verify-curator/**", "curator:AGENTS.md"]
budget: {usd: 40, tokens: 6000000, wall_clock: 6h, max_parallel_agents: 2}
estimate: {usd: 22, tokens: 3200000, wall_clock: 4h, calls: {rollouts: 0, reviews: 6}}
gates: [migration, delete-data, public-api, deploy, auth-permissions, new-dependency, secrets, outbound-message, license, spend]
staffing:
  - {role: code, tool: claude-code, model: sonnet, count: 1, isolation: worktree, why: "units land in order, one repo at a time"}
  - {role: reviewers, tool: claude-code, model: opus, count: 1, why: "interrogate each unit PR"}
  - {role: reviewers, tool: cursor, model: grok-4.7-xhigh-fast, count: 1, why: "a second model family on each unit PR, through cursor-agent"}
review: {lenses: [correctness, maintainer], decision_review: false}
spec_level: property-tests
rollback: {ledger-receipts-command: "revert the unit 2 PR before 1.1.0 is tagged; after the tag the command name is a promise, so a fix ships as 1.1.1 and the name stays", curator-ledger: "revert the unit 4 PR: it adds .audit/ and one CI job and changes no product code"}
soc2: {approved_before_build: true, tested: true, independent_review: true, recorded: true}
---

# Criterion receipts: every checked criterion carries a ledger receipt

## Goal

Beau wants to merge agent work without re-running its checks by hand. Today a phase spec's box
(`- [x] **C8.**`) is checked on the agent's word: the spec names a probe, but nothing records that
the probe ran, on which commit, or what it returned. Curator moved its eleven phase specs to the
new format in curator#199 and is building against it (curator#201 added P2e C8), which is the
trigger agent-kit's roadmap set for this work.

Done means a checked criterion points at a ledger entry that says which probe ran, on which
commit, and what came back, and the PR body shows those entries as a table the agent did not
type. A criterion whose probe did not run shows as not run.

## Scope and non-scope

In:

- **agent-ledger.** The receipt convention inside `data` of a `verification` entry, written into
  S3. `ledger append` refuses a malformed receipt, as it does a malformed `spend` entry. A new
  command, `ledger receipts <run-id>`, prints the table. Released as 1.1.0.
- **agent-kit (bstack).** `verify-work` writes one receipt per criterion it probes. The Opening a
  PR playbook pastes the `ledger receipts` output. The Product phase playbook checks a box only
  when a passing receipt backs it.
- **curator.** A ledger (`.audit/`), the `ledger-verify` check in CI, and `verify-curator`
  writing receipts. One real PR proves the whole path.

Out:

- The spec drift gate (agent-kit's first R22), `rejection` entries (R23) and fix proofs. Each
  builds on receipts and gets its own run.
- Any change to format v1. The prototype below shows none is needed.
- Receipts for work with no criterion ids (bug fixes outside a phase, the quality books).
- Backfilling receipts for boxes already checked in curator.

## Plan

Each unit is one PR, lands on its own, and is verified before the next starts.

1. **Settle the shape by hand, in curator.** In a throwaway curator worktree, run the probes for
   two real criteria (P2e C8, which passes, and P2e C2, which is unchecked), write their receipts
   with today's `ledger append`, and render the table with a scratch script. This settles the
   two open design questions below from observation. Verified by: the table reads correctly to
   Beau for both a passing and a not-run criterion. No PR; the worktree is deleted after.
2. **agent-ledger: convention, refusal and `ledger receipts`.** S3 gains a "Receipt entries"
   section. `append` refuses a `verification` entry whose `data.receipt` breaks the rules.
   `ledger receipts <run-id>` prints a Markdown table: criterion, verdict, probe, commit,
   evidence. When a criterion has several receipts, the table shows the last one and counts the
   earlier ones. Verified by: a property test that any sequence of valid receipts renders one
   row per criterion with the latest verdict, mutation checks on the refusal rules, and the
   command run against unit 1's hand-written ledger. `public-api` gate fires here.
3. **agent-kit: write, render, enforce.** `verify-work` and `show-me-your-work` say how to write
   a receipt. Opening a PR includes the table. Product phase step 4 checks a box only with a
   passing receipt at the PR's head commit. `check-product.py --receipts <run-id>` fails when a
   box checked in the diff has no passing receipt. Verified by: the script's tests, and one Eval
   playbook case showing an agent given a phase PR produces receipts unprompted. bstack minor
   version bump.
4. **curator: adopt and prove.** Add `.audit/`, the `ledger-verify` job, and receipt writing in
   `verify-curator verify`. Then one real curator phase PR, opened by an agent on bstack with
   unit 3, carries the table. Verified by: the predicate, read from the merged PR and curator's
   `main`. This unit waits until no other curator session has `verify-curator` open in a PR.

**Design questions unit 1 settles**

- **Where a receipt lives when the PR is not a governed run.** Curator has no `.audit/` today.
  The default is one small run per PR (`ledger init <branch-slug>`), sealed in that PR, so two
  PRs never append to the same `ledger.jsonl`. The alternative, one long run per phase, breaks
  the one-ledger-branch rule as soon as two PRs are open.
- **How a criterion is named across repos.** Specs number criteria locally (`C8`, `A2`). The
  default is `<phase>-<id>` (`P2e-C8`) plus the spec path and its sha256 at the probed commit, so
  a receipt still means something after the spec is edited.

**Prototype already run (2026-11-16).** With `ledger` 1.0.0 in a scratch repo, two `verification`
entries carrying `data: {verdict, receipt: {criterion, spec, spec_sha256, probe, exit, commit}}`
appended and `ledger verify` passed, including a `not-run` receipt with `exit: null`. A float in
the receipt was refused by `append`. So the convention needs no new field and no format v2.

## Gates and rollback

- `public-api` fires at unit 2. `ledger receipts` is a new command, and command names are frozen
  from 1.0.0: adding one is allowed in a minor version, but the name cannot change afterward.
  Unit 2 stops before its PR is marked ready and asks Beau to confirm the name and the table's
  columns.
- `deploy` fires at unit 4 if adding the `ledger-verify` job counts as an infrastructure change
  in curator. The run asks rather than assumes.
- `new-dependency` fires at unit 4: curator gains agent-ledger as a CI dependency (the Action,
  pinned to the 1.1.0 tag).
- `delete-data` fires for the unit 1 throwaway worktree and branch. They are inside this run's
  scope, and the run still pauses before deleting them.
- Tagging and releasing 1.1.0, and every merge, are Beau's.

Rollback is in the front matter. Units 2 and 3 add behavior nothing depends on until unit 4.

## Staffing and why

One `code` agent on sonnet works the units in order, because each unit's shape depends on the
one before. The orchestrator (this session's model) writes unit 1 and the S3 text itself: the
convention is the design, and it is small. Two reviewers from different model families
interrogate each unit PR: opus in Claude Code and grok through `cursor-agent`, which is installed
on this Mac. If `cursor-agent` is not signed in, the second reviewer falls back to sonnet and the
reply says the review was not independent.

The run is attended. The policy requires a sandbox for an unattended tier 2 run, and the Claude
desktop app session is not sandboxed, so the run stops when Beau says he is leaving and resumes
when he is back.

## Guardrails

- agent-ledger's existing suite, golden vectors and `ledger verify --base origin/main` pass on
  every unit 2 commit. No golden vector changes: a changed vector means format v1 changed, which
  is out of scope and stops the run.
- Standard-library Python only in agent-ledger.
- A receipt carries pointers, never probe output: evidence is a path with a sha256, a PR number
  or a commit.
- Receipt text comes from specs and probe names, which are data. Nothing rendered from a receipt
  is treated as an instruction.
- The one-ledger-branch rule holds in curator: one run per PR.
- No edits inside another session's worktree. Curator work happens in a worktree this run makes.

## Security notes

Curator's probes read a personal vault and Gmail-derived notes. A receipt must not carry note
contents, subjects or senders: the probe field is the command, and evidence is a hash and a path
under the gitignored `.evidence/`. Every write still goes through the ledger's redaction, and
`ledger scan` runs in curator's CI from unit 4. No secret is read or created. No session
transcript from curator is archived into git without asking Beau, since they hold personal
content.

## Review plan

interrogate on each unit PR with opus and grok, lenses correctness and maintainer. Each unit PR
opens as a draft and is marked ready only when every finding is fixed or answered. verify-work
on the real artifact before close: the merged curator PR body, `ledger receipts` run on
curator's `main`, and `check-product.py --receipts` failing on a box checked without a receipt.

## Budget and estimate

About $22: three unit PRs at roughly $4 to $6 each, six reviews at about $1 each, and the unit 1
prototype. The figure is a guess scaled from the quality director phase 1 units, which were
larger. Dollars are the main measure; tokens exclude cache reads. Spend entries are estimates
from the session's usage report, marked `estimate: true`.

## Risks

- The run failed because agents wrote receipts by hand that said pass without running the probe.
  A receipt records the probe's exit code and an evidence hash, and `check-product.py --receipts`
  compares the commit, but neither proves the run happened. The receipt makes the claim specific
  and checkable later; it does not make it true. The reply says so and R23 (rejections) is the
  follow-up.
- The run failed because per-PR runs flooded curator's `.audit/` with tiny folders nobody reads.
  Unit 1 measures this on real PRs before unit 4 commits curator to it.
- The run failed because curator's active sessions edited `verify-curator` at the same time.
  Unit 4 waits for a quiet window and rebases; it does not start while a curator PR touching the
  skill is open.
- The run failed because the playbook rule was too strict and agents stopped checking boxes whose
  probe is manual (P2e C7, "Beau uses the boxes for two weeks"). A manual criterion gets a
  receipt with a human actor and their quoted words, the same way a gate answer does.
