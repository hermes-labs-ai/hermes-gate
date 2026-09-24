# Claude Code hook integration

This reference describes the public Hermes Gate hook interface. Configure
hooks using the supported schema of the installed Claude Code version.
Back up configuration before editing and preserve unrelated hooks.

## Hook commands

| Event | Command | Suggested timeout |
|---|---|---|
| SessionStart | `hermes-gate hook session-start` | 3 seconds |
| Stop | `hermes-gate hook stop` | 10 seconds |
| Shell PreToolUse | `hermes-gate hook pre-tool-use` | 5 seconds |

Resolve the executable in the host environment. Adapt the host event envelope
without duplicating Gate's decision logic.

## Completion contract

For code changes in an adopted repository, run `hermes-gate fast` and one
`hermes-gate review` for the completed diff. Commit, push, and PR boundaries
require matching receipts. Read-only, non-Git, unchanged, and non-code work is
exempt. Hooks do not repair files, invoke reviewers, or authorize publication.

## Integration checks

Use disposable repositories to exercise read-only and non-Git work, passing
and failing code changes, missing profiles, existing dirty files, resume,
and unavailable review providers. Check that a code commit without the required
receipt is rejected and that documentation-only work is not misclassified.

Confirm that Stop makes no model calls, repairs, tracked writes, or repeated
checks for an unchanged diff. Keep host-specific configuration and rollback
backups local. Public bug reports should describe behavior with a minimal
reproduction and omit private hook configuration or account data.
