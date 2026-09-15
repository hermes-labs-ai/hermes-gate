"""The claude-plugin artifact must run without the Python package being pip-installed."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "claude-plugin"


def test_plugin_manifest_and_hooks_files_exist():
    manifest = json.loads((PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text())
    assert manifest["name"] == "hermes-gate"
    assert (PLUGIN_ROOT / "hooks" / "hooks.json").is_file()
    hooks = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text())
    assert set(hooks["hooks"]) == {"SessionStart", "Stop", "PreToolUse"}


def test_plugin_runtime_is_synced_with_the_source_package():
    source = ROOT / "src" / "hermes_gate"
    bundled = PLUGIN_ROOT / "src" / "hermes_gate"

    def runtime_files(directory):
        return sorted(
            p.relative_to(directory)
            for p in directory.rglob("*")
            if p.is_file() and p.suffix in {".py", ".json"} and "__pycache__" not in p.parts
        )

    source_files = runtime_files(source)
    bundled_files = runtime_files(bundled)
    assert bundled_files == source_files
    for relative in source_files:
        assert (bundled / relative).read_bytes() == (source / relative).read_bytes()


def _run_hook(event: str, payload: dict, cwd: Path) -> dict:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, "-S", str(PLUGIN_ROOT / "scripts" / "hermes_gate_hook.py"), event],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        cwd=cwd,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_plugin_session_start_hook_runs_without_a_pip_install(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    output = _run_hook(
        "session-start",
        {"cwd": str(tmp_path), "session_id": "plugin-artifact-test"},
        tmp_path,
    )
    assert output["continue"] is True
    assert output["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "Hermes Gate completion rail is active" in (
        output["hookSpecificOutput"]["additionalContext"]
    )


def test_plugin_pre_tool_use_hook_ignores_non_bash_tools_without_a_pip_install(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    output = _run_hook(
        "pre-tool-use",
        {"cwd": str(tmp_path), "tool_name": "Read"},
        tmp_path,
    )
    assert output == {}


def test_plugin_hook_fails_open_on_an_unknown_event_without_a_pip_install(tmp_path):
    output = _run_hook("bogus-event", {}, tmp_path)
    assert output["continue"] is True
