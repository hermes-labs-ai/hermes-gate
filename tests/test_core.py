from __future__ import annotations

import json
import runpy
import subprocess
import sys
import time
from pathlib import Path

import pytest

from hermes_gate.config import ConfigError, load_config
from hermes_gate.engine import _provider_review_argv, _tool_version, boundary, fast, repair, review
from hermes_gate.execution import run_argv
from hermes_gate.gitstate import diff_digest, session_changed_paths, snapshot
from hermes_gate.receipts import valid_receipt
from hermes_gate.repo_runner import _execute


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
    root: Path, command: list[str] | None = None, *, repair_command: list[str] | None = None
) -> None:
    command = command or [sys.executable, "-c", "raise SystemExit(0)"]
    text = f"""version = 1
[gate]
fast_budget_seconds = 8.0
full_required_local = false
exclusions = [".git/**"]
[lintlang]
enabled = false
argv = ["lintlang", "scan", "{{files}}"]
trigger_globs = []
timeout_seconds = 1.0
blocking_threshold = "MEDIUM"
[review]
provider = "coderabbit"
argv = ["coderabbit", "review", "--agent"]
timeout_seconds = 1.0
material_severities = ["critical", "major"]
material_categories = ["correctness"]
fallback_argv = []
[[fast]]
name = "test"
argv = {json.dumps(command)}
timeout_seconds = 2.0
globs = ["**/*"]
[[full]]
name = "test"
argv = {json.dumps(command)}
timeout_seconds = 2.0
globs = ["**/*"]
"""
    if repair_command:
        text += f"""
[[repair]]
name = "repair"
argv = {json.dumps(repair_command)}
timeout_seconds = 2.0
globs = ["**/*"]
"""
    (root / ".hermes" / "gate.toml").write_text(text, encoding="utf-8")


def test_config_rejects_shell_string_argv(repo: Path) -> None:
    (repo / ".hermes" / "gate.toml").write_text("[[fast]]\nargv = 'pytest -q'\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="array of strings"):
        load_config(repo)


def test_execution_timeout_kills_process_group_and_caps_output(tmp_path: Path) -> None:
    marker = tmp_path / "descendant-survived"
    child_code = (
        "import signal, time; from pathlib import Path; "
        "signal.signal(signal.SIGTERM, lambda *_: None); "
        f"time.sleep(0.8); Path({str(marker)!r}).write_text('bad')"
    )
    parent_code = (
        f"import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
        "time.sleep(10)"
    )
    timeout = run_argv(
        [sys.executable, "-c", parent_code], cwd=tmp_path, timeout_seconds=0.05
    )
    assert timeout.timed_out
    assert timeout.elapsed_ms < 1500
    time.sleep(1)
    assert not marker.exists()
    capped = run_argv(
        [sys.executable, "-c", "print('x'*10000)"], cwd=tmp_path, timeout_seconds=1, output_cap=100
    )
    assert capped.output_truncated
    assert len(capped.stdout.encode()) <= 100


def test_execution_tolerates_process_group_exiting_before_term(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class ExitedProcess:
        pid = 43210
        returncode = 0

        def __init__(self) -> None:
            self.calls = 0

        def communicate(self, timeout: float | None = None) -> tuple[bytes, bytes]:
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired(["probe"], timeout or 0)
            return b"", b""

        def poll(self) -> int:
            return 0

    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: ExitedProcess())
    monkeypatch.setattr(
        "hermes_gate.execution.os.killpg",
        lambda *args: (_ for _ in ()).throw(ProcessLookupError()),
    )

    result = run_argv(["probe"], cwd=tmp_path, timeout_seconds=0.01)

    assert result.timed_out
    assert result.returncode is None


def test_copied_runner_timeout_kills_process_group(tmp_path: Path) -> None:
    marker = tmp_path / "runner-descendant-survived"
    child_code = (
        "import signal, time; from pathlib import Path; "
        "signal.signal(signal.SIGTERM, lambda *_: None); "
        f"time.sleep(0.8); Path({str(marker)!r}).write_text('bad')"
    )
    parent_code = (
        f"import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
        "time.sleep(10)"
    )

    result = _execute([sys.executable, "-c", parent_code], tmp_path, 0.05, "probe")

    assert result["reason"] == "timeout"
    time.sleep(1)
    assert not marker.exists()


def test_tracked_runner_timeout_kills_process_group(tmp_path: Path) -> None:
    tracked_runner = Path(__file__).parents[1] / ".hermes" / "hermes_gate_runner.py"
    namespace = runpy.run_path(str(tracked_runner))
    assert tracked_runner.read_bytes() == (Path(__file__).parents[1] / "src/hermes_gate/repo_runner.py").read_bytes()

    marker = tmp_path / "tracked-runner-descendant-survived"
    child_code = (
        "import signal, time; from pathlib import Path; "
        "signal.signal(signal.SIGTERM, lambda *_: None); "
        f"time.sleep(0.8); Path({str(marker)!r}).write_text('bad')"
    )
    parent_code = (
        f"import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
        "time.sleep(10)"
    )

    result = namespace["_execute"](
        [sys.executable, "-c", parent_code], tmp_path, 0.05, "tracked-probe"
    )

    assert result["reason"] == "timeout"
    time.sleep(1)
    assert not marker.exists()


def test_copied_runner_tolerates_process_group_exiting_before_term(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class ExitedProcess:
        pid = 43210
        returncode = 0

        def __init__(self) -> None:
            self.calls = 0

        def communicate(self, timeout: float | None = None) -> tuple[bytes, bytes]:
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired(["probe"], timeout or 0)
            return b"", b""

        def poll(self) -> int:
            return 0

    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: ExitedProcess())
    monkeypatch.setattr(
        "hermes_gate.repo_runner.os.killpg",
        lambda *args: (_ for _ in ()).throw(ProcessLookupError()),
    )

    result = _execute(["probe"], tmp_path, 0.01, "probe")

    assert result == {"name": "probe", "argv": ["probe"], "status": "FAIL", "reason": "timeout"}


def test_preexisting_dirty_bytes_are_not_session_changes_until_edited(repo: Path) -> None:
    path = repo / "source.py"
    path.write_text("before = 1\n", encoding="utf-8")
    baseline = snapshot(repo)
    assert session_changed_paths(repo, baseline) == []
    path.write_text("after = 2\n", encoding="utf-8")
    assert session_changed_paths(repo, baseline) == ["source.py"]


def test_fast_receipt_is_cached_and_invalidated_after_edit(repo: Path) -> None:
    write_profile(repo)
    source = repo / "source.py"
    source.write_text("ok = True\n", encoding="utf-8")
    first = fast(repo)
    second = fast(repo)
    assert first["status"] == "PASS"
    assert second["status"] == "PASS" and second["cached"] is True
    assert first["receipt"]["command_versions"]
    original_digest = first["receipt"]["diff_sha256"]
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "checkpoint")
    assert valid_receipt(repo, "fast", diff_digest(repo)) is None
    after_commit = fast(repo)
    assert after_commit["receipt"]["repository"]["head"] != first["receipt"]["repository"]["head"]
    source.write_text("ok = False\n", encoding="utf-8")
    assert valid_receipt(repo, "fast", diff_digest(repo)) is None
    third = fast(repo)
    assert third["receipt"]["diff_sha256"] != original_digest


def test_snapshot_binds_symlink_identity_and_target_bytes(repo: Path) -> None:
    target = repo / "target.py"
    target.write_text("first = True\n", encoding="utf-8")
    link = repo / "link.py"
    link.symlink_to("target.py")
    first = snapshot(repo, ["link.py"])

    target.write_text("second = True\n", encoding="utf-8")
    second = snapshot(repo, ["link.py"])

    assert first["link.py"] != second["link.py"]


def test_snapshot_keeps_symlink_identity_when_target_is_unreadable(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = repo / "target.py"
    target.write_text("protected = True\n", encoding="utf-8")
    link = repo / "link.py"
    link.symlink_to("target.py")
    original = Path.read_bytes

    def unreadable(path: Path) -> bytes:
        if path == link:
            raise PermissionError("fixture")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", unreadable)

    assert snapshot(repo, ["link.py"])["link.py"] == "SYMLINK:target.py:UNREADABLE:PermissionError"


def test_failed_check_maps_to_fail_and_boundary_rejects_stale_receipt(repo: Path) -> None:
    write_profile(repo, [sys.executable, "-c", "raise SystemExit(7)"])
    (repo / "source.py").write_text("bad = True\n", encoding="utf-8")
    assert fast(repo)["status"] == "FAIL"
    git(repo, "add", "source.py")
    outcome = boundary(repo, "commit")
    assert outcome["status"] == "FAIL"
    assert outcome["missing"] == ["fast"]


def test_commit_boundary_rejects_receipt_from_previous_head(repo: Path) -> None:
    write_profile(repo)
    git(repo, "add", ".hermes/gate.toml")
    git(repo, "commit", "-qm", "profile")
    source = repo / "source.py"
    source.write_text("value = 1\n", encoding="utf-8")
    assert fast(repo)["status"] == "PASS"
    notes = repo / "notes.md"
    notes.write_text("checkpoint\n", encoding="utf-8")
    git(repo, "add", "notes.md")
    git(repo, "commit", "-qm", "checkpoint")
    git(repo, "add", "source.py")

    outcome = boundary(repo, "commit")

    assert outcome["status"] == "FAIL"
    assert outcome["missing"] == ["fast"]


def test_repair_allows_one_attempt_for_unchanged_digest(repo: Path) -> None:
    write_profile(repo, repair_command=[sys.executable, "-c", "raise SystemExit(0)"])
    (repo / "source.py").write_text("x = 1\n", encoding="utf-8")
    assert repair(repo)["status"] == "PASS"
    assert repair(repo)["status"] == "PARKED"


def test_fast_global_budget_is_bounded(repo: Path) -> None:
    write_profile(repo, [sys.executable, "-c", "import time; time.sleep(10)"])
    profile = (
        (repo / ".hermes" / "gate.toml")
        .read_text(encoding="utf-8")
        .replace("fast_budget_seconds = 8.0", "fast_budget_seconds = 0.05")
    )
    (repo / ".hermes" / "gate.toml").write_text(profile, encoding="utf-8")
    (repo / "source.py").write_text("x = 1\n", encoding="utf-8")
    started = time.monotonic()
    assert fast(repo)["status"] == "FAIL"
    assert time.monotonic() - started < 1.5


def test_fast_command_version_probe_shares_global_budget(repo: Path) -> None:
    tool = repo / "slow-version-tool"
    tool.write_text(
        f"#!{sys.executable}\nimport sys,time\nif '--version' in sys.argv:\n    time.sleep(2)\n",
        encoding="utf-8",
    )
    tool.chmod(0o755)
    write_profile(repo, [str(tool)])
    profile_path = repo / ".hermes" / "gate.toml"
    profile_path.write_text(
        profile_path.read_text(encoding="utf-8").replace(
            "fast_budget_seconds = 8.0", "fast_budget_seconds = 0.5"
        ),
        encoding="utf-8",
    )
    (repo / "source.py").write_text("x = 1\n", encoding="utf-8")
    started = time.monotonic()
    outcome = fast(repo)
    assert outcome["status"] == "PASS"
    assert time.monotonic() - started < 1


def test_relative_check_command_has_accurate_version_receipt(repo: Path) -> None:
    tool = repo / "version-tool"
    tool.write_text(
        f"#!{sys.executable}\nimport sys\n"
        "print('version-tool 9.8.7' if '--version' in sys.argv else 'ok')\n",
        encoding="utf-8",
    )
    tool.chmod(0o755)
    write_profile(repo, ["./version-tool"])
    (repo / "source.py").write_text("x = 1\n", encoding="utf-8")

    outcome = fast(repo)

    assert outcome["status"] == "PASS"
    assert outcome["receipt"]["command_versions"]["./version-tool"] == "version-tool 9.8.7"


def test_relative_path_entry_version_probe_runs_discovered_executable(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    tool = bin_dir / "version-tool"
    tool.write_text(
        f"#!{sys.executable}\nprint('discovered 4.5.6')\n",
        encoding="utf-8",
    )
    tool.chmod(0o755)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", "bin")

    assert _tool_version("version-tool", repo) == "discovered 4.5.6"


def test_conditional_lintlang_runs_once_for_ai_files_and_zero_for_source(repo: Path) -> None:
    counter = repo / "lintlang-count.txt"
    lint_command = [
        sys.executable,
        "-c",
        f"from pathlib import Path; p=Path({str(counter)!r}); p.write_text(str(int(p.read_text())+1) if p.exists() else '1')",
    ]
    write_profile(repo)
    profile_path = repo / ".hermes" / "gate.toml"
    profile = profile_path.read_text(encoding="utf-8")
    profile = profile.replace("enabled = false", "enabled = true", 1)
    profile = profile.replace(
        'argv = ["lintlang", "scan", "{files}"]', f"argv = {json.dumps(lint_command)}"
    )
    profile = profile.replace("trigger_globs = []", 'trigger_globs = ["**/AGENTS.md"]')
    profile_path.write_text(profile, encoding="utf-8")
    (repo / "source.py").write_text("x = 1\n", encoding="utf-8")
    assert fast(repo, files=["source.py"])["status"] == "PASS"
    assert not counter.exists()
    (repo / "AGENTS.md").write_text("agent guidance\n", encoding="utf-8")
    assert fast(repo, files=["AGENTS.md"])["status"] == "PASS"
    assert fast(repo, files=["AGENTS.md"])["status"] == "PASS"
    assert counter.read_text(encoding="utf-8") == "1"


def test_review_nonzero_exit_never_creates_pass_receipt(repo: Path) -> None:
    write_profile(repo)
    profile_path = repo / ".hermes" / "gate.toml"
    provider_argv = [
        sys.executable,
        "-c",
        'print(\'{"type":"complete"}\'); raise SystemExit(7)',
    ]
    profile = profile_path.read_text(encoding="utf-8").replace(
        'argv = ["coderabbit", "review", "--agent"]',
        f"argv = {json.dumps(provider_argv)}",
    )
    profile_path.write_text(profile, encoding="utf-8")
    (repo / "source.py").write_text("ok = True\n", encoding="utf-8")

    assert fast(repo)["status"] == "PASS"
    assert review(repo)["status"] == "REVIEW_UNAVAILABLE"


def test_coderabbit_argv_binds_dirty_and_committed_boundaries(repo: Path) -> None:
    write_profile(repo)
    source = repo / "source.py"
    source.write_text("value = 1\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    source.write_text("value = 2\n", encoding="utf-8")
    config = load_config(repo)
    branch = subprocess.run(
        ["git", "-C", str(repo), "branch", "--show-current"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    dirty = _provider_review_argv(config, repo)
    assert dirty[-5:] == (
        "--include-untracked",
        "--base",
        branch,
        "--base-commit",
        "HEAD",
    )

    git(repo, "add", "source.py")
    git(repo, "commit", "-qm", "change")
    committed = _provider_review_argv(config, repo)
    assert committed[-2] == "--base-commit"
    assert (
        committed[-1]
        == subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD^"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    assert "--committed" in committed
    assert committed[committed.index("--base") + 1] == branch


def test_coderabbit_argv_preserves_inline_base_configuration(repo: Path) -> None:
    write_profile(repo)
    profile_path = repo / ".hermes" / "gate.toml"
    profile_path.write_text(
        profile_path.read_text(encoding="utf-8").replace(
            'argv = ["coderabbit", "review", "--agent"]',
            'argv = ["coderabbit", "review", "--agent", "--base=main"]',
        ),
        encoding="utf-8",
    )

    argv = _provider_review_argv(load_config(repo), repo)

    assert argv == ("coderabbit", "review", "--agent", "--base=main")
