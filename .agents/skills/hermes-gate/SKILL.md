---
name: hermes-gate
description: Use when a coding agent needs a bounded, receipt-bound completion check before claiming a repository's tests/lint pass — it runs the repository's own commands, records exactly which content and tool versions were checked, and reuses a PASS only while those bytes still match. Deterministic, no daemon, no model calls.
license: Apache-2.0
compatibility: Requires Python 3.11+ and Git; installs via pip. No runtime dependencies beyond those. Checked commands (Ruff, pytest, npm scripts, etc.) must already exist in the target repository.
---

# HermesGate

HermesGate turns "run the checks again" into a bounded completion ceremony.
It runs a repository's own declared commands, records exactly which content
and tool versions were checked, and only reuses a PASS while those bytes
still match. It has no daemon and does not repair code, invoke a model, or
authorize a push, pull request, or release on its own.

## Use it for

- Getting a repeatable, receipt-bound PASS/FAIL for the exact commit before
  claiming a repository's checks pass
- Bootstrapping a reviewable `.hermes/gate.toml` profile plus CI workflow in
  a repository that has none yet (`hermes-gate init`)
- Running a fast changed-paths check versus a complete repository-local test
  contract (`hermes-gate fast` vs `hermes-gate full`)
- Diagnosing why a gate is not configured or not applicable (`hermes-gate
  doctor`)

## Do not use it for

- Proving universal correctness, security, or that a repository is safe to
  merge — a PASS proves only that the declared local commands passed for the
  receipt-bound content
- Performing the Git operation itself — `hermes-gate boundary commit|push|
  pr-create|pr-ready` only verifies configured receipts, it never runs Git
- Repairing code — `hermes-gate repair` is a separate, explicit step and may
  mutate files; review the diff

## Quickstart

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install hermes-gate
cd /path/to/your/git-repository
hermes-gate init
hermes-gate fast
hermes-gate full
hermes-gate doctor
```

## Output shape

- Every command emits JSON with exactly one terminal state: `PASS`, `FAIL`,
  `NOT_APPLICABLE`, `NOT_CONFIGURED`, `REVIEW_UNAVAILABLE`, `PARKED`, or
  `ERROR`.
- `hermes-gate fast` — exact content digest, selected paths, checks run,
  elapsed time, and command versions.
- `hermes-gate full` — the same receipt shape over the complete configured
  check set.
- `hermes-gate boundary commit|push|pr-create|pr-ready` — verifies configured
  receipts only; never performs the Git operation itself.

## Common gotchas

- `hermes-gate review` requires an exact fast PASS first; it does not run
  standalone.
- The generated runner requires Python 3.11+ even when the checked project
  supports older Python versions.
- Runtime receipts and baselines live under `.git/hermes-gate/`; generated
  tracked source contains no machine-specific absolute paths.
- `init` never overwrites existing integration files unless `--force` is
  explicit.

## More

Full docs and CLI reference:
https://github.com/hermes-labs-ai/hermes-gate
