from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from hermes_gate.cli import main
from hermes_gate.delegate_judge import judge, read_request


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Test")
    (root / ".hermes").mkdir()
    return root


def write_profile(
    root: Path, command: list[str] | None = None, *, exclusions: list[str] | None = None
) -> None:
    command = command or [sys.executable, "-c", "raise SystemExit(0)"]
    exclusions = exclusions if exclusions is not None else [".git/**"]
    (root / ".hermes" / "gate.toml").write_text(
        f"""[gate]
fast_budget_seconds = 8.0
full_required_local = false
exclusions = {json.dumps(exclusions)}
[lintlang]
enabled = false
[review]
argv = ["coderabbit", "--agent"]
material_severities = ["critical", "major"]
material_categories = ["correctness"]
fallback_argv = []
[[fast]]
name = "test"
argv = {json.dumps(command)}
timeout_seconds = 2.0
globs = ["**/*"]
""",
        encoding="utf-8",
    )


def request(workspace_path: Path, **overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "version": 1,
        "goal": "implement the feature",
        "summary": "did the thing",
        "attempt": 0,
        "max_retries": 2,
        "previous_feedback": None,
        "task_index": 0,
        "subagent_id": "agent-1",
        "session_id": None,
        "model": None,
        "api_calls": None,
        "completed": True,
        "workspace": str(workspace_path),
    }
    base.update(overrides)
    return base


def test_pass_when_fast_gate_passes(repo: Path) -> None:
    write_profile(repo)
    outcome = judge(request(repo))
    assert outcome == {"verdict": "pass", "feedback": ""}


def test_pass_when_all_changed_paths_are_excluded(repo: Path) -> None:
    write_profile(repo, exclusions=[".git/**", "*.md"])
    git(repo, "add", ".hermes/gate.toml")
    git(repo, "commit", "-q", "-m", "init")
    (repo / "notes.md").write_text("scratch\n", encoding="utf-8")
    outcome = judge(request(repo))
    assert outcome == {"verdict": "pass", "feedback": ""}


def test_retry_below_max_retries_then_reject_at_ceiling(repo: Path) -> None:
    write_profile(repo, [sys.executable, "-c", "raise SystemExit(1)"])
    (repo / "source.py").write_text("bad = True\n", encoding="utf-8")
    retrying = judge(request(repo, attempt=0, max_retries=2))
    assert retrying["verdict"] == "retry"
    assert "test" in retrying["feedback"]
    rejecting = judge(request(repo, attempt=2, max_retries=2))
    assert rejecting["verdict"] == "reject"


def test_failure_feedback_omits_raw_stdout(repo: Path) -> None:
    secret_marker = "TOP-SECRET-SOURCE-BYTES"
    write_profile(
        repo,
        [sys.executable, "-c", f"import sys; print({secret_marker!r}); raise SystemExit(1)"],
    )
    (repo / "source.py").write_text("bad = True\n", encoding="utf-8")
    outcome = judge(request(repo, attempt=0, max_retries=1))
    assert outcome["verdict"] == "retry"
    assert secret_marker not in outcome["feedback"]


def test_missing_profile_is_error(repo: Path) -> None:
    outcome = judge(request(repo))
    assert outcome == {"verdict": "error", "feedback": "gate not configured: run hermes-gate init"}


def test_workspace_not_a_repository_is_error(tmp_path: Path) -> None:
    not_a_repo = tmp_path / "plain-dir"
    not_a_repo.mkdir()
    outcome = judge(request(not_a_repo))
    assert outcome == {"verdict": "error", "feedback": "workspace is not a Git repository"}


def test_missing_workspace_directory_is_error(tmp_path: Path) -> None:
    outcome = judge(request(tmp_path / "does-not-exist"))
    assert outcome["verdict"] == "error"
    assert "not a directory" in outcome["feedback"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"version": 2},
        {"workspace": ""},
        {"attempt": -1},
        {"subagent_id": "  "},
    ],
)
def test_invalid_request_fields_are_rejected(repo: Path, overrides: dict[str, object]) -> None:
    outcome = judge(request(repo, **overrides))
    assert outcome["verdict"] == "error"
    assert outcome["feedback"].startswith("invalid request:")


def test_missing_required_field_is_rejected(repo: Path) -> None:
    payload = request(repo)
    del payload["goal"]
    outcome = judge(payload)
    assert outcome == {
        "verdict": "error",
        "feedback": "invalid request: missing required field: goal",
    }


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"attempt": "0"}, "attempt must be an integer"),
        ({"goal": 1}, "goal must be a string"),
        ({"completed": "yes"}, "completed must be a boolean"),
        ({"api_calls": "3"}, "api_calls must be an integer or null"),
        ({"model": 7}, "model must be a string or null"),
    ],
)
def test_wrong_type_field_is_rejected(
    repo: Path, overrides: dict[str, object], message: str
) -> None:
    outcome = judge(request(repo, **overrides))
    assert outcome == {"verdict": "error", "feedback": f"invalid request: {message}"}


def test_read_request_rejects_malformed_json() -> None:
    value, error = read_request(io.StringIO("not json"))
    assert value is None
    assert "not valid JSON" in error


def test_read_request_rejects_non_object_json() -> None:
    value, error = read_request(io.StringIO("[1, 2, 3]"))
    assert value is None
    assert error == "request must be a JSON object"


def test_read_request_accepts_valid_object() -> None:
    value, error = read_request(io.StringIO(json.dumps({"a": 1})))
    assert error == ""
    assert value == {"a": 1}


def test_cli_delegate_judge_pass(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write_profile(repo)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(request(repo))))
    exit_code = main(["delegate-judge"])
    captured = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert captured == {"verdict": "pass", "feedback": ""}


def test_cli_delegate_judge_malformed_stdin_still_exits_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"secret": "DO-NOT-ECHO"'))
    exit_code = main(["delegate-judge"])
    raw = capsys.readouterr().out
    captured = json.loads(raw)
    assert exit_code == 0
    assert raw.count("\n") == 1
    assert set(captured) == {"verdict", "feedback"}
    assert captured["verdict"] == "error"
    assert "DO-NOT-ECHO" not in raw


def test_cli_delegate_judge_internal_error_does_not_leak_exception_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    secret_marker = "SUPER-SECRET-TOKEN-VALUE"

    def explode(_request: dict[str, object]) -> dict[str, str]:
        raise RuntimeError(f"credential {secret_marker} rejected")

    monkeypatch.setattr("hermes_gate.cli.delegate_judge", explode)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(request(tmp_path))))
    exit_code = main(["delegate-judge"])
    raw = capsys.readouterr().out
    captured = json.loads(raw)
    assert exit_code == 0
    assert raw.count("\n") == 1
    assert secret_marker not in raw
    assert "RuntimeError" not in raw
    assert captured == {
        "verdict": "error",
        "feedback": "internal error while judging the workspace",
    }


def test_cli_delegate_judge_does_not_require_cwd_to_be_a_repository(
    tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write_profile(repo)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(request(repo))))
    exit_code = main(["delegate-judge"])
    captured = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert captured["verdict"] == "pass"


def test_existing_commands_are_unaffected(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_profile(repo)
    original_cwd = Path.cwd()
    os.chdir(repo)
    try:
        exit_code = main(["fast"])
    finally:
        os.chdir(original_cwd)
    captured = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert captured["status"] == "PASS"
