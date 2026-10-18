"""The pre-M0 CLI stub: the planned surface is visible and nothing pretends to work."""
import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent_ledger import __version__, cli  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


class CliStubTest(unittest.TestCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = cli.main(list(argv))
            except SystemExit as e:  # argparse exits for --version and --help
                code = e.code
        return code, out.getvalue(), err.getvalue()

    def test_version(self):
        code, out, _ = self.run_cli("--version")
        self.assertEqual(code, 0)
        self.assertIn(__version__, out)

    def test_help_lists_every_planned_command(self):
        code, out, _ = self.run_cli()
        self.assertEqual(code, 0)
        for name in cli.COMMANDS:
            self.assertIn(name, out)

    def test_every_command_is_an_honest_stub(self):
        for name, (_help, spec) in cli.COMMANDS.items():
            with self.subTest(name):
                code, _, err = self.run_cli(name)
                self.assertEqual(code, 2)
                self.assertIn("not implemented", err)
                self.assertTrue((ROOT / spec).is_file(), f"{name} points at missing {spec}")


if __name__ == "__main__":
    unittest.main()
