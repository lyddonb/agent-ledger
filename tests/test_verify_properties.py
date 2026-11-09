"""Property tests for chain verification (S3 acceptance; charter unit 1).

Each property runs many random chains from fixed seeds, so a failure always reproduces. The
assertion for every attack is the same: verify fails, and the first problem it reports is on
the first line the attack touched. Lines before that are untouched, so nothing may be reported there.
"""
import random
import unittest

from helpers import RUN_ID, build_chain, resign

from agent_ledger.verify import Broken, Kind, Verified, verify_bytes

SEEDS = range(40)
CASES_PER_SEED = 10


def chains(min_len: int = 2):
    for seed in SEEDS:
        rng = random.Random(seed)
        for case in range(CASES_PER_SEED):
            yield seed, case, rng, build_chain(rng, rng.randint(min_len, 9))


class VerifyPropertyTest(unittest.TestCase):
    def assertNamesLine(self, report, line: int, ctx: str):
        self.assertIsInstance(report, Broken, f"{ctx}: attack was not detected")
        first = min(p.line for p in report.problems)
        self.assertEqual(first, line, f"{ctx}: expected the first problem on line {line}, got {report.problems}")

    def test_valid_chains_verify(self):
        for seed, case, _rng, lines in chains(min_len=1):
            report = verify_bytes(RUN_ID, b"".join(lines))
            self.assertIsInstance(report, Verified, f"seed {seed} case {case}: {report}")
            self.assertEqual(len(report.entries), len(lines))

    def test_any_changed_byte_is_named(self):
        for seed, case, rng, lines in chains(min_len=1):
            data = bytearray(b"".join(lines))
            pos = rng.randrange(len(data))
            data[pos] = rng.choice([b for b in range(256) if b != data[pos]])
            line = data[:pos].count(b"\n") + 1
            ctx = f"seed {seed} case {case}: byte {pos} (line {line})"
            self.assertNamesLine(verify_bytes(RUN_ID, bytes(data)), line, ctx)

    def test_any_deleted_line_except_the_last_is_named(self):
        for seed, case, rng, lines in chains():
            k = rng.randrange(len(lines) - 1)
            report = verify_bytes(RUN_ID, b"".join(lines[:k] + lines[k + 1:]))
            ctx = f"seed {seed} case {case}: deleted line {k + 1}"
            self.assertNamesLine(report, k + 1, ctx)
            self.assertEqual({(p.line, p.kind) for p in report.problems},
                             {(k + 1, Kind.SEQ_MISMATCH), (k + 1, Kind.PREV_MISMATCH)},
                             f"{ctx}: a deletion is named once, at the gap")

    def test_deleting_the_last_line_is_a_documented_blind_spot(self):
        # A prefix of a valid chain is a valid chain. Seals and `verify --base` (unit 4) catch this.
        for seed, case, _rng, lines in chains():
            report = verify_bytes(RUN_ID, b"".join(lines[:-1]))
            self.assertIsInstance(report, Verified, f"seed {seed} case {case}")

    def test_any_reordered_pair_is_named(self):
        for seed, case, rng, lines in chains():
            i, j = sorted(rng.sample(range(len(lines)), 2))
            swapped = list(lines)
            swapped[i], swapped[j] = swapped[j], swapped[i]
            report = verify_bytes(RUN_ID, b"".join(swapped))
            ctx = f"seed {seed} case {case}: swapped lines {i + 1} and {j + 1}"
            self.assertNamesLine(report, i + 1, ctx)
            self.assertIn(j + 1, {p.line for p in report.problems}, ctx)
            self.assertLessEqual({p.line for p in report.problems}, {i + 1, i + 2, j + 1, j + 2},
                                 f"{ctx}: only the lines at each end of a moved line are named")

    def test_forged_prev_is_named_even_when_the_hash_is_recomputed(self):
        for seed, case, rng, lines in chains(min_len=1):
            k = rng.randrange(len(lines))
            fake = rng.choice([
                "%064x" % rng.getrandbits(256),
                "genesis:" + RUN_ID if k else "%064x" % rng.getrandbits(256),
                "genesis:2026-01-01-other-run",
            ])
            forged = list(lines)
            forged[k] = resign(lines[k], prev=fake)
            report = verify_bytes(RUN_ID, b"".join(forged))
            ctx = f"seed {seed} case {case}: forged prev on line {k + 1}"
            self.assertNamesLine(report, k + 1, ctx)
            self.assertTrue({Kind.PREV_MISMATCH, Kind.SCHEMA} & {p.kind for p in report.problems}, ctx)

    def test_resigned_edit_is_named_on_the_next_line(self):
        for seed, case, rng, lines in chains():
            k = rng.randrange(len(lines) - 1)
            forged = list(lines)
            forged[k] = resign(lines[k], summary="rewritten after the fact")
            report = verify_bytes(RUN_ID, b"".join(forged))
            self.assertNamesLine(report, k + 2, f"seed {seed} case {case}: re-signed line {k + 1}")
            self.assertEqual({p.kind for p in report.problems}, {Kind.PREV_MISMATCH})

    def test_one_edit_does_not_cascade(self):
        for seed, case, rng, lines in chains():
            k = rng.randrange(len(lines))
            edited = list(lines)
            edited[k] = lines[k].replace(b'"seq":', b'"seq":1', 1)  # seq 3 -> 13: hashed, so hash-mismatch
            report = verify_bytes(RUN_ID, b"".join(edited))
            self.assertIsInstance(report, Broken)
            self.assertLessEqual({p.line for p in report.problems}, {k + 1, k + 2},
                                 f"seed {seed} case {case}: {report.problems}")

    def test_line_from_another_run_is_named(self):
        for seed, case, rng, lines in chains():
            other = build_chain(random.Random(seed + 1000), len(lines), run_id="2026-11-09-other")
            k = rng.randrange(len(lines))
            spliced = lines[:k] + [other[k]] + lines[k + 1:]
            report = verify_bytes(RUN_ID, b"".join(spliced))
            self.assertNamesLine(report, k + 1, f"seed {seed} case {case}: spliced line {k + 1}")
            self.assertIn(Kind.RUN_ID_MISMATCH, {p.kind for p in report.problems})

    def test_duplicated_line_is_named(self):
        for seed, case, rng, lines in chains(min_len=1):
            k = rng.randrange(len(lines))
            report = verify_bytes(RUN_ID, b"".join(lines[:k + 1] + [lines[k]] + lines[k + 1:]))
            self.assertNamesLine(report, k + 2, f"seed {seed} case {case}: repeated line {k + 1}")


class FramingTest(unittest.TestCase):
    def setUp(self):
        self.lines = build_chain(random.Random(7), 3)

    def kinds(self, data: bytes):
        report = verify_bytes(RUN_ID, data)
        self.assertIsInstance(report, Broken)
        return {(p.line, p.kind) for p in report.problems}

    def test_empty_file(self):
        self.assertEqual(self.kinds(b""), {(0, Kind.EMPTY_LEDGER)})

    def test_torn_final_line(self):
        self.assertIn((3, Kind.TORN_LINE), self.kinds(b"".join(self.lines)[:-1]))

    def test_blank_line(self):
        self.assertIn((2, Kind.BAD_FRAMING), self.kinds(self.lines[0] + b"\n" + b"".join(self.lines[1:])))

    def test_crlf(self):
        self.assertIn((1, Kind.BAD_FRAMING), self.kinds(b"".join(self.lines).replace(b"\n", b"\r\n")))

    def test_byte_order_mark(self):
        self.assertIn((1, Kind.BAD_FRAMING), self.kinds(b"\xef\xbb\xbf" + b"".join(self.lines)))

    def test_problem_list_is_capped(self):
        report = verify_bytes(RUN_ID, b"x\n" * 200)
        self.assertIsInstance(report, Broken)
        self.assertEqual(len(report.problems), 50)
        self.assertEqual(report.omitted, 150)


if __name__ == "__main__":
    unittest.main()
