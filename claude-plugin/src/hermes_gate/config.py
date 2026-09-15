from __future__ import annotations

import fnmatch
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class CommandSpec:
    name: str
    argv: tuple[str, ...]
    timeout_seconds: float
    globs: tuple[str, ...] = ("**/*",)

    def applies(self, files: list[str]) -> bool:
        return any(any(_matches(path, pattern) for pattern in self.globs) for path in files)

    def expanded_argv(self, files: list[str]) -> list[str]:
        out: list[str] = []
        for part in self.argv:
            if part == "{files}":
                out.extend(files)
            else:
                out.append(part)
        return out


@dataclass(frozen=True)
class LintLangSpec:
    enabled: bool = False
    argv: tuple[str, ...] = ("lintlang", "scan", "--format", "json", "{files}")
    trigger_globs: tuple[str, ...] = ()
    timeout_seconds: float = 4.0
    blocking_threshold: str = "MEDIUM"


@dataclass(frozen=True)
class ReviewSpec:
    provider: str = "coderabbit"
    argv: tuple[str, ...] = ("coderabbit", "review", "--agent")
    timeout_seconds: float = 180.0
    material_severities: tuple[str, ...] = ("critical", "major")
    material_categories: tuple[str, ...] = (
        "correctness",
        "security",
        "data-loss",
        "concurrency",
        "api-contract",
    )
    fallback_argv: tuple[str, ...] = ()


@dataclass(frozen=True)
class AdapterSpec:
    enabled: bool = False
    name: str = "native"
    argv: tuple[str, ...] = ()
    minimum_version: str = ""


@dataclass(frozen=True)
class GateConfig:
    root: Path
    fast_budget_seconds: float = 8.0
    full_required_local: bool = False
    exclusions: tuple[str, ...] = (
        ".git/**",
        ".hermes/hermes_gate_runner.py",
        ".pytest_cache/**",
        ".ruff_cache/**",
        "**/__pycache__/**",
        "vendor/**",
        "node_modules/**",
        "dist/**",
        "build/**",
    )
    fast: tuple[CommandSpec, ...] = ()
    full: tuple[CommandSpec, ...] = ()
    repair: tuple[CommandSpec, ...] = ()
    lintlang: LintLangSpec = field(default_factory=LintLangSpec)
    review: ReviewSpec = field(default_factory=ReviewSpec)
    adapter: AdapterSpec = field(default_factory=AdapterSpec)
    adapter_status: str = "NOT_CONFIGURED"

    def included(self, path: str) -> bool:
        return not any(_matches(path, pattern) for pattern in self.exclusions)


def _matches(path: str, pattern: str) -> bool:
    return fnmatch.fnmatch(path, pattern) or (
        pattern.startswith("**/") and fnmatch.fnmatch(path, pattern[3:])
    )


def _strings(value: Any, key: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ConfigError(f"{key} must be an array of strings")
    if not allow_empty and not value:
        raise ConfigError(f"{key} must not be empty")
    return tuple(value)


def _commands(data: Any, key: str) -> tuple[CommandSpec, ...]:
    if data is None:
        return ()
    if not isinstance(data, list):
        raise ConfigError(f"[[{key}]] must be an array of tables")
    result: list[CommandSpec] = []
    for index, raw in enumerate(data):
        if not isinstance(raw, dict):
            raise ConfigError(f"{key}[{index}] must be a table")
        argv = _strings(raw.get("argv"), f"{key}[{index}].argv")
        timeout = float(raw.get("timeout_seconds", 8.0))
        if timeout <= 0:
            raise ConfigError(f"{key}[{index}].timeout_seconds must be positive")
        result.append(
            CommandSpec(
                name=str(raw.get("name") or f"{key}-{index + 1}"),
                argv=argv,
                timeout_seconds=timeout,
                globs=_strings(raw.get("globs", ["**/*"]), f"{key}[{index}].globs"),
            )
        )
    return tuple(result)


def load_config(root: Path) -> GateConfig:
    path = root / ".hermes" / "gate.toml"
    if not path.is_file():
        raise FileNotFoundError(path)
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError) as exc:
        raise ConfigError(str(exc)) from exc
    gate = raw.get("gate", {})
    lint = raw.get("lintlang", {})
    review = raw.get("review", {})
    adapter = raw.get("adapter", {})
    if not all(isinstance(item, dict) for item in (gate, lint, review, adapter)):
        raise ConfigError("gate, lintlang, review, and adapter must be tables")
    budget = float(gate.get("fast_budget_seconds", 8.0))
    if budget <= 0:
        raise ConfigError("gate.fast_budget_seconds must be positive")
    return GateConfig(
        root=root,
        fast_budget_seconds=budget,
        full_required_local=bool(gate.get("full_required_local", False)),
        exclusions=_strings(gate.get("exclusions", list(GateConfig.exclusions)), "gate.exclusions"),
        fast=_commands(raw.get("fast"), "fast"),
        full=_commands(raw.get("full"), "full"),
        repair=_commands(raw.get("repair"), "repair"),
        lintlang=LintLangSpec(
            enabled=bool(lint.get("enabled", False)),
            argv=_strings(lint.get("argv", list(LintLangSpec.argv)), "lintlang.argv"),
            trigger_globs=_strings(
                lint.get("trigger_globs", []), "lintlang.trigger_globs", allow_empty=True
            ),
            timeout_seconds=float(lint.get("timeout_seconds", 4.0)),
            blocking_threshold=str(lint.get("blocking_threshold", "MEDIUM")).upper(),
        ),
        review=ReviewSpec(
            provider=str(review.get("provider", "coderabbit")),
            argv=_strings(review.get("argv", list(ReviewSpec.argv)), "review.argv"),
            timeout_seconds=float(review.get("timeout_seconds", 180.0)),
            material_severities=tuple(
                item.lower()
                for item in _strings(
                    review.get("material_severities", list(ReviewSpec.material_severities)),
                    "review.material_severities",
                )
            ),
            material_categories=tuple(
                item.lower()
                for item in _strings(
                    review.get("material_categories", list(ReviewSpec.material_categories)),
                    "review.material_categories",
                )
            ),
            fallback_argv=_strings(
                review.get("fallback_argv", []), "review.fallback_argv", allow_empty=True
            ),
        ),
        adapter=AdapterSpec(
            enabled=bool(adapter.get("enabled", False)),
            name=str(adapter.get("name", "native")),
            argv=_strings(adapter.get("argv", []), "adapter.argv", allow_empty=True),
            minimum_version=str(adapter.get("minimum_version", "")),
        ),
        adapter_status=str(gate.get("adapter_status", "NOT_CONFIGURED")),
    )
