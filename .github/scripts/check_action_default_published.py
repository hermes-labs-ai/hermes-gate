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
  transient network errors receive a small bounded retry budget; exhaustion
  fails closed with a readable message, never falling through to a pass.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import yaml

ROOT = Path(__file__).resolve().parents[2]
REPO_SLUG = "hermes-labs-ai/hermes-gate"
PYPI_URL = "https://pypi.org/pypi/hermes-gate/json"
NETWORK_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 1.0


def _input_default(manifest: str, input_name: str) -> str | None:
    """Return an Action input's default using YAML's safe scalar semantics."""
    try:
        document = yaml.safe_load(manifest)
    except yaml.YAMLError as exc:
        raise ValueError(f"action.yml is not valid YAML: {exc}") from exc
    if not isinstance(document, dict):
        return None
    inputs = document.get("inputs")
    if not isinstance(inputs, dict):
        return None
    definition = inputs.get(input_name)
    if not isinstance(definition, dict):
        return None
    value = definition.get("default")
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        preview = repr(value)
        if len(preview) > 80:
            preview = preview[:77] + "..."
        raise TypeError(
            f"inputs.{input_name}.default must be a string; got {type(value).__name__} {preview}"
        )
    return value


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


def _get(
    url: str,
    *,
    headers: dict[str, str],
    timeout: float = 30,
    attempts: int = NETWORK_ATTEMPTS,
    sleep: Callable[[float], None] | None = None,
) -> tuple[int, bytes]:
    """GET `url` with bounded retries for transient transport/server failures."""
    if attempts < 1:
        raise ValueError("network attempts must be at least one")
    transient_statuses = {408, 425, 429, 500, 502, 503, 504}
    last_error: Exception | None = None
    sleep = sleep or time.sleep
    for attempt in range(attempts):
        try:
            with urlopen(Request(url, headers=headers), timeout=timeout) as response:
                status, body = response.status, response.read()
                response_headers = getattr(response, "headers", {})
            retryable = status in transient_statuses or (
                status == 403
                and (
                    response_headers.get("X-RateLimit-Remaining") == "0"
                    or bool(response_headers.get("Retry-After"))
                )
            )
            if not retryable or attempt + 1 == attempts:
                return status, body
            last_error = ValueError(f"HTTP {status}")
        except HTTPError as exc:
            retryable = exc.code in transient_statuses or (
                exc.code == 403
                and (
                    getattr(exc, "headers", {}).get("X-RateLimit-Remaining") == "0"
                    or bool(getattr(exc, "headers", {}).get("Retry-After"))
                )
            )
            if not retryable or attempt + 1 == attempts:
                return exc.code, exc.read()
            last_error = exc
        except (URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt + 1 == attempts:
                raise ValueError(
                    f"network error fetching {url} after {attempts} attempts: {exc}"
                ) from exc
        sleep(RETRY_DELAY_SECONDS * (2**attempt))
    raise ValueError(f"network error fetching {url} after {attempts} attempts: {last_error}")


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
