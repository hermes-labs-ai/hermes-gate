"""Fail closed when the Marketplace Action's default version is not published.

This runs at release time (see `.github/workflows/publish.yml`), not inside the
pytest suite, so the normal PR gate never depends on an unauthenticated network
call. It only proves that the Action's copyable default and the README example
name a version that is actually installable from PyPI right now.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]


def read_action_default() -> str:
    """Return the `version` input's default from action.yml."""
    manifest = (ROOT / "action.yml").read_text(encoding="utf-8")
    match = re.search(r"version:\n(?:.*\n)*?\s*default: (\S+)", manifest)
    if not match:
        raise ValueError("action.yml has no readable 'version' input default")
    return match.group(1)


def read_readme_ref() -> str:
    """Return the version named by the README's copyable Action ref."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    match = re.search(r"uses: hermes-labs-ai/hermes-gate@v(\S+)", readme)
    if not match:
        raise ValueError("README.md has no readable Action ref")
    return match.group(1)


def check_published(version: str, releases: dict) -> None:
    """Reject a version absent from PyPI or published with no files."""
    files = releases.get(version)
    if not files:
        raise ValueError(f"Action default {version!r} is not a published hermes-gate release on PyPI")


def main() -> None:
    default = read_action_default()
    readme_version = read_readme_ref()
    if default != readme_version:
        raise ValueError(
            f"action.yml default {default!r} does not match README ref v{readme_version!r}"
        )
    with urlopen("https://pypi.org/pypi/hermes-gate/json", timeout=30) as response:
        releases = json.load(response)["releases"]
    check_published(default, releases)
    print(f"PASS: Action default {default!r} matches a published PyPI release and the README ref")


if __name__ == "__main__":
    main()
