"""Fail closed unless the Marketplace Action's README example is installable.

This runs at release time (see `.github/workflows/publish.yml`), not inside the
pytest suite, so the normal PR gate never depends on an unauthenticated network
call. The version this guard reasons about is always the one in the README's
copyable `uses: hermes-labs-ai/hermes-gate@vX` example, because that is the
only version a user copying the quickstart actually asks for. `action.yml`'s
`version` input MAY also carry a literal `default: X` (today's main does); when
it does, it must agree with the README. It may instead be empty or absent (the
install version derived at runtime from the Action ref instead, the psf/black
pattern) -- in that case there is nothing to compare it to, so the default
comparison is simply skipped. Whether an empty default is *safe* to skip past
(i.e. whether action.yml actually implements a working ref-derived resolver)
is a property of action.yml, not of this guard: it is asserted by
`tests/test_action_metadata.py`, owned by whichever change makes the default
empty. An earlier revision of this guard tried to re-check that property here
too, via a bare `"github.action_ref" in manifest` substring search; an
independent verifier showed that check wrong in both directions (a comment
mentioning `github.action_ref` with no working resolver still passed; a
resolver spelled as the env var `$GITHUB_ACTION_REF` instead of the context
expression still failed), so it was removed rather than patched further --
it was a second, weaker source of truth for something action.yml's own tests
already own.

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
  `actions/checkout` needs no extra fetch depth. It authenticates with
  `GITHUB_TOKEN` when the workflow provides one (avoids shared-runner-IP rate
  limiting; never required for this public repo, and never sent to PyPI). A
  network failure fails closed with a readable message; it never falls
  through to a pass.
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


def _strip_inline_comment(value: str) -> str:
    """Return `value` with any trailing YAML comment removed.

    A `#` only starts a comment outside of a quoted scalar: for an unquoted
    value, cut at the first " #" (a hash preceded by whitespace) and rstrip;
    for a value that starts with a quote, the scalar ends at its matching
    closing quote and everything after that (including a `#`) is a comment --
    but a `#` *inside* the quotes is ordinary content, not a comment marker.

    Both YAML quote forms escape an embedded quote by doubling/backslashing
    it rather than ending the scalar there: `'it''s'` is the single string
    `it's`, and `"say \\"hi\\""` is `say "hi"`. A naive "find the next quote
    character" scan (hermes-gate review, correctness, major) mistook the
    escape's first character for the terminator on input like
    `'0.1.7'' # incompatible' # note` (a valid, if perverse, single-quoted
    scalar whose real value is `0.1.7' # incompatible`), truncating early and
    accepting a value the guard should have rejected. This scans past an
    escaped quote instead of stopping at it.
    """
    value = value.strip()
    if not value:
        return value
    if value[0] == "'":
        i = 1
        while i < len(value):
            if value[i] == "'":
                if i + 1 < len(value) and value[i + 1] == "'":
                    i += 2
                    continue
                return value[: i + 1]
            i += 1
        return value  # Unterminated quote; treat the remainder as content.
    if value[0] == '"':
        i = 1
        while i < len(value):
            if value[i] == "\\":
                i += 2
                continue
            if value[i] == '"':
                return value[: i + 1]
            i += 1
        return value  # Unterminated quote; treat the remainder as content.
    comment_at = value.find(" #")
    return value[:comment_at].rstrip() if comment_at != -1 else value


def _unquote(value: str) -> str:
    """Undo YAML quoting, including the two escape forms quoted scalars use.

    Deliberately does not implement double-quoted YAML's full escape grammar
    (`\\n`, `\\t`, `\\uXXXX`, ...): a version default that needed those would
    not be a value this guard could sensibly compare against a SemVer-shaped
    README ref, and this script stays stdlib-only and intentionally short of
    a real YAML parser (see `_input_default`).
    """
    if len(value) < 2 or value[0] != value[-1] or value[0] not in "\"'":
        return value
    inner = value[1:-1]
    if value[0] == "'":
        return inner.replace("''", "'")
    return inner.replace('\\"', '"')


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
            raw_value = content[len("default:") :]
            value = _unquote(_strip_inline_comment(raw_value))
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


def _get(url: str, *, headers: dict[str, str], timeout: float = 30) -> tuple[int, bytes]:
    """GET `url`, returning (status, body). Raise ValueError on transport failure."""
    try:
        with urlopen(Request(url, headers=headers), timeout=timeout) as response:
            return response.status, response.read()
    except HTTPError as exc:
        return exc.code, exc.read()
    except (URLError, TimeoutError, OSError) as exc:
        raise ValueError(f"network error fetching {url}: {exc}") from exc


def fetch_pypi_releases() -> dict:
    """Return the `releases` mapping from PyPI's JSON API for hermes-gate.

    Never sends a GitHub token or any Authorization header -- PyPI's JSON API
    is anonymous and unrelated to the GitHub contents lookup below.
    """
    status, body = _get(PYPI_URL, headers={"Accept": "application/json"})
    if status != 200:
        raise ValueError(f"PyPI lookup for hermes-gate failed with HTTP {status} ({PYPI_URL})")
    return json.loads(body)["releases"]


def _github_headers() -> dict[str, str]:
    """Headers for the GitHub contents API, authenticated when possible.

    A shared-IP hosted runner can hit GitHub's low unauthenticated rate limit
    and get a 403/429 that would otherwise fail this guard closed on a
    perfectly good release. `GITHUB_TOKEN` is provided by the publish
    workflow's own `permissions: contents: read`; when present it is sent as
    a bearer token, but the token is never required for this public repo and
    is never attached to the unrelated PyPI request in `fetch_pypi_releases`.
    """
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def tag_has_action_yml(version: str, *, fetch_status=None) -> bool:
    """Return whether tag v{version} of this repo contains action.yml at its root.

    Uses the public GitHub contents API rather than git, so this works without
    changing `actions/checkout`'s default shallow, tagless clone. `fetch_status`
    is injectable for tests; it takes a URL and returns an HTTP status code
    (raising ValueError on a transport failure), matching the shape of the
    real network call below.
    """
    tag = f"v{version}"
    url = f"https://api.github.com/repos/{REPO_SLUG}/contents/action.yml?ref={tag}"
    if fetch_status is None:
        status, _ = _get(url, headers=_github_headers())
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
    `default` is a *consistency* check only when action.yml carries a literal
    one; an empty/absent default (`None`) skips that comparison entirely --
    whether that is *safe* is asserted elsewhere, by action.yml's own tests
    (see module docstring), not re-verified here.

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
