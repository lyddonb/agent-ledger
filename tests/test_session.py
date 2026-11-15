"""`ledger session add` and redaction on append: archives are redacted, hashed and recorded;
anything unconfirmed stays out of the run folder."""
import gzip
import hashlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import T0  # noqa: F401  (puts src on the path)
from test_cli import run_cli

from agent_ledger.entry import AgentActor, EntryType, HumanActor
from agent_ledger.redact import Redactor
from agent_ledger.run import RedactionRefused, append, init_run, repo_sink
from agent_ledger.session import add_session
from agent_ledger.sink import LedgerError
from agent_ledger.verify import Verified, verify_run

RUN = "2026-11-09-sessions"
AGENT = AgentActor("beau-agent@claude-code#70d5", "lyddonb@github", "claude-code", "opus", "70d5ca84")
FAKE_KEY = "sk-" + "ant-api03-" + "Z" * 40


def transcript_lines(*extra):
    return [json.dumps({"type": "user", "message": {"content": "start unit 3"}}),
            json.dumps({"type": "assistant", "message": {"content": "reading the spec"}}),
            *extra]


def refusing_archived_entries():
    """Make append refuse the archived entry as the redactor would, and pass everything else."""
    def fake(*args, **kw):
        if kw["data"].get("archived") is True:
            raise RedactionRefused("line 1: a secret_assignment match survived in a decoded view of the text")
        return append(*args, **kw)
    return mock.patch("agent_ledger.session.append", fake)


class SessionAddTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        self.sink = repo_sink(self.root)
        init_run(self.sink, RUN, actor=HumanActor("lyddonb", "lyddonb@github"), why="test")
        self.src = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.src)

    def transcript(self, lines, name="abc123.jsonl"):
        path = self.src / name
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def add(self, path, **kw):
        return add_session(self.sink, RUN, path, actor=AGENT, redactor=Redactor(), root=self.root, **kw)

    def test_a_planted_secret_never_reaches_the_run_folder(self):
        path = self.transcript(transcript_lines(json.dumps({"tool_result": f"ANTHROPIC_API_KEY={FAKE_KEY}"})))
        result = self.add(path)
        self.assertTrue(result.archived)
        run_dir = self.sink.run_dir(RUN)
        for f in run_dir.rglob("*"):
            if f.is_file():
                data = gzip.decompress(f.read_bytes()) if f.suffix == ".gz" else f.read_bytes()
                self.assertNotIn(b"Z" * 40, data, f"secret found in {f}")
        self.assertEqual(result.entry.data["redactions"], (("anthropic_key", 1),))

    def test_the_entry_holds_both_hashes_and_they_check_out(self):
        path = self.transcript(transcript_lines(json.dumps({"x": f"token: {FAKE_KEY}"})))
        d = self.add(path).entry.data
        archive = self.root / d["path"]
        self.assertEqual(d["path"], f".audit/runs/{RUN}/sessions/abc123.jsonl.gz")
        self.assertEqual(d["raw_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(d["redacted_sha256"], hashlib.sha256(gzip.decompress(archive.read_bytes())).hexdigest())
        self.assertNotEqual(d["raw_sha256"], d["redacted_sha256"])
        self.assertEqual(d["raw_bytes"], path.stat().st_size)
        self.assertEqual(archive.stat().st_mode & 0o777, 0o644)

    def test_the_chain_still_verifies_and_the_entry_is_a_session_entry(self):
        entry = self.add(self.transcript(transcript_lines())).entry
        self.assertIs(entry.type, EntryType.SESSION)
        report = verify_run(self.sink.run_dir(RUN))
        self.assertIsInstance(report, Verified)
        self.assertEqual(report.head.link, entry.hash)

    def test_adding_the_same_transcript_again_writes_nothing(self):
        path = self.transcript(transcript_lines())
        first = self.add(path)
        ledger = (self.sink.run_dir(RUN) / "ledger.jsonl").read_bytes()
        again = self.add(path)
        self.assertFalse(again.new)
        self.assertEqual(again.entry.hash, first.entry.hash)
        self.assertEqual((self.sink.run_dir(RUN) / "ledger.jsonl").read_bytes(), ledger)

    def test_a_grown_transcript_is_archived_again_with_a_new_entry(self):
        path = self.transcript(transcript_lines())
        first = self.add(path)
        path = self.transcript(transcript_lines(json.dumps({"type": "assistant", "message": "more"})))
        second = self.add(path)
        self.assertTrue(second.new)
        self.assertEqual(second.entry.seq, first.entry.seq + 1)
        self.assertNotEqual(second.entry.data["raw_sha256"], first.entry.data["raw_sha256"])
        archive = self.root / second.entry.data["path"]
        self.assertIn(b"more", gzip.decompress(archive.read_bytes()))

    def test_the_archive_is_byte_for_byte_reproducible(self):
        path = self.transcript(transcript_lines())
        archive = self.root / self.add(path).entry.data["path"]
        first = archive.read_bytes()
        archive.unlink()
        redone = self.add(path, session_id="abc123")  # a missing archive is rebuilt
        self.assertTrue(redone.new)
        self.assertEqual(archive.read_bytes(), first)
        other = self.add(self.transcript(transcript_lines(), name="again.jsonl"), session_id="copy")
        self.assertEqual((self.root / other.entry.data["path"]).read_bytes(), first)

    def test_word_ids_and_slugs_are_not_mangled_and_still_dedupe(self):
        path = self.transcript(transcript_lines(), name="disk-usage-analysis-session-one.jsonl")
        first = self.add(path)
        d = first.entry.data
        self.assertEqual(d["session_id"], "disk-usage-analysis-session-one")
        self.assertTrue((self.root / d["path"]).is_file())
        self.assertEqual(first.entry.evidence, (d["path"],))
        self.assertFalse(self.add(path).new)

    def test_a_refused_add_can_be_retried(self):
        path = self.src / "bin.jsonl"
        path.write_bytes(b'{"a": "\xff"}\n')
        self.assertFalse(self.add(path).archived)
        again = self.add(path)
        self.assertTrue(again.new, "a refusal must not be cached as done")
        path.write_bytes(b'{"a": "fixed"}\n')
        self.assertTrue(self.add(path).archived)

    def test_new_patterns_redo_an_archived_transcript(self):
        path = self.transcript(transcript_lines(json.dumps({"t": "acmeSECRET99"})))
        self.add(path)
        redone = add_session(self.sink, RUN, path, actor=AGENT, redactor=Redactor([r"acme\w+"]), root=self.root)
        self.assertTrue(redone.new)
        body = gzip.decompress((self.root / redone.entry.data["path"]).read_bytes())
        self.assertNotIn(b"acmeSECRET99", body)

    def test_dotted_names_get_their_own_ids(self):
        a = self.add(self.transcript(transcript_lines(), name="x.one.jsonl"))
        b = self.add(self.transcript(transcript_lines("{}"), name="x.two.jsonl"))
        self.assertEqual((a.entry.data["session_id"], b.entry.data["session_id"]), ("x.one", "x.two"))

    def test_a_failed_append_leaves_the_previous_archive_alone(self):
        path = self.transcript(transcript_lines())
        archive = self.root / self.add(path).entry.data["path"]
        before = archive.read_bytes()
        path = self.transcript(transcript_lines("{}"))
        ledger = self.sink.run_dir(RUN) / "ledger.jsonl"
        ledger.write_bytes(ledger.read_bytes() + b"torn")
        with self.assertRaises(Exception):
            self.add(path)
        self.assertEqual(archive.read_bytes(), before)
        self.assertEqual([f.name for f in archive.parent.iterdir()], [archive.name], "a staged file was left")

    def test_a_transcript_inside_the_repo_gets_a_warning(self):
        inside = self.root / "raw.jsonl"
        inside.write_text("\n".join(transcript_lines()) + "\n")
        self.assertIn("inside the repo", self.add(inside).warning)

    def test_location_under_home_is_written_with_a_tilde(self):
        home = Path.home().resolve()
        folder = Path(tempfile.mkdtemp(dir=home if os.access(home, os.W_OK) else None))
        self.addCleanup(shutil.rmtree, folder)
        path = folder / "bin.jsonl"
        path.write_bytes(b"\xff\n")
        location = self.add(path).entry.data["location"]
        if folder.resolve().is_relative_to(home):
            self.assertTrue(location.startswith("~/"), location)
            self.assertNotIn(str(home), location)

    def test_unconfirmed_redaction_keeps_the_transcript_out_and_records_where_it_is(self):
        key = "sk-" + "ant-" + "k" * 30
        bad = '{"torn": "\\u0073' + key[1:]  # escaped, on a line that isn't JSON
        path = self.transcript(transcript_lines(bad))
        result = self.add(path)
        self.assertFalse(result.archived)
        d = result.entry.data
        self.assertEqual(d["location"], str(path.resolve()))
        self.assertIn("decoded view", d["reason"])
        self.assertNotIn("path", d)
        self.assertFalse((self.sink.run_dir(RUN) / "sessions").exists())
        self.assertNotIn("k" * 30, (self.sink.run_dir(RUN) / "ledger.jsonl").read_text())

    def test_many_redactions_under_a_password_or_key_name_still_archive(self):
        # As a name -> count object, "url_password":12 read as a password= assignment and the
        # archived entry was refused (seen on a real run); pairs keep the counts readable.
        path = self.transcript(transcript_lines(
            *(json.dumps({"tool_result": f"psql postgres://app:pw{i}x@db/main"}) for i in range(12))))
        result = self.add(path)
        self.assertTrue(result.archived, result.entry.data.get("reason"))
        self.assertEqual(result.entry.data["redactions"], (("url_password", 12),))
        self.assertTrue((self.root / result.entry.data["path"]).is_file())

    def test_an_archived_entry_the_redactor_refuses_still_records_where_the_transcript_is(self):
        path = self.transcript(transcript_lines(
            *(json.dumps({"tool_result": f"psql postgres://app:pw{i}x@db/main"}) for i in range(12))))
        before = len(self.sink.read(RUN).entries)
        with refusing_archived_entries():
            result = self.add(path)
        self.assertFalse(result.archived)
        self.assertEqual(len(self.sink.read(RUN).entries), before + 1, "no pointer entry was written")
        d = result.entry.data
        self.assertEqual(d["location"], str(path.resolve()))
        self.assertEqual(d["raw_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(d["raw_bytes"], path.stat().st_size)
        self.assertIn("archived entry failed redaction", d["reason"])
        self.assertNotIn("path", d)
        self.assertEqual(list((self.sink.run_dir(RUN) / "sessions").iterdir()), [], "an archive was kept")
        self.assertNotIn("pw3x", (self.sink.run_dir(RUN) / "ledger.jsonl").read_text())

    def test_a_transcript_that_is_not_utf8_is_not_archived(self):
        path = self.src / "bin.jsonl"
        path.write_bytes(b'{"a": "\xff\xfe"}\n')
        result = self.add(path)
        self.assertFalse(result.archived)
        self.assertIn("not UTF-8", result.entry.data["reason"])

    def test_session_ids_cannot_escape_the_sessions_folder(self):
        path = self.transcript(transcript_lines())
        for bad in ["../x", "a/b", ".hidden", "", "a..b"]:
            with self.subTest(bad=bad), self.assertRaises(LedgerError):
                self.add(path, session_id=bad)

    def test_a_symlinked_sessions_folder_is_refused(self):
        elsewhere = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, elsewhere)
        os.symlink(elsewhere, self.sink.run_dir(RUN) / "sessions")
        with self.assertRaises(LedgerError):
            self.add(self.transcript(transcript_lines()))
        self.assertEqual(list(elsewhere.iterdir()), [])


class AppendRedactionTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        self.sink = repo_sink(self.root)
        init_run(self.sink, RUN, actor=HumanActor("lyddonb", "lyddonb@github"), why="test")

    def test_append_redacts_every_text_field_before_chaining(self):
        entry = append(self.sink, RUN, actor=AGENT, type=EntryType.DECISION,
                       summary=f"rotated {FAKE_KEY}", why=f"leaked as {FAKE_KEY}",
                       evidence=[f"env:{FAKE_KEY}"], data={"note": [f"key {FAKE_KEY}"], "n": 2})
        line = (self.sink.run_dir(RUN) / "ledger.jsonl").read_text()
        self.assertNotIn("Z" * 40, line)
        self.assertEqual(entry.summary, "rotated [REDACTED:anthropic_key]")
        self.assertEqual(entry.data["n"], 2)

    def test_a_secret_in_a_data_key_never_reaches_the_ledger(self):
        key = "sk-" + "ant-api03-" + "A" * 30
        with self.assertRaises(Exception):
            append(self.sink, RUN, actor=AGENT, type=EntryType.DECISION, summary="s", why="w", data={key: 1})
        self.assertNotIn("A" * 30, (self.sink.run_dir(RUN) / "ledger.jsonl").read_text())

    def test_init_redacts_why(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root)
        entry = init_run(repo_sink(root), RUN, actor=AGENT, why=f"key {FAKE_KEY}")
        self.assertEqual(entry.why, "key [REDACTED:anthropic_key]")


class SessionCliTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        actor = json.dumps(AGENT.to_wire())
        self.actor = actor
        code, _, err = run_cli("init", "sessions", "--date", "2026-11-09", "--actor", actor, "--root", self.root)
        self.assertEqual(code, 0, err)
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home)
        self._home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.home)  # keep the real ~/.agents/policy.yaml out of tests
        self.addCleanup(self._restore_home)

    def _restore_home(self):
        if self._home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._home

    def file(self, *lines, name="s1.jsonl"):
        path = self.root / name
        path.write_text("\n".join(lines) + "\n")
        return path

    def session_add(self, path, *extra):
        return run_cli("session", "add", path, "--run", RUN, "--actor", self.actor, "--root", self.root, *extra)

    def test_archives_and_reports_the_redactions(self):
        code, out, err = self.session_add(self.file(json.dumps({"t": f"key {FAKE_KEY}"})))
        self.assertEqual(code, 0, err)
        self.assertIn("archived s1", out)
        self.assertIn("anthropic_key 1", out)
        self.assertTrue((self.root / ".audit/runs" / RUN / "sessions/s1.jsonl.gz").is_file())

    def test_rerun_says_unchanged(self):
        path = self.file(json.dumps({"t": "hi"}))
        self.session_add(path)
        code, out, _ = self.session_add(path)
        self.assertEqual(code, 0)
        self.assertIn("unchanged", out)

    def test_redact_pattern_flag_and_project_policy_both_apply(self):
        (self.root / ".agents").mkdir()
        (self.root / ".agents/policy.yaml").write_text("security:\n  redact_patterns:\n    - 'acme_[0-9]{6}'\n")
        path = self.file(json.dumps({"t": "acme_123456 and wid-998877"}))
        code, out, err = self.session_add(path, "--redact-pattern", r"wid-\d{6}")
        self.assertEqual(code, 0, err)
        body = gzip.decompress((self.root / ".audit/runs" / RUN / "sessions/s1.jsonl.gz").read_bytes())
        self.assertNotIn(b"acme_123456", body)
        self.assertNotIn(b"wid-998877", body)

    def test_an_unreadable_policy_stops_the_write(self):
        (self.home / ".agents").mkdir()
        (self.home / ".agents/policy.yaml").write_text("security:\n  redact_patterns: [\"x\"]\n")
        before = (self.root / ".audit/runs" / RUN / "ledger.jsonl").read_bytes()
        code, _, err = self.session_add(self.file(json.dumps({"t": "hi"})))
        self.assertEqual(code, 2)
        self.assertIn("single-quoted", err)
        self.assertEqual((self.root / ".audit/runs" / RUN / "ledger.jsonl").read_bytes(), before)

    def test_unconfirmed_exits_1_and_says_it_stayed_out(self):
        key = "sk-" + "ant-" + "k" * 30
        code, _, err = self.session_add(self.file('{"torn": "\\u0073' + key[1:]))
        self.assertEqual(code, 1)
        self.assertIn("stays out of git", err)

    def test_a_refused_archived_entry_exits_1_with_its_pointer_recorded(self):
        with refusing_archived_entries():
            code, _, err = self.session_add(self.file(json.dumps({"t": "hi"})))
        self.assertEqual(code, 1, err)
        self.assertIn("recorded its location", err)
        last = json.loads((self.root / ".audit/runs" / RUN / "ledger.jsonl").read_text().splitlines()[-1])
        self.assertEqual((last["type"], last["data"]["archived"]), ("session", False))

    def test_append_cli_redacts(self):
        code, _, err = run_cli("append", "--run", RUN, "--actor", self.actor, "--type", "decision",
                               "--summary", f"used {FAKE_KEY}", "--why", "test", "--root", self.root)
        self.assertEqual(code, 0, err)
        self.assertNotIn("Z" * 40, (self.root / ".audit/runs" / RUN / "ledger.jsonl").read_text())


if __name__ == "__main__":
    unittest.main()
