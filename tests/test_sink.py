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
from agent_ledger.sink import RepoSink
runs, run_id, me, n, go = sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5]), Path(sys.argv[6])
actor = AgentActor(f"worker@test#{me}", "lyddonb@github", "test", "none", me)
while not go.exists():
    time.sleep(0.001)
for i in range(n):
    append(RepoSink(Path(runs)), run_id, actor=actor, type=EntryType.STATE, summary=f"{me} {i}", why="race")
"""


class ConcurrentAppendTest(unittest.TestCase):
    """S3 acceptance: two subagents appending at once produce one valid chain."""

    def race(self, workers: int, each: int) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sink = RepoSink(Path(tmp) / ".audit" / "runs")
            init_run(sink, RUN_ID, actor=AGENT, why="race")
            go = Path(tmp) / "go"
            procs = [subprocess.Popen([sys.executable, "-c", _WORKER, str(SRC), str(sink.runs_dir), RUN_ID,
                                       f"w{k}", str(each), str(go)], stderr=subprocess.PIPE)
                     for k in range(workers)]
            time.sleep(0.3)  # let every worker reach its wait loop
            go.touch()
            for p in procs:
                _, err = p.communicate(timeout=120)
                self.assertEqual(p.returncode, 0, err.decode())
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


if __name__ == "__main__":
    unittest.main()
