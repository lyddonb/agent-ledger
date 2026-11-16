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
