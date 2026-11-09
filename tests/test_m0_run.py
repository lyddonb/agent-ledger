"""The M0 run's committed ledger verifies, so CI fails if anyone edits a past line."""
import unittest
from pathlib import Path

import helpers  # noqa: F401  (puts src on the path)

from agent_ledger.verify import Verified, verify_run

M0 = Path(__file__).resolve().parent.parent / ".audit" / "runs" / "2026-10-18-m0-foundation"


class M0RunTest(unittest.TestCase):
    def test_the_m0_ledger_verifies_and_starts_with_the_imported_bootstrap_notes(self):
        report = verify_run(M0)
        self.assertIsInstance(report, Verified, getattr(report, "problems", None))
        imported = [e for e in report.entries if "imported" in e.data]
        self.assertEqual([e.seq for e in imported], list(range(2, 10)))
        self.assertEqual([e.data["imported"]["source"] for e in imported],
                         [f"bootstrap-notes.md#{n}" for n in range(1, 9)])
        self.assertEqual(imported[0].type.value, "approval")
        self.assertEqual(imported[0].actor.to_wire()["kind"], "human")


if __name__ == "__main__":
    unittest.main()
