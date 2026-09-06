# Changelog

All notable changes to HermesGate will be documented here.

The format follows Keep a Changelog, and the project uses Semantic Versioning.

## [Unreleased]

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

[Unreleased]: https://github.com/hermes-labs-ai/hermes-gate/compare/v0.1.2...HEAD
[0.1.2]: https://github.com/hermes-labs-ai/hermes-gate/releases/tag/v0.1.2
[0.1.1]: https://github.com/hermes-labs-ai/hermes-gate/releases/tag/v0.1.1
[0.1.0]: https://github.com/hermes-labs-ai/hermes-gate/releases/tag/v0.1.0
