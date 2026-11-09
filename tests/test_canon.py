"""The canonical form is the format contract. These vectors and the golden ledger pin it.

If a test here fails after a code change, the ledger format changed. That is the `public-api`
gate once v1 is tagged: don't update a vector or the golden hash to make it pass.
"""
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

import helpers  # noqa: F401  (puts src on sys.path)

from agent_ledger._canon import CanonError, canonical_bytes, parse_canonical
from agent_ledger.entry import (
    AgentActor, ChainHead, EntryType, HumanActor, encode_line, next_entry,
)
from agent_ledger.verify import Verified, verify_run

VECTORS = Path(__file__).resolve().parent / "vectors"
GOLDEN_RUN = VECTORS / "2026-11-08-golden"
GOLDEN_HEAD = "2465b40d3da45b11eeb8b3f3b5b0a79c60ac46b2dc15d7fc30b858d8b1ef25da"  # also reproduced by an independent Node verifier


def vector_bytes(v) -> bytes:
    if "repeat" in v:
        r = v["repeat"]
        return (r["prefix"] + r["open"] * r["times"] + r["close"] * r["times"] + r["suffix"]).encode()
    return bytes.fromhex(v["hex"])


def golden_lines() -> list[bytes]:
    """The golden run, built from fixed inputs. Its bytes are checked in under vectors/."""
    head = ChainHead.genesis("2026-11-08-golden")
    agent = AgentActor("beau-agent@claude-code#7f3c", "lyddonb@github", "claude-code", "opus", "7f3c1a2b")
    human = HumanActor("lyddonb", "lyddonb@github")
    specs = [
        (human, EntryType.APPROVAL, "Charter approved as written", "Reviewed in chat",
         ["charter.md sha256 bb9611ab"], {}),
        (agent, EntryType.DECISION, "Canonical form is RFC 8785 with integers only",
         "Auditors can reuse a JCS library", ["docs/specs/S3-audit-ledger.md:40"],
         {"options": ["ascii", "utf-8"], "chosen": "utf-8"}),
        (agent, EntryType.SPEND, "Unit 1 spend so far", "Budget check", [],
         {"usd_micros": 4250000, "tokens": 812345, "wall_s": 5400}),
        (human, EntryType.GATE_ANSWER, "Ja, weiter so – “quoted” 😀", "Unicode text stays raw",
         [], {"gate": "new-dependency", "answer": "no", "nested": {"a": [None, True, False, -1], "s": "line\u2028sep\x7f"}}),
    ]
    lines = []
    for i, (actor, etype, summary, why, evidence, data) in enumerate(specs):
        entry = next_entry(head, now=datetime(2026, 11, 8, 20, 0, i, tzinfo=timezone.utc), actor=actor,
                           type=etype, summary=summary, why=why, evidence=evidence, data=data)
        lines.append(encode_line(entry))
        head = entry.head
    return lines


class CanonicalVectorTest(unittest.TestCase):
    def test_vectors(self):
        vectors = json.loads((VECTORS / "canonical.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(vectors), 30)
        for v in vectors:
            with self.subTest(v["name"]):
                line = vector_bytes(v)
                if v["expect"] == "ok":
                    self.assertEqual(canonical_bytes(parse_canonical(line)), line)
                else:
                    with self.assertRaises(CanonError) as cm:
                        parse_canonical(line)
                    self.assertEqual(cm.exception.kind, v["expect"], str(cm.exception))

    def test_encoder_rejects_what_the_parser_rejects(self):
        for bad in [{"n": 1.5}, {"n": float("nan")}, {"n": 2**53}, {"A": 1}, {"s": "\ud800"},
                    {"b": b"bytes"}, {1: "int key"}]:
            with self.subTest(repr(bad)):
                with self.assertRaises(CanonError):
                    canonical_bytes(bad)

    def test_bool_is_never_an_integer(self):
        self.assertEqual(canonical_bytes({"t": True, "o": 1}), b'{"o":1,"t":true}')


class GoldenLedgerTest(unittest.TestCase):
    def test_golden_ledger_bytes_are_stable(self):
        self.assertEqual((GOLDEN_RUN / "ledger.jsonl").read_bytes(), b"".join(golden_lines()))

    def test_golden_ledger_verifies_to_the_pinned_head(self):
        report = verify_run(GOLDEN_RUN)
        self.assertIsInstance(report, Verified, str(report))
        self.assertEqual(report.head.link, GOLDEN_HEAD)


if __name__ == "__main__":
    unittest.main()
