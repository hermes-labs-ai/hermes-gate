from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable

from .status import Status


MATERIAL_CATEGORIES = {
    "correctness",
    "security",
    "data-loss",
    "concurrency",
    "api-contract",
}
MATERIAL_SEVERITIES = {"critical", "major"}


@dataclass(frozen=True)
class Finding:
    severity: str
    category: str
    path: str
    line: int | None
    message: str


@dataclass(frozen=True)
class NormalizedReview:
    status: Status
    findings: tuple[Finding, ...]
    suppressed_count: int
    reason: str = ""


def normalize_coderabbit_output(
    payload: str | dict[str, Any] | list[Any],
    *,
    material_severities: Iterable[str] = MATERIAL_SEVERITIES,
    material_categories: Iterable[str] = MATERIAL_CATEGORIES,
) -> NormalizedReview:
    events: list[dict[str, Any]] = []
    if isinstance(payload, str):
        for line in payload.splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                events.append(item)
    elif isinstance(payload, dict):
        events = [payload]
    elif isinstance(payload, list):
        events = [item for item in payload if isinstance(item, dict)]
    if not events:
        return NormalizedReview(Status.REVIEW_UNAVAILABLE, (), 0, "no parseable agent events")

    severities = {item.lower() for item in material_severities}
    categories = {item.lower() for item in material_categories}
    findings: list[Finding] = []
    suppressed = 0
    completed = False
    error = ""
    for event in events:
        event_type = str(event.get("type", "")).lower()
        if event_type == "error":
            error = str(event.get("message") or event.get("error") or "provider error")
        if event_type in {"complete", "completed"}:
            completed = True
        if event_type != "finding":
            continue
        severity = str(event.get("severity", "info")).lower()
        message = str(
            event.get("message")
            or event.get("title")
            or event.get("codegenInstructions")
            or "CodeRabbit finding"
        )
        category = _category(event, message)
        material = severity in severities and category in categories
        if not material:
            suppressed += 1
            continue
        line = event.get("line") or event.get("lineNumber")
        findings.append(
            Finding(
                severity=severity,
                category=category,
                path=str(event.get("fileName") or event.get("path") or ""),
                line=int(line) if isinstance(line, int | float) else None,
                message=message,
            )
        )

    if error:
        return NormalizedReview(Status.REVIEW_UNAVAILABLE, tuple(findings), suppressed, error)
    if not completed:
        return NormalizedReview(
            Status.REVIEW_UNAVAILABLE, (), suppressed, "review did not complete"
        )
    return NormalizedReview(
        Status.FAIL if findings else Status.PASS,
        tuple(findings),
        suppressed,
    )


def _category(event: dict[str, Any], message: str) -> str:
    raw = str(event.get("category") or event.get("typeName") or "").lower().replace("_", "-")
    if raw in MATERIAL_CATEGORIES:
        return raw
    text = f"{raw} {message}".lower()
    mapping = (
        (
            "security",
            (
                "security",
                "vulnerab",
                "injection",
                "credential",
                "auth",
                "interpolat",
                "placeholder syntax",
                "bound parameter",
            ),
        ),
        ("data-loss", ("data loss", "truncate", "overwrite", "corrupt", "delete")),
        ("concurrency", ("race", "deadlock", "concurr", "thread", "atomic")),
        ("api-contract", ("api contract", "breaking change", "compatib", "schema")),
        ("correctness", ("correctness", "logic", "exception", "crash", "wrong", "bug", "null")),
    )
    for category, needles in mapping:
        if any(needle in text for needle in needles):
            return category
    return "other"
