from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .gitstate import diff_digest, git_dir, head, repo_identity

SCHEMA = "hermes-gate/receipt-v1"


def receipt_dir(root: Path) -> Path:
    path = git_dir(root) / "hermes-gate" / "receipts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def receipt_path(root: Path, kind: str) -> Path:
    return receipt_dir(root) / f"{kind}.json"


def write_receipt(
    root: Path,
    kind: str,
    *,
    status: str,
    digest: str,
    elapsed_ms: int,
    command_versions: dict[str, str] | None = None,
    checks: list[dict[str, Any]] | None = None,
    findings: list[dict[str, Any]] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "kind": kind,
        "status": status,
        "created_at": datetime.now(UTC).isoformat(),
        "repository": repo_identity(root),
        "diff_sha256": digest,
        "elapsed_ms": elapsed_ms,
        "command_versions": command_versions or {},
        "checks": checks or [],
        "findings": findings or [],
    }
    if extra:
        payload.update(extra)
    _atomic_json(receipt_path(root, kind), payload)
    return payload


def read_receipt(root: Path, kind: str) -> dict[str, Any] | None:
    path = receipt_path(root, kind)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return raw if isinstance(raw, dict) else None


def valid_receipt(root: Path, kind: str, digest: str | None = None) -> dict[str, Any] | None:
    raw = read_receipt(root, kind)
    expected = digest or diff_digest(root)
    if not raw:
        return None
    if raw.get("schema") != SCHEMA or raw.get("kind") != kind:
        return None
    if raw.get("status") != "PASS" or raw.get("diff_sha256") != expected:
        return None
    if raw.get("repository", {}).get("root") != str(root.resolve()):
        return None
    if raw.get("repository", {}).get("head") != head(root):
        return None
    return raw


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
