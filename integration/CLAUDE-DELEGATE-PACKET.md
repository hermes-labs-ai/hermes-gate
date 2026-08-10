# Claude Code delegate packet — Hermes Gate v0.1

This packet is for one tool-enabled Claude Code delegate. Codex must not inspect or modify Claude’s
protected hooks, settings, project memory, or live instruction files.

## Objective

Install the equivalent Hermes Gate completion rail for every Claude Code session, preserving every
unrelated hook and instruction byte. Use the already installed absolute `hermes-gate` executable.

## Required behavior

1. Back up the exact protected files you will change into a private Hermes Gate state directory.
2. Merge, do not replace, equivalent command hooks:
   - `SessionStart` → `hermes-gate hook session-start`, timeout 3 seconds.
   - `Stop` → `hermes-gate hook stop`, timeout 10 seconds.
   - shell `PreToolUse` → `hermes-gate hook pre-tool-use`, timeout 5 seconds.
3. Add only this compact completion contract to the appropriate global Claude instruction surface:

   > Hermes Gate is active. For code changes, run `hermes-gate fast` before completion and one
   > `hermes-gate review` for a completed diff. Commit/push/PR boundaries require matching receipts.
   > Read-only, non-Git, unchanged, and non-code work is exempt. Hooks never repair files, spawn a
   > reviewer, authorize public actions, or continue an unchanged Stop.

4. Do not install a daemon, task registry, dashboard, watcher, model hook, auto-repair, or a second
   semantic reviewer.
5. Preserve existing owner-authorization hooks as separate and authoritative.
6. If the Claude hook schema differs, adapt only the outer event/output envelope. Do not fork the
   Hermes Gate decision logic.

## Fresh-session acceptance

Exercise disposable repositories/sessions for: read-only, non-Git, code PASS, code FAIL, missing
profile, existing dirty bytes, resume, provider unavailable, and public action without an owner
token. Prove Stop performs no model call, repair, continuation, tracked write, or unchanged-digest
rerun. Prove the real protected command boundary denies a code commit without fast and does not deny
quoted prose or docs-only work.

Do not create a remote, push, PR, release, account, token, subscription, or payment.

## Return receipt

Write only a non-sensitive behavioral receipt to
`~/.local/state/hermes-gate/claude-delegate-receipt.json` with:

- schema `hermes-gate/claude-delegate-v1`;
- delegate identity/version and UTC completion time;
- PASS/FAIL for each acceptance case;
- hashes of installed hook definitions and compact instruction, never their protected contents;
- preserved unrelated-hook count;
- exact rollback command;
- limitations and any user action still required.

Return the receipt path and its SHA-256. Do not return protected file contents.
