"""The CLI: init, append, verify and show work; every other planned command is an honest stub."""
import io
import json
import random
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from helpers import RUN_ID, build_chain

from agent_ledger import __version__, cli

ROOT = Path(__file__).resolve().parent.parent


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = cli.main([str(a) for a in argv])
        except SystemExit as e:  # argparse exits for --version, --help and usage errors
            code = e.code
    return code, out.getvalue(), err.getvalue()


class CliSurfaceTest(unittest.TestCase):
    def test_version(self):
        code, out, _ = run_cli("--version")
        self.assertEqual(code, 0)
        self.assertIn(__version__, out)

    def test_help_lists_every_planned_command(self):
        code, out, _ = run_cli()
        self.assertEqual(code, 0)
        for name in cli.COMMANDS:
            self.assertIn(name, out)

    def test_every_handler_is_a_planned_command(self):
        self.assertLessEqual(set(cli.HANDLERS), set(cli.COMMANDS))

    def test_every_unbuilt_command_is_an_honest_stub(self):
        stubs = set(cli.COMMANDS) - set(cli.HANDLERS)
        self.assertEqual(stubs, {"import-tsv"})
        for name in stubs:
            with self.subTest(name):
                code, _, err = run_cli(name)
                self.assertEqual(code, 2)
                self.assertIn("not implemented", err)
                spec = cli.COMMANDS[name][1]
                self.assertTrue((ROOT / spec).is_file(), f"{name} points at missing {spec}")


class VerifyCommandTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.run_dir = Path(tmp.name) / RUN_ID
        self.run_dir.mkdir()
        self.lines = build_chain(random.Random(1), 4)

    def write(self, lines):
        (self.run_dir / "ledger.jsonl").write_bytes(b"".join(lines))

    def test_valid_run_exits_0_and_prints_the_head(self):
        self.write(self.lines)
        code, out, _ = run_cli("verify", self.run_dir)
        self.assertEqual(code, 0)
        self.assertIn(f"ok {RUN_ID}: 4 entries, head seq 4", out)
        self.assertIn("chain-only", out)

    def test_tampered_run_exits_1_and_names_the_line(self):
        self.lines[2] = self.lines[2].replace(b'"v":1', b'"v":1 ', 1)
        self.write(self.lines)
        code, out, _ = run_cli("verify", self.run_dir)
        self.assertEqual(code, 1)
        self.assertIn(f"{self.run_dir / 'ledger.jsonl'}:3: non-canonical:", out)
        self.assertIn("FAILED", out)

    def test_dot_works_from_inside_the_run_folder(self):
        import os
        self.write(self.lines)
        cwd = os.getcwd()
        os.chdir(self.run_dir)
        self.addCleanup(os.chdir, cwd)
        code, out, _ = run_cli("verify", ".")
        self.assertEqual(code, 0, out)

    def test_symlinked_ledger_is_refused(self):
        real = self.run_dir.parent / "elsewhere.jsonl"
        real.write_bytes(b"".join(self.lines))
        (self.run_dir / "ledger.jsonl").symlink_to(real)
        code, out, _ = run_cli("verify", self.run_dir)
        self.assertEqual(code, 1)
        self.assertIn("not-a-file", out)

    def test_missing_ledger_is_a_failed_check(self):
        code, out, _ = run_cli("verify", self.run_dir)
        self.assertEqual(code, 1)
        self.assertIn("missing-ledger", out)

    def test_folder_that_is_not_a_run_id_fails(self):
        other = self.run_dir.parent / "not-a-run"
        other.mkdir()
        code, out, _ = run_cli("verify", other)
        self.assertEqual(code, 1)
        self.assertIn("bad-run-folder", out)

    def test_missing_folder_is_an_error(self):
        code, _, err = run_cli("verify", self.run_dir.parent / "2026-01-01-nope")
        self.assertEqual(code, 2)
        self.assertIn("not a directory", err)

    def test_base_with_a_run_folder_is_a_usage_error(self):
        code, _, err = run_cli("verify", ".", "--base", "main")
        self.assertEqual(code, 2)
        self.assertIn("whole repo", err)


AGENT = json.dumps({"kind": "agent", "id": "claude@claude-code#a0f4", "on_behalf_of": "lyddonb@github",
                    "tool": "claude-code", "model": "opus", "session": "a0f4072e"})
HUMAN = '{"kind": "human", "id": "lyddonb", "on_behalf_of": "lyddonb@github"}'
DEMO = "2026-11-08-demo"


class WriteCommandsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.run_dir = self.root / ".audit" / "runs" / DEMO

    def init(self, *extra):
        return run_cli("init", "demo", "--date", "2026-11-08", "--actor", AGENT, "--root", self.root, *extra)

    def append(self, *args):
        return run_cli("append", "--run", DEMO, "--actor", AGENT, "--root", self.root, *args)

    def test_init_append_show_round_trip(self):
        code, out, err = self.init("--why", "Prove the CLI")
        self.assertEqual(code, 0, err)
        self.assertIn(f"started {DEMO}", out)
        code, out, err = self.append("--type", "decision", "--summary", "Use flock", "--why", "stdlib only",
                                     "--evidence", "src/agent_ledger/sink.py:1", "--evidence", "PR 2",
                                     "--data", '{"options": ["flock", "lockfile"], "chosen": "flock"}')
        self.assertEqual(code, 0, err)
        self.assertIn(f"appended {DEMO} seq 2", out)
        code, out, err = run_cli("append", "--run", DEMO, "--actor", HUMAN, "--root", self.root,
                                 "--type", "gate-answer", "--summary", "Yes", "--why", "Reviewed")
        self.assertEqual(code, 0, err)

        code, out, _ = run_cli("verify", self.run_dir)
        self.assertEqual(code, 0, out)
        entries = [json.loads(line) for line in (self.run_dir / "ledger.jsonl").read_text().splitlines()]
        self.assertEqual([e["type"] for e in entries], ["state", "decision", "gate-answer"])
        self.assertEqual(entries[1]["evidence"], ["src/agent_ledger/sink.py:1", "PR 2"])
        self.assertEqual(entries[1]["data"], {"chosen": "flock", "options": ["flock", "lockfile"]})
        self.assertEqual(entries[2]["actor"]["kind"], "human")

        code, out, _ = run_cli("show", DEMO, "--root", self.root)
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertEqual(lines[0].split(), ["seq", "ts", "type", "actor", "summary"])
        self.assertEqual(lines[3].split()[:4], ["2", entries[1]["ts"], "decision", "agent:claude@claude-code#a0f4"])
        self.assertTrue(lines[3].endswith("  Use flock"), lines[3])
        self.assertTrue(lines[4].strip() == "why: stdlib only", lines[4])
        self.assertIn(f"{DEMO}: 3 entries, verified", lines[-1])

    def test_root_defaults_to_the_enclosing_repo(self):
        import os
        (self.root / ".git").mkdir()
        sub = self.root / "src" / "deep"
        sub.mkdir(parents=True)
        cwd = os.getcwd()
        os.chdir(sub)
        self.addCleanup(os.chdir, cwd)
        code, _, err = run_cli("init", "demo", "--date", "2026-11-08", "--actor", AGENT)
        self.assertEqual(code, 0, err)
        self.assertTrue((self.run_dir / "ledger.jsonl").is_file())

    def test_show_marks_imported_entries(self):
        self.init()
        code, _, err = self.append("--type", "decision", "--summary", "Old call", "--why", "w", "--data",
                                   '{"imported": {"source": "notes.md#1", "source_ts": "2026-11-09T02:51:37Z",'
                                   ' "by": "claude@claude-code#a0f4"}}')
        self.assertEqual(code, 0, err)
        _, out, _ = run_cli("show", DEMO, "--root", self.root)
        self.assertIn("[imported] Old call", out)

    def test_show_labels_the_actor_kind_and_escapes_invisible_characters(self):
        self.init()
        code, _, err = run_cli("append", "--run", DEMO, "--root", self.root, "--type", "approval",
                               "--actor", '{"kind": "human", "id": "lyddonb\u200b", "on_behalf_of": "x@github"}',
                               "--summary", "Approved \u202edetrats", "--why", "w")
        self.assertEqual(code, 0, err)
        _, out, _ = run_cli("show", DEMO, "--root", self.root)
        self.assertIn("agent:claude@claude-code#a0f4", out)
        self.assertIn("human:lyddonb\\u200b", out)
        code, _, err = run_cli("append", "--run", DEMO, "--root", self.root, "--type", "approval",
                               "--actor", '{"kind": "human", "id": "lyddonb\u3164", "on_behalf_of": "x@github"}',
                               "--summary", "Filler\ufe0e", "--why", "w")
        self.assertEqual(code, 0, err)
        _, out, _ = run_cli("show", DEMO, "--root", self.root)
        self.assertIn("human:lyddonb\\u3164", out)
        code, _, err = run_cli("append", "--run", DEMO, "--root", self.root, "--type", "approval",
                               "--actor", '{"kind": "human", "id": "lydd\\u043enb", "on_behalf_of": "x@github"}',
                               "--summary", "Cyrillic o", "--why", "w")
        self.assertEqual(code, 0, err)
        _, out, _ = run_cli("show", DEMO, "--root", self.root)
        self.assertIn("human:lydd\\u043enb", out, "a look-alike login must not print as lyddonb")
        self.assertIn("Filler\\ufe0e", out)
        self.assertIn("Approved \\u202edetrats", out)
        self.assertNotIn("\u202e", out)
        self.assertNotIn("\u200b", out)

    def test_outside_a_repo_without_root_is_an_error(self):
        import os
        cwd = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, cwd)
        code, _, err = run_cli("init", "demo", "--actor", AGENT)
        self.assertEqual(code, 2)
        self.assertIn("--root", err)
        self.assertFalse((self.root / ".audit").exists())

    def test_init_twice_is_refused(self):
        self.init()
        code, _, err = self.init()
        self.assertEqual(code, 2)
        self.assertIn("already has a ledger.jsonl", err)

    def test_append_to_a_broken_ledger_exits_1_and_names_the_problem(self):
        self.init()
        ledger = self.run_dir / "ledger.jsonl"
        ledger.write_bytes(ledger.read_bytes().replace(b"Run started", b"Run startex"))
        code, out, err = self.append("--type", "state", "--summary", "s", "--why", "w")
        self.assertEqual(code, 1)
        self.assertIn("refusing", err)
        self.assertIn(f"{ledger}:1: hash-mismatch", out)
        code, out, _ = run_cli("show", DEMO, "--root", self.root)
        self.assertEqual(code, 1)

    def test_bad_input_is_a_usage_error_and_writes_nothing(self):
        self.init()
        before = (self.run_dir / "ledger.jsonl").read_bytes()
        cases = {
            "agent approval": ("--type", "approval", "--summary", "s", "--why", "w"),
            "two-line summary": ("--type", "state", "--summary", "a\nb", "--why", "w"),
            "float data": ("--type", "spend", "--summary", "s", "--why", "w", "--data", '{"usd": 4.25}'),
            "array data": ("--type", "state", "--summary", "s", "--why", "w", "--data", "[1]"),
            "unknown type": ("--type", "nope", "--summary", "s", "--why", "w"),
            "missing why": ("--type", "state", "--summary", "s"),
        }
        for name, args in cases.items():
            with self.subTest(name):
                code, _, _ = self.append(*args)
                self.assertEqual(code, 2)
        code, _, _ = run_cli("append", "--run", DEMO, "--actor", '{"kind": "robot"}', "--root", self.root,
                             "--type", "state", "--summary", "s", "--why", "w")
        self.assertEqual(code, 2)
        self.assertEqual((self.run_dir / "ledger.jsonl").read_bytes(), before)

    def test_append_to_an_unknown_run_is_an_error(self):
        code, _, err = self.append("--type", "state", "--summary", "s", "--why", "w")
        self.assertEqual(code, 2)
        self.assertIn("ledger init", err)

    def test_bad_run_id_is_an_error(self):
        code, _, err = run_cli("append", "--run", "../escape", "--actor", AGENT, "--root", self.root,
                               "--type", "state", "--summary", "s", "--why", "w")
        self.assertEqual(code, 2)
        self.assertIn("run id", err)


if __name__ == "__main__":
    unittest.main()
