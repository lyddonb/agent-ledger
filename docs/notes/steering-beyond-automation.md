# Notes: "Steering AI Beyond Automation"

Source: Arvind Narayanan and Unger, [Steering AI Beyond Automation](https://www.normaltech.ai/p/steering-ai-beyond-automation), *AI as Normal Technology* (normaltech.ai), read 2026-11-15.
These notes come from a summary of the article, not a close reading of the full text. Check the original before quoting it.

## The article in brief

The authors argue that most AI work aims at full automation because of incentives, benchmark convenience and CS culture, not because automation is inevitable. They propose a research agenda for human-AI collaboration with four problems:

1. **White-box agents.** Expose the intermediate process (sources, assumptions, methods, uncertainties, artifacts) so people can inspect it and interact with it. Most fields lack the process traces that code has in its repositories.
2. **Low latency.** Users choose between fast, shallow chatbots and slow, thorough agents. Supervising slow agents breaks flow; a fast front model that hands deep work to a background model could restore it.
3. **Continuous collaboration.** "Human in the loop" usually means a human who starts the work and checks it. Real collaboration needs shared state, interruptions, parallel work and mid-task steering.
4. **Evaluate the collaboration.** Benchmarks measure autonomous performance. Open questions: how much the AI should disagree, how deferential it should be, how to measure team success over time. Simulated users may reward the wrong behavior.

Other points that bear on us: a trilemma for agent deployment (fully autonomous, reliable in high-stakes settings, general-purpose: pick two); oversight must be effective, not rubber-stamping; AI should help people build skill (upskilling); ambient agents raise privacy and discretion questions.

## How it maps onto agent-kit, agentfiles and agent-ledger

**Already strong**

- *White-box:* ledger entries carry what, why, evidence and result; transcripts are archived and redacted; `ledger show` reads a run. Gap: the human reads it after the run, not during it.
- *Effective oversight:* charter approval by hash, the human's own words in `gate-answer`, `--typed-by-me` versus relayed approvals.
- *Trilemma:* the tiers already trade autonomy for reliability at tier 3. Worth saying so in the Large work item playbook.

**Gaps, in priority order**

1. **Human-initiated steering mid-run.** Today the human starts a run (charter), answers gates the agent raises, and merges. There is no first-class way to redirect a run. Idea: a `steer` entry type, a way for the human to post one (`ledger steer <run-id>` or a `steer.md` in the run folder), and a playbook step that checks for steers between units. Check whether a new entry type fits format v1 or needs v2.
2. **Collaboration metrics.** The ledger already holds gates per unit, gate-answer latency, overrides, reversed decisions and (with 1) steers. A `ledger stats` command or a section in the weekly brief would show whether runs need less human time. M3 Harness lab should score human cost (interventions, review minutes) alongside pass rate, or it will optimize for autonomy. Be wary of simulated users.
3. **Disagreement entries.** beau-mode already stops when evidence rules out the requested change, but those moments aren't recorded. A `disagreement` entry (agent's position, human's call, later outcome) would let us calibrate deference from evidence.
4. **An `interactive` role for attended work** (lands in agentfiles and agent-kit). A fast model talks to the human while `code` and `judgment` subagents do the deep work in the background, for tier 1 work while the human is present. This is the kind of real conflict D11 asks for before adding a role: latency against depth.
5. **Upskilling.** The ledger and transcripts are exactly the process traces the article says most fields lack. Promote `ledger explain <commit>`, and consider an optional "teach me what changed" step at seal time that runs agent-kit's `teach` skill over the ledger.

Caution: the "Dr Eggbot" maintenance idea in agent-kit is what the article calls an ambient agent. The `outbound-message` gate covers part of the privacy risk; it would need its own charter.
