"""Run charters (S1): the front matter reader, the tier checks, and approval in the ledger."""
import json
import random
import shutil
import tempfile
import unittest
from pathlib import Path

from helpers import T0  # noqa: F401  (puts src on the path)
from test_cli import run_cli

from agent_ledger.charter import (DEFAULT_GATES, CharterError, approval_status, approve_charter,
                                  check_charter, new_charter, parse_front_matter, read_charter)
from agent_ledger.entry import AgentActor, EntryType, HumanActor
from agent_ledger.run import append, init_run, repo_sink
from agent_ledger.sink import LedgerError

ROOT = Path(__file__).resolve().parent.parent
M0 = ROOT / ".audit/runs/2026-10-18-m0-foundation/charter.md"
RUN = "2026-11-10-export-retries"
HUMAN = HumanActor("lyddonb", "lyddonb@github")
AGENT = AgentActor("claude@claude-code#70d5", "lyddonb@github", "claude-code", "opus", "70d5ca84")
SECTIONS = ["Goal", "Scope and non-scope", "Plan", "Gates and rollback", "Staffing and why", "Guardrails",
            "Security notes", "Review plan", "Budget and estimate", "Risks (pre-mortem)"]

FRONT = {
    "run_id": RUN,
    "title": '"Make export retries idempotent"',
    "tier": "2",
    "predicate": '"60k-row export writes 60,000 rows after a mid-run retry"',
    "scope": "[src/export/**, tests/export/**]",
    "budget": "{usd: 40, tokens: 6000000, wall_clock: 6h, max_parallel_agents: 4}",
    "estimate": "{usd: 25, tokens: 3500000, wall_clock: 3h, calls: {rollouts: 0, reviews: 4}}",
    "gates": "[" + ", ".join(DEFAULT_GATES) + "]   # all defaults",
    "staffing": "\n  - {role: code, tool: claude-code, model: sonnet, count: 2, isolation: worktree}"
                "\n  - {role: reviewers, tool: claude-code, model: [opus, sonnet], count: 2}",
    "review": "{lenses: [correctness, operability, maintainer], decision_review: false}",
    "spec_level": "property-tests",
    "rollback": '{migration: "expand then contract; old column kept until PR 3"}',
    "soc2": "{approved_before_build: true, tested: true, independent_review: true, recorded: true}",
}


def charter_text(front=None, sections=SECTIONS, drop=()):
    front = dict(FRONT, **(front or {}))
    lines = ["---"]
    for k, v in front.items():
        if k in drop:
            continue
        lines.append(f"{k}:{v}" if v.startswith("\n") else f"{k}: {v}")
    lines += ["---", "", "# Export retries", ""]
    for s in sections:
        lines += [f"## {s}", "", "Text.", ""]
    return "\n".join(lines)


class ReaderTest(unittest.TestCase):
    def test_the_real_m0_charter_reads_and_passes(self):
        c = read_charter(M0)
        self.assertEqual(c.fields["tier"], 2)
        self.assertEqual(c.fields["budget"], {"usd": 60, "tokens": 10000000, "wall_clock": "10h", "max_parallel_agents": 3})
        self.assertEqual(c.fields["scope"][0]["agent-ledger"][0], "src/**")
        self.assertEqual(c.fields["staffing"][1]["model"], "sonnet")
        self.assertEqual(c.fields["staffing"][2]["model"], ["opus", "sonnet"])
        self.assertIn("`ledger verify`", c.fields["predicate"])
        self.assertEqual(c.fields["soc2"]["recorded"], "partial")
        self.assertEqual(check_charter(c, "2026-10-18-m0-foundation"), [])

    def test_unsupported_yaml_is_refused_with_its_line(self):
        cases = {
            "anchor": "a: &x 1",
            "block scalar": "a: |",
            "nested block map": "a:\n  b: 1",
            "unquoted colon": "a: one: two",
            "indented key": " a: 1",
            "bad flow": "a: [1, 2",
            "trailing text": "a: [1] extra",
            "duplicate key": "a: 1\na: 2",
            "duplicate flow key": "a: {b: 1, b: 2}",
            "tab": "a:\t1",
        }
        for label, text in cases.items():
            with self.subTest(label):
                with self.assertRaises(CharterError) as cm:
                    parse_front_matter(text.split("\n"), 2)
                self.assertGreaterEqual(cm.exception.line, 2)

    def test_comments_and_quotes(self):
        fields, _ = parse_front_matter([
            'a: "has # hash" # comment',
            "b: 'it''s'",
            'c: [x, "y, z", {k: "v: w"}]',
            "d: true",
            "e: -3",
        ], 2)
        self.assertEqual(fields, {"a": "has # hash", "b": "it's", "c": ["x", "y, z", {"k": "v: w"}],
                                  "d": True, "e": -3})

    def test_random_values_round_trip(self):
        def gen(rng, depth=0):
            roll = rng.random()
            if depth > 2 or roll < 0.4:
                return rng.choice([rng.randint(-99, 10**9), True, False, None,
                                   "".join(rng.choice("abc xyz:#,[]{}'\\\"é/*-.") for _ in range(rng.randint(1, 12)))])
            if roll < 0.7:
                return [gen(rng, depth + 1) for _ in range(rng.randint(0, 3))]
            return {f"k{i}": gen(rng, depth + 1) for i in range(rng.randint(0, 3))}

        def dump(v):
            if isinstance(v, dict):
                return "{" + ", ".join(f"{k}: {dump(x)}" for k, x in v.items()) + "}"
            if isinstance(v, list):
                return "[" + ", ".join(dump(x) for x in v) + "]"
            if v is None:
                return "null"
            if isinstance(v, bool):
                return "true" if v else "false"
            if isinstance(v, int):
                return str(v)
            return json.dumps(v, ensure_ascii=False)

        for seed in range(300):
            rng = random.Random(seed)
            value = gen(rng)
            with self.subTest(seed=seed):
                fields, _ = parse_front_matter([f"v: {dump(value)}"], 2)
                self.assertEqual(fields["v"], value)


class CheckTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)

    def check(self, text, run_id=RUN):
        path = self.dir / "charter.md"
        path.write_text(text)
        return [p.message for p in check_charter(read_charter(path), run_id)]

    def test_a_complete_tier_2_charter_passes(self):
        self.assertEqual(self.check(charter_text()), [])

    def test_each_required_field_is_named_when_missing(self):
        for key in FRONT:
            with self.subTest(key):
                problems = self.check(charter_text(drop=(key,)))
                self.assertTrue(any(repr(key) in p for p in problems), problems)

    def test_tier_1_needs_only_the_basics(self):
        keep = ("run_id", "title", "tier", "predicate", "scope", "gates")
        self.assertEqual(self.check(charter_text({"tier": "1"}, sections=[], drop=[k for k in FRONT if k not in keep])), [])

    def test_tier_3_needs_security_and_decision_review(self):
        problems = self.check(charter_text({"tier": "3"}))
        self.assertTrue(any("security lens" in p for p in problems))
        self.assertTrue(any("decision_review: true" in p for p in problems))
        ok = self.check(charter_text({"tier": "3", "review": "{lenses: [correctness, security], decision_review: true}"}))
        self.assertEqual(ok, [])

    def test_bad_values_are_named(self):
        cases = {
            "over budget": ({"estimate": "{usd: 99, tokens: 1, wall_clock: 1h, calls: {}}"}, "over budget.usd"),
            "over time": ({"estimate": "{usd: 1, tokens: 1, wall_clock: 2d, calls: {}}"}, "wall_clock is over"),
            "removed gate": ({"gates": "[spend]"}, "missing migration"),
            "wrong run": ({"run_id": "2026-01-01-other"}, "doesn't match"),
            "bad tier": ({"tier": "4"}, "tier must be"),
            "bad duration": ({"budget": "{usd: 40, tokens: 1, wall_clock: soon, max_parallel_agents: 1}"}, "duration"),
            "zero agents": ({"budget": "{usd: 40, tokens: 6000000, wall_clock: 6h, max_parallel_agents: 0}"}, "max_parallel_agents"),
            "bad spec level": ({"spec_level": "vibes"}, "spec_level must be"),
            "staffing count": ({"staffing": "[{role: code, tool: x, model: y, count: 0}]"}, "count must be"),
            "placeholder": ({"predicate": '"TODO later"'}, "placeholder"),
            "soc2": ({"soc2": "{tested: true}"}, "soc2 needs"),
        }
        for label, (front, expect) in cases.items():
            with self.subTest(label):
                problems = self.check(charter_text(front))
                self.assertTrue(any(expect in p for p in problems), problems)

    def test_sections_must_all_be_there_in_order(self):
        missing = self.check(charter_text(sections=SECTIONS[:-1]))
        self.assertTrue(any("## Risks" in p for p in missing), missing)
        swapped = SECTIONS[:]
        swapped[0], swapped[1] = swapped[1], swapped[0]
        self.assertTrue(any("out of order" in p for p in self.check(charter_text(sections=swapped))))

    def test_the_template_fails_until_filled(self):
        path = new_charter(self.dir / "runs", RUN, title="Make export retries idempotent", tier=2)
        problems = [p.message for p in check_charter(read_charter(path), RUN)]
        self.assertTrue(any("placeholder" in p for p in problems))
        with self.assertRaises(LedgerError):
            new_charter(self.dir / "runs", RUN, title="again", tier=2)


class ApprovalTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        self.sink = repo_sink(self.root)
        self.path = self.sink.run_dir(RUN) / "charter.md"
        self.path.parent.mkdir(parents=True)
        self.path.write_text(charter_text())

    def approve(self, **kw):
        return approve_charter(self.sink, RUN, actor=HUMAN, why="reviewed", quote="Approved",
                               channel="claude-code chat", relayed_by=None, append=append, **kw)

    def status(self):
        return approval_status(self.sink, RUN, read_charter(self.path).sha256)

    def test_a_run_without_a_ledger_is_draft_and_cannot_be_approved(self):
        self.assertEqual(self.status().state, "draft")
        with self.assertRaises(LedgerError):
            self.approve()

    def test_approve_records_the_hash_and_editing_returns_it_to_draft(self):
        init_run(self.sink, RUN, actor=AGENT, why="test")
        first = self.approve()
        self.assertTrue(first.new)
        self.assertIs(first.entry.type, EntryType.APPROVAL)
        self.assertEqual(first.entry.data["charter_sha256"], read_charter(self.path).sha256)
        self.assertEqual(self.status().state, "approved")
        self.assertFalse(self.approve().new, "approving the same charter twice is a no-op")
        self.path.write_text(charter_text({"budget": "{usd: 30, tokens: 6000000, wall_clock: 6h, max_parallel_agents: 4}"}))
        self.assertEqual(self.status().state, "stale")
        second = self.approve()
        self.assertEqual(second.entry.data["supersedes"], first.entry.seq)
        self.assertEqual(self.status().state, "approved")

    def test_a_charter_that_fails_check_is_not_approved(self):
        init_run(self.sink, RUN, actor=AGENT, why="test")
        self.path.write_text(charter_text(drop=("budget",)))
        with self.assertRaises(CharterError):
            self.approve()
        self.assertEqual([e.type for e in self.sink.read(RUN).entries], [EntryType.STATE])

    def test_an_agent_cannot_approve(self):
        init_run(self.sink, RUN, actor=AGENT, why="test")
        with self.assertRaises(Exception):
            approve_charter(self.sink, RUN, actor=AGENT, why="w", quote=None, channel=None,
                            relayed_by=None, append=append)

    def test_older_approvals_with_the_hash_in_evidence_count(self):
        init_run(self.sink, RUN, actor=AGENT, why="test")
        sha = read_charter(self.path).sha256
        append(self.sink, RUN, actor=HUMAN, type=EntryType.APPROVAL, summary="Approved", why="w",
               evidence=[f"charter.md sha256 {sha}, as committed in abc1234"])
        self.assertEqual(self.status().state, "approved")


class CharterCliTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        self.human = json.dumps(HUMAN.to_wire())
        self.agent = json.dumps(AGENT.to_wire())

    def cli(self, *args):
        return run_cli(*args, "--root", self.root)

    def test_new_check_approve_edit_flow(self):
        code, out, err = self.cli("charter", "new", "export-retries", "--date", "2026-11-10", "--title", "Retries")
        self.assertEqual(code, 0, err)
        code, out, _ = self.cli("charter", "check", RUN)
        self.assertEqual(code, 1)
        self.assertIn("placeholder", out)
        (self.root / ".audit/runs" / RUN / "charter.md").write_text(charter_text())
        code, out, _ = self.cli("charter", "check", RUN, "--require-approved")
        self.assertEqual(code, 1)
        self.assertIn("draft: not approved", out)
        self.assertEqual(self.cli("init", "export-retries", "--date", "2026-11-10", "--actor", self.agent)[0], 0)
        code, _, err = self.cli("charter", "approve", RUN, "--actor", self.agent)
        self.assertEqual(code, 2)
        self.assertIn("only a human", err)
        code, out, err = self.cli("charter", "approve", RUN, "--actor", self.human, "--quote", "Approved",
                                  "--relayed-by", "claude@claude-code#70d5")
        self.assertEqual(code, 0, err)
        code, out, _ = self.cli("charter", "check", RUN, "--require-approved")
        self.assertEqual(code, 0)
        self.assertIn("approved at seq 2", out)
        path = self.root / ".audit/runs" / RUN / "charter.md"
        path.write_text(path.read_text().replace("Text.", "Edited.", 1))
        code, out, _ = self.cli("charter", "check", RUN, "--require-approved")
        self.assertEqual(code, 1)
        self.assertIn("edited since the approval at seq 2", out)

    def test_render_is_an_honest_stub(self):
        code, _, err = run_cli("charter", "render", RUN)
        self.assertEqual(code, 2)
        self.assertIn("M2", err)

    def test_an_unreadable_charter_is_exit_2_with_the_line(self):
        path = self.root / ".audit/runs" / RUN / "charter.md"
        path.parent.mkdir(parents=True)
        path.write_text("---\ntier: &a 2\n---\n")
        code, _, err = self.cli("charter", "check", RUN)
        self.assertEqual(code, 2)
        self.assertIn("line 2", err)


if __name__ == "__main__":
    unittest.main()
