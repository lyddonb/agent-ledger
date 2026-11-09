"""The repo sink and append: one chain, whoever writes and however they race."""
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import timedelta
from pathlib import Path

from helpers import RUN_ID, T0, rand_actor

import random

from agent_ledger.entry import ChainHead, EntryType, HumanActor, next_entry
from agent_ledger.run import append, init_run
from agent_ledger.sink import ChainBroken, NoSuchRun, RepoSink, RunExists, StaleHead
from agent_ledger.verify import Kind, Verified, verify_run

SRC = Path(__file__).resolve().parent.parent / "src"
AGENT = rand_actor(random.Random(0), human=False)


def ticking(start=T0):
    t = [start]

    def clock():
        t[0] += timedelta(seconds=1)
        return t[0]
    return clock


class RepoSinkTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.sink = RepoSink(Path(tmp.name) / ".audit" / "runs")
        self.ledger = self.sink.run_dir(RUN_ID) / "ledger.jsonl"

    def start(self):
        return init_run(self.sink, RUN_ID, actor=AGENT, why="test", clock=ticking())

    def test_init_writes_one_verified_entry_and_nothing_else(self):
        first = self.start()
        report = verify_run(self.sink.run_dir(RUN_ID))
        self.assertIsInstance(report, Verified)
        self.assertEqual(report.entries, (first,))
        self.assertEqual(sorted(p.name for p in self.ledger.parent.iterdir()), ["ledger.jsonl"],
                         "the temp file must not be left behind")
        self.assertEqual(self.ledger.stat().st_mode & 0o777, 0o644)

    def test_init_keeps_an_existing_run_folder(self):
        folder = self.sink.run_dir(RUN_ID)
        folder.mkdir(parents=True)
        (folder / "charter.md").write_text("charter")
        self.start()
        self.assertEqual((folder / "charter.md").read_text(), "charter")

    def test_init_refuses_a_run_that_has_a_ledger(self):
        self.start()
        before = self.ledger.read_bytes()
        with self.assertRaises(RunExists):
            self.start()
        self.assertEqual(self.ledger.read_bytes(), before)

    def test_append_chains_onto_the_head(self):
        self.start()
        clock = ticking(T0 + timedelta(hours=1))
        a = append(self.sink, RUN_ID, actor=AGENT, type=EntryType.DECISION, summary="a", why="w",
                   evidence=["x.py:1"], data={"n": 1}, clock=clock)
        b = append(self.sink, RUN_ID, actor=HumanActor("lyddonb", "lyddonb@github"),
                   type=EntryType.GATE_ANSWER, summary="b", why="w", clock=clock)
        self.assertEqual((a.seq, b.seq, b.prev), (2, 3, a.hash))
        report = verify_run(self.sink.run_dir(RUN_ID))
        self.assertIsInstance(report, Verified)
        self.assertEqual(report.entries[1:], (a, b))

    def test_write_refuses_an_entry_chained_to_a_stale_head(self):
        first = self.start()
        stale = next_entry(first.head, now=T0, actor=AGENT, type=EntryType.STATE, summary="s", why="w")
        append(self.sink, RUN_ID, actor=AGENT, type=EntryType.STATE, summary="won", why="w", clock=ticking())
        before = self.ledger.read_bytes()
        with self.assertRaises(StaleHead):
            self.sink.write(stale)
        self.assertEqual(self.ledger.read_bytes(), before)

    def test_write_refuses_a_second_genesis(self):
        self.start()
        other = next_entry(ChainHead.genesis(RUN_ID), now=T0, actor=AGENT, type=EntryType.STATE,
                           summary="s", why="w")
        with self.assertRaises(StaleHead):
            self.sink.write(other)

    def test_append_refuses_a_broken_ledger_and_leaves_it_alone(self):
        self.start()
        append(self.sink, RUN_ID, actor=AGENT, type=EntryType.STATE, summary="s", why="w", clock=ticking())
        tampered = self.ledger.read_bytes().replace(b'"summary":"s"', b'"summary":"t"')
        self.ledger.write_bytes(tampered)
        with self.assertRaises(ChainBroken) as caught:
            append(self.sink, RUN_ID, actor=AGENT, type=EntryType.STATE, summary="x", why="w")
        self.assertEqual(caught.exception.report.problems[0].kind, Kind.HASH_MISMATCH)
        self.assertEqual(self.ledger.read_bytes(), tampered)

    def test_append_refuses_after_a_torn_write(self):
        self.start()
        with self.ledger.open("ab") as f:
            f.write(b'{"v":1,"seq":2')
        with self.assertRaises(ChainBroken) as caught:
            append(self.sink, RUN_ID, actor=AGENT, type=EntryType.STATE, summary="x", why="w")
        self.assertIn(Kind.TORN_LINE, {p.kind for p in caught.exception.report.problems})

    def test_append_refuses_a_symlinked_ledger(self):
        self.start()
        real = self.ledger.parent.parent / "elsewhere.jsonl"
        os.replace(self.ledger, real)
        self.ledger.symlink_to(real)
        before = real.read_bytes()
        with self.assertRaises(ChainBroken) as caught:
            append(self.sink, RUN_ID, actor=AGENT, type=EntryType.STATE, summary="x", why="w")
        self.assertEqual(caught.exception.report.problems[0].kind, Kind.NOT_A_FILE)
        self.assertEqual(real.read_bytes(), before)

    def test_append_to_a_run_that_was_never_started(self):
        with self.assertRaises(NoSuchRun):
            append(self.sink, RUN_ID, actor=AGENT, type=EntryType.STATE, summary="x", why="w")

    def test_run_ids_cannot_escape_the_runs_folder(self):
        for bad in ("../2026-01-01-x", "2026-01-01-x/../../y", "/etc", "2026-13-01-x"):
            with self.subTest(bad), self.assertRaises(ValueError):
                self.sink.run_dir(bad)

    def test_write_follows_a_ledger_replaced_while_it_waited_for_the_lock(self):
        import threading
        from agent_ledger.sink import _locked
        first = self.start()
        entry = next_entry(first.head, now=T0, actor=AGENT, type=EntryType.STATE, summary="late", why="w")
        result = {}
        with _locked(self.ledger, RUN_ID, exclusive=True):
            writer = threading.Thread(target=lambda: result.setdefault("r", self.sink.write(entry)))
            writer.start()
            time.sleep(0.3)  # the writer is now blocked in flock on the original file
            copy = self.ledger.with_name("copy.jsonl")
            copy.write_bytes(self.ledger.read_bytes())
            os.replace(copy, self.ledger)  # what git checkout does
        writer.join(5)
        self.assertFalse(writer.is_alive())
        report = verify_run(self.sink.run_dir(RUN_ID))
        self.assertIsInstance(report, Verified)
        self.assertEqual([e.summary for e in report.entries], ["Run started", "late"],
                         "the write must land in the file now at ledger.jsonl, not the replaced one")

    def test_a_write_that_fails_midway_leaves_the_ledger_as_it_was(self):
        from unittest import mock
        from agent_ledger import sink as sink_mod
        self.start()
        before = self.ledger.read_bytes()
        real = sink_mod._write_all

        def half_then_fail(fd, data):
            real(fd, data[: len(data) // 2])
            raise OSError(28, "No space left on device")
        with mock.patch.object(sink_mod, "_write_all", half_then_fail), self.assertRaises(OSError):
            append(self.sink, RUN_ID, actor=AGENT, type=EntryType.STATE, summary="x", why="w")
        self.assertEqual(self.ledger.read_bytes(), before)
        append(self.sink, RUN_ID, actor=AGENT, type=EntryType.STATE, summary="y", why="w")
        self.assertIsInstance(verify_run(self.sink.run_dir(RUN_ID)), Verified)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root ignores file modes")
    def test_an_unwritable_ledger_is_an_io_error_not_a_broken_chain(self):
        self.start()
        self.ledger.chmod(0o444)
        self.addCleanup(self.ledger.chmod, 0o644)
        with self.assertRaises(PermissionError):
            append(self.sink, RUN_ID, actor=AGENT, type=EntryType.STATE, summary="x", why="w")

    def test_symlinked_folders_are_refused(self):
        from agent_ledger.sink import LedgerError
        elsewhere = Path(self.sink.runs_dir.parent.parent) / "elsewhere"
        elsewhere.mkdir()
        self.sink.runs_dir.mkdir(parents=True)
        self.sink.run_dir(RUN_ID).symlink_to(elsewhere)
        with self.assertRaisesRegex(LedgerError, "symlink"):
            self.start()
        self.assertEqual(list(elsewhere.iterdir()), [])
        with self.assertRaisesRegex(LedgerError, "symlink"):
            append(self.sink, RUN_ID, actor=AGENT, type=EntryType.STATE, summary="x", why="w")

    def test_imported_marker_must_have_its_spec_shape(self):
        from agent_ledger.entry import EntryError
        self.start()
        good = {"source": "notes.md#1", "source_ts": "2026-11-09T02:51:37Z", "by": "claude@claude-code#a0f4"}
        append(self.sink, RUN_ID, actor=AGENT, type=EntryType.DECISION, summary="x", why="w",
               data={"imported": good})
        for bad in (True, {"source": "x"}, {**good, "source_ts": "yesterday"},
                    {**good, "source_ts": "2026-02-31T00:00:00Z"}, {**good, "by": ""}):
            with self.subTest(bad), self.assertRaises(EntryError):
                append(self.sink, RUN_ID, actor=AGENT, type=EntryType.DECISION, summary="x", why="w",
                       data={"imported": bad})

    def test_a_ledger_replaced_mid_write_is_reported_not_lost_silently(self):
        from unittest import mock
        from agent_ledger import sink as sink_mod
        from agent_ledger.sink import LedgerError
        first = self.start()
        entry = next_entry(first.head, now=T0, actor=AGENT, type=EntryType.STATE, summary="late", why="w")
        real = sink_mod._write_all

        def write_then_checkout(fd, data):
            real(fd, data)
            copy = self.ledger.with_name("copy.jsonl")
            copy.write_bytes(self.ledger.read_bytes()[: -len(data)])
            os.replace(copy, self.ledger)
        with mock.patch.object(sink_mod, "_write_all", write_then_checkout):
            with self.assertRaisesRegex(LedgerError, "replaced during the write"):
                self.sink.write(entry)

    def test_spend_entries_follow_the_s3_shape(self):
        from agent_ledger.entry import EntryError
        self.start()
        good = {"usd_micros": 4250000, "tokens": 812345, "cache_read_tokens": 9000000, "wall_s": 5400,
                "estimate": True, "note": "other keys are allowed"}
        append(self.sink, RUN_ID, actor=AGENT, type=EntryType.SPEND, summary="s", why="w", data=good)
        bad_cases = {
            "no usd_micros": {"tokens": 1},
            "negative": {"usd_micros": -1},
            "bool as count": {"usd_micros": True},
            "string count": {"usd_micros": 1, "tokens": "812345"},
            "non-bool estimate": {"usd_micros": 1, "estimate": 1},
        }
        for name, data in bad_cases.items():
            with self.subTest(name), self.assertRaises(EntryError):
                append(self.sink, RUN_ID, actor=AGENT, type=EntryType.SPEND, summary="s", why="w", data=data)
        append(self.sink, RUN_ID, actor=AGENT, type=EntryType.DECISION, summary="s", why="w",
               data={"usd_micros": "only spend entries are checked"})

    def test_seal_is_an_honest_stub(self):
        with self.assertRaisesRegex(NotImplementedError, "unit 4"):
            self.sink.seal(RUN_ID, "0" * 64, "abc")


# Each worker waits for the go file, then appends N entries as its own agent.
_WORKER = r"""
import os, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from agent_ledger.entry import AgentActor, EntryType
from agent_ledger.run import append
from agent_ledger.sink import RepoSink, StaleHead
runs, run_id, me, n, go = sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5]), Path(sys.argv[6])
actor = AgentActor(f"worker@test#{me}", "lyddonb@github", "test", "none", me)


class CountingSink(RepoSink):
    lost = 0

    def write(self, entry):
        try:
            super().write(entry)
        except StaleHead:
            CountingSink.lost += 1
            raise


while not go.exists():
    time.sleep(0.001)
for i in range(n):
    append(CountingSink(Path(runs)), run_id, actor=actor, type=EntryType.STATE, summary=f"{me} {i}", why="race")
print(CountingSink.lost)
"""


class ConcurrentAppendTest(unittest.TestCase):
    """S3 acceptance: two subagents appending at once produce one valid chain."""

    def race(self, workers: int, each: int) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sink = RepoSink(Path(tmp) / ".audit" / "runs")
            init_run(sink, RUN_ID, actor=AGENT, why="race")
            go = Path(tmp) / "go"
            procs = [subprocess.Popen([sys.executable, "-c", _WORKER, str(SRC), str(sink.runs_dir), RUN_ID,
                                       f"w{k}", str(each), str(go)],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                     for k in range(workers)]
            time.sleep(0.3)  # let every worker reach its wait loop
            go.touch()
            lost = 0
            for p in procs:
                out, err = p.communicate(timeout=120)
                self.assertEqual(p.returncode, 0, err.decode())
                lost += int(out)
            self.assertGreater(lost, 0, "no writer ever lost a race, so this run proved nothing about the lock")
            report = verify_run(sink.run_dir(RUN_ID))
            self.assertIsInstance(report, Verified, getattr(report, "problems", None))
            self.assertEqual(len(report.entries), 1 + workers * each)
            got = sorted(e.summary for e in report.entries[1:])
            self.assertEqual(got, sorted(f"w{k} {i}" for k in range(workers) for i in range(each)),
                             "every append must land exactly once")
            for k in range(workers):
                mine = [e.summary for e in report.entries if e.summary.startswith(f"w{k} ")]
                self.assertEqual(mine, [f"w{k} {i}" for i in range(each)], "a worker's own appends stay in order")

    def test_two_processes_appending_at_once_make_one_chain(self):
        self.race(workers=2, each=40)

    def test_four_processes_appending_at_once_make_one_chain(self):
        self.race(workers=4, each=15)

    def test_two_processes_starting_the_same_run_at_once_make_one_ledger(self):
        script = (
            "import sys, time; from pathlib import Path; sys.path.insert(0, sys.argv[1])\n"
            "from agent_ledger.entry import AgentActor\n"
            "from agent_ledger.run import init_run\n"
            "from agent_ledger.sink import RepoSink, RunExists\n"
            "go = Path(sys.argv[4])\n"
            "while not go.exists(): time.sleep(0.001)\n"
            "actor = AgentActor('w@test#' + sys.argv[5], 'lyddonb@github', 'test', 'none', sys.argv[5])\n"
            "try: init_run(RepoSink(Path(sys.argv[2])), sys.argv[3], actor=actor, why='race'); print('won')\n"
            "except RunExists: print('lost')\n")
        with tempfile.TemporaryDirectory() as tmp:
            sink = RepoSink(Path(tmp) / ".audit" / "runs")
            go = Path(tmp) / "go"
            procs = [subprocess.Popen([sys.executable, "-c", script, str(SRC), str(sink.runs_dir), RUN_ID,
                                       str(go), f"w{k}"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                     for k in range(2)]
            time.sleep(0.3)
            go.touch()
            outcomes = []
            for p in procs:
                out, err = p.communicate(timeout=60)
                self.assertEqual(p.returncode, 0, err.decode())
                outcomes.append(out.decode().strip())
            self.assertEqual(sorted(outcomes), ["lost", "won"])
            report = verify_run(sink.run_dir(RUN_ID))
            self.assertIsInstance(report, Verified)
            self.assertEqual(len(report.entries), 1)
            self.assertEqual(sorted(p.name for p in sink.run_dir(RUN_ID).iterdir()), ["ledger.jsonl"])


if __name__ == "__main__":
    unittest.main()
