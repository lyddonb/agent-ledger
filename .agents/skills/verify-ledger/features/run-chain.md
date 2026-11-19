# Run chain

A user starts a run with `ledger init`, records entries with `ledger append`, reads them back with `ledger show`, and checks them with `ledger verify`. Each entry is chained to the one before it by hash, so a hand edit to any past line makes `verify` fail and point at the line, and `append` refuses to write to a chain that fails.

## Sub-features

- `chain-init`: `init <slug>` creates `.audit/runs/<date>-<slug>/ledger.jsonl` holding the genesis entry.
- `chain-append`: `append` adds one entry and prints its seq and hash.
- `chain-show`: `show <run-id>` prints a table, and a footer saying the chain verified.
- `chain-verify`: `verify <run folder>` checks one run, and `verify` checks every run, the index and the seals.
- `chain-tamper`: a byte changed in a past line fails verify, with the line number.
- `chain-refuse`: `append` on a broken chain exits 1 and writes nothing.

## How to get to it (user POV)

- `ledger init`, `ledger append`, `ledger show` and `ledger verify` from any shared entry point in the README. The drive uses `ci/run_ledger.py`.
- `ledger verify` also runs in the `ledger-verify` Action on every PR. Not driven here.
- agent-kit's show-me-your-work skill calls `ledger init` and `ledger append` for agents. Not driven here.

## Driving it with control-ledger

Preconditions:

- A fresh workspace from `$C launch`, with `$C doctor` exiting 0.
- No run ending in `-chain` under `.audit/runs/`.

- **Start a run.** Run `$C cli -- init chain --actor '<agent JSON>'`, or let `$C drive run-chain` do it. Exit 0, stdout `started <run-id>: ... seq 0 <hash>`, and `ledger.jsonl` has 1 line.
- **Append a decision.** `append --run <run-id> --actor '<agent JSON>' --type decision --summary "Chose the repo sink" --why ... --evidence README.md`. Exit 0, stdout `appended <run-id> seq 1 <hash>`, and `ledger.jsonl` has 2 lines.
- **Show it.** `show <run-id>`. Exit 0. The table has a `decision` row with the summary, and the footer reads `<run-id>: 2 entries, verified`.
- **Verify one run.** `verify .audit/runs/<run-id>`. Exit 0, and stdout starts `ok <run-id>: 2 entries`.
- **Verify the repo.** `verify`. Exit 0.
- **Tamper.** Edit `ledger.jsonl` by hand so line 2's summary says `Chose the s3 sink`. Then `verify .audit/runs/<run-id>` exits 1, prints `FAILED <run-id>`, and names `ledger.jsonl:2:`. `verify` with no folder also exits 1.
- **Append refuses.** `append` to the tampered run exits 1, stderr says `refusing: the ledger fails verify`, and `ledger.jsonl` is byte-for-byte unchanged.
- **Restore.** Write the original bytes back. `verify .audit/runs/<run-id>` exits 0 again.

## Gotchas

- The `ledger` on PATH may be an older pinned build. Use `control-ledger`, which runs the checkout's code.
- Without a `.git` above the working directory, `ledger` refuses to guess a root. Outside the workspace it would climb to the agent-ledger checkout and write to its real `.audit/`. `launch` gives the workspace its own `.git` for this reason.
- `init` refuses a run id that already exists. A second drive of the same feature needs a new workspace.
- `verify <folder>` is a chain-only check. It can't see lines cut from the end, so its stdout carries a note saying so. That note is expected, not a failure.
