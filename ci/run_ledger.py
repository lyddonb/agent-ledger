"""Run the `ledger` CLI from the Action's own copy of agent-ledger, never the checked-out repo's.

The Action runs this with `python -I`, so neither the working directory nor PYTHONPATH is on
sys.path. A pull request that adds its own `agent_ledger` package can't replace the code that
checks it.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent_ledger.cli import main  # noqa: E402

sys.exit(main(sys.argv[1:]))
