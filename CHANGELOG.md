# Changelog

All notable changes to HermesGate will be documented here.

The format follows Keep a Changelog, and the project uses Semantic Versioning.

## [Unreleased]

## [0.1.3] - 2026-09-09

### Fixed

- The repository runner no longer reports a pass over zero bytes in a hosted
  checkout. `actions/checkout` produces a pristine tree, so the worktree, index
  and untracked scope selected nothing and a file-driven stage returned PASS
  having read nothing. A file-driven stage with no selection is now recorded as
  `NOT_APPLICABLE` with its own reason, and a run in which no stage executed
  reports `NOT_APPLICABLE` rather than PASS.
- `full` now runs every declared stage and aggregates the failures instead of
  returning at the first non-PASS check, so a multi-stage profile can no longer
  hide later stages behind an early failure. `fast` is unchanged: it keeps both
  its first-failure exit and its selection guard, so a stage whose globs match
  nothing is still skipped outright and the local budget still holds.
- A stage that cannot be launched at all - missing, not executable, or the wrong
  binary format - is that stage's failure rather than the whole gate's, and the
  remaining declared stages still run. Unusable `argv` declarations, including a
  scalar where an array is required, are reported as that stage's error instead
  of raising out of the run, and the run names every unusable and failed stage.

### Added

- The runner accepts an explicit review range: `--base REV` (or
  `HERMES_GATE_BASE`) compares the merge base of that revision with HEAD, and
  `--all` reviews every committed byte against the empty tree. The two are
  mutually exclusive, and a base that is not present in the checkout is a hard
  error naming `fetch-depth: 0` rather than a silently empty change set. With no
  base supplied, local behaviour is unchanged: worktree, index and untracked
  bytes. The resolved range is published to child stages through
  `HERMES_GATE_RANGE`, is used by `diff-check`, and is recorded as `range` on the
  receipt.
- The test suite isolates itself from the gate that runs it: pytest is a declared
  gate stage, so `HERMES_GATE_BASE` and `HERMES_GATE_RANGE` are cleared before
  each test and set explicitly where a test needs them. Without that, fixture
  repositories would inherit the enclosing repository's base revision and the
  runner regressions would fail under CI while passing locally.
- `hermes-gate init` now generates a workflow that checks out with
  `fetch-depth: 0`, passes the pull request base to the gate, and sweeps every
  committed byte with `--all` outside pull requests, so a generated rail reviews
  real bytes on a hosted runner.
- Release checks bind the GitHub tag, package and runner versions, tracked runner,
  wheel/sdist metadata, and packaged source bytes before build and again before PyPI upload.
- The quickstart now includes installation from the published PyPI package.

### Changed

- Package author metadata now uses the current Hermes Labs address
  `roli@hermes-labs.ai`, matching every other published Hermes Labs package.
  Published 0.1.2 metadata on PyPI is immutable and permanently keeps the
  legacy address; 0.1.3 is the release that exposes the corrected value.

## [0.1.2] - 2026-09-06

### Fixed

- Fast checks now skip deleted paths instead of invoking file-based checks on
  files that no longer exist in the worktree.

## [0.1.1] - 2026-09-06

### Fixed

- Generated whitespace checks now cover selected working-tree, staged, and
  untracked files.
- The copied runner bounds concurrent stdout/stderr capture and reports when
  output was truncated.
- Hermes Gate's own profile checks its maintained runner.

## [0.1.0] - 2026-08-08

### Added

- Bounded `fast`, `review`, and `full` workflows with digest-bound receipts.
- Repository initialization, rollback, diagnostics, and Codex lifecycle integration.
- Zero-dependency repository-native runner.
- Canonical stdlib `gate-result/v1` loader and validator.
- Optional isolated PyGate 0.2.0+ and QuickGate.js 0.2.3+ adapters.
- Git-internal runtime state, command-version receipts, OSS policy files, and package schema data.

[Unreleased]: https://github.com/hermes-labs-ai/hermes-gate/compare/v0.1.3...HEAD
[0.1.3]: https://github.com/hermes-labs-ai/hermes-gate/releases/tag/v0.1.3
[0.1.2]: https://github.com/hermes-labs-ai/hermes-gate/releases/tag/v0.1.2
[0.1.1]: https://github.com/hermes-labs-ai/hermes-gate/releases/tag/v0.1.1
[0.1.0]: https://github.com/hermes-labs-ai/hermes-gate/releases/tag/v0.1.0
