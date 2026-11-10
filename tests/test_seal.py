"""Seals, INDEX.jsonl and `verify --base`, in real temporary git repos."""
import json
import random
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from helpers import T0  # noqa: F401  (puts src on the path)
from test_cli import run_cli

from agent_ledger import _git
from agent_ledger.entry import AgentActor, EntryType, HumanActor, encode_line, next_entry
from agent_ledger.index import IndexBroken, IndexOk, next_line, verify_index_bytes
from agent_ledger.repo_verify import verify_repo
from agent_ledger.run import append, init_run, repo_sink, seal_run
from agent_ledger.sink import LedgerError
from agent_ledger.verify import Kind

AGENT = AgentActor("claude@claude-code#70d5", "lyddonb@github", "claude-code", "opus", "70d5ca84")
HUMAN = HumanActor("lyddonb", "lyddonb@github")
RUN_A, RUN_B = "2026-11-09-alpha", "2026-11-09-bravo"


class GitRepo:
    def __init__(self, test: unittest.TestCase):
        self.root = Path(tempfile.mkdtemp()).resolve()
        test.addCleanup(shutil.rmtree, self.root)
        self.git("init", "-q", "-b", "main")
        self.sink = repo_sink(self.root)

    def git(self, *args, check=True):
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
                               "-c", "commit.gpgsign=false", *args],
                              cwd=self.root, capture_output=True, text=True, check=check).stdout

    def commit(self, message="work"):
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD").strip()

    def start(self, run_id, n=2):
        init_run(self.sink, run_id, actor=HUMAN, why="test")
        for i in range(n):
            append(self.sink, run_id, actor=AGENT, type=EntryType.DECISION, summary=f"step {i}", why="w")

    def ledger(self, run_id):
        return self.sink.run_dir(run_id) / "ledger.jsonl"

    def kinds(self, base=None):
        report = verify_repo(self.root, base)
        return report, [(f.path, f.problem.kind) for f in report.findings]


class SealTest(unittest.TestCase):
    def setUp(self):
        self.repo = GitRepo(self)
        self.repo.start(RUN_A)

    def seal(self, run_id=RUN_A):
        return seal_run(self.repo.sink, self.repo.root, run_id, actor=AGENT)

    def test_seal_names_head_and_commit_and_indexes_the_run(self):
        commit = self.repo.commit()
        before = self.repo.sink.read(RUN_A).head
        result = self.seal()
        self.assertTrue(result.new)
        e = result.entry
        self.assertIs(e.type, EntryType.SEAL)
        self.assertEqual(e.prev, before.link, "the seal follows the head it seals")
        self.assertEqual(dict(e.data), {"commit": commit})
        index = self.repo.sink.read_index()
        self.assertEqual([(l.run_id, l.seal, l.commit) for l in index.lines], [(RUN_A, e.hash, commit)])
        self.repo.commit("seal")
        report, kinds = self.repo.kinds()
        self.assertEqual(kinds, [])
        self.assertTrue(report.ok)
        self.assertEqual((report.sealed, report.index_lines), (1, 1))

    def test_seal_refuses_a_ledger_not_committed_at_head(self):
        self.repo.commit()
        append(self.repo.sink, RUN_A, actor=AGENT, type=EntryType.DECISION, summary="late", why="w")
        with self.assertRaisesRegex(LedgerError, "aren't committed"):
            self.seal()
        self.assertFalse(self.repo.sink.index_path.exists())

    def test_a_sealed_run_takes_no_more_entries(self):
        self.repo.commit()
        self.seal()
        with self.assertRaisesRegex(LedgerError, "sealed"):
            append(self.repo.sink, RUN_A, actor=AGENT, type=EntryType.DECISION, summary="x", why="w")
        with self.assertRaisesRegex(LedgerError, "ledger seal"):
            append(self.repo.sink, "2026-11-09-alpha", actor=AGENT, type=EntryType.SEAL, summary="x", why="w")

    def test_sealing_again_is_a_no_op(self):
        self.repo.commit()
        first = self.seal()
        index = self.repo.sink.index_path.read_bytes()
        again = self.seal()
        self.assertFalse(again.new)
        self.assertFalse(again.indexed)
        self.assertEqual(again.entry.hash, first.entry.hash)
        self.assertEqual(self.repo.sink.index_path.read_bytes(), index)

    def test_appending_a_seal_by_hand_is_refused(self):
        with self.assertRaisesRegex(LedgerError, "ledger seal"):
            append(self.repo.sink, RUN_A, actor=AGENT, type=EntryType.SEAL, summary="x", why="w",
                   data={"commit": "0" * 40})

    def test_a_squash_merge_loses_the_seal_commit_and_verify_says_why(self):
        self.repo.commit("start")
        self.repo.git("checkout", "-q", "-b", "work")
        self.repo.start(RUN_B)
        self.repo.commit("run b")
        self.seal(RUN_B)
        self.repo.commit("seal b")
        self.repo.git("checkout", "-q", "main")
        self.repo.git("merge", "-q", "--squash", "work")
        self.repo.commit("squashed")
        self.repo.git("branch", "-q", "-D", "work")
        self.repo.git("reflog", "expire", "--expire=now", "--all")
        self.repo.git("gc", "-q", "--prune=now")
        _, kinds = self.repo.kinds()
        self.assertIn((f".audit/runs/{RUN_B}/ledger.jsonl", Kind.SEAL_COMMIT), kinds)

    def test_a_seal_commit_that_exists_but_is_off_this_history_is_named(self):
        self.repo.commit("start")
        self.repo.git("checkout", "-q", "-b", "work")
        self.repo.start(RUN_B)
        self.repo.commit("run b")
        self.seal(RUN_B)
        self.repo.commit("seal b")
        self.repo.git("checkout", "-q", "main")
        self.repo.git("merge", "-q", "--squash", "work")
        self.repo.commit("squashed")  # `work` is kept, so the seal's commit still exists
        _, kinds = self.repo.kinds()
        self.assertIn((f".audit/runs/{RUN_B}/ledger.jsonl", Kind.SEAL_COMMIT), kinds)

    def test_a_merge_commit_keeps_the_seal_commit(self):
        self.repo.commit("start")
        self.repo.git("checkout", "-q", "-b", "work")
        self.repo.start(RUN_B)
        self.repo.commit("run b")
        self.seal(RUN_B)
        self.repo.commit("seal b")
        self.repo.git("checkout", "-q", "main")
        self.repo.git("merge", "-q", "--no-ff", "-m", "merge", "work")
        report, kinds = self.repo.kinds(base="HEAD^1")
        self.assertEqual(kinds, [])
        self.assertTrue(report.ok)

    def test_two_branches_sealing_at_once_conflict_and_resealing_resolves_it(self):
        self.repo.commit("start")
        self.repo.git("checkout", "-q", "-b", "a")
        self.seal(RUN_A)
        self.repo.commit("seal a")
        self.repo.git("checkout", "-q", "main")
        self.repo.git("checkout", "-q", "-b", "b")
        self.repo.start(RUN_B)
        self.repo.commit("run b")
        self.seal(RUN_B)
        self.repo.commit("seal b")
        self.repo.git("checkout", "-q", "main")
        self.repo.git("merge", "-q", "--no-ff", "-m", "merge a", "a")
        base = self.repo.git("rev-parse", "HEAD").strip()
        self.repo.git("checkout", "-q", "b")
        self.repo.git("merge", "-q", "--no-ff", "-m", "merge main", "main", check=False)
        self.assertIn("INDEX.jsonl", self.repo.git("diff", "--name-only", "--diff-filter=U"))
        self.repo.git("checkout", "--theirs", ".audit/INDEX.jsonl")  # main's index
        again = self.seal(RUN_B)
        self.assertTrue(again.indexed)
        self.repo.commit("resolve")
        index = self.repo.sink.read_index()
        self.assertEqual([l.run_id for l in index.lines], [RUN_A, RUN_B])
        report, kinds = self.repo.kinds(base=base)
        self.assertEqual(kinds, [])

    def test_an_append_racing_the_seal_makes_it_refuse_not_seal_the_wrong_head(self):
        self.repo.commit()
        before = self.repo.ledger(RUN_A).read_bytes()
        raced = []

        def clock():
            if not raced:  # another writer appends just as the seal is chained
                raced.append(append(self.repo.sink, RUN_A, actor=AGENT, type=EntryType.DECISION,
                                    summary="racing", why="w"))
            return T0

        with self.assertRaisesRegex(LedgerError, "appended while sealing"):
            seal_run(self.repo.sink, self.repo.root, RUN_A, actor=AGENT, clock=clock)
        entries = self.repo.sink.read(RUN_A).entries
        self.assertNotEqual(entries[-1].type, EntryType.SEAL)
        self.assertTrue(self.repo.ledger(RUN_A).read_bytes().startswith(before))
        self.assertFalse(self.repo.sink.index_path.exists())

    def test_a_broken_index_stops_the_seal_before_anything_is_written(self):
        self.repo.commit()
        self.repo.sink.index_path.write_bytes(b"not json\n")
        before = self.repo.ledger(RUN_A).read_bytes()
        with self.assertRaises(LedgerError):
            self.seal()
        self.assertEqual(self.repo.ledger(RUN_A).read_bytes(), before)

    def test_an_index_that_already_names_an_unsealed_run_stops_the_seal(self):
        self.repo.commit()
        self.repo.sink.index_path.write_bytes(next_line((0, "genesis:index"), now=T0, run_id=RUN_A,
                                                        seal="d" * 64, commit="c" * 40))
        before = self.repo.ledger(RUN_A).read_bytes()
        with self.assertRaisesRegex(LedgerError, "already names"):
            self.seal()
        self.assertEqual(self.repo.ledger(RUN_A).read_bytes(), before)

    def test_resealing_when_the_index_names_another_seal_refuses(self):
        self.repo.commit()
        self.seal()
        self.repo.sink.index_path.write_bytes(next_line((0, "genesis:index"), now=T0, run_id=RUN_A,
                                                        seal="d" * 64, commit="c" * 40))
        with self.assertRaisesRegex(LedgerError, "another seal"):
            self.seal()

    def test_a_symlinked_index_is_refused(self):
        self.repo.commit()
        elsewhere = self.repo.root / "elsewhere.jsonl"
        elsewhere.write_bytes(b"")
        self.repo.sink.index_path.symlink_to(elsewhere)
        with self.assertRaisesRegex(LedgerError, "symlink"):
            self.seal()

    def test_concurrent_seals_of_different_runs_make_one_index_chain(self):
        runs = [f"2026-11-09-run{i}" for i in range(6)]
        for r in runs:
            self.repo.start(r, n=0)
        self.repo.commit()
        errors = []

        def go(r):
            try:
                self.seal(r)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=go, args=(r,)) for r in runs]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        index = self.repo.sink.read_index()
        self.assertEqual(sorted(l.run_id for l in index.lines), runs)
        self.assertEqual([l.seq for l in index.lines], list(range(1, 7)))


class TamperTest(unittest.TestCase):
    """Each way someone could rewrite sealed or merged history is named by verify."""

    def setUp(self):
        self.repo = GitRepo(self)
        self.repo.start(RUN_A)
        self.repo.commit()
        self.sealed = seal_run(self.repo.sink, self.repo.root, RUN_A, actor=AGENT).entry
        self.base = self.repo.commit("sealed")

    def test_clean(self):
        report, kinds = self.repo.kinds(base=self.base)
        self.assertEqual(kinds, [])
        self.assertTrue(report.ok)

    def test_an_entry_forged_after_the_seal(self):
        forged = next_entry(self.sealed.head, now=T0, actor=AGENT, type=EntryType.DECISION, summary="x", why="w")
        with open(self.repo.ledger(RUN_A), "ab") as f:
            f.write(encode_line(forged))
        _, kinds = self.repo.kinds()
        self.assertIn((f".audit/runs/{RUN_A}/ledger.jsonl", Kind.AFTER_SEAL), kinds)

    def test_a_sealed_run_missing_from_the_index(self):
        self.repo.sink.index_path.write_bytes(b"")
        _, kinds = self.repo.kinds()
        self.assertIn((f".audit/runs/{RUN_A}/ledger.jsonl", Kind.UNINDEXED_SEAL), kinds)

    def test_an_index_line_for_a_run_that_was_never_sealed(self):
        self.repo.start(RUN_B, n=0)
        line = next_line(self.repo.sink.read_index().head, now=T0, run_id=RUN_B, seal="a" * 64, commit=self.base)
        with open(self.repo.sink.index_path, "ab") as f:
            f.write(line)
        _, kinds = self.repo.kinds()
        self.assertIn((".audit/INDEX.jsonl", Kind.INDEX_MISMATCH), kinds)

    def test_an_index_line_naming_another_seal_is_named(self):
        self.repo.sink.index_path.write_bytes(next_line((0, "genesis:index"), now=T0, run_id=RUN_A,
                                                        seal="e" * 64, commit=self.sealed.data["commit"]))
        _, kinds = self.repo.kinds()
        self.assertIn((".audit/INDEX.jsonl", Kind.INDEX_MISMATCH), kinds)

    def test_a_seal_naming_a_commit_that_holds_another_head(self):
        # Rebuild the run with an extra entry before the seal, as if entries were cut after
        # the commit: the seal's commit no longer holds the head the seal follows.
        lines = self.repo.ledger(RUN_A).read_bytes().splitlines(keepends=True)
        report = self.repo.sink.read(RUN_A)
        pre = report.entries[-2]
        extra = next_entry(pre.head, now=T0, actor=AGENT, type=EntryType.DECISION, summary="inserted", why="w")
        seal = next_entry(extra.head, now=T0, actor=AGENT, type=EntryType.SEAL, summary="Run sealed", why="w",
                          evidence=[self.sealed.data["commit"]], data=dict(self.sealed.data))
        self.repo.ledger(RUN_A).write_bytes(b"".join(lines[:-1]) + encode_line(extra) + encode_line(seal))
        idx = self.repo.sink.index_path
        idx.write_bytes(next_line((0, "genesis:index"), now=T0, run_id=RUN_A, seal=seal.hash,
                                  commit=self.sealed.data["commit"]))
        _, kinds = self.repo.kinds()
        self.assertIn((f".audit/runs/{RUN_A}/ledger.jsonl", Kind.SEAL_COMMIT), kinds)

    def forge_seal(self, data):
        report = self.repo.sink.read(RUN_A)
        pre = report.entries[-2]
        seal = next_entry(pre.head, now=T0, actor=AGENT, type=EntryType.SEAL, summary="Run sealed",
                          why="w", data=data)
        lines = self.repo.ledger(RUN_A).read_bytes().splitlines(keepends=True)
        self.repo.ledger(RUN_A).write_bytes(b"".join(lines[:-1]) + encode_line(seal))
        return seal

    def test_a_seal_with_a_missing_or_hostile_commit_is_a_finding_not_a_crash(self):
        for data in [{}, {"commit": "-h"}, {"commit": "HEAD"}, {"commit": "--output=/tmp/x"}]:
            with self.subTest(data=data):
                self.forge_seal(data)
                _, kinds = self.repo.kinds()
                self.assertIn((f".audit/runs/{RUN_A}/ledger.jsonl", Kind.SEAL_COMMIT), kinds)
                with self.assertRaisesRegex(LedgerError, "no valid commit"):
                    self.repo.sink.index_path.write_bytes(b"")
                    seal_run(self.repo.sink, self.repo.root, RUN_A, actor=AGENT)

    def test_base_sees_a_merged_ledger_under_an_odd_folder_name(self):
        odd = self.repo.root / ".audit" / "runs" / "2026-11-09-caf\u00e9 run"
        odd.mkdir()
        (odd / "ledger.jsonl").write_bytes(b"merged\n")
        base = self.repo.commit("odd")
        (odd / "ledger.jsonl").write_bytes(b"")
        _, kinds = self.repo.kinds(base=base)
        self.assertIn((".audit/runs/2026-11-09-caf\u00e9 run/ledger.jsonl", Kind.BASE_CHANGED), kinds)

    def test_base_catches_an_edited_cut_or_deleted_merged_ledger(self):
        path = self.repo.ledger(RUN_A)
        original = path.read_bytes()
        rel = f".audit/runs/{RUN_A}/ledger.jsonl"
        lines = original.splitlines(keepends=True)
        for label, data in [("edited", lines[0].replace(b"test", b"TEST") + b"".join(lines[1:])),
                            ("cut", b"".join(lines[:-1])),
                            ("emptied", b"")]:
            with self.subTest(label):
                path.write_bytes(data)
                _, kinds = self.repo.kinds(base=self.base)
                self.assertIn((rel, Kind.BASE_CHANGED), kinds)
        path.unlink()
        _, kinds = self.repo.kinds(base=self.base)
        self.assertIn((rel, Kind.BASE_CHANGED), kinds)
        path.write_bytes(original)

    def test_base_catches_a_rewritten_index(self):
        idx = self.repo.sink.index_path
        idx.write_bytes(next_line((0, "genesis:index"), now=T0, run_id=RUN_A, seal=self.sealed.hash,
                                  commit=self.sealed.data["commit"]))
        _, kinds = self.repo.kinds(base=self.base)
        self.assertIn((".audit/INDEX.jsonl", Kind.BASE_CHANGED), kinds)

    def test_base_accepts_lines_added_at_the_end(self):
        self.repo.start(RUN_B)
        _, kinds = self.repo.kinds(base=self.base)
        self.assertEqual(kinds, [])

    def test_a_bad_base_ref_is_an_error(self):
        with self.assertRaises(_git.GitError):
            verify_repo(self.repo.root, "no-such-ref")
        with self.assertRaises(_git.GitError):
            verify_repo(self.repo.root, "--output=/tmp/x")


class IndexFormatTest(unittest.TestCase):
    def build(self, n, rng):
        head, out = (0, "genesis:index"), []
        for i in range(n):
            seal = "".join(rng.choice("0123456789abcdef") for _ in range(64))
            commit = "".join(rng.choice("0123456789abcdef") for _ in range(40))
            line = next_line(head, now=T0, run_id=f"2026-11-0{1 + i % 9}-r{i}", seal=seal, commit=commit)
            obj = json.loads(line)
            head = (obj["seq"], obj["hash"])
            out.append(line)
        return out

    def test_any_changed_byte_deleted_or_swapped_line_is_detected(self):
        for seed in range(40):
            rng = random.Random(seed)
            lines = self.build(rng.randint(2, 6), rng)
            self.assertIsInstance(verify_index_bytes(b"".join(lines)), IndexOk)
            data = bytearray(b"".join(lines))
            i = rng.randrange(len(data) - 1)  # not the final LF
            data[i] = (data[i] + rng.randint(1, 255)) % 256
            with self.subTest(seed=seed, mutation="byte"):
                self.assertIsInstance(verify_index_bytes(bytes(data)), IndexBroken)
            k = rng.randrange(len(lines) - 1)
            with self.subTest(seed=seed, mutation="delete"):
                self.assertIsInstance(verify_index_bytes(b"".join(lines[:k] + lines[k + 1:])), IndexBroken)
            swapped = lines[:]
            swapped[k], swapped[k + 1] = swapped[k + 1], swapped[k]
            with self.subTest(seed=seed, mutation="swap"):
                self.assertIsInstance(verify_index_bytes(b"".join(swapped)), IndexBroken)

    def test_a_run_indexed_twice_is_named(self):
        rng = random.Random(1)
        line = self.build(1, rng)[0]
        obj = json.loads(line)
        second = next_line((obj["seq"], obj["hash"]), now=T0, run_id=obj["run_id"], seal="b" * 64, commit="c" * 40)
        report = verify_index_bytes(line + second)
        self.assertIsInstance(report, IndexBroken)
        self.assertEqual([p.kind for p in report.problems], [Kind.DUPLICATE_SEAL])


class SealCliTest(unittest.TestCase):
    def setUp(self):
        self.repo = GitRepo(self)
        self.actor = json.dumps(AGENT.to_wire())
        self.repo.start(RUN_A)

    def test_seal_then_verify_with_base(self):
        base = self.repo.commit()
        code, out, err = run_cli("seal", RUN_A, "--actor", self.actor, "--root", self.repo.root)
        self.assertEqual(code, 0, err)
        self.assertIn("sealed", out)
        self.repo.commit("seal")
        code, out, err = run_cli("verify", "--base", base, "--root", self.repo.root)
        self.assertEqual(code, 0, out + err)
        self.assertIn("1 sealed, 1 indexed", out)
        code, out, _ = run_cli("seal", RUN_A, "--actor", self.actor, "--root", self.repo.root)
        self.assertEqual(code, 0)
        self.assertIn("nothing to do", out)

    def test_uncommitted_ledger_is_exit_2_with_the_reason(self):
        code, _, err = run_cli("seal", RUN_A, "--actor", self.actor, "--root", self.repo.root)
        self.assertEqual(code, 2)
        self.assertIn("commit", err)

    def test_verify_fails_with_exit_1_and_names_the_line(self):
        base = self.repo.commit()
        path = self.repo.ledger(RUN_A)
        path.write_bytes(path.read_bytes().replace(b"step 0", b"step X"))
        code, out, _ = run_cli("verify", "--base", base, "--root", self.repo.root)
        self.assertEqual(code, 1)
        self.assertIn(f".audit/runs/{RUN_A}/ledger.jsonl:2: base-changed", out)

    def test_append_after_seal_is_refused_by_the_cli(self):
        self.repo.commit()
        run_cli("seal", RUN_A, "--actor", self.actor, "--root", self.repo.root)
        code, _, err = run_cli("append", "--run", RUN_A, "--actor", self.actor, "--type", "decision",
                               "--summary", "s", "--why", "w", "--root", self.repo.root)
        self.assertEqual(code, 2)
        self.assertIn("sealed", err)


if __name__ == "__main__":
    unittest.main()
