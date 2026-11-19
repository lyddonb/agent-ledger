# Seal

When a run is done, `ledger seal <run-id>` appends a `seal` entry that names the commit holding the run's ledger, and adds the run to `.audit/INDEX.jsonl`. It refuses when the ledger has uncommitted changes. Sealing again does nothing, a sealed run takes no more entries, and `ledger verify` counts sealed and indexed runs.

## Sub-features

- `seal-uncommitted`: seal refuses with exit 2 and writes no `INDEX.jsonl` while the ledger isn't committed at HEAD.
- `seal-new`: seal on a committed run appends a seal entry naming HEAD, and writes one `INDEX.jsonl` line.
- `seal-again`: a second seal exits 0 and says `nothing to do`.
- `seal-closed`: append to a sealed run exits 2 and says it is sealed.
- `seal-verify`: `verify` reports `1 sealed, 1 indexed`, and `verify --base HEAD` passes.

## How to get to it (user POV)

- `ledger seal <run-id> --actor '<JSON>'` from any shared entry point. The drive uses `ci/run_ledger.py`.
- agent-kit's show-me-your-work skill seals a governed run at the end. Not driven here.
- The `ledger-verify` Action checks seals on every PR. Not driven here.

## Driving it with control-ledger

Preconditions:

- A fresh workspace from `$C launch`, with `$C doctor` exiting 0. `launch` makes one commit, so HEAD exists.
- No run ending in `-seal`.

- **Start a run and record one entry.** `init seal`, then `append --type state --summary "Unit 1 merged"`. Both exit 0.
- **Seal too early.** `seal <run-id> --actor '<agent JSON>'`. Exit 2, stderr says to commit the ledger, and there is no `.audit/INDEX.jsonl`.
- **Commit.** In the workspace repo, `git add -A .audit && git commit`. (The drive runs git with no global config and signing off.)
- **Seal.** Same command. Exit 0, stdout starts `sealed <run-id>`, the last entry is `type: seal` with `data.commit` equal to HEAD, and `INDEX.jsonl` has one line naming the run.
- **Seal again.** Exit 0, and stdout says `nothing to do`.
- **Append after the seal.** `append --type state`. Exit 2, and stderr says the run is sealed.
- **Commit the seal, then verify.** `git add -A .audit && git commit`, then `verify`. Exit 0, with `1 sealed, 1 indexed` in stdout. `verify --base HEAD` also exits 0.

## Gotchas

- In a repo with no commits at all, seal fails with a bare git error (`git rev-parse --verify --quiet HEAD^{commit}:` and nothing after it), not the "commit the ledger" message. `launch` makes a first commit so the drive tests the documented refusal.
- Seal compares the ledger on disk with the one at HEAD byte for byte. Any write after the commit, including from another feature's run, has to be committed first.
- `INDEX.jsonl` is itself a chain. Editing it by hand fails every later seal and `verify`.
