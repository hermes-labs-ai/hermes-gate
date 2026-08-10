# Contributing to HermesGate

HermesGate accepts focused fixes that preserve its bounded, receipt-based model.

## Development setup

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]'
ruff check .
pytest
```

Use Python 3.11 or newer. Keep runtime dependencies at zero unless a change cannot
be implemented safely with the standard library.

## Change requirements

- Add or update tests for behavior changes.
- Keep command execution argv-based; do not introduce implicit shells.
- Keep generated runtime state under the repository Git directory or the user cache.
- Never auto-install PyGate, QuickGate.js, reviewers, or project tools.
- Preserve the canonical bytes of `schemas/gate-result-v1.schema.json`.
- Document what a receipt proves and what it does not prove.

Before handing off a change, run `ruff check .`, `pytest`, a wheel build, an
isolated install smoke test, and an absolute-path/credential scan.

By contributing, you agree that your contribution is licensed under Apache-2.0.
