# Receipts

A receipt is a `verification` entry that records which probe ran for a spec criterion, on which commit, and what came back. `ledger append` refuses a receipt that breaks the rules, such as VERIFIED with a failing exit code. `ledger receipts <run-id>` prints a Markdown table for a PR body, with one row per criterion in natural id order, showing its latest verdict and how many earlier receipts it has.

## Sub-features

- `receipts-empty`: a run with no receipts prints `<run-id>: no receipts`.
- `receipts-append`: a receipt in `--data` (`verdict` plus `receipt` with criterion, spec, spec_sha256, probe, commit and exit) is accepted.
- `receipts-refuse`: VERIFIED with exit 1 is refused with exit 2, and the message says to record it as NOT VERIFIED.
- `receipts-table`: stdout is only the table, with rows in natural order (`P2-C1` before `P10-C1`), the latest verdict per criterion, `1 earlier` on a criterion recorded twice, and the evidence pointer.

## How to get to it (user POV)

- `ledger append --type verification --data '<receipt JSON>'`. The drive uses this.
- agent-kit's receipt.py writes receipts for spec probes in governed runs. Not driven here.
- `ledger receipts <run-id>`, pasted into a PR's `## Verification` by the Opening a PR playbook. The drive uses this.

## Driving it with control-ledger

Preconditions:

- A fresh workspace from `$C launch`, with `$C doctor` exiting 0.
- No run ending in `-receipts`.

- **Start a run.** `init receipts`. Exit 0.
- **No receipts yet.** `receipts <run-id>`. Exit 0, and stdout is exactly `<run-id>: no receipts`.
- **Record three receipts.** Append, in this order: `P10-C1` VERIFIED exit 0 with `--evidence evidence/p10.txt`, `P2-C1` NOT VERIFIED exit 1, then `P2-C1` VERIFIED exit 0. Each uses `spec docs/phases/verify.md`, a 64-hex `spec_sha256`, and a 40-hex commit. Each exits 0.
- **Refuse a bad one.** Append `P2-C2` VERIFIED with exit 1. Exit 2, stderr mentions `NOT VERIFIED`, and the ledger line count is unchanged.
- **Print the table.** `receipts <run-id>`. Exit 0. Every non-empty stdout line starts with `|`. The data rows are `P2-C1` then `P10-C1`. The `P2-C1` row says `VERIFIED` and `1 earlier`, and the `P10-C1` row shows `evidence/p10.txt`.

## Gotchas

- A criterion id has no leading zero (`C8`, not `C08`), or append refuses it.
- Notes about receipts that break the rules go to stderr, because a line straight after the table would render as another row. Check stdout and stderr separately.
- Sorting is natural in the prefix and the number (`P2` before `P10`, `C2` before `C10`). A plain string sort gives the wrong order, which was a real bug, fixed in 95c8b1e.
