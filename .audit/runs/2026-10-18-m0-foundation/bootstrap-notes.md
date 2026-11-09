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

## 2. decision

| Field | Value |
|---|---|
| ts | 2026-11-09T03:08:33Z |
| run_id | 2026-10-18-m0-foundation |
| actor | `{kind: agent, id: claude@claude-code#883d, on_behalf_of: lyddonb@github, tool: claude-code, model: opus, session: 883d1e36}` |
| type | decision |
| summary | Unit 1 design: base on arena candidate 1 (opus), trimmed; the sonnet judge picked the same base |
| why | It keeps an Entry from ever holding a stale hash, makes `next_entry` the only way to mint an entry, and links lines on stored seq and hash so problems don't cascade. Its id-format, length-cap and line-size rules were dropped as unrequired by S2 or S3 |
| evidence | Arena sketches and judge scores in the session scratchpad (not committed); `src/agent_ledger/entry.py`, `src/agent_ledger/verify.py` |
| data | `{candidates: [opus, sonnet], judge: sonnet, base: candidate-1, grafted: [attack-to-finding table, real-calendar ts check]}` |

## 3. decision

| Field | Value |
|---|---|
| ts | 2026-11-09T03:08:34Z |
| run_id | 2026-10-18-m0-foundation |
| actor | `{kind: agent, id: claude@claude-code#883d, on_behalf_of: lyddonb@github, tool: claude-code, model: opus, session: 883d1e36}` |
| type | decision |
| summary | Canonical form is RFC 8785 (JCS) restricted to integers within ±(2^53−1) and keys matching `[a-z][a-z0-9_]{0,63}`, raw UTF-8 |
| why | An auditor can check hashes with an off-the-shelf JCS library; with no floats and ASCII keys, JCS has no ambiguous cases. Pure-ASCII output (the sonnet candidate) was rejected because no standard defines it |
| evidence | `docs/specs/S3-audit-ledger.md` "Format v1: canonical form"; `tests/vectors/canonical.json`; golden head `2465b40d…25da` reproduced by an independent Node verifier |
| data | `{gate: public-api, frozen: false}` |

## 4. decision

| Field | Value |
|---|---|
| ts | 2026-11-09T03:08:35Z |
| run_id | 2026-10-18-m0-foundation |
| actor | `{kind: agent, id: claude@claude-code#883d, on_behalf_of: lyddonb@github, tool: claude-code, model: opus, session: 883d1e36}` |
| type | decision |
| summary | A line's bytes must equal the canonical encoding of what it parses to; verify never normalizes before hashing |
| why | Without this, a whitespace or escape edit re-canonicalizes to the same hash and "any changed byte is detected" would be false |
| evidence | `src/agent_ledger/_canon.py` `parse_canonical`; `tests/test_verify_properties.py` `test_any_changed_byte_is_named` |
| data | `{}` |

## 5. decision

| Field | Value |
|---|---|
| ts | 2026-11-09T03:08:36Z |
| run_id | 2026-10-18-m0-foundation |
| actor | `{kind: agent, id: claude@claude-code#883d, on_behalf_of: lyddonb@github, tool: claude-code, model: opus, session: 883d1e36}` |
| type | decision |
| summary | Unit 1 ships `ledger verify RUN_FOLDER` with exit codes 0 verified, 1 failed, 2 usage or read error, and lists every problem as `path:line: kind: message` |
| why | The task scopes verify to one run folder; the no-argument and `--base` forms belong to unit 4. A missing ledger in a run folder counts as failed, not as an I/O error |
| evidence | `src/agent_ledger/cli.py`; `tests/test_cli.py` |
| data | `{max_problems: 50}` |

## 6. decision

| Field | Value |
|---|---|
| ts | 2026-11-09T03:08:37Z |
| run_id | 2026-10-18-m0-foundation |
| actor | `{kind: agent, id: claude@claude-code#883d, on_behalf_of: lyddonb@github, tool: claude-code, model: opus, session: 883d1e36}` |
| type | decision |
| summary | Chain-only verify does not detect entries cut from the end or a suffix rewritten with recomputed hashes; this is stated in the spec, the CLI output and a test |
| why | The hash has no secret, so a prefix of a valid chain is valid. Seals and `verify --base` (unit 4) anchor the head in git |
| evidence | `tests/test_verify_properties.py` `test_deleting_the_last_line_is_a_documented_blind_spot`; S3 "What `ledger verify` detects" |
| data | `{closes_in: unit-4}` |

## 7. decision

| Field | Value |
|---|---|
| ts | 2026-11-09T03:08:38Z |
| run_id | 2026-10-18-m0-foundation |
| actor | `{kind: agent, id: claude@claude-code#883d, on_behalf_of: lyddonb@github, tool: claude-code, model: opus, session: 883d1e36}` |
| type | decision |
| summary | Format v1 has no floats, so S3's `spend` example `{usd, ...}` can't hold dollars as written; proposed `usd_micros`, left open for Beau |
| why | Float canonicalization differs across languages and would break cross-language verification. Per AGENTS.md the spec conflict is raised, not silently resolved |
| evidence | `docs/specs/S3-audit-ledger.md` "Open (spend amounts)" |
| data | `{needs: human-decision, before: public-api-gate}` |
