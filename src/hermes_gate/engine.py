from __future__ import annotations

import fnmatch
import json
import os
import shutil
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .adapters import run_adapter
from .config import ConfigError, GateConfig, load_config
from .classification import is_code_path
from .execution import Execution, run_argv
from .gitstate import changed_paths, diff_digest, git, git_dir, scope_paths, staged_paths
from .providers import normalize_coderabbit_output
from .receipts import read_receipt, valid_receipt, write_receipt
from .repo_runner import run as run_repository
from .status import Status


def fast(root: Path, *, files: list[str] | None = None) -> dict[str, Any]:
    started = time.monotonic()
    try:
        config = load_config(root)
    except FileNotFoundError:
        return result("fast", Status.NOT_CONFIGURED, started, reason="run hermes-gate init")
    except ConfigError as exc:
        return result("fast", Status.ERROR, started, reason=f"invalid profile: {exc}")
    selected = [
        path
        for path in (files if files is not None else scope_paths(root))
        if config.included(path)
    ]
    digest = diff_digest(root, selected)
    cached = valid_receipt(root, "fast", digest)
    if cached:
        return result("fast", Status.PASS, started, receipt=cached, cached=True)
    runner_result = _run_gate(config, "fast", root, selected)
    checks = list(runner_result.get("checks", []))
    status = Status(str(runner_result["status"]))
    reason = str(runner_result.get("reason", ""))
    elapsed = time.monotonic() - started
    lintlang_paths = [path for path in selected if os.path.lexists(root / path)]
    if status in {Status.PASS, Status.NOT_APPLICABLE} and _lintlang_applies(config, lintlang_paths):
        remaining = config.fast_budget_seconds - elapsed
        if remaining <= 0:
            status, reason = Status.FAIL, "fast budget exhausted before LintLang"
        else:
            check = _run_spec(
                config.lintlang.argv,
                lintlang_paths,
                root,
                min(config.lintlang.timeout_seconds, remaining),
                "lintlang",
            )
            checks.append(check)
            if check["status"] != Status.PASS:
                status, reason = Status.FAIL, str(check.get("reason", "LintLang failed"))
    receipt = write_receipt(
        root,
        "fast",
        status=status.value,
        digest=digest,
        elapsed_ms=_elapsed(started),
        command_versions=_command_versions(
            runner_result,
            checks,
            root,
            deadline=started + config.fast_budget_seconds,
        ),
        checks=checks,
        extra={
            "checked_paths": selected,
            "reason": reason,
            "runner_version": runner_result.get("runner_version"),
        },
    )
    return result("fast", status, started, reason=reason, receipt=receipt)


def full(root: Path) -> dict[str, Any]:
    started = time.monotonic()
    try:
        config = load_config(root)
    except FileNotFoundError:
        return result("full", Status.NOT_CONFIGURED, started, reason="run hermes-gate init")
    except ConfigError as exc:
        return result("full", Status.ERROR, started, reason=f"invalid profile: {exc}")
    selected = [path for path in scope_paths(root) if config.included(path)]
    digest = diff_digest(root, selected)
    runner_result = _run_gate(config, "full", root, selected)
    status = Status(str(runner_result["status"]))
    receipt = write_receipt(
        root,
        "full",
        status=status.value,
        digest=digest,
        elapsed_ms=_elapsed(started),
        command_versions=_command_versions(
            runner_result, list(runner_result.get("checks", [])), root
        ),
        checks=list(runner_result.get("checks", [])),
        extra={"checked_paths": selected, "reason": runner_result.get("reason", "")},
    )
    if status is Status.PASS:
        _state_file(root, "review-budget.json").unlink(missing_ok=True)
    return result(
        "full", status, started, reason=str(runner_result.get("reason", "")), receipt=receipt
    )


def repair(root: Path) -> dict[str, Any]:
    started = time.monotonic()
    try:
        config = load_config(root)
    except FileNotFoundError:
        return result("repair", Status.NOT_CONFIGURED, started, reason="run hermes-gate init")
    except ConfigError as exc:
        return result("repair", Status.ERROR, started, reason=f"invalid profile: {exc}")
    selected = [path for path in scope_paths(root) if config.included(path)]
    if not selected:
        return result("repair", Status.NOT_APPLICABLE, started, reason="no changed files")
    if not config.repair:
        return result("repair", Status.NOT_CONFIGURED, started, reason="no [[repair]] command")
    digest = diff_digest(root, selected)
    state_path = _state_file(root, "repair-budget.json")
    state = _read_json(state_path)
    if state.get("digest") == digest and int(state.get("attempts", 0)) >= 1:
        return result(
            "repair",
            Status.PARKED,
            started,
            reason="one deterministic repair already attempted for this diff",
        )
    _write_json(state_path, {"digest": digest, "attempts": 1})
    checks: list[dict[str, Any]] = []
    status, reason = Status.PASS, ""
    for spec in config.repair:
        if not spec.applies(selected):
            continue
        check = _run_spec(spec.argv, selected, root, spec.timeout_seconds, spec.name)
        checks.append(check)
        if check["status"] != Status.PASS:
            status, reason = Status.FAIL, str(check.get("reason", "repair failed"))
            break
    return result("repair", status, started, reason=reason, checks=checks)


def review(root: Path) -> dict[str, Any]:
    started = time.monotonic()
    try:
        config = load_config(root)
    except FileNotFoundError:
        return result("review", Status.NOT_CONFIGURED, started, reason="run hermes-gate init")
    except ConfigError as exc:
        return result("review", Status.ERROR, started, reason=f"invalid profile: {exc}")
    selected = [path for path in scope_paths(root) if config.included(path)]
    digest = diff_digest(root, selected)
    if not valid_receipt(root, "fast", digest):
        return result(
            "review",
            Status.PARKED,
            started,
            reason="matching fast PASS required; run hermes-gate fast",
        )
    cached = valid_receipt(root, "review", digest)
    if cached:
        return result("review", Status.PASS, started, receipt=cached, cached=True)
    budget_path = _state_file(root, "review-budget.json")
    budget = _read_json(budget_path)
    digests = list(budget.get("digests", []))
    if digest not in digests and len(digests) >= 2:
        return result(
            "review",
            Status.PARKED,
            started,
            reason="one initial review and one re-review exhausted; run hermes-gate full",
        )
    if digest not in digests:
        digests.append(digest)
        _write_json(budget_path, {"digests": digests})

    provider_version = _tool_version(config.review.argv[0], root)
    provider_argv = _provider_review_argv(config, root)
    execution = run_argv(provider_argv, cwd=root, timeout_seconds=config.review.timeout_seconds)
    if execution.returncode not in {0, None}:
        normalized = normalize_coderabbit_output(
            {"type": "error", "message": f"provider exited {execution.returncode}"},
            material_severities=config.review.material_severities,
            material_categories=config.review.material_categories,
        )
    else:
        normalized = normalize_coderabbit_output(
            execution.stdout,
            material_severities=config.review.material_severities,
            material_categories=config.review.material_categories,
        )
    provider = config.review.provider
    if (
        execution.unavailable
        or execution.timed_out
        or normalized.status is Status.REVIEW_UNAVAILABLE
    ):
        fallback = _fallback_review(config, root, digest)
        if fallback is not None:
            execution, normalized, provider, provider_version = fallback
    status = normalized.status
    findings = [asdict(item) for item in normalized.findings]
    reason = normalized.reason
    receipt = write_receipt(
        root,
        "review",
        status=status.value,
        digest=digest,
        elapsed_ms=_elapsed(started),
        command_versions={provider: provider_version},
        findings=findings,
        checks=[_execution_dict(execution, provider)],
        extra={
            "provider": provider,
            "suppressed_count": normalized.suppressed_count,
            "reviewed_paths": selected,
            "reason": reason,
        },
    )
    return result("review", status, started, reason=reason, receipt=receipt, findings=findings)


def boundary(root: Path, action: str) -> dict[str, Any]:
    started = time.monotonic()
    raw_selected = staged_paths(root) if action == "commit" else scope_paths(root)
    if not any(is_code_path(path) for path in raw_selected):
        return result(
            "boundary", Status.PASS, started, reason="non-code boundary is exempt", required=[]
        )
    try:
        config = load_config(root)
    except FileNotFoundError:
        return result("boundary", Status.NOT_CONFIGURED, started, reason="run hermes-gate init")
    except ConfigError as exc:
        return result("boundary", Status.ERROR, started, reason=f"invalid profile: {exc}")
    selected = [path for path in raw_selected if config.included(path)]
    digest = diff_digest(root, selected)
    requirements = ["fast"]
    if action in {"push", "pr-create", "pr-ready"}:
        requirements.append("review")
        if config.full_required_local:
            requirements.append("full")
    missing: list[str] = []
    for kind in requirements:
        if valid_receipt(root, kind, digest):
            continue
        if action == "commit" and _receipt_covers(root, kind, raw_selected):
            continue
        missing.append(kind)
    if missing:
        commands = " && ".join(f"hermes-gate {kind}" for kind in missing)
        return result(
            "boundary",
            Status.FAIL,
            started,
            reason=f"{action} requires matching {' + '.join(missing)} PASS receipt(s); run {commands}",
            required=requirements,
            missing=missing,
        )
    return result("boundary", Status.PASS, started, required=requirements)


def _receipt_covers(root: Path, kind: str, selected: list[str]) -> bool:
    receipt = read_receipt(root, kind)
    checked = receipt.get("checked_paths", []) if receipt else []
    if not receipt or not set(selected).issubset(set(checked)):
        return False
    return valid_receipt(root, kind, diff_digest(root, checked)) is not None


def _lintlang_applies(config: GateConfig, files: list[str]) -> bool:
    return config.lintlang.enabled and any(
        any(
            fnmatch.fnmatch(path, pattern)
            or (pattern.startswith("**/") and fnmatch.fnmatch(path, pattern[3:]))
            for pattern in config.lintlang.trigger_globs
        )
        for path in files
    )


def _run_gate(config: GateConfig, mode: str, root: Path, selected: list[str]) -> dict[str, Any]:
    if config.adapter.enabled:
        timeout = (
            config.fast_budget_seconds
            if mode == "fast"
            else sum(spec.timeout_seconds for spec in config.full) or 600.0
        )
        return run_adapter(
            config.adapter,
            root=root,
            mode=mode,
            checked_paths=selected,
            timeout_seconds=timeout,
        )
    return run_repository(mode, root=root, files=selected)


def _command_versions(
    runner_result: dict[str, Any],
    checks: list[dict[str, Any]],
    root: Path,
    *,
    deadline: float | None = None,
) -> dict[str, str]:
    versions = {
        str(name): value if isinstance(value, str) else json.dumps(value, sort_keys=True)
        for name, value in dict(runner_result.get("command_versions", {})).items()
    }
    for check in checks:
        argv = check.get("argv", [])
        if isinstance(argv, list) and argv and isinstance(argv[0], str):
            name = argv[0]
            if name in versions:
                continue
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                versions[name] = "BUDGET_EXHAUSTED"
            else:
                versions[name] = _tool_version(
                    name,
                    root,
                    timeout_seconds=min(3, remaining) if remaining is not None else 3,
                )
    return versions


def _run_spec(
    argv: tuple[str, ...], files: list[str], root: Path, timeout: float, name: str
) -> dict[str, Any]:
    expanded: list[str] = []
    for part in argv:
        expanded.extend(files if part == "{files}" else [part])
    return _execution_dict(run_argv(expanded, cwd=root, timeout_seconds=timeout), name)


def _execution_dict(execution: Execution, name: str) -> dict[str, Any]:
    if execution.unavailable:
        status, reason = Status.FAIL, "executable unavailable"
    elif execution.timed_out:
        status, reason = Status.FAIL, "timeout"
    elif execution.returncode == 0:
        status, reason = Status.PASS, ""
    else:
        status, reason = Status.FAIL, f"exit {execution.returncode}"
    return {
        "name": name,
        "argv": list(execution.argv),
        "status": status.value,
        "returncode": execution.returncode,
        "elapsed_ms": execution.elapsed_ms,
        "stdout": execution.stdout,
        "stderr": execution.stderr,
        "timed_out": execution.timed_out,
        "unavailable": execution.unavailable,
        "output_truncated": execution.output_truncated,
        "reason": reason,
    }


def _fallback_review(config: GateConfig, root: Path, digest: str):
    argv = config.review.fallback_argv
    output_dir: Path | None = None
    if not argv and shutil.which("hermes-pr-review") and not changed_paths(root):
        parent = git(root, "rev-parse", "HEAD^", check=False)
        if parent.returncode:
            return None
        output_dir = git_dir(root) / "hermes-gate" / "providers" / "hermes-pr-review" / digest
        output_dir.mkdir(parents=True, exist_ok=True)
        argv = (
            "hermes-pr-review",
            "--repo",
            str(root),
            "--base",
            parent.stdout.decode().strip(),
            "--output-dir",
            str(output_dir),
            "--run-codex",
            "--model",
            "gpt-5.6-terra",
            "--reasoning-effort",
            "high",
            "--deadline-seconds",
            str(min(config.review.timeout_seconds, 180.0)),
        )
    if not argv:
        return None
    execution = run_argv(argv, cwd=root, timeout_seconds=config.review.timeout_seconds + 5)
    if execution.unavailable or execution.timed_out:
        return None
    if output_dir is not None:
        try:
            raw = json.loads((output_dir / "review.json").read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None
        if raw.get("verdict") == "UNEVALUATED":
            return None
        events: list[dict[str, Any]] = []
        for finding in raw.get("findings", []):
            events.append(
                {
                    "type": "finding",
                    "severity": "major"
                    if finding.get("severity") in {"ERROR", "WARNING"}
                    else "minor",
                    "fileName": finding.get("path", ""),
                    "line": finding.get("line"),
                    "message": f"correctness: {finding.get('title', '')} {finding.get('body', '')}",
                    "category": "correctness",
                }
            )
        events.append({"type": "complete"})
        normalized = normalize_coderabbit_output(
            events,
            material_severities=config.review.material_severities,
            material_categories=config.review.material_categories,
        )
    else:
        # Configured fallbacks must emit the CodeRabbit-compatible JSONL event contract.
        normalized = normalize_coderabbit_output(
            execution.stdout,
            material_severities=config.review.material_severities,
            material_categories=config.review.material_categories,
        )
    if normalized.status is Status.REVIEW_UNAVAILABLE:
        return None
    name = argv[0]
    return execution, normalized, name, _tool_version(name, root)


def _provider_review_argv(config: GateConfig, root: Path) -> tuple[str, ...]:
    """Bind CodeRabbit to the exact local dirty or committed boundary."""
    argv = tuple(config.review.argv)
    boundary_flags = ("--base", "--base-commit", "--committed", "--uncommitted")
    if config.review.provider != "coderabbit" or any(
        item == flag or item.startswith(f"{flag}=") for item in argv for flag in boundary_flags
    ):
        return argv
    branch = git(root, "branch", "--show-current", check=False).stdout.decode().strip()
    base_selector: tuple[str, ...] = ("--base", branch) if branch else ()
    if changed_paths(root):
        return (*argv, "--include-untracked", *base_selector, "--base-commit", "HEAD")
    base = git(root, "merge-base", "HEAD", "@{upstream}", check=False)
    if base.returncode:
        base = git(root, "rev-parse", "HEAD^", check=False)
    if base.returncode:
        return (*argv, "--include-untracked", *base_selector, "--base-commit", "HEAD")
    return (
        *argv,
        "--committed",
        *base_selector,
        "--base-commit",
        base.stdout.decode().strip(),
    )


def _tool_version(name: str, root: Path, *, timeout_seconds: float = 3) -> str:
    path = shutil.which(name)
    if path:
        executable = str(Path(path).resolve())
    else:
        candidate = root / name
        if not candidate.is_file() or not os.access(candidate, os.X_OK):
            return "UNAVAILABLE"
        executable = str(candidate)
    execution = run_argv(
        [executable, "--version"],
        cwd=root,
        timeout_seconds=max(0.001, timeout_seconds),
        output_cap=2048,
    )
    lines = (execution.stdout or execution.stderr).strip().splitlines()
    return lines[0][:200] if lines else "UNKNOWN"


def _state_file(root: Path, name: str) -> Path:
    from .gitstate import git_dir

    path = git_dir(root) / "hermes-gate" / "state" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _elapsed(started: float) -> int:
    return round((time.monotonic() - started) * 1000)


def result(command: str, status: Status, started: float, **extra: Any) -> dict[str, Any]:
    return {
        "schema": "hermes-gate/result-v1",
        "command": command,
        "status": status.value,
        "elapsed_ms": _elapsed(started),
        **extra,
    }
