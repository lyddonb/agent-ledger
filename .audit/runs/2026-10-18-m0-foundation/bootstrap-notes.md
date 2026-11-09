# Bootstrap notes: M0 Foundation

Units 1 and 2 build the ledger, so this run can't be recorded in it as it happens. Each entry below uses the S3 entry fields and is imported, marked as imported, when unit 2 lands (charter, SOC 2 gaps). Append only: never edit or reorder a past entry.

`charter.md` stays byte-for-byte as approved. The hash below covers that exact file. Any edit to the charter needs a new approval entry.

## 1. approval

| Field | Value |
|---|---|
| ts | 2026-11-09T02:51:37Z |
| run_id | 2026-10-18-m0-foundation |
| actor | `{kind: human, id: lyddonb}` |
| type | approval |
| summary | Charter approved as written, with no edits |
| why | Beau replied "Approved" in a Claude Code session after reviewing the charter, which is the approval route the charter names |
| evidence | `charter.md` sha256 `bb9611ab693eecb8a78362ea9b995317de475ced287e50dc00ca1e376ea68e6e`, as committed in 5853a01 |
| data | `{open_questions: "deferred: CLI install method to unit 7, gate-run project to unit 9"}` |
