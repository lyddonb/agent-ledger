# Ideas

Not planned; worth remembering.

- **`ledger explain <commit>`**: given a merged commit, print the charter, approvals, gates, reviews and verification that led to it. The auditor's one-liner.
- **Ledger viewer**: a static HTML page generated from `.audit/` for people who won't read JSONL.
- **Signed entries**: sign each seal with the committer's SSH key, so an entry proves who wrote it, not only that it wasn't changed.
- **Cost dashboard**: spend per run, per tier and per tool over time, from `spend` entries.
- **Ledger for non-agent work**: the same chain for human-only changes that need an audit trail.
