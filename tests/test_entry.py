"""The entry model: what can and can't be constructed or decoded."""
import json
import random
import unittest
from dataclasses import replace
from datetime import datetime, timezone

from helpers import RUN_ID, build_chain, rand_actor

from agent_ledger._canon import parse_canonical
from agent_ledger.entry import (
    AgentActor, ChainHead, Entry, EntryError, EntryType, HumanActor, decode_entry, encode_line, next_entry,
)

NOW = datetime(2026, 11, 8, 20, 0, 0, tzinfo=timezone.utc)
AGENT = AgentActor("beau-agent@claude-code#7f3c", "lyddonb@github", "claude-code", "opus", "7f3c1a2b")
HUMAN = HumanActor("lyddonb", "lyddonb@github")


def make(**overrides):
    args = dict(now=NOW, actor=AGENT, type=EntryType.DECISION, summary="s", why="w")
    args.update(overrides)
    return next_entry(ChainHead.genesis(RUN_ID), **args)


def decode_line(line: bytes):
    return decode_entry(parse_canonical(line.rstrip(b"\n")))


class EntryModelTest(unittest.TestCase):
    def assertSchema(self, fn, kind="schema"):
        with self.assertRaises(EntryError) as cm:
            fn()
        self.assertEqual(cm.exception.kind, kind, str(cm.exception))

    def test_round_trip(self):
        rng = random.Random(3)
        for line in build_chain(rng, 40):
            entry = decode_line(line)
            self.assertEqual(encode_line(entry), line)

    def test_first_entry_links_to_genesis(self):
        e = make()
        self.assertEqual((e.seq, e.prev, e.v), (1, f"genesis:{RUN_ID}", 1))
        nxt = next_entry(e.head, now=NOW, actor=AGENT, type=EntryType.STATE, summary="s", why="w")
        self.assertEqual((nxt.seq, nxt.prev), (2, e.hash))

    def test_ts_is_utc_to_the_second(self):
        from datetime import timedelta
        local = datetime(2026, 11, 8, 13, 0, 0, 999999, tzinfo=timezone(timedelta(hours=-7)))
        self.assertEqual(make(now=local).ts, "2026-11-08T20:00:00Z")
        with self.assertRaises(ValueError):
            make(now=datetime(2026, 11, 8))

    def test_an_entry_cannot_hold_a_stale_hash(self):
        e = make()
        self.assertSchema(lambda: replace(e, summary="changed"), "hash-mismatch")

    def test_approvals_and_gate_answers_must_come_from_a_human(self):
        for t in (EntryType.APPROVAL, EntryType.GATE_ANSWER):
            self.assertSchema(lambda: make(type=t))
            self.assertEqual(make(type=t, actor=HUMAN).actor, HUMAN)

    def test_text_fields_are_one_line(self):
        for bad in ["", "two\nlines", "tab\there", "nel\x85", "ls ", "esc\x1b"]:
            with self.subTest(repr(bad)):
                self.assertSchema(lambda: make(summary=bad))
                self.assertSchema(lambda: make(why=bad))
                self.assertSchema(lambda: make(evidence=[bad]))

    def test_evidence_must_be_a_list_not_a_string(self):
        with self.assertRaises(TypeError):
            make(evidence="abc123")

    def test_data_must_be_ledger_json(self):
        for bad in [{"usd": 1.5}, {"Bad": 1}, {"n": 2**53}]:
            with self.subTest(repr(bad)):
                self.assertSchema(lambda: make(data=bad))

    def test_actor_fields_are_required(self):
        self.assertSchema(lambda: AgentActor("a", "", "t", "m", "s"))
        self.assertSchema(lambda: HumanActor("lyddonb", ""))

    def test_run_id_follows_s1(self):
        self.assertSchema(lambda: ChainHead.genesis("m0-foundation"))
        self.assertSchema(lambda: ChainHead.genesis("2026-10-18-M0"))
        self.assertEqual(ChainHead.genesis("2026-10-18-m0-foundation").link, "genesis:2026-10-18-m0-foundation")

    def test_entries_are_immutable(self):
        e = make(data={"a": [1, {"b": 2}]})
        with self.assertRaises(TypeError):
            e.data["a"] = 1  # type: ignore[index]
        self.assertIsInstance(e.data["a"], tuple)


class DecodeTest(unittest.TestCase):
    """decode_entry is the parse boundary: each wire problem has one kind."""

    def wire(self, **changes):
        obj = json.loads(encode_line(make()))
        obj.update(changes)
        for k in [k for k, v in changes.items() if v is DROP]:
            del obj[k]
        return obj

    def kind(self, obj):
        with self.assertRaises(EntryError) as cm:
            decode_entry(obj)
        return cm.exception.kind

    def test_version_is_checked_first(self):
        self.assertEqual(self.kind(self.wire(v=2, seq="garbage")), "unsupported-version")
        self.assertEqual(self.kind(self.wire(v=DROP)), "unsupported-version")
        self.assertEqual(self.kind(self.wire(v=True)), "unsupported-version")

    def test_closed_key_set(self):
        self.assertEqual(self.kind(self.wire(extra="x")), "schema")
        self.assertEqual(self.kind(self.wire(data=DROP)), "schema")

    def test_field_types(self):
        cases = [
            {"seq": True}, {"seq": 0}, {"ts": "2026-02-30T00:00:00Z"}, {"ts": "2026-11-08T20:00:00+00:00"},
            {"type": "chat"}, {"actor": {"kind": "robot", "id": "x", "on_behalf_of": "y"}},
            {"actor": {"kind": "human", "id": "x", "on_behalf_of": "y", "tool": "t"}},
            {"evidence": "abc"}, {"data": []}, {"prev": "genesis:2026-01-01-other"}, {"hash": "ABC"},
        ]
        for change in cases:
            with self.subTest(change):
                self.assertEqual(self.kind(self.wire(**change)), "schema")

    def test_hash_mismatch(self):
        self.assertEqual(self.kind(self.wire(summary="edited")), "hash-mismatch")


DROP = object()


if __name__ == "__main__":
    unittest.main()
