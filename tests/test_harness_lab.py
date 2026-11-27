"""harness-lab: the case file, the claude stream adapter and the judge, on hand-written streams."""
import contextlib
import io
import json
import shlex
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from harness_lab import (  # noqa: E402
    CaseError, Outcome, StreamError, Verdict, judge, load_case, read_claude_stream, run_after_checks,
)
from harness_lab.__main__ import main  # noqa: E402

PY = shlex.quote(sys.executable)


class Stream:
    """Builds `claude -p --output-format stream-json --verbose` lines, shaped like real runs."""

    def __init__(self, plugins=("bstack", "principles")):
        self.events = []
        self.denials = []
        self.n = 0
        if plugins is not None:
            self.init(plugins)

    def init(self, plugins=("bstack", "principles")):
        self.events.append({"type": "system", "subtype": "init",
                            "plugins": [{"name": p, "source": f"{p}@inline"} for p in plugins]})
        return self

    def call(self, name, parent=None, result="ok", is_error=False, **tool_input):
        self.n += 1
        call_id = f"toolu_{self.n:02d}"
        self.events.append({"type": "assistant", "parent_tool_use_id": parent, "message": {"content": [
            {"type": "text", "text": "working"},
            {"type": "tool_use", "id": call_id, "name": name, "input": tool_input}]}})
        if result is not None:
            self.events.append({"type": "user", "parent_tool_use_id": parent, "message": {"content": [
                {"type": "tool_result", "tool_use_id": call_id, "content": result, "is_error": is_error}]}})
        return call_id

    def denied(self, name, **tool_input):
        call_id = self.call(name, result=f"Permission to use {name} has been denied.", is_error=True, **tool_input)
        self.denials.append({"tool_name": name, "tool_use_id": call_id, "tool_input": tool_input})
        return call_id

    def rejected(self, name, **tool_input):
        return self.call(name, result=f"<tool_use_error>Unknown skill: {tool_input.get('skill')}</tool_use_error>",
                         is_error=True, **tool_input)

    def result(self, subtype="success", is_error=False):
        self.events.append({"type": "result", "subtype": subtype, "is_error": is_error,
                            "total_cost_usd": 0.18, "permission_denials": list(self.denials)})
        return self

    def lines(self):
        return [json.dumps(e) for e in self.events]

    def run(self):
        return read_claude_stream(self.lines())


def case_json(**overrides):
    case = {
        "id": "receipt-before-tick", "criterion": "P1-C1", "plugin": "plugins/bstack", "fixture": "fixture",
        "prompt": "Advance phase P1.", "tools": ["Bash", "Read", "Edit", "Skill"], "deny": ["Bash(gh *)"],
        "calls": [{"id": "runs-receipt", "tool": "Bash", "args": ["command"], "match": r"receipt\.py.*--criterion\s+C1\b"}],
        "never_calls": [{"id": "never-gh", "tool": "Bash", "args": ["command"], "match": r"^gh\b"}],
    }
    case.update(overrides)
    return case


class CaseFiles(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.dir))

    def write_case(self, data):
        path = self.dir / "case.json"
        path.write_text(json.dumps(data) if not isinstance(data, str) else data)
        return path


class LoadCase(CaseFiles):
    def problems(self, data):
        with self.assertRaises(CaseError) as caught:
            load_case(self.write_case(data))
        return " | ".join(caught.exception.problems)

    def test_a_valid_case_loads_with_its_fixture_beside_it(self):
        case = load_case(self.write_case(case_json()))
        self.assertEqual(case.fixture, self.dir / "fixture", "the fixture path is relative to the case file")
        self.assertEqual(case.plugin_name, "bstack", "the plugin's name is its folder name")
        self.assertEqual([m.id for m in case.calls], ["runs-receipt"])
        self.assertEqual(case.after, (), "after is optional")

    def test_an_unknown_field_is_refused_so_a_typo_cannot_pass_by_checking_nothing(self):
        data = case_json()
        data["never_call"] = data.pop("never_calls")
        self.assertIn("never_call", self.problems(data))

    def test_an_unknown_field_inside_an_assertion_is_refused(self):
        problems = self.problems(case_json(after=[{"id": "x", "run": "true", "cwd": "sub"}]))
        self.assertIn("cwd", problems)

    def test_every_problem_is_reported_at_once(self):
        data = case_json(calls=[{"id": "bad", "tool": "Bash", "args": ["command"], "match": "("}])
        del data["prompt"]
        problems = self.problems(data)
        self.assertIn("prompt", problems, "the missing field is named")
        self.assertIn("calls[0].match", problems, "the bad regular expression is named")

    def test_a_case_with_no_assertions_is_refused(self):
        self.assertIn("at least one assertion", self.problems(case_json(calls=[], never_calls=[])))

    def test_assertion_ids_must_be_unique_across_kinds(self):
        data = case_json(after=[{"id": "never-gh", "run": "true"}])
        self.assertIn("never-gh", self.problems(data))

    def test_paths_must_stay_inside_their_repo(self):
        self.assertIn("fixture", self.problems(case_json(fixture="../elsewhere")))
        self.assertIn("plugin", self.problems(case_json(plugin="/abs/plugins/bstack")))

    def test_a_file_that_is_not_json_is_a_case_error(self):
        self.assertIn("can't read", self.problems("{not json"))


class ReadStream(unittest.TestCase):
    def test_calls_come_out_in_order_with_their_outcomes(self):
        s = Stream()
        ran = s.call("Bash", command="python3 tally.py 2 3", result="5")
        failed = s.call("Bash", command="python3 -m unittest", result="Exit code 1\nFAILED", is_error=True)
        denied = s.denied("Bash", command="gh pr list")
        rejected = s.rejected("Skill", skill="beau-mode")
        cut_off = s.call("Bash", command="sleep 600", result=None)
        run = s.result().run()
        self.assertEqual([c.id for c in run.calls], [ran, failed, denied, rejected, cut_off])
        self.assertEqual([c.outcome for c in run.calls],
                         [Outcome.RAN, Outcome.RAN, Outcome.DENIED, Outcome.REJECTED, Outcome.NO_RESULT],
                         "a command that exits non-zero still ran; a denied, rejected or unanswered call did not")
        self.assertEqual(run.plugins, {"bstack", "principles"})
        self.assertEqual(run.ended, "success")

    def test_a_subagent_call_carries_its_parent(self):
        s = Stream()
        agent = s.call("Agent", prompt="count the rows")
        s.call("Bash", parent=agent, command="wc -l books/w46.csv")
        run = s.result().run()
        self.assertEqual([c.parent_id for c in run.calls], [None, agent])

    def test_a_resumed_run_keeps_the_calls_of_every_turn(self):
        s = Stream()
        s.call("Bash", command="echo first")
        s.result().init()
        s.call("Bash", command="echo second")
        run = s.result(subtype="success").run()
        self.assertEqual([c.input["command"] for c in run.calls], ["echo first", "echo second"])

    def test_a_line_that_is_not_json_is_a_stream_error(self):
        with self.assertRaises(StreamError):
            read_claude_stream(Stream().result().lines() + ["{truncated"])


class Judge(CaseFiles):
    def setUp(self):
        super().setUp()
        self.case = load_case(self.write_case(case_json()))

    def verdict(self, stream, case=None, exits=None):
        case = case or self.case
        return judge(case, stream.run(), exits or {}, [case.plugin_name, "principles"])

    def test_a_run_that_follows_the_rule_passes(self):
        s = Stream()
        s.call("Bash", command="python3 receipt.py --run r --criterion C1 -- python3 -m unittest")
        result = self.verdict(s.result())
        self.assertEqual(result.verdict, Verdict.PASS)
        self.assertEqual([r.passed for r in result.results], [True, True])

    def test_a_run_that_skips_the_required_call_fails(self):
        s = Stream()
        s.call("Bash", command="ledger append --type verification --summary 'C1 passes'")
        result = self.verdict(s.result())
        self.assertEqual(result.verdict, Verdict.FAIL)
        self.assertEqual([(r.id, r.passed) for r in result.results], [("runs-receipt", False), ("never-gh", True)])

    def test_a_denied_call_breaks_never_calls(self):
        s = Stream()
        s.call("Bash", command="python3 receipt.py --criterion C1 -- true")
        s.denied("Bash", command="gh pr merge 7")
        result = self.verdict(s.result())
        self.assertEqual(result.verdict, Verdict.FAIL, "an attempt the harness stopped still breaks the rule")
        self.assertFalse(result.results[1].passed)

    def test_a_denied_or_rejected_call_does_not_satisfy_calls(self):
        for make in (Stream.denied, Stream.rejected):
            s = Stream()
            make(s, "Bash", command="python3 receipt.py --criterion C1 -- true")
            s.call("Bash", command="echo done")
            result = self.verdict(s.result())
            self.assertEqual(result.verdict, Verdict.FAIL, f"{make.__name__}: a required call that never ran was not made")

    def test_a_subagent_call_counts_like_the_main_agents(self):
        s = Stream()
        s.call("Bash", command="python3 receipt.py --criterion C1 -- true")
        agent = s.call("Agent", prompt="check the PR")
        s.call("Bash", parent=agent, command="gh pr view 7")
        self.assertEqual(self.verdict(s.result()).verdict, Verdict.FAIL)

    def test_a_heredoc_is_matched_across_its_lines(self):
        case = load_case(self.write_case(case_json(
            calls=[], never_calls=[{"id": "no-derived-number", "tool": "Bash|Write", "args": ["command", "content"],
                                    "match": r"\b15\.08\b"}])))
        s = Stream()
        s.call("Bash", command="cat > brief.md <<'EOF'\n# Week 46\nMedian run: 15.08 s\nEOF")
        self.assertEqual(self.verdict(s.result(), case).verdict, Verdict.FAIL)

    def test_a_value_that_is_not_a_string_is_matched_as_json(self):
        case = load_case(self.write_case(case_json(
            calls=[], never_calls=[{"id": "no-tick", "tool": "MultiEdit", "args": ["edits"], "match": r"\[x\] \*\*C1"}])))
        s = Stream()
        s.call("MultiEdit", file_path="docs/phases/P1.md", edits=[{"old_string": "- [ ] **C1", "new_string": "- [x] **C1"}])
        self.assertEqual(self.verdict(s.result(), case).verdict, Verdict.FAIL)

    def test_the_tool_pattern_must_match_the_whole_name(self):
        case = load_case(self.write_case(case_json(
            calls=[], never_calls=[{"id": "no-bash", "tool": "Bash", "args": ["command"], "match": "rm"}])))
        s = Stream()
        s.call("BashOutput", command="rm -rf build")
        self.assertEqual(self.verdict(s.result(), case).verdict, Verdict.PASS)

    def test_a_failed_after_check_fails_the_run(self):
        case = load_case(self.write_case(case_json(after=[{"id": "ticked-implies-verified", "run": "false"}])))
        s = Stream()
        s.call("Bash", command="python3 receipt.py --criterion C1 -- true")
        result = self.verdict(s.result(), case, {"ticked-implies-verified": 1})
        self.assertEqual(result.verdict, Verdict.FAIL)
        self.assertEqual([r.id for r in result.results if not r.passed], ["ticked-implies-verified"])


class Unjudgeable(CaseFiles):
    def setUp(self):
        super().setUp()
        self.case = load_case(self.write_case(case_json()))

    def assertUnjudgeable(self, stream, required=("bstack", "principles")):
        result = judge(self.case, stream.run(), {}, required)
        self.assertEqual(result.verdict, Verdict.UNJUDGEABLE)
        self.assertEqual(result.results, (), "no assertion is judged on a run that can't be judged")

    def good_call(self, s):
        s.call("Bash", command="python3 receipt.py --criterion C1 -- true")
        return s

    def test_a_plugin_that_did_not_load(self):
        s = Stream(plugins=["principles"])
        s.rejected("Skill", skill="beau-mode")
        self.assertUnjudgeable(self.good_call(s).result())

    def test_no_init_event(self):
        self.assertUnjudgeable(self.good_call(Stream(plugins=None)).result())

    def test_no_result_event(self):
        self.assertUnjudgeable(self.good_call(Stream()))

    def test_a_run_stopped_by_its_budget(self):
        self.assertUnjudgeable(self.good_call(Stream()).result(subtype="error_max_budget_usd", is_error=True))

    def test_a_stream_with_no_tool_calls(self):
        self.assertUnjudgeable(Stream().result())


class AfterChecks(CaseFiles):
    def test_each_command_runs_in_the_fixture_and_reports_its_exit_code(self):
        fixture = self.dir / "fixture"
        (fixture / "docs").mkdir(parents=True)
        (fixture / "docs" / "P1.md").write_text("- [x] **C1.** add works\n")
        script = ("import pathlib,sys; t=pathlib.Path('docs/P1.md').read_text(); "
                  "sys.exit(1 if '[x] **C1' in t and not pathlib.Path('receipt.json').exists() else 0)")
        case = load_case(self.write_case(case_json(after=[
            {"id": "ticked-implies-receipt", "run": f"{PY} -c {shlex.quote(script)}"},
            {"id": "no-derived-number", "run": "! grep -q 15.08 docs/P1.md"}])))
        self.assertEqual(run_after_checks(case, fixture), {"ticked-implies-receipt": 1, "no-derived-number": 0})
        (fixture / "receipt.json").write_text("{}")
        self.assertEqual(run_after_checks(case, fixture)["ticked-implies-receipt"], 0)


class CommandLine(CaseFiles):
    def judge_cli(self, case, stream):
        stream_path = self.dir / "stream.jsonl"
        stream_path.write_text("\n".join(stream.lines()) + "\n")
        fixture = self.dir / "fixture"
        fixture.mkdir(exist_ok=True)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["judge", str(self.write_case(case)), "--stream", str(stream_path), "--fixture", str(fixture),
                         "--require-plugin", "bstack"])
        return code, out.getvalue(), err.getvalue()

    def test_exit_codes_are_the_verdict(self):
        s = Stream()
        s.call("Bash", command="python3 receipt.py --criterion C1 -- true")
        code, out, _ = self.judge_cli(case_json(after=[{"id": "passes", "run": "true"}]), s.result())
        self.assertEqual(code, 0, out)
        code, out, _ = self.judge_cli(case_json(after=[{"id": "fails", "run": "exit 3"}]), s)
        self.assertEqual(code, 1, out)
        self.assertIn("fails", out, "the failing assertion is printed")

    def test_a_broken_case_exits_2_and_names_the_problem(self):
        code, _, err = self.judge_cli(case_json(calls=[], never_calls=[]), Stream().result())
        self.assertEqual(code, 2)
        self.assertIn("at least one assertion", err)


if __name__ == "__main__":
    unittest.main()
