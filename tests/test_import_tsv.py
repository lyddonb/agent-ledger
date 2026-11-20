"""`ledger import-tsv`: an old decisions.tsv becomes one run, whole or not at all (S3 "Migration").

Every row here is made up. A real trail belongs to its project and stays out of this repo.
"""
import hashlib
import json
import random
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from helpers import T0, rand_actor, rand_text
from test_cli import AGENT, HUMAN, run_cli

from agent_ledger.entry import ChainHead, EntryType, next_entry
from agent_ledger.import_tsv import TsvError, import_tsv, parse_tsv, source_ts
from agent_ledger.run import repo_sink
from agent_ledger.sink import ChainBroken, LedgerError
from agent_ledger.verify import Verified, verify_run

RUN = "2026-11-19-imported-decisions"
IMPORTER = json.loads(AGENT)["id"]
RECORD_ACTOR = json.dumps({"kind": "agent", "id": "claude@claude-code#unknown", "on_behalf_of": "lyddonb@github",
                           "tool": "claude-code", "model": "unknown", "session": "unknown"})
FAKE_KEY = "sk-" + "ant-api03-" + "Q" * 40  # assembled, so this file holds no key
HAND_KEPT = ("when\twhat\twhy\tevidence\tresult\n"
             "2026-10-24\tKeep the teapot lid on a hinge\tloose lids get lost\tdocs/teapot.md\tkept\n"
             "2026-10-26T10:34\tSay \"no\" to a second spout\tone pour at a time\t\t\n"
             "2026-11-15T23:00:07Z\tGlaze, batch 3\ttwo cups cracked\tkiln log at 0a1b2c3; shelf 4\t12 cups\n")


def tsv(*rows, header="when\twhat\twhy\tevidence\tresult"):
    return "\n".join([header, *rows]) + "\n"


class ImportCommandTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "repo"
        (self.root / ".evidence").mkdir(parents=True)
        self.file = self.root / ".evidence" / "decisions.tsv"
        self.ledger = self.root / ".audit" / "runs" / RUN / "ledger.jsonl"

    def run_import(self, text=None, *extra, file=None):
        file = file or self.file
        if text is not None:
            file.write_bytes(text.encode() if isinstance(text, str) else text)
        return run_cli("import-tsv", file, "--root", self.root, "--date", "2026-11-19", "--actor", AGENT,
                       "--record-actor", RECORD_ACTOR, *extra)

    def entries(self, ledger=None):
        return [json.loads(line) for line in (ledger or self.ledger).read_text().splitlines()]

    def assert_nothing_written(self):
        self.assertFalse((self.root / ".audit").exists(), "a refused import must write nothing")

    def test_each_record_becomes_an_imported_decision(self):
        code, out, err = self.run_import(HAND_KEPT)
        self.assertEqual(code, 0, err)
        self.assertIn(f"imported 3 records from .evidence/decisions.tsv into {RUN}", out)
        first, a, b, c = self.entries()
        self.assertEqual((first["type"], first["actor"]["id"], first["summary"]), ("state", IMPORTER, "Run started"))
        self.assertEqual(first["data"], {"state": "started", "source": {
            "path": ".evidence/decisions.tsv", "rows": 3, "sha256": hashlib.sha256(HAND_KEPT.encode()).hexdigest()}})
        self.assertEqual((a["type"], a["summary"], a["why"], a["evidence"]),
                         ("decision", "Keep the teapot lid on a hinge", "loose lids get lost", ["docs/teapot.md"]))
        self.assertEqual(a["data"], {"result": "kept", "when": "2026-10-24", "imported": {
            "source": ".evidence/decisions.tsv#2", "source_ts": "2026-10-24T00:00:00Z", "by": IMPORTER}})
        self.assertEqual(a["actor"], json.loads(RECORD_ACTOR))
        # An empty evidence or result cell adds nothing, and a quote is ordinary text.
        self.assertEqual((b["summary"], b["evidence"]), ('Say "no" to a second spout', []))
        self.assertEqual(b["data"], {"when": "2026-10-26T10:34", "imported": {
            "source": ".evidence/decisions.tsv#3", "source_ts": "2026-10-26T10:34:00Z", "by": IMPORTER}})
        # The whole cell is one pointer, and a time already in ts form isn't kept twice.
        self.assertEqual(c["evidence"], ["kiln log at 0a1b2c3; shelf 4"])
        self.assertEqual(c["data"], {"result": "12 cups", "imported": {
            "source": ".evidence/decisions.tsv#4", "source_ts": "2026-11-15T23:00:07Z", "by": IMPORTER}})

    def test_ts_is_when_it_was_chained_never_the_records_time(self):
        self.run_import(HAND_KEPT)
        for e in self.entries()[1:]:
            self.assertGreater(e["ts"], "2026-11-19T00:00:00Z")
            self.assertNotEqual(e["ts"], e["data"]["imported"]["source_ts"])

    def test_the_run_verifies_and_show_tags_every_record(self):
        self.run_import(HAND_KEPT)
        code, out, _ = run_cli("verify", self.ledger.parent)
        self.assertEqual(code, 0, out)
        self.assertIn(f"ok {RUN}: 4 entries", out)
        code, out, err = run_cli("show", RUN, "--root", self.root)
        self.assertEqual(code, 0, err)
        self.assertEqual(out.count("[imported] "), 3)
        self.assertNotIn("[imported] Run started", out)

    def test_the_log_sh_columns_import_too(self):
        code, _, err = self.run_import(tsv("2026-10-24T10:34:00Z\tunit 2\tLock the file itself\tno lock file to commit\t"
                                           "src/sink.py\tkept\t",
                                           header="ts\tphase\tdecision\twhy\tevidence\tresult\t").replace("\t\n", "\n"))
        self.assertEqual(code, 0, err)
        entry = self.entries()[1]
        self.assertEqual((entry["summary"], entry["why"]), ("Lock the file itself", "no lock file to commit"))
        self.assertEqual({k: v for k, v in entry["data"].items() if k != "imported"},
                         {"phase": "unit 2", "result": "kept"})

    def test_log_sh_formula_guard_quote_is_taken_off_and_its_repeated_header_skipped(self):
        header = "ts\tphase\tdecision\twhy\tevidence\tresult"
        self.run_import(tsv("2026-10-24T10:34:00Z\t'-\t'-O2 stays off\t'=SUM is what a sheet would run\t"
                            "'quoted' pointer\t'+1", header, "2026-10-24T10:35:00Z\tp\tnext\tw\t\t", header=header))
        first, entry, after = self.entries()
        self.assertEqual((entry["summary"], entry["why"], entry["evidence"], entry["data"]["result"]),
                         ("-O2 stays off", "=SUM is what a sheet would run", ["'quoted' pointer"], "+1"))
        self.assertEqual(entry["data"]["phase"], "-")
        self.assertEqual((first["data"]["source"]["rows"], after["data"]["imported"]["source"]),
                         (2, ".evidence/decisions.tsv#4"))

    def test_a_hand_kept_trail_keeps_its_own_leading_quote(self):
        self.run_import(tsv("2026-10-24\t'-O2' is off\t'@scope/pkg' is pinned\t\t"))
        entry = self.entries()[1]
        self.assertEqual((entry["summary"], entry["why"]), ("'-O2' is off", "'@scope/pkg' is pinned"))

    def test_crlf_and_a_byte_order_mark_are_read_as_plain_lines(self):
        code, _, err = self.run_import(b"\xef\xbb\xbf" + HAND_KEPT.replace("\n", "\r\n").encode())
        self.assertEqual(code, 0, err)
        self.assertEqual([e["summary"] for e in self.entries()[1:]],
                         ["Keep the teapot lid on a hinge", 'Say "no" to a second spout', "Glaze, batch 3"])
        self.assertEqual([e["data"].get("result") for e in self.entries()[1:]], ["kept", None, "12 cups"])
        self.assertNotIn("\\r", self.ledger.read_text())

    def test_one_bad_record_means_nothing_is_written_and_every_bad_line_is_named(self):
        text = tsv("2026-10-24\tgood\tfine\t\t",
                   "2026-10-24\tthree cells",
                   "yesterday\tbad time\tw\t\t",
                   "2026-10-24\t\tno summary\t\t",
                   "2026-10-24\tno why\t\t\t",
                   "2026-10-24\tsplit line\tw\t\t",
                   "",
                   "2026-10-24\tsix\tcells\ta\tb\tc")
        code, out, err = self.run_import(text)
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("nothing imported, 7 problems", err)
        for line, what in ((3, "2 cells"), (4, "not a date or time"), (5, "summary must be a non-empty"),
                           (6, "why must be a non-empty"), (7, "summary must be one line"), (8, "1 cell,"),
                           (9, "6 cells")):
            self.assertRegex(err, rf"line {line}: [^\n]*{what}")
        self.assertNotIn("line 2:", err)
        self.assert_nothing_written()

    def test_a_header_that_names_other_columns_is_refused(self):
        cases = {
            "unknown column": ("when\twhat\twhy\tmood", "cell 4 is not a column name"),
            "no time": ("what\twhy\tevidence", "no column for the when; name one ts or when"),
            "no summary": ("when\twhy", "no column for the summary; name one decision or what"),
            "two summaries": ("when\twhat\tdecision\twhy", "two columns hold the summary (what, decision)"),
        }
        for name, (header, message) in cases.items():
            with self.subTest(name):
                code, _, err = self.run_import(tsv("2026-10-24\ta\tb\tc", header=header))
                self.assertEqual(code, 2)
                self.assertIn(f"line 1: {message}", err)
                self.assert_nothing_written()
        for name, text in {"empty": "", "header only": tsv(), "not UTF-8": b"when\twhat\twhy\n\xff\ta\tb\n"}.items():
            with self.subTest(name):
                code, _, err = self.run_import(text)
                self.assertEqual(code, 2)
                self.assertIn("nothing imported", err)
                self.assert_nothing_written()

    def test_a_missing_header_never_prints_the_first_record(self):
        code, out, err = self.run_import(f"2026-10-24\tRotate {FAKE_KEY}\tw\n")
        self.assertEqual(code, 2)
        self.assertNotIn(FAKE_KEY[:12], out + err)
        self.assertIn("line 1: cell 2 is not a column name", err)

    def test_a_file_is_imported_once(self):
        runs = self.root / ".audit" / "runs"
        for odd in ("not-a-run", "2026-11-01-broken"):  # folders the check must step over
            (runs / odd).mkdir(parents=True)
        (runs / "2026-11-01-broken" / "ledger.jsonl").write_text("{not json\n")
        (runs / ".DS_Store").write_text("")
        self.assertEqual(self.run_import(HAND_KEPT)[0], 0)
        before = self.ledger.read_bytes()
        code, _, err = self.run_import(None, "--slug", "again")
        self.assertEqual(code, 2)
        self.assertIn(f"already imported as {RUN}", err)
        self.assertFalse((runs / "2026-11-19-again").exists())
        # It is the bytes that count, not the path: a copy elsewhere is the same file,
        # and the same path with one more record is a new one.
        copy = self.root / "copy.tsv"
        code, _, err = self.run_import(HAND_KEPT, "--slug", "copy", file=copy)
        self.assertEqual(code, 2)
        self.assertIn(f"already imported as {RUN}", err)
        code, _, err = self.run_import(HAND_KEPT + "2026-11-16\tone more\tw\t\t\n", "--slug", "grown")
        self.assertEqual(code, 0, err)
        # Another file can't take the same run either.
        other = self.root / "other.tsv"
        code, _, err = self.run_import(tsv("2026-10-25\tanother\ttrail\t\t"), file=other)
        self.assertEqual(code, 2)
        self.assertIn(f"{RUN} already has a ledger.jsonl", err)
        self.assertEqual(self.ledger.read_bytes(), before)
        code, _, err = self.run_import(None, "--slug", "second-trail", file=other)
        self.assertEqual(code, 0, err)

    def test_a_secret_in_any_cell_is_redacted_like_append(self):
        text = tsv(f"2026-10-24\tRotate {FAKE_KEY}\tw\t\t",
                   "2026-10-24\tclean\tw\t\t",
                   f"2026-10-24\ts\twhy {FAKE_KEY}\tlog {FAKE_KEY}\tresult {FAKE_KEY}")
        code, out, err = self.run_import(text)
        self.assertEqual(code, 0, err)
        self.assertNotIn(FAKE_KEY, self.ledger.read_text())
        self.assertNotIn(FAKE_KEY, out + err)
        self.assertEqual(self.entries()[1]["summary"], "Rotate [REDACTED:anthropic_key]")
        self.assertIn("redacted on line 2: anthropic_key 1", out)
        self.assertIn("redacted on line 4: anthropic_key 3", out)
        self.assertNotIn("line 3", out)
        code, out, _ = run_cli("scan", "--root", self.root)
        self.assertEqual(code, 0, out)

    def test_a_policy_pattern_redacts_too_and_so_does_the_first_entry(self):
        code, out, err = self.run_import(tsv("2026-10-24\tcustomer ACME-4411 moved\tw\t\t"),
                                         "--redact-pattern", "ACME-[0-9]+", "--why", "trail for ACME-4411")
        self.assertEqual(code, 0, err)
        first, entry = self.entries()
        self.assertEqual(entry["summary"], "customer [REDACTED:policy_1] moved")
        self.assertEqual(first["why"], "trail for [REDACTED:policy_1]")
        self.assertIn("redacted in --why: policy_1 1", out)

    def test_a_pattern_that_would_redact_the_source_refuses_the_import(self):
        cases = {"the sha256": ("[0-9a-f]{40}", self.file), "the path": ("decisions", self.file),
                 "the marker": ("claude-code#a0f4", self.file)}
        for name, (pattern, file) in cases.items():
            with self.subTest(name):
                code, _, err = self.run_import(HAND_KEPT, "--redact-pattern", pattern, file=file)
                self.assertEqual(code, 2)
                self.assertIn("a redaction pattern matches", err)
                self.assert_nothing_written()

    def test_a_cell_nested_too_deeply_to_check_is_a_problem_not_a_crash(self):
        code, _, err = self.run_import(tsv("2026-10-24\t" + "[" * 3000 + "]" * 3000 + "\tw\t\t"))
        self.assertEqual(code, 2)
        self.assertIn("line 2: refusing: the text is nested too deeply to check for secrets", err)
        self.assert_nothing_written()

    def test_append_refuses_the_same_deep_text_cleanly(self):
        (self.root / ".git").mkdir()
        self.assertEqual(run_cli("init", "deep", "--date", "2026-11-19", "--root", self.root, "--actor", AGENT)[0], 0)
        ledger = self.root / ".audit" / "runs" / "2026-11-19-deep" / "ledger.jsonl"
        before = ledger.read_bytes()
        code, _, err = run_cli("append", "--run", "2026-11-19-deep", "--root", self.root, "--actor", AGENT, "--type",
                               "decision", "--summary", "[" * 3000 + "]" * 3000, "--why", "w")
        self.assertEqual(code, 2)
        self.assertIn("ledger append: refusing: the text is nested too deeply to check for secrets", err)
        self.assertEqual(ledger.read_bytes(), before)

    def test_a_file_outside_the_repo_is_named_without_its_folder(self):
        with tempfile.TemporaryDirectory() as elsewhere:
            file = Path(elsewhere) / "old-trail.tsv"
            code, _, err = self.run_import(HAND_KEPT, file=file)
            self.assertEqual(code, 0, err)
        first, entry = self.entries()[:2]
        self.assertEqual(first["data"]["source"]["path"], "old-trail.tsv")
        self.assertEqual(entry["data"]["imported"]["source"], "old-trail.tsv#2")

    def test_the_command_needs_both_actors_and_a_real_slug(self):
        self.file.write_text(HAND_KEPT)
        base = ("import-tsv", self.file, "--root", self.root)
        self.assertEqual(run_cli(*base, "--actor", AGENT)[0], 2)
        self.assertEqual(run_cli(*base, "--record-actor", RECORD_ACTOR)[0], 2)
        code, _, err = self.run_import(None, "--slug", "Not A Slug")
        self.assertEqual(code, 2)
        self.assertIn("must be <yyyy-mm-dd>-<slug>", err)
        code, _, err = run_cli("import-tsv", self.root / "missing.tsv", "--root", self.root, "--actor", AGENT,
                               "--record-actor", RECORD_ACTOR)
        self.assertEqual(code, 2)
        self.assertIn("ledger import-tsv:", err)
        self.assert_nothing_written()

    def test_a_file_that_is_a_symlink_loop_is_refused_cleanly(self):
        loop = self.root / "loop.tsv"
        loop.symlink_to(loop)
        code, _, err = self.run_import(None, file=loop)
        self.assertEqual(code, 2)
        self.assertIn("ledger import-tsv:", err)
        self.assert_nothing_written()

    def test_a_humans_records_keep_the_human_as_actor(self):
        code, _, err = self.run_import(HAND_KEPT, "--record-actor", HUMAN)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.entries()[1]["actor"], json.loads(HUMAN))
        self.assertEqual(self.entries()[1]["data"]["imported"]["by"], IMPORTER)


class SourceTsTest(unittest.TestCase):
    def test_a_part_the_record_leaves_out_is_zero(self):
        for when, ts in {"2026-10-24": "2026-10-24T00:00:00Z", "2026-10-26T10:34": "2026-10-26T10:34:00Z",
                         "2026-10-26T10:34Z": "2026-10-26T10:34:00Z", "2026-10-26T10:34:59": "2026-10-26T10:34:59Z",
                         "2026-11-08T19:51:55Z": "2026-11-08T19:51:55Z"}.items():
            self.assertEqual(source_ts(when), ts, when)

    def test_anything_else_is_not_a_time(self):
        for when in ("", "2026-02-30", "2026-10-24T24:00", "2026-10-24T10:61", "2026-10-24T10:34:60Z",
                     "2026-10-24 10:34", "2026-10-24T10:34+02:00", "2026-10-24Z", "24/10/2026", "yesterday",
                     "2026-10-24T10", " 2026-10-24", "2026-10-24\n", "٢٠٢٦-10-24"):
            self.assertIsNone(source_ts(when), repr(when))


class ImportPropertyTest(unittest.TestCase):
    def test_any_table_of_one_line_cells_round_trips_into_a_chain_that_verifies(self):
        rng = random.Random(20261119)
        for trial in range(25):
            with self.subTest(trial), tempfile.TemporaryDirectory() as tmp:
                sink = repo_sink(Path(tmp))
                rows = [(f"2026-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}", rand_text(rng), rand_text(rng),
                         rng.choice(["", rand_text(rng)]), rng.choice(["", rand_text(rng)]))
                        for _ in range(rng.randint(1, 40))]
                raw = tsv(*("\t".join(r) for r in rows)).encode()
                ticks = iter(range(10_000))
                done = import_tsv(sink, RUN, raw, "trail.tsv", actor=rand_actor(rng, human=False),
                                  record_actor=rand_actor(rng, human=rng.random() < 0.3),
                                  clock=lambda: T0 + timedelta(seconds=next(ticks)))
                report = verify_run(sink.run_dir(RUN))
                self.assertIsInstance(report, Verified)
                self.assertEqual(report.entries, done.entries)
                self.assertEqual(len(report.entries), len(rows) + 1)
                for (when, what, why, evidence, result), e in zip(rows, report.entries[1:]):
                    self.assertEqual((e.type, e.summary, e.why, e.evidence, e.data.get("result", "")),
                                     (EntryType.DECISION, _unguarded(what), _unguarded(why),
                                      (_unguarded(evidence),) if evidence else (), _unguarded(result)))
                    self.assertEqual(e.data["imported"]["source_ts"], when + "T00:00:00Z")
                    self.assertEqual(e.data["when"], when)

    def test_parse_reports_or_returns_and_never_half_reads(self):
        rng = random.Random(7)
        pool = list("ab\t\t\n\r'=-2026T:Z0-") + ["2026-10-24", " ", "é"]
        for _ in range(400):
            raw = ("when\twhat\twhy\n" + "".join(rng.choice(pool) for _ in range(rng.randint(0, 60)))).encode()
            try:
                rows = parse_tsv(raw)
            except TsvError as e:
                self.assertTrue(e.problems)
                continue
            self.assertEqual(len(rows), raw.rstrip(b"\n").count(b"\n"))


def _unguarded(cell):
    return cell[1:] if len(cell) > 1 and cell[0] == "'" and cell[1] in "=+-@" else cell


class CreateWholeRunTest(unittest.TestCase):
    def test_a_chain_that_does_not_link_never_reaches_the_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            sink = repo_sink(Path(tmp))
            actor = rand_actor(random.Random(0), human=False)
            make = lambda head: next_entry(head, now=T0, actor=actor, type=EntryType.STATE, summary="s", why="w")
            first = make(ChainHead.genesis(RUN))
            with self.assertRaises(ChainBroken):
                sink.create(first, [make(first.head), make(first.head)])
            self.assertFalse((sink.run_dir(RUN) / "ledger.jsonl").exists())
            sink.create(first, [make(first.head)])
            self.assertEqual(len(sink.read(RUN).entries), 2)
            with self.assertRaises(LedgerError):
                sink.create(first)


if __name__ == "__main__":
    unittest.main()
