"""Fail closed unless the Marketplace Action's README example is installable.

This runs at release time (see `.github/workflows/publish.yml`), not inside the
pytest suite, so the normal PR gate never depends on an unauthenticated network
call. The version this guard reasons about is always the one in the README's
copyable `uses: hermes-labs-ai/hermes-gate@vX` example, because that is the
only version a user copying the quickstart actually asks for. `action.yml`'s
`version` input MAY also carry a literal `default: X` (today's main does); when
it does, it must agree with the README. It may instead be empty or absent
(the install version derived at runtime from `github.action_ref` instead, the
psf/black pattern) -- in that case there is nothing to compare it to, so the
default is skipped entirely and the guard reasons about the README ref alone.

Two cases, independent of whether a literal default is present:

* Releasing tag vX whose README already says `@vX`: X cannot be on PyPI yet --
  publishing X is what this workflow run is about to do -- so requiring it
  would deadlock every legitimate release. `actions/checkout` resolved the
  release ref, so the checked-out tree already IS vX's own tree; this case
  only needs that tree to actually contain `action.yml`.
* Every other case (a local run, PR CI, or a release whose README still names
  some earlier version Y != X): the named version must already be a published
  PyPI release, AND its tag must exist and contain `action.yml`. That second
  half closes a real incident: main briefly named `@v0.1.6` in the README
  while tag `v0.1.6` predates the Marketplace Action and has no `action.yml`
  at all -- the previous version of this guard checked only PyPI and the
  README/default match, so it printed PASS. The tag check is proven against
  the public GitHub contents API (`action.yml?ref=vX`) so a shallow, tagless
  `actions/checkout` needs no extra fetch depth and no token for this public
  repository. A network failure fails closed with a readable message; it
  never falls through to a pass.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
REPO_SLUG = "hermes-labs-ai/hermes-gate"
PYPI_URL = "https://pypi.org/pypi/hermes-gate/json"


def _direct_child_range(
    lines: list[tuple[int, str]], start: int, end: int, key: str
) -> tuple[int, int] | None:
    """Find `key:` as a direct child within lines[start:end]; return the index
    range of *its* nested block (exclusive of the `key:` line itself), or None.

    "Direct child" means: at the minimum indentation level present in
    [start:end) (YAML siblings share one indent), matched by the *entire*
    stripped line equalling `f"{key}:"` -- not merely containing it, which is
    what let a substring match like "python-version:" satisfy a search for
    "version:" before this rewrite.
    """
    if start >= end:
        return None
    child_level = min(indent for indent, _ in lines[start:end])
    for i in range(start, end):
        indent, content = lines[i]
        if indent == child_level and content == f"{key}:":
            j = i + 1
            while j < end and lines[j][0] > child_level:
                j += 1
            return i + 1, j
    return None


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _input_default(manifest: str, input_name: str) -> str | None:
    """Return the literal `default:` scalar for `inputs.<input_name>` in an
    action.yml manifest, or None when the input, its default key, or a
    non-empty value is absent.

    Structural and indentation-aware rather than a single regex over the raw
    text, and stdlib-only: this repository ships zero runtime dependencies
    (`dependencies = []` in pyproject.toml) and the publish workflow's `build`
    job never installs PyYAML, so this parser has to stay dependency-free too.
    Scoping strictly to the `inputs.<input_name>` block (see
    `_direct_child_range`) means input declaration order does not matter and
    an unrelated input whose key merely contains "<input_name>" as a
    substring (e.g. `python-version` vs. `version`) cannot be matched instead.
    """
    lines = [
        (len(raw) - len(raw.lstrip(" ")), stripped)
        for raw in manifest.splitlines()
        if (stripped := raw.strip()) and not stripped.startswith("#")
    ]
    inputs_range = _direct_child_range(lines, 0, len(lines), "inputs")
    if inputs_range is None:
        return None
    input_range = _direct_child_range(lines, *inputs_range, input_name)
    if input_range is None:
        return None
    start, end = input_range
    if start >= end:
        return None
    field_level = min(indent for indent, _ in lines[start:end])
    for indent, content in lines[start:end]:
        if indent == field_level and content.startswith("default:"):
            value = _unquote(content[len("default:") :].strip())
            return value or None
    return None


def read_action_default() -> str | None:
    """Return the `version` input's default from action.yml, or None when it
    is absent or an empty string (the version is then pinned at install time
    by the Action ref itself rather than by a literal default)."""
    manifest = (ROOT / "action.yml").read_text(encoding="utf-8")
    return _input_default(manifest, "version")


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
        raise ValueError(f"hermes-gate {version!r} is not a published PyPI release")


def _get(url: str, *, headers: dict[str, str]) -> tuple[int, bytes]:
    """GET `url`, returning (status, body). Raise ValueError on transport failure."""
    try:
        with urlopen(Request(url, headers=headers), timeout=10) as response:
            return response.status, response.read()
    except HTTPError as exc:
        return exc.code, exc.read()
    except (URLError, TimeoutError, OSError) as exc:
        raise ValueError(f"network error fetching {url}: {exc}") from exc


def fetch_pypi_releases() -> dict:
    """Return the `releases` mapping from PyPI's JSON API for hermes-gate."""
    status, body = _get(PYPI_URL, headers={"Accept": "application/json"})
    if status != 200:
        raise ValueError(f"PyPI lookup for hermes-gate failed with HTTP {status} ({PYPI_URL})")
    return json.loads(body)["releases"]


def tag_has_action_yml(version: str, *, fetch_status=None) -> bool:
    """Return whether tag v{version} of this repo contains action.yml at its root.

    Uses the public GitHub contents API rather than git, so this works without
    changing `actions/checkout`'s default shallow, tagless clone and without a
    token for this public repository. `fetch_status` is injectable for tests;
    it takes a URL and returns an HTTP status code (raising ValueError on a
    transport failure), matching the shape of the real network call below.
    """
    tag = f"v{version}"
    url = f"https://api.github.com/repos/{REPO_SLUG}/contents/action.yml?ref={tag}"
    if fetch_status is None:
        status, _ = _get(url, headers={"Accept": "application/vnd.github+json"})
    else:
        status = fetch_status(url)
    if status == 200:
        return True
    if status == 404:
        return False
    raise ValueError(f"GitHub contents lookup for tag {tag!r} failed with HTTP {status} ({url})")


def evaluate(
    *,
    default: str | None,
    readme_version: str,
    release_tag: str,
    local_action_yml_exists: bool,
    fetch_pypi_releases=fetch_pypi_releases,
    tag_has_action_yml=tag_has_action_yml,
) -> str:
    """Return a PASS message, or raise ValueError describing the failure.

    The README ref (`readme_version`) is the version this guard reasons about.
    `default` is a *consistency* check when action.yml carries a literal one;
    an empty/absent default (`None`) skips that comparison entirely rather
    than failing, since the version is then pinned by the ref at install time.

    Pure decision logic, factored out of `main()` so tests can inject fake
    `fetch_pypi_releases`/`tag_has_action_yml` callables instead of touching
    the network or the real action.yml/README.md.
    """
    if default and default != readme_version:
        raise ValueError(
            f"action.yml default {default!r} does not match README ref v{readme_version!r}"
        )

    if release_tag == f"v{readme_version}":
        # actions/checkout resolved the release ref, so the checked-out tree
        # already IS v{readme_version}'s own tree; PyPI cannot have this
        # version yet because publishing it is what this run is about to do.
        if not local_action_yml_exists:
            raise ValueError(
                f"release tag {release_tag!r} checkout has no action.yml at the repository root"
            )
        default_note = (
            f"action.yml default {default!r} matches it"
            if default
            else "action.yml has no literal default (version is pinned by the ref at install time)"
        )
        return (
            f"PASS: README ref v{readme_version} is the release tag {release_tag!r} "
            f"being published; {default_note}; action.yml is present in the checked-out tree"
        )

    releases = fetch_pypi_releases()
    check_published(readme_version, releases)
    if not tag_has_action_yml(readme_version):
        raise ValueError(
            f"tag 'v{readme_version}' does not contain action.yml, so the README Action "
            f"ref names a tag that cannot install the Marketplace Action"
        )
    return (
        f"PASS: README ref v{readme_version} matches a published PyPI release and "
        f"tag 'v{readme_version}' contains action.yml"
    )


def main() -> None:
    default = read_action_default()
    readme_version = read_readme_ref()
    release_tag = os.environ.get("RELEASE_TAG", "")
    message = evaluate(
        default=default,
        readme_version=readme_version,
        release_tag=release_tag,
        local_action_yml_exists=(ROOT / "action.yml").is_file(),
    )
    print(message)


if __name__ == "__main__":
    main()
