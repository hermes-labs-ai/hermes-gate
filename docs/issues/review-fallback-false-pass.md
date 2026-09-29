# Automatic review fallback can issue a false PASS after reviewer failure

## Observed failure

On 2026-09-29, hermes-gate 0.1.6 reviewed the clean committed diff for
little-canary PR #132. CodeRabbit was unavailable, so Gate invoked its automatic
`hermes-pr-review` fallback. The child exited 2 after Claude returned invalid
review JSON (`limitations` was a string rather than an array). The child's
`review.json` still contained `"verdict": "PASS"` and empty `findings`, but the
outer workflow never validated that review. Gate converted the raw file into a
`complete` event and wrote a Gate review PASS receipt with zero findings.

The failed PASS receipt was discarded. A separate Codex-backed fallback run
returned exit 0 with `outer_workflow_status: VALIDATED`, verdict PASS, and zero
findings. This issue concerns receipt integrity, not the quality of that later
review. `kwik-gate run --mode full` runs deterministic checks; semantic review
is the separate `hermes-gate review` command.

## Small reproduction

1. Use a Gate profile with CodeRabbit review and no configured fallback, on a
   clean committed diff with an installed `hermes-pr-review`.
2. Have CodeRabbit return unavailable.
3. Have `hermes-pr-review` write a raw `review.json` containing
   `{"verdict":"PASS","findings":[]}` and then exit 2 before validation.
4. Run `hermes-gate review` after a matching fast PASS.

**Actual:** Gate treats the raw empty findings as a completed review and writes
a PASS receipt. **Expected:** Gate returns `REVIEW_UNAVAILABLE`, never caches
that result as PASS, and records that the attempted fallback was unusable. A
later valid retry for the same diff must remain possible.

## Cause and acceptance criteria

`_fallback_review` in `src/hermes_gate/engine.py` reads the raw `review.json`
without checking the child's return code or its validated outer receipt. That
file is an unvalidated intermediate artifact.

- A nonzero fallback exit, including exit 2 with raw PASS JSON, cannot produce
  a Gate PASS or FAIL semantic review receipt.
- A zero exit without the child's matching validated receipt cannot produce a
  Gate semantic PASS. The receipt must identify the reviewed base and HEAD.
- Validated PASS and FINDINGS results continue to map into Gate outcomes, and
  failed fallback attempts remain retryable.
- A regression test recreates the failed child with raw PASS JSON and verifies
  the receipt status and later valid retry.
