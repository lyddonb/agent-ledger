# Working in agent-ledger

- Standard-library Python only, 3.10 or newer. Adding any dependency is the `new-dependency` gate: stop and ask.
- The specs in `docs/specs/` are the contract. When code and spec disagree, raise it; don't silently change either.
- The ledger entry format is a public contract once v1 is tagged. Changing it is the `public-api` gate.
- Tests: `python -m unittest discover -s tests -v`. Chain and redaction code needs property-style tests with fixed seeds.
- Never put real secrets in tests. Use obviously fake ones that still match the patterns.
- The M0 charter in `.audit/runs/2026-10-18-m0-foundation/` sets scope, gates and budget for current work. Read it before starting.
