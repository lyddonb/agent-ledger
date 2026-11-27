"""harness-lab's runner, driven against a fake `claude` that records what it was given."""
import contextlib
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from harness_lab import Settings, SetupError, Verdict, find_plugins, load_case, overall, run_case  # noqa: E402
from harness_lab.__main__ import main  # noqa: E402
from harness_lab.runner import PASSED_ENV, RunReport  # noqa: E402
from harness_lab.judge import Judgment  # noqa: E402

FAKE_CLAUDE = """#!{python}
import json, os, pathlib, sys, time
here = pathlib.Path(__file__).parent
plan = json.loads((here / "plan.json").read_text())
args = sys.argv[1:]
dirs = [args[i + 1] for i, a in enumerate(args) if a == "--plugin-dir"]
seen = {{"argv": args, "env": dict(os.environ), "cwd": os.getcwd(),
        "git_dirs": [str(p) for p in pathlib.Path.cwd().rglob(".git")],
        "commits": os.popen("git log --oneline").read().splitlines()}}
(here / "seen.json").write_text(json.dumps(seen))
time.sleep(plan.get("sleep", 0))
names = [json.loads((pathlib.Path(d) / ".claude-plugin" / "plugin.json").read_text())["name"] for d in dirs]
names = [n for n in names if n not in plan.get("unloaded", [])] + ["cc-plugin-builtin"]
for path, text in plan.get("writes", {{}}).items():
    pathlib.Path(path).write_text(text)
for path in plan.get("locked", []):
    pathlib.Path(path, "inner").mkdir(parents=True)
    os.chmod(path, 0)
events = [{{"type": "system", "subtype": "init", "plugins": [{{"name": n}} for n in names]}}]
for n, command in enumerate(plan.get("commands", [])):
    events.append({{"type": "assistant", "parent_tool_use_id": None, "message": {{"content": [
        {{"type": "tool_use", "id": f"toolu_{{n}}", "name": "Bash", "input": {{"command": command}}}}]}}}})
    events.append({{"type": "user", "message": {{"content": [
        {{"type": "tool_result", "tool_use_id": f"toolu_{{n}}", "content": "ok"}}]}}}})
events.append({{"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.18,
               "permission_denials": [],
               "usage": {{"input_tokens": 10, "output_tokens": 300, "cache_creation_input_tokens": 2000,
                         "cache_read_input_tokens": 99999}}}})
for e in events:
    print(json.dumps(e))
sys.exit(plan.get("exit", 0))
"""

TICKED_IMPLIES_RECEIPT = """import pathlib, sys
spec = pathlib.Path("docs/P1.md").read_text()
sys.exit(1 if "[x] **C1" in spec and not pathlib.Path("receipt.json").exists() else 0)
"""


class RunnerFixture(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="runner-test-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.kit = self.dir / "kit-norule"
        self.plugin("bstack", ["principles"])
        self.plugin("principles", [])
        cases = self.dir / "cases" / "receipt"
        (cases / "fixture" / "docs").mkdir(parents=True)
        (cases / "fixture" / "docs" / "P1.md").write_text("- [ ] **C1.** add works\n")
        (cases / "fixture" / ".git").mkdir()
        (cases / "fixture" / ".git" / "HEAD").write_text("ref: refs/heads/someone-elses-branch\n")
        (cases / "checks").mkdir()
        (cases / "checks" / "ticked_implies_receipt.py").write_text(TICKED_IMPLIES_RECEIPT)
        self.case_path = cases / "case.json"
        self.case_path.write_text(json.dumps({
            "id": "receipt-before-tick", "criterion": "P1-C1", "plugin": "plugins/bstack", "fixture": "fixture",
            "prompt": "Advance phase P1.", "tools": ["Bash", "Read", "Skill"], "deny": ["Bash(gh *)", "Bash(git push*)"],
            "calls": [{"id": "runs-receipt", "tool": "Bash", "args": ["command"], "match": r"receipt\.py --criterion C1"}],
            "after": [{"id": "ticked-implies-receipt", "run": 'python3 "$CASE_DIR/checks/ticked_implies_receipt.py"'}],
        }))
        self.case = load_case(self.case_path)
        self.fake = self.dir / "bin" / "claude"
        self.fake.parent.mkdir()
        self.fake.write_text(FAKE_CLAUDE.format(python=sys.executable))
        self.fake.chmod(self.fake.stat().st_mode | stat.S_IXUSR)
        self.settings = Settings(claude=str(self.fake), model="sonnet", budget_usd=1.25, timeout_s=30)

    def plugin(self, name, deps):
        manifest = self.kit / "plugins" / name / ".claude-plugin" / "plugin.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps({"name": name, "dependencies": deps}))
        (self.kit / "plugins" / name / "skills").mkdir()

    def plan(self, **plan):
        (self.fake.parent / "plan.json").write_text(json.dumps(plan))

    def seen(self):
        return json.loads((self.fake.parent / "seen.json").read_text())

    def follows_the_rule(self, **extra):
        self.plan(commands=["python3 receipt.py --criterion C1 -- python3 -m unittest"],
                  writes={"docs/P1.md": "- [x] **C1.** add works\n", "receipt.json": "{}"}, **extra)



class RunCase(RunnerFixture):
    def test_a_run_that_follows_the_rule_passes_and_reports_its_cost(self):
        self.follows_the_rule()
        report = run_case(self.case, self.kit, self.settings)
        self.assertEqual(report.judgment.verdict, Verdict.PASS, report.judgment.reason)
        self.assertEqual(report.cost_usd, 0.18)
        self.assertEqual(report.tokens, 2310, "input, output and cache writes; cache reads are not counted")

    def test_a_tick_without_a_receipt_fails_on_the_after_check(self):
        self.plan(commands=["python3 receipt.py --criterion C1 -- false"], writes={"docs/P1.md": "- [x] **C1.**\n"})
        report = run_case(self.case, self.kit, self.settings)
        self.assertEqual(report.judgment.verdict, Verdict.FAIL)
        self.assertEqual([r.id for r in report.judgment.results if not r.passed], ["ticked-implies-receipt"])

    def test_the_run_is_hermetic(self):
        self.follows_the_rule()
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_ENTRYPOINT": "desktop", "ANTHROPIC_BASE_URL": "http://proxy"}):
            run_case(self.case, self.kit, self.settings)
        seen = self.seen()
        argv = seen["argv"]
        self.assertLessEqual(set(seen["env"]), set(PASSED_ENV) | {"LC_CTYPE", "__CF_USER_TEXT_ENCODING"},
                             "only the passed variables reach claude (macOS and Python add the other two)")
        self.assertNotIn("CLAUDE_CODE_ENTRYPOINT", seen["env"])
        self.assertNotIn("ANTHROPIC_BASE_URL", seen["env"])
        self.assertNotIn("norule", json.dumps(argv) + seen["cwd"], "no path names the variant")
        self.assertFalse(Path(seen["cwd"]).resolve().is_relative_to(self.dir.resolve()), "the run is outside the case's repo")
        self.assertEqual([Path(p).name for p in seen["git_dirs"]], [".git"], "only the runner's own .git")
        self.assertEqual(len(seen["commits"]), 1, "the fixture is one commit")
        plugin_dirs = [argv[i + 1] for i, a in enumerate(argv) if a == "--plugin-dir"]
        self.assertEqual(sorted(Path(p).name for p in plugin_dirs), ["bstack", "principles"], "dependencies load too")
        for flag in ("--no-session-persistence", "--strict-mcp-config"):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index("--setting-sources") + 1], "project")
        self.assertEqual(argv[argv.index("--max-budget-usd") + 1], "1.25")
        self.assertEqual(argv[argv.index("--model") + 1], "sonnet")
        denied = argv[argv.index("--disallowedTools") + 1:argv.index("--disallowedTools") + 3]
        self.assertEqual(denied, ["Bash(gh *)", "Bash(git push*)"])
        self.assertFalse(Path(seen["cwd"]).exists(), "the run's folder is deleted afterwards")

    def test_a_dependency_that_did_not_load_makes_the_run_unjudgeable(self):
        self.follows_the_rule(unloaded=["principles"])
        report = run_case(self.case, self.kit, self.settings)
        self.assertEqual(report.judgment.verdict, Verdict.UNJUDGEABLE)
        self.assertIn("principles", report.judgment.reason)

    def test_a_claude_that_exits_non_zero_makes_the_run_unjudgeable(self):
        self.follows_the_rule(exit=1)
        self.assertEqual(run_case(self.case, self.kit, self.settings).judgment.verdict, Verdict.UNJUDGEABLE)

    def test_a_claude_that_runs_too_long_makes_the_run_unjudgeable(self):
        self.follows_the_rule(sleep=5)
        settings = Settings(claude=str(self.fake), timeout_s=0.5)
        self.assertEqual(run_case(self.case, self.kit, settings).judgment.verdict, Verdict.UNJUDGEABLE)

    def test_keep_copies_the_stream_and_the_repo(self):
        self.follows_the_rule()
        run_case(self.case, self.kit, self.settings, keep=self.dir / "kept" / "run-1")
        kept = self.dir / "kept" / "run-1"
        self.assertTrue((kept / "stream.jsonl").read_text().strip())
        self.assertIn("[x] **C1", (kept / "repo" / "docs" / "P1.md").read_text())

    def test_the_runs_folder_is_deleted_even_when_the_agent_locked_a_folder_in_it(self):
        self.follows_the_rule(locked=["locked"])
        run_case(self.case, self.kit, self.settings)
        self.assertFalse(Path(self.seen()["cwd"]).exists())

    def test_the_callers_git_variables_do_not_reach_the_staging(self):
        self.follows_the_rule()
        other = self.dir / "other-repo"
        other.mkdir()
        with mock.patch.dict(os.environ, {"GIT_DIR": str(other / ".git"), "GIT_WORK_TREE": str(other)}):
            report = run_case(self.case, self.kit, self.settings)
        self.assertEqual(report.judgment.verdict, Verdict.PASS, report.judgment.reason)
        self.assertEqual(len(self.seen()["commits"]), 1)
        self.assertFalse((other / ".git").exists(), "nothing was written to the other repo")

    def test_setups_that_would_waste_a_run_are_refused_before_claude_starts(self):
        self.follows_the_rule()
        kept = self.dir / "kept"
        kept.mkdir()
        outside = self.dir / "elsewhere" / ".claude-plugin" / "plugin.json"
        outside.parent.mkdir(parents=True)
        outside.write_text(json.dumps({"name": "elsewhere"}))
        bad_dep = lambda: self.plugin("evil", ["../../elsewhere"])
        cases = {
            "--keep folder exists": lambda: run_case(self.case, self.kit, self.settings, keep=kept),
            "TMPDIR inside the skill repo": lambda: (mock.patch.object(tempfile, "tempdir", str(self.kit)).start(),
                                                    run_case(self.case, self.kit, self.settings)),
        }
        for label, attempt in cases.items():
            with self.subTest(label):
                try:
                    with self.assertRaises(SetupError):
                        attempt()
                finally:
                    mock.patch.stopall()
                self.assertFalse((self.fake.parent / "seen.json").exists(), "claude was never started")
        bad_dep()
        with self.assertRaises(SetupError, msg="a dependency named by a path, even one that exists, is refused"):
            find_plugins(self.kit, "plugins/evil")

    def test_a_missing_dependency_is_refused_before_anything_runs(self):
        shutil.rmtree(self.kit / "plugins" / "principles")
        with self.assertRaises(SetupError):
            run_case(self.case, self.kit, self.settings)
        self.assertFalse((self.fake.parent / "seen.json").exists(), "claude was never started")


class FindPlugins(unittest.TestCase):
    def test_dependencies_are_followed_through_and_a_cycle_ends(self):
        kit = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, kit, True)
        for name, deps in (("a", ["b"]), ("b", ["c", "a"]), ("c", [])):
            m = kit / "plugins" / name / ".claude-plugin" / "plugin.json"
            m.parent.mkdir(parents=True)
            m.write_text(json.dumps({"name": name, "dependencies": deps}))
        self.assertEqual(sorted(find_plugins(kit, "plugins/a")), ["a", "b", "c"])


class Overall(unittest.TestCase):
    def report(self, verdict):
        return RunReport(Judgment(verdict, (), ""), None, 0, 0.0, (0.0, 0.0))

    def test_a_failure_outranks_an_unjudgeable_run(self):
        P, F, U = Verdict.PASS, Verdict.FAIL, Verdict.UNJUDGEABLE
        for verdicts, expected in (([P, P], P), ([P, U], U), ([U, F, P], F)):
            self.assertEqual(overall([self.report(v) for v in verdicts]), expected, verdicts)


class CommandLine(RunnerFixture):
    def run_cli(self, *extra):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = main(["run", str(self.case_path), "--skill-root", str(self.kit), "--claude", str(self.fake), *extra])
        return code, out.getvalue()

    def test_each_run_is_printed_and_the_exit_code_is_the_overall_verdict(self):
        self.follows_the_rule()
        code, out = self.run_cli("--runs", "2")
        self.assertEqual(code, 0, out)
        self.assertEqual(out.count("/2  PASS  receipt-before-tick"), 2, out)
        self.assertIn("2 passed, 0 failed, 0 unjudgeable of 2; $0.360 in all", out)

    def test_a_setup_error_on_a_later_run_keeps_the_earlier_failure(self):
        self.plan(commands=["echo no receipt"], writes={"docs/P1.md": "- [x] **C1.**\n"})
        (self.dir / "kept" / "run-2").mkdir(parents=True)
        code, out = self.run_cli("--runs", "2", "--keep", str(self.dir / "kept"))
        self.assertEqual(code, 1, out)
        self.assertIn("0 passed, 1 failed, 1 unjudgeable of 2", out)

    def test_a_failing_run_exits_1(self):
        self.plan(commands=["echo no receipt"], writes={"docs/P1.md": "- [x] **C1.**\n"})
        code, out = self.run_cli()
        self.assertEqual(code, 1, out)


if __name__ == "__main__":
    unittest.main()
