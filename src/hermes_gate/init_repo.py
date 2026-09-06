from __future__ import annotations

import hashlib
import json
import shutil
import tomllib
from pathlib import Path
from typing import Any

from . import repo_runner
from .gitstate import ContentReadError, git_dir, repo_identity, snapshot


def initialize(root: Path, *, force: bool = False) -> dict[str, Any]:
    hermes = root / ".hermes"
    profile = hermes / "gate.toml"
    runner = hermes / "hermes_gate_runner.py"
    workflow = root / ".github" / "workflows" / "hermes-quality.yml"
    targets = [profile, runner, workflow]
    existing = [path for path in targets if path.exists()]
    if existing and not force:
        return {
            "status": "PARKED",
            "reason": "refusing to overwrite existing integration; rerun with --force after review",
            "existing": [str(path.relative_to(root)) for path in existing],
        }
    try:
        preflight_snapshot = snapshot(root)
    except ContentReadError as exc:
        return {"status": "PARKED", "reason": f"cannot read complete repository bytes: {exc}"}
    backup_root = git_dir(root) / "hermes-gate" / "install-backup"
    backup_root.mkdir(parents=True, exist_ok=True)
    backup_manifest: dict[str, str] = {}
    for path in existing:
        relative = path.relative_to(root)
        destination = backup_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
        backup_manifest[str(relative)] = hashlib.sha256(path.read_bytes()).hexdigest()

    hermes.mkdir(parents=True, exist_ok=True)
    workflow.parent.mkdir(parents=True, exist_ok=True)
    profile.write_text(_detected_profile(root), encoding="utf-8")
    source = Path(repo_runner.__file__).read_bytes()
    runner.write_bytes(source)
    workflow.write_text(_workflow(root), encoding="utf-8")
    runner_sha = hashlib.sha256(source).hexdigest()
    manifest = {
        "schema": "hermes-gate/install-v1",
        "repository": repo_identity(root),
        "files": {
            str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in targets
        },
        "runner_sha256": runner_sha,
        "runner_version": repo_runner.RUNNER_VERSION,
        "backups": backup_manifest,
        "rollback": "hermes-gate uninstall-repo",
    }
    manifest_path = _install_manifest_path(root)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    generated = snapshot(root, [str(path.relative_to(root)) for path in targets])
    baseline = {"repository": repo_identity(root), "dirty": {**preflight_snapshot, **generated}}
    state_path = git_dir(root) / "hermes-gate" / "baseline.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(baseline, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "status": "PASS",
        "profile": str(profile),
        "runner": str(runner),
        "runner_sha256": runner_sha,
        "workflow": str(workflow),
        "adapter_status": "NATIVE_DEFAULT",
        "reason": "repository-native commands remain active; primitive adapters are opt-in",
    }


def uninstall(root: Path) -> dict[str, Any]:
    manifest_path = _manifest_path_for_read(root)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"status": "NOT_CONFIGURED", "reason": "no install manifest"}
    backup_root = git_dir(root) / "hermes-gate" / "install-backup"
    files = manifest.get("files")
    if not isinstance(files, dict):
        return {"status": "NOT_CONFIGURED", "reason": "malformed install manifest"}

    # Never restore/remove an early target before proving that every generated
    # target is still the exact installed byte sequence.  A later local edit
    # must park the entire rollback, not leave a partially restored integration.
    planned: list[tuple[str, Path, Path]] = []
    for relative, installed_sha in files.items():
        if not isinstance(relative, str) or not isinstance(installed_sha, str):
            return {"status": "NOT_CONFIGURED", "reason": "malformed install manifest"}
        target = root / relative
        try:
            target.relative_to(root)
        except ValueError:
            return {"status": "NOT_CONFIGURED", "reason": "malformed install manifest"}
        if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() != installed_sha:
            return {
                "status": "PARKED",
                "reason": f"installed file changed: {relative}; preserve it manually",
            }
        planned.append((relative, target, backup_root / relative))

    restored: list[str] = []
    removed: list[str] = []
    for relative, target, backup in planned:
        if backup.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, target)
            restored.append(relative)
        elif target.exists():
            target.unlink()
            removed.append(relative)
    manifest_path.unlink(missing_ok=True)
    return {"status": "PASS", "restored": restored, "removed": removed}


def verify_runner(root: Path) -> tuple[bool, str]:
    manifest_path = _manifest_path_for_read(root)
    runner = root / ".hermes" / "hermes_gate_runner.py"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        actual = hashlib.sha256(runner.read_bytes()).hexdigest()
    except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        return False, str(exc)
    expected = str(manifest.get("runner_sha256", ""))
    return actual == expected, actual


def _detected_profile(root: Path) -> str:
    python = (root / "pyproject.toml").is_file() or (root / "requirements.txt").is_file()
    javascript = (root / "package.json").is_file()
    fast: list[tuple[str, list[str], float, list[str]]] = []
    full: list[tuple[str, list[str], float, list[str]]] = []
    repair: list[tuple[str, list[str], float, list[str]]] = []
    adapter = "NATIVE_DEFAULT"
    reviewer = "coderabbit"
    if python:
        fast.append(
            (
                "python-parse",
                [
                    "python3",
                    "-c",
                    "import ast,pathlib,sys; [ast.parse(pathlib.Path(p).read_bytes(), filename=p) for p in sys.argv[1:]]",
                    "{files}",
                ],
                6.0,
                ["**/*.py"],
            )
        )
        if (root / "src").is_dir():
            pytest_argv = [
                "python3",
                "-c",
                "import sys; sys.path.insert(0, 'src'); import pytest; raise SystemExit(pytest.main(['-q']))",
            ]
        else:
            pytest_argv = ["python3", "-m", "pytest", "-q"]
        full.append(("pytest", pytest_argv, 180.0, ["**/*"]))
        if shutil.which("ruff"):
            fast.insert(0, ("ruff", ["ruff", "check", "{files}"], 6.0, ["**/*.py"]))
            full.insert(0, ("ruff", ["ruff", "check", "."], 60.0, ["**/*"]))
            repair.append(("ruff-fix", ["ruff", "check", "--fix", "{files}"], 8.0, ["**/*.py"]))
    elif javascript:
        package = _package_scripts(root / "package.json")
        manager = "pnpm" if (root / "pnpm-lock.yaml").exists() else "npm"
        if "lint" in package:
            fast.append(
                (
                    "lint",
                    [manager, "run", "lint", "--", "{files}"],
                    8.0,
                    ["**/*.js", "**/*.jsx", "**/*.ts", "**/*.tsx"],
                )
            )
            full.append(("lint", [manager, "run", "lint"], 120.0, ["**/*"]))
        if "typecheck" in package:
            full.append(("typecheck", [manager, "run", "typecheck"], 120.0, ["**/*"]))
        if "test" in package:
            full.append(("test", [manager, "test", "--", "--runInBand"], 180.0, ["**/*"]))
        if "build" in package:
            full.append(("build", [manager, "run", "build"], 180.0, ["**/*"]))
    diff_argv = ["python3", ".hermes/hermes_gate_runner.py", "diff-check", "{files}"]
    if not full:
        full.append(("diff-check", diff_argv, 10.0, ["**/*"]))
    lines = [
        "version = 1",
        "",
        "[gate]",
        "fast_budget_seconds = 8.0",
        "full_required_local = false",
        f'adapter_status = "{adapter}"',
        'exclusions = [".git/**", ".hermes/hermes_gate_runner.py", ".pytest_cache/**", ".ruff_cache/**", "**/__pycache__/**", "vendor/**", "node_modules/**", "dist/**", "build/**"]',
        "",
        "[adapter]",
        "enabled = false",
        'name = "native"',
        "argv = []",
        'minimum_version = ""',
        "",
        "[lintlang]",
        f"enabled = {'true' if shutil.which('lintlang') else 'false'}",
        'argv = ["lintlang", "scan", "--format", "json", "--fail-on", "review", "{files}"]',
        'trigger_globs = ["**/AGENTS.md", "**/CLAUDE.md", "**/prompts/**", "**/*.prompt", ".codex/**", ".claude/**"]',
        "timeout_seconds = 4.0",
        'blocking_threshold = "MEDIUM"',
        "",
        "[review]",
        'provider = "coderabbit"',
        f"argv = {json.dumps([reviewer, 'review', '--agent'])}",
        "timeout_seconds = 180.0",
        'material_severities = ["critical", "major"]',
        'material_categories = ["correctness", "security", "data-loss", "concurrency", "api-contract"]',
        "fallback_argv = []",
    ]
    fast.append(("diff-check", diff_argv, 4.0, ["**/*"]))
    for table, commands in (("fast", fast), ("full", full), ("repair", repair)):
        for name, argv, timeout, globs in commands:
            lines.extend(
                [
                    "",
                    f"[[{table}]]",
                    f'name = "{name}"',
                    f"argv = {json.dumps(argv)}",
                    f"timeout_seconds = {timeout}",
                    f"globs = {json.dumps(globs)}",
                ]
            )
    return "\n".join(lines) + "\n"


def _workflow(root: Path) -> str:
    steps = [
        "      - uses: actions/checkout@v4",
        "      - uses: actions/setup-python@v5",
        "        with:",
        "          python-version: '3.12'",
    ]
    if (root / "pyproject.toml").is_file():
        install_target = ".[test]" if _has_test_extra(root / "pyproject.toml") else "."
        tools: list[str] = []
        if install_target == ".":
            if shutil.which("ruff"):
                tools.append("ruff")
            if shutil.which("pytest") or (root / "tests").is_dir():
                tools.append("pytest")
        tool_suffix = " " + " ".join(tools) if tools else ""
        steps.extend(
            [
                "      - name: Install project and declared gate tools",
                f"        run: python3 -m pip install -e '{install_target}'{tool_suffix}",
            ]
        )
    if (root / "package.json").is_file():
        install_command = "npm ci" if (root / "package-lock.json").is_file() else "npm install"
        steps.extend(
            [
                "      - uses: actions/setup-node@v4",
                "        with:",
                "          node-version: '22'",
                "      - name: Install JavaScript dependencies",
                f"        run: {install_command}",
            ]
        )
    steps.extend(
        [
            "      - name: Run declared full gate",
            "        run: python3 .hermes/hermes_gate_runner.py full",
        ]
    )
    return "\n".join(
        [
            "name: Hermes quality rail",
            "",
            "on:",
            "  pull_request:",
            "  workflow_dispatch:",
            "",
            "permissions:",
            "  contents: read",
            "",
            "jobs:",
            "  full:",
            "    runs-on: ubuntu-latest",
            "    steps:",
            *steps,
            "",
        ]
    )


def _package_scripts(path: Path) -> dict[str, str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    scripts = raw.get("scripts", {}) if isinstance(raw, dict) else {}
    return scripts if isinstance(scripts, dict) else {}


def _install_manifest_path(root: Path) -> Path:
    return git_dir(root) / "hermes-gate" / "install.json"


def _manifest_path_for_read(root: Path) -> Path:
    current = _install_manifest_path(root)
    legacy = root / ".hermes" / "install.json"
    return current if current.exists() else legacy


def _has_test_extra(path: Path) -> bool:
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return False
    project = raw.get("project", {}) if isinstance(raw, dict) else {}
    optional = project.get("optional-dependencies", {}) if isinstance(project, dict) else {}
    return isinstance(optional, dict) and isinstance(optional.get("test"), list)
