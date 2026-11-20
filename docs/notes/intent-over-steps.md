# Notes: intent over steps

Source: a thread (shared by Beau, 2026-11-16) describing a personal agent system that spends most of its effort refining intent rather than steps. It replaces CLAUDE.md and AGENTS.md with a constitution, a preferences file and a life-goals file. Work runs against a single "Ideal State Artifact" of testable criteria. One eval system proves the work, and a loop of logged complaints, experiments and replay improves the system. These notes come from the thread as pasted, not from the author's code.

We are not dropping AGENTS.md or CLAUDE.md. The useful idea is what goes in them: intent and outcomes, with steps only where they enforce a gate. AGENTS.md stays as the read-order router, CLAUDE.md stays `@AGENTS.md`, and agentfiles keeps writing one generated block that points at beau-mode.

## How the thread maps onto agent-kit, agentfiles and agent-ledger

| Thread | What we have | Status |
|---|---|---|
| Constitution (philosophy, hard rules) | `principles` plugin, beau-mode | In place |
| Preferences (outcomes, not steps) | agentfiles profile `instructions:` | In place; audit wording (agentfiles IDEAS) |
| TELOS (life goals at the top) | `docs/VISION.md` per product, whose goals table has Measure, Lever and First move | **Done in bstack 0.22.0** for product goals (was agent-kit R24). Life goals stay in the PKM vault (agent-kit IDEAS) |
| Ideal State Artifact, three levels, criteria with probes, anti-criteria, outlives every run | Phase specs | **Done in bstack 0.11.0** |
| Ask open questions when direction is fuzzy | Product phase playbook, `grilling` | In place |
| Hooks block code changes when the spec wasn't read or updated | Nothing | agent-kit R22 (CI first, then a hook) |
| The Algorithm, effort scales with the task | beau-mode playbooks, tiers 1 to 3 | In place |
| Hardest thinking to the smartest model | `roles.yaml` (`judgment`, `code`) | In place |
| The builder never grades its own work; another vendor reviews | `interrogate`, `arena`, cross-model reviewers | In place |
| A bug that survives two fixes gets a fresh diagnosis | Bug fix playbook: two fixes that didn't hold go back to the symptom with `root-cause`; three failed rounds on one check go to a person | Shipped in bstack 0.21.0. Not yet seen on a real case: agent-kit R26 |
| One eval system; every criterion maps to an eval; receipts | `verify-work`, verify-APP skills, qualitydirector, ledger entries | Receipts per criterion: agent-kit R21 and "Criterion receipts" here |
| Test in a real browser; "should work" is banned | `verify-work`, `create-verification-skill` | In place; a lint for unverified claims is in agent-kit IDEAS |
| ERRATA: log complaints; a rejected-but-passing result names the missing check | `reflect`, `correct`; the Product phase playbook now does this inside one spec | Cross-session log: agent-kit R23 and "`rejection` entries" here |
| HILLCLIMB: improve below-target numbers by experiment | Hillclimb playbook planned (agent-kit R17), quality and CFO books | agent-kit IDEAS |
| REPLAY: one record of everything | The ledger and archived transcripts | In place |
| A fix counts only when the old code fails and the new code passes | `correct` proves each check against a real past mistake; Product phase runs probes before and after | In place |
| Remove the cause instead of adding a rule | `correct` (architecture, then types, lint, tests, docs last), `encode-lessons-in-structure` | In place |
| WORKFLOWS on a schedule | Scheduled tasks (CFO brief, quality brief) | In place |
| VIGIL decides text, digest or nothing | Nothing | agent-kit IDEAS (notifier) |
| CENSUS: everything registered, one owner per job | `agentfiles.lock`, `watched.csv`, `identity.scheduled_owner` | Spread out; agentfiles IDEAS |
| Skills describe ideal states, not procedures | Playbooks are deliberately procedural | Audit with evidence: agent-kit R25 |
| Hooks enforce what prose can't | Policy gates are prose plus `ledger` checks | agentfiles IDEAS (gates as hooks) |
| Permissions in one table | `~/.agents/policy.yaml` gates | In place |
| Outside content is data | Security rules, redaction | In place |

## Where we disagree with the thread

- **Deleting instruction files.** The router file is cheap and every tool reads it. Keep it thin instead.
- **Every skill as an ideal state.** Our playbooks carry gates that weaker models in other tools would skip. Cut steps only with evidence (agent-kit R25).
- **One new eval system.** A shared receipt format over the verifiers we have (R21) gets the same property without rebuilding them.
- **Life goals in the dev kit.** They belong in the PKM vault. A pointer at most, from a private profile.
- **"Euphoric surprise" as the summit.** Useful as level 3 to aim at, but it can't be confirmed in advance, so it is never a gate.
