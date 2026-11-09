"""The CLI: `verify` works on a run folder, and every other planned command is an honest stub."""
import io
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
        self.assertEqual(stubs, {"init", "append", "show", "seal", "session", "charter", "import-tsv"})
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

    def test_missing_argument_is_a_usage_error(self):
        code, _, _ = run_cli("verify")
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
