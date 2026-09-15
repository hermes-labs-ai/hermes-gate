from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from .codex_install import installed as codex_installed
from .config import ConfigError, load_config
from .execution import run_argv
from .gitstate import ContentReadError, diff_digest, repo_root, scope_paths
from .init_repo import verify_runner
from .receipts import read_receipt
from .status import Status


def _provider_auth_status(returncode: int, output: str) -> str:
    """Interpret CodeRabbit's agent event instead of trusting its status exit code."""
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("authenticated") is True:
            return "AUTHENTICATED"
        if event.get("authenticated") is False:
            return "AUTH_REQUIRED"
        status = str(event.get("status", "")).lower()
        if status in {"not_authenticated", "unauthenticated", "auth_required"}:
            return "AUTH_REQUIRED"
        if status in {"authenticated", "signed_in"}:
            return "AUTHENTICATED"
    return "AUTHENTICATED" if returncode == 0 else "AUTH_REQUIRED"


def diagnose(start: Path | None = None) -> dict[str, Any]:
    began = time.monotonic()
    root = repo_root(start)
    codex = codex_installed()
    local_coderabbit = Path.home() / ".local" / "bin" / "coderabbit"
    provider: dict[str, Any] = {
        "binary": shutil.which("coderabbit")
        or shutil.which("cr")
        or (str(local_coderabbit) if local_coderabbit.is_file() else None)
    }
    if provider["binary"]:
        execution = run_argv(
            [provider["binary"], "auth", "status", "--agent"],
            cwd=root or Path.cwd(),
            timeout_seconds=5,
            output_cap=4096,
        )
        detail = (execution.stdout or execution.stderr).strip()[:1000]
        provider.update(
            {
                "status": _provider_auth_status(execution.returncode, detail),
                "detail": detail,
            }
        )
    else:
        provider["status"] = "NOT_CONFIGURED"
    if root is None:
        return {
            "schema": "hermes-gate/doctor-v1",
            "status": Status.NOT_APPLICABLE.value,
            "repository": None,
            "codex": codex,
            "provider": provider,
            "elapsed_ms": round((time.monotonic() - began) * 1000),
        }
    try:
        config = load_config(root)
        profile = {"status": "PASS", "adapter_status": config.adapter_status}
    except FileNotFoundError:
        profile = {"status": "NOT_CONFIGURED", "next_action": "hermes-gate init"}
    except ConfigError as exc:
        profile = {"status": "ERROR", "reason": str(exc)}
    runner_ok, runner_detail = verify_runner(root)
    try:
        digest = diff_digest(root, scope_paths(root))
        input_error = None
    except ContentReadError as exc:
        digest = None
        input_error = str(exc)
    receipts: dict[str, Any] = {}
    for kind in ("fast", "review", "full"):
        receipt = read_receipt(root, kind)
        receipts[kind] = {
            "status": "MISSING" if receipt is None else receipt.get("status", "ERROR"),
            "stale": bool(receipt and (digest is None or receipt.get("diff_sha256") != digest)),
        }
    statuses = [profile["status"], "PASS" if runner_ok else "NOT_CONFIGURED"]
    overall = "ERROR" if input_error else ("PASS" if all(item == "PASS" for item in statuses) else "NOT_CONFIGURED")
    return {
        "schema": "hermes-gate/doctor-v1",
        "status": overall,
        "repository": str(root),
        "profile": profile,
        "runner": {"status": "PASS" if runner_ok else "NOT_CONFIGURED", "detail": runner_detail},
        **({"input_error": input_error} if input_error else {}),
        "codex": codex,
        "provider": provider,
        "receipts": receipts,
        "elapsed_ms": round((time.monotonic() - began) * 1000),
    }
