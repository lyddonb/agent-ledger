# agent-ledger

A tamper-evident record of what AI agents did in your repo, and why.

agent-ledger is the runtime behind **governed agent runs**. Before a large run starts, a person approves its charter (goal, gates, budget). As the run proceeds, agents write a hash-chained ledger of their decisions, gate answers, verification results and spend. Session transcripts are archived beside it, redacted. CI verifies every chain on every PR, so an auditor can reconstruct how any merged change was approved, built, tested and reviewed, without trusting the agent that wrote it.

> **Status: v1.1.0.** 1.1.0 adds receipts. M0 is done: a real tier 2 run used every part of it. `ledger init`, `ledger append` and `ledger show` write and read a run's chain, and `ledger verify` checks every run, the index and the seals (`--base origin/main` adds the append-only check against main). Every write is redacted, `ledger session add` archives redacted transcripts, `ledger seal` closes a run and indexes it, `ledger spend` checks recorded spend against the charter budget, `ledger receipts` prints a run's verification receipts (which probe ran on which commit, and what came back) as a Markdown table for a PR body, and `ledger charter new|check|approve` write, check and approve a run's charter. `ledger import-tsv` (added after 1.1.0, so not in that release) copies an old `decisions.tsv` trail into a run. `ledger scan` looks for secrets in `.audit`, and the `ledger-verify` Action (`action.yml`) runs verify and scan on every PR. Format v1 ([S3](docs/specs/S3-audit-ledger.md)) and the command names are frozen: a format change needs format v2, and renaming or removing a command needs a new major version. Flags, output text and conventions inside `data` can still change. See [the roadmap](ROADMAP.md) and the [M0 charter](.audit/runs/2026-10-18-m0-foundation/charter.md).

## How it fits with agent-kit and agentfiles

| Repo | Job | In this system |
|---|---|---|
| [agent-kit](https://github.com/lyddonb/agent-kit) | Content: skills, playbooks, principles | Tells agents when to write a charter, log a decision or stop at a gate |
| [agentfiles](https://github.com/lyddonb/agentfiles) | Configuration: installs everything into each tool | Writes `~/.agents/policy.yaml` (gates, budgets, rules) and installs this CLI |
| **agent-ledger** (this repo) | Runtime: the records and the checks | The `ledger` CLI, storage sinks, redaction, the `ledger-verify` CI action, and later Harness lab |

Why a separate repo: the ledger format is a promise about records you keep for years, so it needs its own versioning, and CI or an auditor must be able to verify chains without installing any agent skills.

## What it will do

Planned commands (specified in [S3](docs/specs/S3-audit-ledger.md) and [S1](docs/specs/S1-run-charter.md)):

```text
ledger init <slug> --actor ...    start a run: folder + first entry
ledger charter new|check|approve   write, validate and approve the run charter
ledger append --run ... --actor ... --type ... --summary ... --why ... [--evidence ...] [--data ...]
ledger session add <transcript>    redact, compress, archive, hash into the chain
ledger verify [--base origin/main] chains intact, ledgers only grew, seals point at real commits
ledger show <run-id>               readable table of a run
ledger seal <run-id>               close the chain and anchor it in .audit/INDEX.jsonl
ledger spend <run-id>              spend entries added up against the charter budget; exit 1 at a limit
ledger receipts <run-id>           verification receipts as a Markdown table, latest per criterion
ledger import-tsv <file> --actor ... --record-actor ...   an old decisions.tsv as a new run
```

### Bringing in an old `decisions.tsv`

Before the ledger, show-me-your-work kept decisions in a tab-separated file. `ledger import-tsv` copies one into a new run, so the old decisions sit in `.audit/runs/` with the rest. Each record becomes a `decision` entry marked `data.imported`, and `ledger show` tags it `[imported]`. The entry's `ts` is the time of the import. The time the record gives is kept in `data.imported.source_ts`.

Run it from the repo root. `--actor` is who runs the import. `--record-actor` is who wrote the records: the file doesn't say, so you do, with `unknown` for a part nobody knows.

```bash
ledger import-tsv .evidence/decisions.tsv --actor '{"kind":"human","id":"lyddonb","on_behalf_of":"lyddonb@github"}' --record-actor '{"kind":"agent","id":"claude@claude-code#unknown","on_behalf_of":"lyddonb@github","tool":"claude-code","model":"unknown","session":"unknown"}'
```

The run is named `<today>-imported-decisions`; `--slug` and `--date` change that. If any record can't be read, the command lists every bad line and writes nothing. A file is imported once: a second import of the same bytes is refused. Every cell is redacted as `ledger append` redacts. [S3, "Migration"](docs/specs/S3-audit-ledger.md#migration) has the column names and the rules.

In a project, the records live under `.audit/`:

```text
.audit/
  INDEX.jsonl                       one seal per merged run, itself chained
  runs/<run-id>/
    charter.md                      what was approved
    ledger.jsonl                    the run's hash chain
    sessions/<session-id>.jsonl.gz  redacted transcripts
```

## Principles

- **Standard-library Python only.** An auditor should be able to read and run the verifier with nothing else installed.
- **Tamper-evident, not "immutable".** A hash chain makes any rewrite detectable; protected branches and CI make it hard. Write-once external storage plugs in through a sink later.
- **Pointers, not payloads.** Ledger entries cite commits, PRs, files and hashes. Secrets and customer data never enter the ledger.
- **A human approves; agents act inside that approval.** Every entry names the agent and the person it acts for.
- **SOC 2 change management is the baseline.** Where a run can't meet it, its charter says so.

## Docs

- [docs/specs/](docs/specs/): S1 run charter, S2 policy and identity, S3 ledger and sessions (drafts).
- [ROADMAP.md](ROADMAP.md): milestones M0 to M4 for this repo.
- [HUMAN_TASKS.md](HUMAN_TASKS.md): what needs you.
- [IDEAS.md](IDEAS.md): maybe-laters.

The full plan (PRD, roadmap and all six specs) is in the "Governed agent runs" planning doc. Until a spec is approved, that doc is canonical and the copy here is a draft.

## License

MIT. See [LICENSE](LICENSE).
