# Roadmap

The full plan, with all milestones and the reasoning, is in the "Governed agent runs" planning doc. This file tracks what lands in **this** repo.

| Milestone | This repo's part | Starts when |
|---|---|---|
| **M0. Foundation** (done: v1.0.0, 2026-11-15) | The `ledger` CLI (init, append, verify, show, seal, spend, session add, charter, import-tsv, scan), the repo sink and sink interface, redaction and the session archive, actor identity, the `ledger-verify` Action | You approve the [M0 charter](.audit/runs/2026-10-18-m0-foundation/charter.md) |
| **Receipts** (v1.1.0, 2026-11-16) | The receipt convention for `verification` entries and `ledger receipts`, from the run `2026-11-17-criterion-receipts` | Done |
| M1. Quality and security | Charter fields for the spec ladder and security lens; review entries | M0's gate passes |
| M2. Enforcement | `ledger charter render --audio/--video`; spend calibration from recorded runs | M1's gate passes |
| M3. Harness lab | A second package, `harness-lab`: test agent-setup changes on a project's own tasks (spec S6 in the plan doc). Score human cost (interventions, review time) as well as pass rate, so it doesn't optimize for autonomy alone ([notes](docs/notes/steering-beyond-automation.md)) | One app has a verification skill |
| M4. Collaboration and compliance | `s3-object-lock` and `external` sinks; role-based approvers | A teammate or an audit needs it |

## M0 units

See the charter's Plan section. In short: (1) entry model, hashing and verify; (2) init, append, show and the repo sink; (3) redaction and sessions; (4) seal and `verify --base`; (5) charter commands; (6) the Action. Units 7 and 8 land in agentfiles and agent-kit; unit 9 is the gate run.
