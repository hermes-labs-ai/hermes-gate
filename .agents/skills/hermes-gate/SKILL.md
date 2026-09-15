---
name: hermes-gate
description: Use when a coding agent needs a bounded, receipt-bound completion rail — a cached fast gate for session-scoped deterministic checks, plus repair/review/full/boundary commands that produce exact machine-readable receipts before a commit, PR, or release is called done.
license: MIT
compatibility: Requires Python 3.10+; installs via `pip install hermes-gate`, executable name is `hermes-gate`. `hermes-gate init` writes a repository profile and runner into the target repo; `fast` requires that profile to exist first.
---

# hermes-gate

hermes-gate is a receipt-bound completion rail for coding agents. It runs a
declared, bounded set of deterministic checks per repository, caches the
session-scoped fast path, and refuses to report success without an exact
machine-readable receipt — closing the gap where an agent claims "done" with
no verifiable evidence.

## Use it for

- Gating a coding-agent session's completion claim against a repo-declared
  deterministic contract (`hermes-gate fast`)
- Validating an exact Git or PR boundary before merge (`hermes-gate
  boundary`)
- Running the complete declared repository contract for a release
  (`hermes-gate full`)
- Read-only diagnostics on an installation without mutating anything
  (`hermes-gate doctor`)

## Do not use it for

- A general-purpose CI runner — it wraps and gates an already-declared
  contract, it does not define your test/lint/build pipeline from scratch
- Enforcing anything in a repo that hasn't run `hermes-gate init` — `fast`
  reports `NOT_CONFIGURED` rather than inventing a check set
- Proving semantic correctness — it proves the declared checks ran and
  passed, not that the change is a good idea

## Quickstart

```bash
pip install hermes-gate
hermes-gate --help
```

Real output running `fast` against a repo with no installed profile:

```json
{
  "command": "fast",
  "elapsed_ms": 0,
  "reason": "run hermes-gate init",
  "schema": "hermes-gate/result-v1",
  "status": "NOT_CONFIGURED"
}
```

After `hermes-gate init` installs a profile, the same command runs the
repo's declared checks and returns a `PASS`/`FAIL` receipt against
`hermes-gate/result-v1`.

## Commands

```
hermes-gate init                 # install a repository profile, runner, workflow, baseline
hermes-gate fast                 # run the cached session-scoped deterministic gate
hermes-gate repair               # run one configured deterministic repair
hermes-gate review               # run one bounded independent review
hermes-gate full                 # run the complete declared repository contract
hermes-gate boundary             # validate exact receipts for a Git or PR boundary
hermes-gate doctor               # read-only installation and receipt diagnostics
```

## Output shape

- Every command returns `hermes-gate/result-v1` JSON: `command`,
  `elapsed_ms`, `status`, plus a `reason` when not `PASS`
- `status` values include `NOT_CONFIGURED`, `PASS`, `FAIL` — a missing
  profile is a distinct, explicit state, never silently treated as pass
- `doctor` is read-only and safe to run at any time to check installation
  state without side effects

## Common gotchas

- Running `fast` before `init` always returns `NOT_CONFIGURED` — this is
  correct behavior, not a bug; there is no implicit default check set.
- `boundary` validates an *exact* Git/PR boundary — it will not pass on a
  fuzzy or partial diff match.
- `full` runs the complete contract and is expected to be slower than
  `fast`; use `fast` for the per-turn session-scoped loop and `full` before
  a release.

## More

Full docs and repository-profile reference:
https://github.com/hermes-labs-ai/hermes-gate
