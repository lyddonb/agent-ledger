"""`ledger scan`, the Action's runner, and the Action's own shell step, on real git repos."""
import gzip
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import T0  # noqa: F401  (puts src on the path)
from test_cli import run_cli
from test_seal import AGENT, GitRepo, RUN_A

from agent_ledger import scan
from agent_ledger.entry import EntryType
from agent_ledger.redact import Redactor
from agent_ledger.run import append
from agent_ledger.scan import scan_repo

ROOT = Path(__file__).resolve().parent.parent
FAKE_KEY = "sk-" + "ant-api03-" + "Q" * 40  # assembled, so this file holds no key


class ScanTest(unittest.TestCase):
    def setUp(self):
        self.repo = GitRepo(self)
        self.repo.start(RUN_A)
        self.run_dir = self.repo.sink.run_dir(RUN_A)
        self.base = self.repo.commit()

    def kinds(self, base=None):
        _, findings = scan_repo(self.repo.root, Redactor(), base)
        return [(f.path, f.line, f.what) for f in findings]

    def test_a_clean_audit_folder_passes(self):
        self.assertEqual(self.kinds(), [])

    def test_secrets_written_around_the_cli_are_found_with_their_line(self):
        (self.run_dir / "charter.md").write_text(f"---\ntitle: x\n---\nuse {FAKE_KEY}\n")
        ledger = self.run_dir / "ledger.jsonl"
        ledger.write_bytes(ledger.read_bytes() + json.dumps({"why": f"key {FAKE_KEY}"}).encode() + b"\n")
        found = self.kinds()
        self.assertIn((f".audit/runs/{RUN_A}/charter.md", 4, "anthropic_key"), found)
        self.assertIn((f".audit/runs/{RUN_A}/ledger.jsonl", 4, "anthropic_key"), found)

    def test_a_secret_hidden_by_json_escapes_is_found(self):
        (self.run_dir / "notes.md").write_text('{"t": "\\u0073' + FAKE_KEY[1:] + '"}\n')
        self.assertIn((f".audit/runs/{RUN_A}/notes.md", 1, "anthropic_key"), self.kinds())

    def test_a_secret_inside_a_session_archive_is_found(self):
        sessions = self.run_dir / "sessions"
        sessions.mkdir()
        (sessions / "s1.jsonl.gz").write_bytes(gzip.compress(b'{"a":1}\n{"t":"' + FAKE_KEY.encode() + b'"}\n'))
        self.assertIn((f".audit/runs/{RUN_A}/sessions/s1.jsonl.gz", 2, "anthropic_key"), self.kinds())

    def test_files_that_cannot_be_scanned_are_findings(self):
        (self.run_dir / "blob.bin").write_bytes(b"\xff\xfe\x00")
        (self.run_dir / "bad.gz").write_bytes(b"not gzip")
        (self.run_dir / "link.md").symlink_to(self.run_dir / "ledger.jsonl")
        found = {(p, w) for p, _, w in self.kinds()}
        self.assertIn((f".audit/runs/{RUN_A}/blob.bin", "not UTF-8 text"), found)
        self.assertIn((f".audit/runs/{RUN_A}/bad.gz", "not a readable gzip file"), found)
        self.assertTrue(any(p.endswith("link.md") and "symlink" in w for p, w in found))

    def test_a_gzip_bomb_stops_at_the_cap(self):
        sessions = self.run_dir / "sessions"
        sessions.mkdir()
        (sessions / "big.jsonl.gz").write_bytes(gzip.compress(b"a" * 1000 + b"\n" * 5000))
        old = scan.MAX_DECOMPRESSED
        scan.MAX_DECOMPRESSED = 2000
        self.addCleanup(setattr, scan, "MAX_DECOMPRESSED", old)
        self.assertIn((f".audit/runs/{RUN_A}/sessions/big.jsonl.gz", 0, "too large to scan when decompressed"),
                      self.kinds())

    def test_base_scans_only_committed_changes(self):
        (self.run_dir / "old.md").write_text(f"{FAKE_KEY}\n")
        base = self.repo.commit("a secret already merged")
        self.assertEqual(self.kinds(base), [], "unchanged files aren't rescanned")
        append(self.repo.sink, RUN_A, actor=AGENT, type=EntryType.DECISION, summary="s", why="w")
        (self.run_dir / "new.md").write_text(f"x\n{FAKE_KEY}\n")
        self.assertEqual(self.kinds(base), [], "uncommitted files aren't what merges")
        self.repo.commit("pr")
        self.assertEqual(self.kinds(base), [(f".audit/runs/{RUN_A}/new.md", 2, "anthropic_key")])

    def test_cli_names_the_line_and_pattern_but_never_the_secret(self):
        (self.run_dir / "charter.md").write_text(f"x\n{FAKE_KEY}\n")
        code, out, err = run_cli("scan", "--root", self.repo.root)
        self.assertEqual(code, 1)
        self.assertIn(f".audit/runs/{RUN_A}/charter.md:2: secret: a anthropic_key match", out)
        self.assertNotIn("Q" * 20, out + err)


class RunnerTest(unittest.TestCase):
    def test_a_repos_own_agent_ledger_package_cannot_replace_the_checker(self):
        repo = GitRepo(self)
        repo.start(RUN_A)
        repo.commit()
        fake = repo.root / "agent_ledger"
        fake.mkdir()
        (fake / "__init__.py").write_text("")
        (fake / "cli.py").write_text("import sys\nprint('HIJACKED')\ndef main(argv): return 0\n")
        proc = subprocess.run([sys.executable, "-I", str(ROOT / "ci/run_ledger.py"), "verify", "--root", str(repo.root)],
                              cwd=repo.root, env=dict(os.environ, PYTHONPATH=str(repo.root)), capture_output=True, text=True)
        self.assertNotIn("HIJACKED", proc.stdout)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("1 runs", proc.stdout)


def _action_script():
    """The shell the Action's `ledger verify and scan` step runs, exactly as action.yml has it."""
    text = (ROOT / "action.yml").read_text()
    block = text.split("      run: |\n", 1)[1]
    lines = []
    for line in block.split("\n"):
        if line and not line.startswith("        "):
            break
        lines.append(line[8:])
    return "\n".join(lines)


class ActionScriptTest(unittest.TestCase):
    """Runs the Action's step on a real repo, with `python` pointing at this interpreter."""

    def setUp(self):
        self.repo = GitRepo(self)
        self.repo.start(RUN_A)
        self.repo.commit("main")
        self.repo.git("update-ref", "refs/remotes/origin/main", "HEAD")
        self.bin = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.bin)
        (self.bin / "python").symlink_to(sys.executable)

    def run_step(self, **env):
        full = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}", GITHUB_WORKSPACE=str(self.repo.root),
                    RUNNER_SCRIPT=str(ROOT / "ci/run_ledger.py"), BASE_INPUT="", PR_BASE="", PUSH_BEFORE="")
        full.update(env)
        return subprocess.run(["bash", "-c", _action_script()], cwd=self.repo.root, env=full,
                              capture_output=True, text=True)

    def test_a_pull_request_is_checked_against_its_base_branch(self):
        append(self.repo.sink, RUN_A, actor=AGENT, type=EntryType.DECISION, summary="more", why="w")
        self.repo.commit("pr")
        proc = self.run_step(PR_BASE="main")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("merged lines unchanged since origin/main", proc.stdout)
        self.assertIn("ok: no secrets in 1 files changed since origin/main", proc.stdout)

    def test_a_rewritten_merged_line_fails_the_job(self):
        path = self.repo.ledger(RUN_A)
        path.write_bytes(path.read_bytes().replace(b"step 0", b"step X"))
        self.repo.commit("rewrite")
        proc = self.run_step(PR_BASE="main")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("base-changed", proc.stdout)
        self.assertIn("::error::ledger-verify failed", proc.stdout)

    def test_a_secret_fails_the_job_even_when_verify_passes(self):
        (self.repo.sink.run_dir(RUN_A) / "notes.md").write_text(FAKE_KEY + "\n")
        self.repo.commit("secret")
        proc = self.run_step(PR_BASE="main")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("merged lines unchanged", proc.stdout, "verify still ran and passed")
        self.assertIn("secret: a anthropic_key match", proc.stdout)

    def test_a_push_uses_the_commit_before_it(self):
        before = self.repo.git("rev-parse", "HEAD").strip()
        append(self.repo.sink, RUN_A, actor=AGENT, type=EntryType.DECISION, summary="more", why="w")
        self.repo.commit("push")
        proc = self.run_step(PUSH_BEFORE=before)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn(f"since {before}", proc.stdout)

    def test_a_new_branch_push_warns_and_checks_chains(self):
        proc = self.run_step(PUSH_BEFORE="0" * 40)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("::warning::no base", proc.stdout)
        self.assertIn("ok: 1 runs", proc.stdout)

    def test_a_shallow_clone_is_refused(self):
        shallow = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, shallow)
        self.repo.commit("second")
        subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{self.repo.root}", str(shallow / "c")],
                       check=True, capture_output=True)
        full = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}", GITHUB_WORKSPACE=str(shallow / "c"),
                    RUNNER_SCRIPT=str(ROOT / "ci/run_ledger.py"), BASE_INPUT="", PR_BASE="main", PUSH_BEFORE="")
        proc = subprocess.run(["bash", "-c", _action_script()], cwd=shallow / "c", env=full,
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("fetch-depth: 0", proc.stdout)

    def test_the_step_cannot_be_hijacked_by_the_checked_repo(self):
        fake = self.repo.root / "agent_ledger"
        fake.mkdir()
        (fake / "__init__.py").write_text("")
        (fake / "cli.py").write_text("print('HIJACKED')\ndef main(argv): return 0\n")
        (self.repo.root / "ci").mkdir()
        (self.repo.root / "ci" / "run_ledger.py").write_text("print('HIJACKED')\n")
        (self.repo.root / "sitecustomize.py").write_text("print('HIJACKED')\n")  # runs at startup unless -I
        proc = self.run_step(PR_BASE="main", PYTHONPATH=str(self.repo.root))
        self.assertNotIn("HIJACKED", proc.stdout + proc.stderr)
        self.assertIn("ok: 1 runs", proc.stdout)

    def test_every_expression_reaches_the_shell_through_env(self):
        script = _action_script()
        self.assertIsNone(re.search(r"\$\{\{", script), "no ${{ }} spliced into the run block")


if __name__ == "__main__":
    unittest.main()
