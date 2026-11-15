"""`ledger spend`: a run's spend entries, added up, against its charter budget."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from helpers import T0  # also puts src on the path
from test_cli import run_cli

from agent_ledger.entry import AgentActor, EntryType, HumanActor, next_entry
from agent_ledger.run import append, init_run, repo_sink
from agent_ledger.spend import total_spend, unrecorded_spend

RUN = "2026-11-14-budget"
AGENT = AgentActor("beau-agent@claude-code#70d5", "lyddonb@github", "claude-code", "opus", "70d5ca84")
CHARTER = "---\nbudget: {usd: 10, tokens: 1000, wall_clock: 1h, max_parallel_agents: 1}\n---\n"


class SpendCliTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        self.sink = repo_sink(self.root)
        init_run(self.sink, RUN, actor=HumanActor("lyddonb", "lyddonb@github"), why="test")
        (self.sink.run_dir(RUN) / "charter.md").write_text(CHARTER)

    def spend(self, **data):
        append(self.sink, RUN, actor=AGENT, type=EntryType.SPEND, summary="unit spend", why="w", data=data)

    def check(self):
        return run_cli("spend", RUN, "--root", self.root)

    def test_entries_add_up_and_under_budget_exits_0_with_what_is_left(self):
        self.spend(usd_micros=2_500_000, tokens=100, estimate=True)
        self.spend(usd_micros=1_250_000, tokens=50)
        code, out, err = self.check()
        self.assertEqual(code, 0, out + err)
        self.assertIn("2 spend entries, 1 estimated", out)
        self.assertIn("usd    $3.75 used of $10.00, $6.25 left", out)
        self.assertIn("tokens 150 used of 1,000, 850 left", out)

    def test_exactly_at_the_usd_limit_exits_1(self):
        self.spend(usd_micros=4_000_000)
        self.spend(usd_micros=6_000_000)
        code, out, _ = self.check()
        self.assertEqual(code, 1, out)
        self.assertIn("usd    $10.00 used of $10.00: $0.00 over", out)
        self.assertIn("LIMIT REACHED: usd", out)

    def test_over_the_token_limit_alone_exits_1(self):
        self.spend(usd_micros=1, tokens=600)
        self.spend(usd_micros=1, tokens=600)
        code, out, _ = self.check()
        self.assertEqual(code, 1, out)
        self.assertIn("tokens 1,200 used of 1,000: 200 over", out)
        self.assertIn("LIMIT REACHED: tokens", out)
        self.assertNotIn("usd and", out)

    def test_no_charter_reports_totals_and_exits_0(self):
        (self.sink.run_dir(RUN) / "charter.md").unlink()
        self.spend(usd_micros=99_000_000)
        code, out, _ = self.check()
        self.assertEqual(code, 0, out)
        self.assertIn("$99.00 used; no limit in the charter", out)
        self.assertIn("no charter.md", out)

    def test_a_broken_chain_exits_1_without_a_total(self):
        self.spend(usd_micros=1)
        ledger = self.sink.run_dir(RUN) / "ledger.jsonl"
        ledger.write_bytes(ledger.read_bytes().replace(b"unit spend", b"unit spenT"))
        code, out, err = self.check()
        self.assertEqual(code, 1)
        self.assertIn("fails verify", err)
        self.assertNotIn("used", out)

    def test_an_unreadable_charter_exits_2(self):
        (self.sink.run_dir(RUN) / "charter.md").write_text("no front matter\n")
        code, _, err = self.check()
        self.assertEqual(code, 2)
        self.assertIn("front matter", err)


class SpendModelTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        self.sink = repo_sink(self.root)
        init_run(self.sink, RUN, actor=HumanActor("lyddonb", "lyddonb@github"), why="test")

    def add(self, t, **data):
        append(self.sink, RUN, actor=AGENT, type=t, summary=t.value, why="w", data=data)
        return self.sink.read(RUN).entries

    def test_entries_written_before_the_rule_are_skipped_by_seq(self):
        # append refuses this shape now; next_entry builds it as the M0 run did (its seq 19).
        entries = self.add(EntryType.SPEND, usd_micros=5)
        old = next_entry(entries[-1].head, now=T0, actor=AGENT, type=EntryType.SPEND, summary="fix",
                         why="w", data={"corrects": [2]})
        total = total_spend([*entries, old])
        self.assertEqual((total.usd_micros, total.entries, total.skipped), (5, 1, (old.seq,)))

    def test_unrecorded_spend_names_the_last_verification_without_spend_after_it(self):
        self.assertIsNone(unrecorded_spend(self.sink.read(RUN).entries), "no verification yet")
        entries = self.add(EntryType.VERIFICATION)
        self.assertEqual(unrecorded_spend(entries).seq, 2)
        self.assertIsNone(unrecorded_spend(self.add(EntryType.SPEND, usd_micros=1)))
        self.assertEqual(unrecorded_spend(self.add(EntryType.VERIFICATION)).seq, 4)


if __name__ == "__main__":
    unittest.main()
