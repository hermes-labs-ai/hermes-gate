# AGENTS.md — hermes-gate

<!-- Instruction contract v1.0 — 2026-09-09 -->

Priority order: preserve receipt integrity and safety boundaries; preserve public API
behavior; then minimize the diff. Treat each user request as an independent task and
carry prior task state forward only when the user explicitly asks.

## What this repo is

HermesGate: a bounded, receipt-based completion rail for coding sessions. It runs a
repository's own declared commands, records exactly which content and tool versions
were checked, and reuses a PASS only while those bytes still match. It has no daemon
and no runtime dependencies beyond Python 3.11+ and Git.

## Key paths

- `src/hermes_gate/` — package source (CLI, engine, adapters, receipts, doctor)
- `tests/` — pytest suite
- `schemas/` — `gate-result-v1.schema.json` and `receipt.schema.json` (canonical bytes; do not reformat)
- `.hermes/` — this repo's own dogfooded gate config (`gate.toml`)

## Minimal commands

```bash
python -m pip install -e '.[test]'
ruff check .
pytest
python -m build
```

## Do not use it for

- repairing code, invoking a model, or authorizing a push, pull request, or release
- proving correctness beyond "these exact bytes passed these exact declared commands"

## Constraints

- Keep command execution argv-based; do not introduce implicit shells.
- Keep generated runtime state under the repository Git directory or the user cache.
- Never auto-install PyGate, QuickGate.js, reviewers, or project tools.
- Preserve the canonical bytes of `schemas/gate-result-v1.schema.json`.
- Keep runtime dependencies at zero unless a change cannot be implemented safely with
  the standard library.
- Document what a receipt proves and what it does not prove.

## Definition of done

- `ruff check .` and `pytest` pass
- a wheel builds (`python -m build`) and installs cleanly in an isolated environment
- new behavior has a covering test
- no absolute local paths or credentials were introduced
