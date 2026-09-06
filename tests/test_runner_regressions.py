from __future__ import annotations

import json
import subprocess
import sys
import tracemalloc
from pathlib import Path

import pytest

from hermes_gate.init_repo import initialize
from hermes_gate.repo_runner import _execute


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
@pytest.mark.parametrize("size", [32768, 32769, 40001])
def test_runner_reports_each_stream_truncation(tmp_path: Path, stream: str, size: int) -> None:
    result = _execute(
        [sys.executable, "-c", f"import sys; sys.{stream}.write('x' * {size})"],
        tmp_path, 5, "output-probe",
    )
    assert result["status"] == "PASS"
    assert len(result[stream]) == min(size, 32768)
    assert result["output_truncated"] is (size > 32768)


@pytest.mark.parametrize("mode", ["fast", "full"])
@pytest.mark.parametrize("state", ["unstaged", "staged", "untracked", "staged_then_cleaned"])
@pytest.mark.parametrize("bad", [False, True])
def test_generated_whitespace_check_covers_git_states(
    tmp_path: Path, mode: str, state: str, bad: bool
) -> None:
    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.com")
    path = tmp_path / "notes with spaces.txt"
    path.write_text("baseline\n")
    git("add", ".")
    git("commit", "-qm", "baseline")
    assert initialize(tmp_path)["status"] == "PASS"
    if state == "untracked":
        path = tmp_path / "new notes.txt"
    path.write_text("changed" + (" " if bad else "") + "\n")
    if state in {"staged", "staged_then_cleaned"}:
        git("add", "--", path.name)
    if state == "staged_then_cleaned":
        path.write_text("clean working copy\n")
    proc = subprocess.run(
        [sys.executable, str(tmp_path / ".hermes/hermes_gate_runner.py"), mode],
        cwd=tmp_path, capture_output=True, text=True,
    )
    result = json.loads(proc.stdout)
    assert result["status"] == ("FAIL" if bad else "PASS"), result
    if bad:
        assert "trailing whitespace" in result["checks"][-1]["stdout"]


def test_unsupported_runner_runtime_has_actionable_error(tmp_path: Path) -> None:
    runner = Path(__file__).parents[1] / "src/hermes_gate/repo_runner.py"
    proc = subprocess.run(
        [sys.executable, "-c", "import runpy,sys; sys.version_info=(3,10,0); runpy.run_path(sys.argv[1])", str(runner)],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert proc.returncode != 0
    assert "Python 3.11 or newer" in proc.stderr
    assert "Traceback" not in proc.stderr
def test_runner_capture_memory_is_bounded_for_large_streams(tmp_path: Path) -> None:
    tracemalloc.start()
    try:
        result = _execute(
            [sys.executable, "-c", "import sys; sys.stdout.write('x' * 8_000_000); sys.stderr.write('y' * 8_000_000)"],
            tmp_path, 5, "bounded-output",
        )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result["status"] == "PASS"
    assert result["output_truncated"] is True
    assert len(result["stdout"]) == len(result["stderr"]) == 32768
    assert peak < 4_000_000, f"Capture retained memory proportional to emitted output: {peak} bytes"
