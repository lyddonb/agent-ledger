# Ideas

Not planned; worth remembering.

- **`ledger explain <commit>`**: given a merged commit, print the charter, approvals, gates, reviews and verification that led to it. The auditor's one-liner.
- **Ledger viewer**: a static HTML page generated from `.audit/` for people who won't read JSONL.
- **Signed entries**: sign each seal with the committer's SSH key, so an entry proves who wrote it, not only that it wasn't changed.
- **Cost dashboard**: spend per run, per tier and per tool over time, from `spend` entries.
- **Ledger for non-agent work**: the same chain for human-only changes that need an audit trail.

## From "Steering AI Beyond Automation" (2026-11-15)

Context and reasoning: [docs/notes/steering-beyond-automation.md](docs/notes/steering-beyond-automation.md).

- **Human-initiated steering (`steer` entries)**: today the human starts a run, answers gates the agent raises, and merges, with no first-class way to redirect mid-run. Add a `steer` entry type, a way to post one (`ledger steer <run-id>` or `steer.md` in the run folder), and a playbook step in agent-kit that checks for steers between units. Check whether it fits format v1.
- **Collaboration metrics (`ledger stats`)**: gates per unit, gate-answer latency, human overrides, reversed decisions and steers per run, so we can see whether runs need less human time. Could feed the weekly brief.
- **`disagreement` entries**: when the agent pushes back on the human (beau-mode's "evidence rules out the change"), record the agent's position, the human's call and, later, which was right, to calibrate how deferential agents should be.

## From "intent over steps" (2026-11-16)

Context and reasoning: [docs/notes/intent-over-steps.md](docs/notes/intent-over-steps.md). agent-kit's roadmap items R21 and R23 depend on these.

- **Criterion receipts**: shipped in 1.1.0 (S3, "Receipt entries").
- **`rejection` entries**: when the human rejects work whose checks passed, record what was rejected, the missing criterion or probe, and the criterion added. `reflect` and `correct` in agent-kit read them across sessions. A new entry type, so check it against format v1 like `steer` and `disagreement`.
- **`flag` as its own entry type**: flags start as `lesson` entries with `data.kind: "flag"` (Flags unit 1). When `rejection` forces format v2, promote `flag` to its own type in the same bump, so verify can check its keys.
- **`ledger flag`**: one command that finds this week's `flags-<machine>` run, starts it when there is none, and appends the flag. Flags unit 1 tried plain `ledger append` and kept it: the append is one command, and the awkward part is finding the run (a few lines of shell in S3), which a `--flag` shorthand on `append` wouldn't remove. Prioritize when agents following agent-kit R45 get the lookup wrong. It is a new command, so the `public-api` gate.
- **Fix proofs**: a verification entry that cites the probe failing on the base commit and passing on the fix commit, so "the fix counts only when the old code fails" is checkable from the ledger.


## From agent-kit's R32 run reflect (2026-11-27)

- **`charter approve` checks the human actor**: run `2026-11-27-r32-hook-gates` recorded its approval with a bare login as the id. `charter approve` records an approval once and refuses a second, so the slip stood. Have it read `identity.human` from the policy and refuse an actor whose `on_behalf_of` isn't `identity.human` or whose id isn't that value without `@github`, or add a `--dry-run` that prints the entry it would append. Changing what the command accepts is the `public-api` gate.
- **The tests fail fast on an old Python**: the system `python3` on Beau's Mac is 3.9, and the suite under it gave one confusing error before a rerun under `uv run --python 3.12` passed. A check at the top of the test entry point (and the Makefile or CI target) that exits with "needs Python 3.10 or newer; run it with uv run --python 3.12" would make the cause plain.
