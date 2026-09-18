"""The claude-plugin artifact must run without the Python package being pip-installed."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "claude-plugin"

# github/awesome-copilot's external-plugin-quality-gates.mjs, kept in sync by hand:
# https://agent-plugins.org/schemas/1.0.0/plugin.schema.json
AGENT_PLUGIN_SCHEMA_URL = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
AGENT_PLUGIN_ALLOWED_TOP_LEVEL_FIELDS = {
    "$schema",
    "name",
    "version",
    "description",
    "author",
    "homepage",
    "repository",
    "license",
    "keywords",
    "extensions",
}
AGENT_PLUGIN_ALLOWED_AUTHOR_FIELDS = {"name", "email", "url"}
AGENT_PLUGIN_NAME_PATTERN = re.compile(r"^(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")


def test_plugin_manifest_and_hooks_files_exist():
    manifest = json.loads((PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text())
    assert manifest["name"] == "hermes-gate"
    assert (PLUGIN_ROOT / "hooks" / "hooks.json").is_file()
    hooks = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text())
    assert set(hooks["hooks"]) == {"SessionStart", "Stop", "PreToolUse"}


def test_root_agent_plugin_manifest_exists_at_the_plugin_root():
    """Agent Plugins v1.0.0 (used by the awesome-copilot intake) expects plugin.json
    directly at the plugin root, not nested under .claude-plugin/ or .github/plugin/.
    """
    assert (PLUGIN_ROOT / "plugin.json").is_file()


def test_root_agent_plugin_manifest_matches_the_agent_plugins_v1_schema():
    manifest = json.loads((PLUGIN_ROOT / "plugin.json").read_text())

    assert manifest["$schema"] == AGENT_PLUGIN_SCHEMA_URL

    assert isinstance(manifest["name"], str)
    assert 1 <= len(manifest["name"]) <= 64
    assert AGENT_PLUGIN_NAME_PATTERN.match(manifest["name"])

    for field in ("version", "description"):
        assert isinstance(manifest[field], str) and manifest[field].strip()

    for field in ("homepage", "repository", "license"):
        assert field not in manifest or isinstance(manifest[field], str)

    assert isinstance(manifest.get("author"), dict)
    assert set(manifest["author"]) <= AGENT_PLUGIN_ALLOWED_AUTHOR_FIELDS
    assert all(isinstance(value, str) for value in manifest["author"].values())

    assert isinstance(manifest.get("keywords", []), list)
    assert all(isinstance(keyword, str) for keyword in manifest.get("keywords", []))

    assert set(manifest) <= AGENT_PLUGIN_ALLOWED_TOP_LEVEL_FIELDS


def test_root_agent_plugin_manifest_name_and_version_match_the_release():
    manifest = json.loads((PLUGIN_ROOT / "plugin.json").read_text())
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())

    assert manifest["name"] == "hermes-gate"
    assert manifest["version"] == pyproject["project"]["version"]


def test_claude_manifest_version_matches_the_release():
    claude_manifest = json.loads((PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text())
    root_manifest = json.loads((PLUGIN_ROOT / "plugin.json").read_text())
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())

    assert claude_manifest["version"] == pyproject["project"]["version"]
    assert claude_manifest["version"] == root_manifest["version"]


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
