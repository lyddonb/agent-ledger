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
from agent_ledger.entry import AgentActor, EntryError, EntryType, HumanActor
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
            "deep list": "a: " + "[" * 2000 + "]" * 2000,
            "deep map": "a: " + "{a: " * 2000 + "1" + "}" * 2000,
            "huge int": "a: " + "9" * 5000,
            "leading zero": "a: 010",
        }
        for label, text in cases.items():
            with self.subTest(label):
                with self.assertRaises(CharterError) as cm:
                    parse_front_matter(text.split("\n"), 2)
                self.assertGreaterEqual(cm.exception.line, 2)

    def test_values_yaml_would_type_differently_must_be_quoted(self):
        for text in ["a: [src:]", "a: True", "a: .5", "a: 10.", "a: 1e3", "a: 0x10", "a: 1_000", "a: 2026-11-10",
                     "a: 10:30", "a: yes", "a: Off", "a: NULL", "a: +1", "a: .inf"]:
            with self.subTest(text), self.assertRaises(CharterError):
                parse_front_matter([text], 2)
        fields, _ = parse_front_matter(['a: [none, "2026-11-10", 10h, grok-4.7-x, "yes"]'], 2)
        self.assertEqual(fields["a"], ["none", "2026-11-10", "10h", "grok-4.7-x", "yes"])

    def test_comments_and_quotes(self):
        fields, _ = parse_front_matter([
            "f: it's # a comment",
            'g: "say \\"hi\\" # not a comment"',
        ], 2)
        self.assertEqual(fields, {"f": "it's", "g": 'say "hi" # not a comment'})
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


def _reader_agrees(case_text, expected):
    """None when the reader agrees with YAML's reading (or refuses); else a description."""
    try:
        fields, _ = parse_front_matter([f"v: {case_text}"], 2)
    except CharterError:
        return None  # refusing is always safe
    got = fields["v"]

    def typed(v):
        if isinstance(v, dict):
            return "__type__" in v or any(typed(x) for x in v.values())
        if isinstance(v, list):
            return any(typed(x) for x in v)
        return False

    if typed(expected):
        return f"accepted {case_text!r} as {got!r}, but YAML reads it as {expected!r}"
    if got != expected or type(got) is not type(expected):
        return f"read {case_text!r} as {got!r}, YAML as {expected!r}"
    return None


class YamlAgreementTest(unittest.TestCase):
    """Wherever the reader accepts a value, a real YAML parser reads the same value."""

    def test_against_pyyaml_vectors(self):
        vectors = json.loads((ROOT / "tests/vectors/charter-yaml.json").read_text())
        self.assertGreater(len(vectors), 500)
        problems = [p for p in (_reader_agrees(v["text"], v["yaml"]) for v in vectors) if p]
        self.assertEqual(problems, [])

    def test_against_live_pyyaml(self):
        try:
            import yaml  # optional: only where PyYAML happens to be installed; never a dependency
        except ImportError:
            self.skipTest("PyYAML isn't installed; the committed vectors cover CI")
        from yaml_corpus import corpus, yaml_view
        problems = []
        for seed in range(5):
            for text in corpus(seed=seed, n=1000):
                try:
                    got = yaml.safe_load(f"v: {text}\n")
                    expected = yaml_view(got["v"]) if isinstance(got, dict) and set(got) == {"v"} else {"__type__": "shape"}
                except yaml.YAMLError:
                    expected = {"__type__": "error"}
                p = _reader_agrees(text, expected)
                if p:
                    problems.append(p)
        self.assertEqual(problems[:10], [])


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
            "staffing count": ({"staffing": "[{role: code, tool: x, model: m, count: 0}]"}, "count must be"),
            "placeholder": ({"predicate": '"TODO later"'}, "placeholder"),
            "soc2": ({"soc2": "{tested: true}"}, "soc2 needs"),
            "placeholder key": ({"rollback": '{TODO: "undo it"}'}, "placeholder"),
            "other placeholder": ({"predicate": '"tbd"'}, "placeholder"),
            "bool tier": ({"tier": "true"}, "tier must be"),
            "zero wall clock": ({"budget": "{usd: 40, tokens: 6000000, wall_clock: 0m, max_parallel_agents: 4}",
                                 "estimate": "{usd: 1, tokens: 1, wall_clock: 0m, calls: {}}"}, "positive duration"),
            "bool count": ({"staffing": "[{role: code, tool: x, model: m, count: true}]"}, "count must be"),
            "bool budget": ({"budget": "{usd: true, tokens: 6000000, wall_clock: 6h, max_parallel_agents: 4}"}, "budget.usd"),
        }
        for label, (front, expect) in cases.items():
            with self.subTest(label):
                problems = self.check(charter_text(front))
                self.assertTrue(any(expect in p for p in problems), problems)

    def test_hidden_line_breaks_and_bidi_are_refused(self):
        budget = "{usd: 4000, tokens: 600000000, wall_clock: 9d, max_parallel_agents: 40}"
        for ch in ["\u2028", "\u2029", "\x85", "\u202e", "\u2066", "\x0b"]:
            text = charter_text({"budget": budget + " #" + ch + "budget: {usd: 40}"})
            with self.subTest(ch=hex(ord(ch))), self.assertRaisesRegex(CharterError, "U\\+"):
                self.check(text)
        for ch in ["\u200b", "\ufeff", "\u2060"]:
            with self.subTest(ch=hex(ord(ch))), self.assertRaises(CharterError):
                self.check(charter_text({"title": f'"Ex{ch}port"'}))

    def test_unknown_fields_are_named(self):
        for front, expect in [({"budget_override": "{usd: 100000}"}, "unknown field 'budget_override'"),
                              ({"budget": "{usd: 40, tokens: 6000000, wall_clock: 6h, max_parallel_agents: 4, cap: 1}"},
                               "unknown budget field 'cap'")]:
            with self.subTest(expect):
                self.assertTrue(any(expect in p for p in self.check(charter_text(front))))

    def test_soc2_gaps_need_a_section(self):
        gap = {"soc2": "{approved_before_build: false, tested: true, independent_review: true, recorded: true}"}
        self.assertTrue(any("SOC 2 gaps" in p for p in self.check(charter_text(gap))))
        sections = SECTIONS[:-1] + ["SOC 2 gaps", SECTIONS[-1]]
        self.assertEqual(self.check(charter_text(gap, sections=sections)), [])

    def test_calls_scope_and_tier_1_budgets_are_checked(self):
        cases = [({"estimate": "{usd: 1, tokens: 1, wall_clock: 1h, calls: {rollouts: -5}}"}, "calls counts"),
                 ({"scope": "[null, 3]"}, "scope must be"),
                 ({"scope": '[""]'}, "scope must be")]
        for front, expect in cases:
            with self.subTest(expect):
                self.assertTrue(any(expect in p for p in self.check(charter_text(front))))
        keep = ("run_id", "title", "tier", "predicate", "scope", "gates", "budget")
        tier1 = charter_text({"tier": "1", "budget": "{usd: 0, tokens: 1, wall_clock: 1h, max_parallel_agents: 1}"},
                             sections=[], drop=[k for k in FRONT if k not in keep])
        self.assertTrue(any("budget.usd" in p for p in self.check(tier1)))

    def test_fences_close_only_on_a_matching_fence(self):
        hidden = "\n".join(f"## {s}" for s in SECTIONS)
        text = charter_text(sections=[]) + "\n````\n```\n" + hidden + "\n````\n"
        self.assertTrue(any("## Goal" in p for p in self.check(text)))
        indented = charter_text(sections=[]) + "\n    ```\n" + hidden + "\n"
        self.assertEqual([p for p in self.check(indented) if "## " in p], [],
                         "an indented ``` is not a fence, so the headings count")

    def test_tier_3_with_malformed_lenses_is_a_problem_not_a_crash(self):
        problems = self.check(charter_text({"tier": "3", "review": "{lenses: 5, decision_review: true}"}))
        self.assertTrue(any("security lens" in p for p in problems), problems)

    def test_placeholders_in_the_body_are_named(self):
        text = charter_text().replace("## Plan\n\nText.", "## Plan\n\nTBD after the spike.")
        self.assertTrue(any("body still has a placeholder" in p for p in self.check(text)))

    def test_headings_hidden_in_code_or_comments_do_not_count(self):
        hidden = "\n".join(f"## {s}" for s in SECTIONS)
        for wrap in (f"```\n{hidden}\n```", f"<!--\n{hidden}\n-->"):
            with self.subTest(wrap=wrap[:3]):
                problems = self.check(charter_text(sections=[]) + "\n" + wrap + "\n")
                self.assertTrue(any("## Goal" in p for p in problems), problems)

    def test_sections_must_all_be_there_in_order(self):
        missing = self.check(charter_text(sections=SECTIONS[:-1]))
        self.assertTrue(any("## Risks" in p for p in missing), missing)
        swapped = SECTIONS[:]
        swapped[0], swapped[1] = swapped[1], swapped[0]
        self.assertTrue(any("out of order" in p for p in self.check(charter_text(sections=swapped))))

    def test_the_template_fails_until_filled(self):
        path = new_charter(self.dir / "runs", RUN, title="Make export retries idempotent", tier=2)
        charter = read_charter(path)
        flagged = {p.line for p in check_charter(charter, RUN) if "placeholder" in p.message}
        lines = path.read_text().split("\n")
        todo_lines = set()
        for n, line in enumerate(lines, 1):
            if "TODO" in line.split(" #")[0]:
                key = n
                while lines[key - 1].startswith("  - "):  # a list item is reported on its key's line
                    key -= 1
                todo_lines.add(key)
        self.assertEqual(todo_lines - flagged, set(), "every TODO is flagged, rollback included")
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
        kw.setdefault("sha256", read_charter(self.path).sha256)
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
        with self.assertRaises(EntryError):
            approve_charter(self.sink, RUN, actor=AGENT, sha256=read_charter(self.path).sha256, why="w",
                            quote=None, channel=None, relayed_by=None, append=append)

    def test_older_approvals_with_the_hash_in_evidence_count(self):
        init_run(self.sink, RUN, actor=AGENT, why="test")
        sha = read_charter(self.path).sha256
        append(self.sink, RUN, actor=HUMAN, type=EntryType.APPROVAL, summary="Approved", why="w",
               evidence=[f"see old note: charter.md sha256 {sha}"])
        self.assertEqual(self.status().state, "draft", "the legacy form must be the whole pointer")
        append(self.sink, RUN, actor=HUMAN, type=EntryType.APPROVAL, summary="Approved", why="w",
               evidence=[f"charter.md sha256 {sha}, as committed in abc1234"])
        self.assertEqual(self.status().state, "approved")

    def test_editing_then_reverting_is_approved_again(self):
        init_run(self.sink, RUN, actor=AGENT, why="test")
        self.approve()
        original = self.path.read_bytes()
        self.path.write_bytes(original + b"\nmore\n")
        self.assertEqual(self.status().state, "stale")
        self.path.write_bytes(original)
        self.assertEqual(self.status().state, "approved")

    def test_approval_is_bound_to_the_reviewed_hash(self):
        init_run(self.sink, RUN, actor=AGENT, why="test")
        reviewed = read_charter(self.path).sha256
        self.path.write_text(charter_text({"budget": "{usd: 40, tokens: 6000000, wall_clock: 6h, max_parallel_agents: 4}",
                                           "predicate": '"edited after review"'}))
        with self.assertRaisesRegex(CharterError, "changed since it was reviewed"):
            self.approve(sha256=reviewed[:12])
        with self.assertRaisesRegex(CharterError, "at least the first 12"):
            self.approve(sha256=read_charter(self.path).sha256[:8])
        self.assertEqual([e.type for e in self.sink.read(RUN).entries], [EntryType.STATE])

    def test_legacy_evidence_must_be_the_exact_form(self):
        init_run(self.sink, RUN, actor=AGENT, why="test")
        sha = read_charter(self.path).sha256
        append(self.sink, RUN, actor=HUMAN, type=EntryType.APPROVAL, summary="x", why="w",
               evidence=[f"charter.md sha256 {sha}, rejected"])
        self.assertEqual(self.status().state, "draft")

    def test_a_symlinked_charter_is_refused(self):
        elsewhere = self.root / "elsewhere.md"
        elsewhere.write_bytes(self.path.read_bytes())
        self.path.unlink()
        self.path.symlink_to(elsewhere)
        with self.assertRaisesRegex(CharterError, "symlink"):
            read_charter(self.path)


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
        code, _, err = self.cli("charter", "approve", RUN, "--actor", self.agent, "--sha256", "0" * 12)
        self.assertEqual(code, 2)
        self.assertIn("only a human", err)
        code, out, _ = self.cli("charter", "check", RUN)
        sha = out.split("sha256 ", 1)[1].split()[0][:12]
        code, _, err = self.cli("charter", "approve", RUN, "--actor", self.human, "--sha256", sha,
                                "--quote", "Approved", "--relayed-by", "claude@claude-code#70d5")
        self.assertEqual(code, 1, "a relayed approval must quote the hash prefix")
        self.assertIn("hash prefix", err)
        code, out, err = self.cli("charter", "approve", RUN, "--actor", self.human, "--sha256", sha,
                                  "--quote", f"Approved {sha}", "--relayed-by", "claude@claude-code#70d5")
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
