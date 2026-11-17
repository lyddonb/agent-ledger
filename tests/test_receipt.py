"""Receipts (S3 "Receipt entries"): `ledger append` refuses a malformed one, and `ledger receipts`
prints the latest per criterion as a Markdown table that hostile text can't break."""
import json
import random
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from helpers import T0  # also puts src on the path
from test_cli import run_cli

from agent_ledger.entry import AgentActor, EntryError, EntryType, HumanActor, next_entry
from agent_ledger.run import append, init_run, repo_sink

RUN = "2026-11-16-receipts"
AGENT = AgentActor("beau-agent@claude-code#70d5", "lyddonb@github", "claude-code", "opus", "70d5ca84")
SHA = "a" * 64
COMMIT = "b" * 40


def receipt(**overrides):
    base = {"criterion": "P2e-C8", "spec": "docs/phases/P2e.md", "spec_sha256": SHA,
            "probe": "uv run python -m unittest", "commit": COMMIT, "exit": 0}
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not ...}


def data(verdict="VERIFIED", **overrides):
    return {"verdict": verdict, "receipt": receipt(**overrides)}


class ReceiptTestCase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        self.sink = repo_sink(self.root)
        init_run(self.sink, RUN, actor=HumanActor("lyddonb", "lyddonb@github"), why="test")

    def count(self):
        return len(self.sink.read(RUN).entries)

    def add(self, payload, evidence=()):
        return append(self.sink, RUN, actor=AGENT, type=EntryType.VERIFICATION, summary="probe ran",
                      why="w", evidence=evidence, data=payload)

    def table(self):
        code, out, err = run_cli("receipts", RUN, "--root", self.root)
        self.assertEqual(code, 0, out + err)
        return out


class RefusalTest(ReceiptTestCase):
    def refuse(self, payload, *names):
        before = self.count()
        with self.assertRaises(EntryError) as caught:
            self.add(payload)
        self.assertEqual(caught.exception.kind, "schema")
        for name in names:
            self.assertIn(name, str(caught.exception))
        self.assertEqual(self.count(), before, "a refused receipt must not be written")

    def test_a_receipt_needs_a_verdict(self):
        self.refuse({"receipt": receipt()}, "data.verdict")

    def test_the_verdict_is_one_of_three(self):
        for bad in ("verified", "PASS", "", None, True, ["VERIFIED"]):
            with self.subTest(verdict=bad):
                self.refuse({"verdict": bad, "receipt": receipt()}, "data.verdict")

    def test_the_receipt_is_an_object(self):
        for bad in (None, "P2e-C8", ["x"], 3):
            with self.subTest(receipt=bad):
                self.refuse({"verdict": "VERIFIED", "receipt": bad}, "data.receipt must be an object")

    def test_every_required_key_is_required(self):
        for key in ("criterion", "spec", "spec_sha256", "probe", "commit"):
            with self.subTest(key=key):
                self.refuse(data(**{key: ...}), f"data.receipt.{key}")

    def test_the_criterion_matches_its_pattern(self):
        for bad in ("C8", "P2e-c8", "P2e-8", "-C8", "P2e-C", "2e-C8", "P2e-C8 ", "P2e-C8\n", "P_2-C8", "P2e-CC8"):
            with self.subTest(criterion=bad):
                self.refuse(data(criterion=bad), "data.receipt.criterion")

    def test_a_criterion_number_has_no_leading_zero(self):
        self.refuse(data(criterion="P2e-C08"), "data.receipt.criterion")

    def test_the_spec_hash_is_64_lowercase_hex(self):
        for bad in ("a" * 63, "a" * 65, "A" * 64, "g" * 64, "a" * 64 + "\n", 12):
            with self.subTest(spec_sha256=bad):
                self.refuse(data(spec_sha256=bad), "data.receipt.spec_sha256")

    def test_the_commit_is_40_or_64_lowercase_hex(self):
        for bad in ("b" * 39, "b" * 41, "b" * 63, "B" * 40, "z" * 40, "b" * 40 + "\n", "main", 7):
            with self.subTest(commit=bad):
                self.refuse(data(commit=bad), "data.receipt.commit")

    def test_exit_is_an_integer_or_null(self):
        for bad in (True, False, 0.0, 1.5, "0", [0]):
            with self.subTest(exit=bad):
                self.refuse(data("NOT VERIFIED", exit=bad), "data.receipt.exit")

    def test_dirty_is_a_boolean(self):
        for bad in ("true", 1, 0, None):
            with self.subTest(dirty=bad):
                self.refuse(data("INCONCLUSIVE", dirty=bad), "data.receipt.dirty")

    def test_spec_probe_and_not_run_are_not_empty(self):
        for key in ("spec", "probe"):
            with self.subTest(key=key):
                self.refuse(data(**{key: ""}), f"data.receipt.{key}")
        self.refuse(data("INCONCLUSIVE", not_run=""), "data.receipt.not_run")
        self.refuse(data("INCONCLUSIVE", not_run=None), "data.receipt.not_run")

    def test_verified_with_a_failing_exit_is_refused(self):
        self.refuse(data("VERIFIED", exit=1), "VERIFIED", "data.receipt.exit")
        self.refuse(data("VERIFIED", exit=-9), "VERIFIED", "data.receipt.exit")

    def test_verified_with_something_not_run_is_refused(self):
        self.refuse(data("VERIFIED", not_run="the browser half"), "VERIFIED", "data.receipt.not_run")

    def test_verified_with_uncommitted_changes_is_refused(self):
        self.refuse(data("VERIFIED", dirty=True), "VERIFIED", "data.receipt.dirty")

    def test_a_refusal_reaches_the_command_line_as_exit_2(self):
        before = self.count()
        code, out, err = run_cli(
            "append", "--root", self.root, "--run", RUN, "--actor", json.dumps(AGENT.to_wire()),
            "--type", "verification", "--summary", "s", "--why", "w",
            "--data", json.dumps({"verdict": "VERIFIED", "receipt": receipt(exit=3)}))
        self.assertEqual(code, 2, out + err)
        self.assertIn("data.receipt.exit", err)
        self.assertEqual(self.count(), before)


class RedactionTest(ReceiptTestCase):
    def test_a_receipt_that_redaction_breaks_is_refused_and_the_error_says_redaction_did_it(self):
        from agent_ledger.redact import Redactor
        before = self.count()
        with self.assertRaises(EntryError) as caught:
            append(self.sink, RUN, actor=AGENT, type=EntryType.VERIFICATION, summary="s", why="w",
                   data=data("VERIFIED"), redactor=Redactor(extra=[r"\b[0-9a-f]{40}\b"]))
        self.assertIn("redaction changed the receipt", str(caught.exception))
        self.assertIn("data.receipt.commit", str(caught.exception))
        self.assertEqual(self.count(), before)


class AcceptedTest(ReceiptTestCase):
    def test_a_probe_with_no_exit_code(self):
        self.add(data("VERIFIED", exit=None))
        self.assertIn("| P2e-C8 | VERIFIED | `uv run python -m unittest` |", self.table())

    def test_extra_keys_are_allowed_in_the_receipt(self):
        self.add(data("VERIFIED", host="ci-3", duration_s=12))
        self.assertEqual(self.count(), 2)

    def test_a_sha256_commit(self):
        self.add(data("VERIFIED", commit="c" * 64))
        self.assertIn("`ccccccc`", self.table())

    def test_a_failed_probe_can_have_any_exit_and_be_dirty(self):
        self.add(data("NOT VERIFIED", exit=2, dirty=True))
        self.add(data("INCONCLUSIVE", criterion="P2e-C9", exit=None, dirty=True, not_run="the live half"))
        self.assertEqual(self.count(), 3)

    def test_a_verification_entry_with_no_receipt_is_unchanged(self):
        for payload in (None, {}, {"verdict": "VERIFIED"}, {"anything": [1, {"x": None}], "verdict": "PASS"}):
            self.add(payload)
        self.assertEqual(self.count(), 5)
        self.assertEqual(self.table().strip(), f"{RUN}: no receipts")

    def test_another_entry_type_may_carry_a_receipt_key_untouched(self):
        append(self.sink, RUN, actor=AGENT, type=EntryType.DECISION, summary="n", why="w", data={"receipt": 7})
        self.assertEqual(self.count(), 2)


class ReceiptsCliTest(ReceiptTestCase):
    def rows(self):
        return self.table().splitlines()[2:]

    def test_the_last_receipt_for_a_criterion_wins_and_counts_the_earlier_ones(self):
        self.add(data("NOT VERIFIED", exit=5), evidence=["out/first.txt#aa"])
        self.add(data("NOT VERIFIED", exit=4))
        self.add(data("VERIFIED", exit=0, commit="d" * 40), evidence=["out/last.txt#bb", "out/other.txt#cc"])
        out = self.table()
        self.assertEqual(out.splitlines()[0], "| Criterion | Verdict | Probe | Commit | Evidence | Entry |")
        self.assertEqual(self.rows(), [
            "| P2e-C8 | VERIFIED | `uv run python -m unittest` (exit 0) | `ddddddd` | `out/last.txt#bb` "
            "| seq 4, 2 earlier |"])

    def test_cells_for_not_run_dirty_and_a_missing_exit_or_evidence(self):
        self.add(data("INCONCLUSIVE", exit=None, dirty=True, not_run="the live half"))
        self.assertEqual(self.rows(), [
            "| P2e-C8 | INCONCLUSIVE (not run: `the live half`) (uncommitted changes) "
            "| `uv run python -m unittest` | `bbbbbbb` |  | seq 2 |"])

    def test_criteria_are_in_natural_id_order(self):
        for criterion in ("P2e-C10", "P2e-C2", "P10-C1", "P2e-B7", "P2-C1", "P2e-C1"):
            self.add(data(criterion=criterion))
        self.assertEqual([r.split(" | ")[0] for r in self.rows()],
                         ["| P2-C1", "| P2e-B7", "| P2e-C1", "| P2e-C2", "| P2e-C10", "| P10-C1"])

    def test_a_run_with_no_receipts(self):
        code, out, _ = run_cli("receipts", RUN, "--root", self.root)
        self.assertEqual((code, out), (0, f"{RUN}: no receipts\n"))

    def test_a_receipt_written_before_the_rules_is_skipped_and_its_seq_named(self):
        self.add(data("VERIFIED", criterion="P2e-C2"))
        # append refuses this now; the sink takes it, as a version before the rules did (seq 3 and 4).
        for payload in (data("VERIFIED", exit=7), {"receipt": receipt()}):
            head = self.sink.read(RUN).entries[-1].head
            self.sink.write(next_entry(head, now=T0, actor=AGENT, type=EntryType.VERIFICATION,
                                       summary="old", why="w", data=payload))
        self.add(data("VERIFIED", criterion="P2e-C3"))
        code, out, err = run_cli("receipts", RUN, "--root", self.root)
        self.assertEqual(code, 0, err)
        self.assertEqual(len(out.splitlines()), 4, out)  # the note is not in the table's output
        self.assertEqual(err, "note: not shown, receipt breaks the rules: seq 3 (P2e-C8), seq 4 (P2e-C8)\n")

    def test_a_row_says_when_a_later_receipt_for_its_criterion_cannot_be_read(self):
        self.add(data("VERIFIED"))
        head = self.sink.read(RUN).entries[-1].head
        self.sink.write(next_entry(head, now=T0, actor=AGENT, type=EntryType.VERIFICATION, summary="old",
                                   why="w", data=data("NOT VERIFIED", exit="1")))
        self.add(data("VERIFIED", criterion="P2e-C9"))
        c8, c9 = self.rows()
        self.assertIn("| VERIFIED (a later receipt breaks the rules: seq 3) |", c8)
        self.assertIn("| VERIFIED |", c9)

    def test_an_imported_receipt_says_so(self):
        marker = {"source": "decisions.tsv:4", "source_ts": "2026-10-01T00:00:00Z", "by": AGENT.id}
        self.add({**data("VERIFIED"), "imported": marker})
        self.assertTrue(self.rows()[0].endswith("| seq 2, imported |"), self.rows()[0])

    def test_only_invalid_receipts_still_says_no_receipts_and_names_them(self):
        head = self.sink.read(RUN).entries[-1].head
        self.sink.write(next_entry(head, now=T0, actor=AGENT, type=EntryType.VERIFICATION, summary="old",
                                   why="w", data={"receipt": 1}))
        code, out, err = run_cli("receipts", RUN, "--root", self.root)
        self.assertEqual(code, 0)
        self.assertEqual((out, err), (f"{RUN}: no receipts\n", "note: not shown, receipt breaks the rules: seq 2\n"))

    def test_a_run_that_fails_verify_exits_1_without_a_table(self):
        self.add(data("VERIFIED"))
        ledger = self.sink.run_dir(RUN) / "ledger.jsonl"
        ledger.write_bytes(ledger.read_bytes().replace(b"probe ran", b"probe ron"))
        code, out, err = run_cli("receipts", RUN, "--root", self.root)
        self.assertEqual(code, 1)
        self.assertIn("fails verify", err)
        self.assertNotIn("Criterion", out)

    def test_an_unknown_run_exits_2(self):
        code, out, err = run_cli("receipts", "2026-01-01-nope", "--root", self.root)
        self.assertEqual((code, out), (2, ""))
        self.assertIn("ledger receipts:", err)


HOSTILE = ["a|b", "||", "`", "``x`", "x`", "`x", "a\nb", "a\r\nb", "a\rb", "tab\there", "<script>alert(1)</script>",
           "@octocat please", " lead", "trail ", "  both  ", "line sep", "para sep", "back\\", "\\|", "\\\\|",
           "nul\x00x", "esc\x1b[31m", "bidi‮flip", "| P9-C9 | VERIFIED | `x` |", "\x0b\x0c\x1c\x85", "[l](http://x)",
           "*b* _i_ # h", "![i](http://x/y.png)", "plain"]
VERDICTS = ["VERIFIED", "NOT VERIFIED", "INCONCLUSIVE"]


def split_cells(line: str) -> list[str]:
    """Cells of a table row, splitting on pipes that no backslash escapes."""
    parts = re.split(r"(?<!\\)\|", line)
    assert parts[0] == "" and parts[-1] == "", line
    return [p.strip() for p in parts[1:-1]]


def code_span(text: str) -> str | None:
    """The content of `text` when it is exactly one CommonMark code span, else None."""
    m = re.match(r"(`+)(?!`)", text)
    if not m:
        return None
    fence = m.group(1)
    end = next((c.start() for c in re.finditer(r"`+", text[len(fence):]) if len(c.group()) == len(fence)), None)
    if end is None or text[len(fence) + end + len(fence):] != "":
        return None
    body = text[len(fence):len(fence) + end].replace("\\|", "|")
    if body.startswith(" ") and body.endswith(" ") and body.strip():
        body = body[1:-1]
    return body


class BacktickTest(ReceiptTestCase):
    def test_a_probe_with_backticks_renders_as_one_code_span_holding_it_exactly(self):
        probes = ["`", "``", "a`b", "`a", "a`", "a``b`c", "```x```", "x | `y` | z", "`|`"]
        for i, probe in enumerate(probes, start=1):
            with self.subTest(probe=probe):
                self.add(data("NOT VERIFIED", criterion=f"P1-C{i}", probe=probe, exit=None))
        for probe, row in zip(probes, self.table().splitlines()[2:]):
            self.assertEqual(code_span(split_cells(row)[2]), probe, row)


class HostileTextPropertyTest(ReceiptTestCase):
    SEEDS = range(40)
    CASES = 10

    def hostile_line(self, rng):
        """Evidence is one line by the entry format, so it can't hold the control characters."""
        return re.sub(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]", "", self.hostile(rng)) or "e"

    def hostile(self, rng):
        return "".join(rng.choice(HOSTILE) + rng.choice(["", " ", "x"]) for _ in range(rng.randint(1, 3)))

    def test_the_table_keeps_its_shape_and_each_row_shows_the_last_verdict(self):
        for seed in self.SEEDS:
            rng = random.Random(seed)
            for case in range(self.CASES):
                with self.subTest(seed=seed, case=case):
                    sink_root = Path(tempfile.mkdtemp())
                    self.addCleanup(shutil.rmtree, sink_root)
                    sink = repo_sink(sink_root)
                    init_run(sink, RUN, actor=HumanActor("lyddonb", "lyddonb@github"), why="test")
                    last = {}
                    for _ in range(rng.randint(1, 6)):
                        verdict = rng.choice(VERDICTS)
                        criterion = f"P{rng.randint(1, 3)}e-C{rng.randint(1, 12)}"
                        extra = {}
                        if verdict == "INCONCLUSIVE":
                            extra = {"not_run": self.hostile(rng), "dirty": rng.random() < 0.5}
                        append(sink, RUN, actor=AGENT, type=EntryType.VERIFICATION, summary="s", why="w",
                               evidence=[self.hostile_line(rng)],
                               data=data(verdict, criterion=criterion, probe=self.hostile(rng),
                                         spec=self.hostile(rng), exit=None if verdict != "NOT VERIFIED" else 1,
                                         **extra))
                        last[criterion] = verdict
                    code, out, err = run_cli("receipts", RUN, "--root", sink_root)
                    self.assertEqual(code, 0, err)
                    lines = out.splitlines()
                    self.assertEqual(out.count("\n"), len(lines), "one newline per line")
                    self.assertEqual(len(lines), 2 + len(last), out)
                    for line in lines:
                        self.assertEqual(len(split_cells(line)), 6, repr(line))
                    shown = {}
                    for line in lines[2:]:
                        cells = split_cells(line)
                        shown[cells[0]] = cells[1].split(" (")[0]
                    self.assertEqual(shown, last)

    def test_invisible_and_line_breaking_characters_are_written_out(self):
        self.add(data("INCONCLUSIVE", probe="bidi\u202eflip a\nb fill\u3164er x\u0336y", not_run="tab\there"))
        row = self.table().splitlines()[2]
        self.assertIn("`bidi\\u202eflip a\\u000ab fill\\u3164er x\\u0336y`", row)
        self.assertIn("`tab\\u0009here`", row)
        self.assertTrue(row.isascii(), row)

    def test_hostile_text_stays_inside_code_spans(self):
        self.add(data("INCONCLUSIVE", probe="<script>x</script> @octocat | `a` ![i](u)", not_run="@octocat <b>"))
        row = self.table().splitlines()[2]
        for cell in split_cells(row)[1:5]:
            stripped = re.sub(r"(`+) ?.*? ?\1", "", cell)  # remove every code span
            self.assertNotRegex(stripped, r"[<>@|\[\]!]", cell)


if __name__ == "__main__":
    unittest.main()
