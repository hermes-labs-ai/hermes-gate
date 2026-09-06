# HermesGate

HermesGate turns “run the checks again” into a bounded completion ceremony. It
runs the repository's own commands, records exactly which content and tool
versions were checked, and reuses a PASS only while those bytes still match.

It has no daemon and no runtime dependencies beyond Python 3.11+ and Git.
Lifecycle hooks inspect receipts; they do not repair code, invoke a model, or
authorize a push, pull request, or release.

## Five-minute quickstart

Install the published package, then enter the repository you want to gate:

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

For HermesGate development, run `python -m pip install .` from a local checkout
instead. Release builds verify the tag, package and runner versions, tracked
runner, distribution metadata, and packaged source bytes before upload; they also reject versions
superseded on PyPI after environment approval.

`init` detects repository-native Python or JavaScript commands and writes a
reviewable `.hermes/gate.toml`, a checksum-bound stdlib runner, and a CI
workflow. Existing integration files are not overwritten unless
`hermes-gate init --force` is explicit.

The copied runner also requires Python 3.11 or newer, even when the project
it checks supports older Python versions. Run Gate under a supported interpreter;
profile commands may select the project's own interpreter separately.

New default whitespace checks inspect selected working-tree, staged and untracked
files using Git's whitespace rules. Existing profiles are preserved; adopting the
new check in an existing repository requires a reviewed runner/profile update.
The copied runner drains stdout and stderr concurrently while retaining at most
32 KiB of each stream; its truncation flag reports any discarded output.

## Completion workflows

| Workflow | Use it for | Receipt boundary |
| --- | --- | --- |
| `hermes-gate fast` | Short feedback over current changed paths | Exact content digest, selected paths, checks, elapsed time, and command versions |
| `hermes-gate review` | One bounded semantic review after fast passes | Exact digest, provider version, normalized findings, and suppression count |
| `hermes-gate full` | Complete repository-local test contract | Exact digest, full checks, elapsed time, and command versions |

A typical local ceremony is:

```bash
hermes-gate fast
hermes-gate review
hermes-gate full
hermes-gate boundary commit
```

The boundary command only verifies configured receipts. It never performs the Git
operation or grants owner authorization.

## CLI

```text
hermes-gate init [--force]
hermes-gate fast
hermes-gate repair
hermes-gate review
hermes-gate full
hermes-gate boundary commit|push|pr-create|pr-ready
hermes-gate doctor
hermes-gate uninstall-repo
hermes-gate --version
```

Commands emit JSON with one terminal state: `PASS`, `FAIL`,
`NOT_APPLICABLE`, `NOT_CONFIGURED`, `REVIEW_UNAVAILABLE`, `PARKED`, or
`ERROR`.

## Repository-native default

The generated profile uses argv arrays and the repository's existing Ruff,
pytest, npm, lint, typecheck, test, or build commands. It never installs these
tools. Runtime receipts, baselines, install manifests, and adapter artifacts live
under `.git/hermes-gate/`; generated tracked source contains no machine-specific
absolute paths.

```toml
[gate]
fast_budget_seconds = 8.0
full_required_local = false

[[fast]]
name = "ruff"
argv = ["ruff", "check", "{files}"]
timeout_seconds = 6.0
globs = ["**/*.py"]
```

`{files}` expands to separate argv entries. Shell strings are rejected.

## Optional primitive adapters

HermesGate packages a standard-library loader and validator for the shared
`gate-result/v1` schema. PyGate and QuickGate.js adapters are opt-in and
isolated; HermesGate never downloads or installs them.

PyGate 0.2.0 or newer:

```toml
[adapter]
enabled = true
name = "pygate"
argv = ["pygate"]
minimum_version = "0.2.0"
```

QuickGate.js 0.2.3 or newer:

```toml
[adapter]
enabled = true
name = "quick-gate"
argv = ["quick-gate"]
minimum_version = "0.2.3"
```

Adapter output is written below `.git/hermes-gate/adapters/`, validated against
the packaged contract, and then normalized into a HermesGate receipt. An absent,
old, or invalid primitive returns `ERROR`; it never falls through to an
unreviewed installation.

## Review and hooks

`review` requires an exact fast PASS. CodeRabbit agent-mode JSONL is the initial
provider boundary. If it is unavailable, a clean committed diff may use an
installed `hermes-pr-review` fallback. Unparseable, unauthenticated, timed-out,
or unavailable review output is never relabeled PASS.

`install-codex` and `uninstall-codex` are intentionally hidden integration
commands. Codex integration preserves unrelated hook and instruction content.
Claude settings and instruction surfaces remain outside Codex's ownership.

## What PASS means

A PASS proves only that the declared commands passed for the receipt-bound local
content. It does not prove universal correctness, security, mergeability, public
release, adoption, or permission to push, publish, or create a pull request.

See [SECURITY.md](SECURITY.md), [CONTRIBUTING.md](CONTRIBUTING.md), and
[CHANGELOG.md](CHANGELOG.md) for project policy and release history.
