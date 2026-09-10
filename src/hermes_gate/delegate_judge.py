"""``hermes-gate delegate-judge``: judge one child workspace with the fast gate.

Wire contract (see README "Review and hooks"): read exactly one JSON request
object from stdin and write exactly one JSON ``{"verdict", "feedback"}``
object to stdout. This module never runs a shell and never changes the
process's working directory; every gate call is bound to the caller-supplied
``workspace`` argument, so concurrent invocations of this command in separate
processes cannot race.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TextIO

from .engine import fast
from .gitstate import repo_root
from .status import Status

REQUEST_VERSION = 1
MAX_FEEDBACK_CHARS = 800
MAX_FAILED_CHECKS = 5

# name -> expected type for fields the contract always sends with a concrete value.
_REQUIRED_FIELDS: dict[str, type] = {
    "version": int,
    "goal": str,
    "summary": str,
    "attempt": int,
    "max_retries": int,
    "task_index": int,
    "subagent_id": str,
    "completed": bool,
    "workspace": str,
}
# name -> expected type for fields the contract may send as ``null``.
_NULLABLE_FIELDS: dict[str, type] = {
    "previous_feedback": str,
    "session_id": str,
    "model": str,
    "api_calls": int,
}
_TYPE_LABELS: dict[type, str] = {int: "an integer", str: "a string", bool: "a boolean"}
_PASSING_STATUSES = (Status.PASS, Status.NOT_APPLICABLE)


def read_request(stream: TextIO) -> tuple[dict[str, Any] | None, str]:
    """Read the one required JSON request object from ``stream``.

    Returns ``(request, "")`` on success, or ``(None, reason)`` when the bytes
    on the stream are not a single JSON object.
    """

    try:
        raw = stream.read()
    except OSError as exc:
        return None, f"cannot read stdin: {exc}"
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, f"stdin is not valid JSON: {exc}"
    if not isinstance(value, dict):
        return None, "request must be a JSON object"
    return value, ""


def validate_request(value: dict[str, Any]) -> str:
    """Return "" when ``value`` matches the consumer contract, else a compact reason."""

    for field, kind in _REQUIRED_FIELDS.items():
        if field not in value:
            return f"missing required field: {field}"
        if not _matches(value[field], kind):
            return f"{field} must be {_TYPE_LABELS[kind]}"
    for field, kind in _NULLABLE_FIELDS.items():
        if field in value and value[field] is not None and not _matches(value[field], kind):
            return f"{field} must be {_TYPE_LABELS[kind]} or null"
    if value["version"] != REQUEST_VERSION:
        return f"unsupported request version: {value['version']!r}"
    if not value["workspace"].strip():
        return "workspace must not be empty"
    if not value["subagent_id"].strip():
        return "subagent_id must not be empty"
    if value["attempt"] < 0 or value["max_retries"] < 0:
        return "attempt and max_retries must be non-negative"
    return ""


def _matches(value: Any, kind: type) -> bool:
    if kind is bool:
        return isinstance(value, bool)
    if kind is int:
        return isinstance(value, int) and not isinstance(value, bool)
    return isinstance(value, kind)


def judge(request: dict[str, Any]) -> dict[str, str]:
    """Map one delegate-judge request to a bounded ``{"verdict", "feedback"}`` object."""

    error = validate_request(request)
    if error:
        return {"verdict": "error", "feedback": _bound(f"invalid request: {error}")}
    workspace = Path(request["workspace"]).expanduser()
    if not workspace.is_dir():
        reason = f"workspace is not a directory: {workspace}"
        return {"verdict": "error", "feedback": _bound(reason)}
    root = repo_root(workspace)
    if root is None:
        return {"verdict": "error", "feedback": "workspace is not a Git repository"}
    outcome = fast(root)
    return _map_outcome(outcome, attempt=request["attempt"], max_retries=request["max_retries"])


def _map_outcome(outcome: dict[str, Any], *, attempt: int, max_retries: int) -> dict[str, str]:
    try:
        status = Status(str(outcome.get("status", Status.ERROR.value)))
    except ValueError:
        status = Status.ERROR
    reason = str(outcome.get("reason") or "")
    if status in _PASSING_STATUSES:
        return {"verdict": "pass", "feedback": ""}
    if status is Status.NOT_CONFIGURED:
        return {"verdict": "error", "feedback": _bound(f"gate not configured: {reason}")}
    if status is Status.FAIL:
        verdict = "retry" if attempt < max_retries else "reject"
        return {"verdict": verdict, "feedback": _bound(_fail_feedback(outcome, reason))}
    # ERROR, and any status `fast` is not documented to return (PARKED,
    # REVIEW_UNAVAILABLE): treat conservatively as an adapter/tool problem.
    return {"verdict": "error", "feedback": _bound(f"gate error: {reason or status.value}")}


def _fail_feedback(outcome: dict[str, Any], reason: str) -> str:
    receipt = outcome.get("receipt")
    checks = receipt.get("checks") if isinstance(receipt, dict) else None
    lines = [reason or "fast gate failed"]
    if isinstance(checks, list):
        failing = [
            check
            for check in checks
            if isinstance(check, dict)
            and str(check.get("status", "")).lower() not in {"pass", "skipped", "not_applicable"}
        ]
        for check in failing[:MAX_FAILED_CHECKS]:
            name = str(check.get("name", "check"))
            detail = str(check.get("reason") or check.get("status") or "failed")
            lines.append(f"- {name}: {detail}")
        overflow = len(failing) - MAX_FAILED_CHECKS
        if overflow > 0:
            lines.append(f"...and {overflow} more failing check(s)")
    return "\n".join(lines)


def _bound(text: str) -> str:
    if len(text) <= MAX_FEEDBACK_CHARS:
        return text
    return text[: MAX_FEEDBACK_CHARS - 1].rstrip() + "…"
