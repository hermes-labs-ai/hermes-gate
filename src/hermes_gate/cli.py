from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable

from .codex_install import install as install_codex
from .codex_install import uninstall as uninstall_codex
from .doctor import diagnose
from .engine import boundary, fast, full, repair, review
from .gitstate import repo_root
from .hooks import read_stdin_payload, run_hook
from .init_repo import initialize, uninstall
from .status import EXIT_CODES, Status


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="hermes-gate", description="Bounded session-wide completion rail"
    )
    root.add_argument("--version", action="version", version="hermes-gate 0.1.0")
    sub = root.add_subparsers(dest="command", required=True)
    init = sub.add_parser(
        "init", help="install a repository profile, runner, workflow, and baseline"
    )
    init.add_argument("--force", action="store_true")
    sub.add_parser("fast", help="run the cached session-scoped deterministic gate")
    sub.add_parser("repair", help="run one configured deterministic repair")
    sub.add_parser("review", help="run one bounded independent review")
    sub.add_parser("full", help="run the complete declared repository contract")
    bound = sub.add_parser("boundary", help="validate exact receipts for a Git or PR boundary")
    bound.add_argument("action", choices=("commit", "push", "pr-create", "pr-ready"))
    sub.add_parser("doctor", help="read-only installation and receipt diagnostics")
    sub.add_parser("install-codex", help=argparse.SUPPRESS)
    sub.add_parser("uninstall-codex", help=argparse.SUPPRESS)
    sub.add_parser("uninstall-repo", help=argparse.SUPPRESS)
    hook = sub.add_parser("hook", help=argparse.SUPPRESS)
    hook.add_argument("event", choices=("session-start", "stop", "pre-tool-use"))
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "hook":
        output = run_hook(args.event, read_stdin_payload())
        print(json.dumps(output, sort_keys=True))
        return 0
    if args.command == "doctor":
        output = diagnose(Path.cwd())
        return _emit(output)
    if args.command == "install-codex":
        executable = Path(sys.argv[0])
        output = install_codex(executable)
        return _emit(output)
    if args.command == "uninstall-codex":
        return _emit(uninstall_codex())
    root = repo_root(Path.cwd())
    if root is None:
        return _emit(
            {
                "schema": "hermes-gate/result-v1",
                "command": args.command,
                "status": Status.NOT_APPLICABLE.value,
                "reason": "not a Git repository",
                "elapsed_ms": 0,
            }
        )
    routes: dict[str, Callable[[], dict[str, Any]]] = {
        "init": lambda: _wrap_init(root, args.force),
        "fast": lambda: fast(root),
        "repair": lambda: repair(root),
        "review": lambda: review(root),
        "full": lambda: full(root),
        "boundary": lambda: boundary(root, args.action),
        "uninstall-repo": lambda: _wrap_uninstall(root),
    }
    return _emit(routes[args.command]())


def _wrap_init(root: Path, force: bool) -> dict[str, Any]:
    started = time.monotonic()
    value = initialize(root, force=force)
    return {
        "schema": "hermes-gate/result-v1",
        "command": "init",
        "elapsed_ms": round((time.monotonic() - started) * 1000),
        **value,
    }


def _wrap_uninstall(root: Path) -> dict[str, Any]:
    started = time.monotonic()
    value = uninstall(root)
    return {
        "schema": "hermes-gate/result-v1",
        "command": "uninstall-repo",
        "elapsed_ms": round((time.monotonic() - started) * 1000),
        **value,
    }


def _emit(output: dict[str, Any]) -> int:
    print(json.dumps(output, indent=2, sort_keys=True))
    try:
        status = Status(str(output.get("status", Status.ERROR.value)))
    except ValueError:
        status = Status.ERROR
    return EXIT_CODES[status]


if __name__ == "__main__":
    raise SystemExit(main())
