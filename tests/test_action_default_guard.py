"""Regression coverage for the release-time Action-default guard.

`evaluate` is the pure decision function `check_action_default_published.py`
uses at release time; `fetch_pypi_releases` and `tag_has_action_yml` are its
only network seams, and `_input_default` is its action.yml parser. Every test
here injects fakes for the network seams, so none of this touches PyPI or
GitHub. None of these tests assert a specific version literal in action.yml
(the manifest is on its way to dropping a literal default entirely) --
`evaluate` and `_input_default` are exercised with inline fixture strings
instead.
"""

import runpy
from pathlib import Path

import pytest

_MODULE = runpy.run_path(
    str(Path(__file__).parents[1] / ".github/scripts/check_action_default_published.py")
)
evaluate = _MODULE["evaluate"]
check_published = _MODULE["check_published"]
tag_has_action_yml = _MODULE["tag_has_action_yml"]
fetch_pypi_releases = _MODULE["fetch_pypi_releases"]
_input_default = _MODULE["_input_default"]


def _releases(*published: str) -> dict:
    return {version: [{}] for version in published}


def _unreachable(*_args, **_kwargs):
    raise AssertionError("must not touch the network for this case")


# --- evaluate(): cases (a)-(f), re-expressed around the README ref, plus both
# --- states of `default` (a literal string, and None for empty/absent). ---


@pytest.mark.parametrize("default", ["0.1.8", None], ids=["literal-default", "no-default"])
def test_releasing_the_tag_being_published_does_not_require_pypi(default) -> None:
    """(a) releasing vX with README @vX not yet on PyPI -> pass, no network calls."""
    message = evaluate(
        default=default,
        readme_version="0.1.8",
        release_tag="v0.1.8",
        local_action_yml_exists=True,
        fetch_pypi_releases=_unreachable,
        tag_has_action_yml=_unreachable,
    )
    assert "PASS" in message
    assert "0.1.8" in message


@pytest.mark.parametrize("default", ["0.1.7", None], ids=["literal-default", "no-default"])
def test_releasing_a_newer_tag_still_checks_an_older_readme_ref(default) -> None:
    """(b) releasing vX, README @vY (Y<X) already published, tag vY has action.yml -> pass."""
    message = evaluate(
        default=default,
        readme_version="0.1.7",
        release_tag="v0.1.8",
        local_action_yml_exists=True,
        fetch_pypi_releases=lambda: _releases("0.1.7"),
        tag_has_action_yml=lambda version: version == "0.1.7",
    )
    assert "PASS" in message


def test_tag_missing_action_yml_fails_and_names_the_tag() -> None:
    """(c) README @vY published but tag vY lacks action.yml (the real v0.1.6 case) -> fail."""
    with pytest.raises(ValueError, match="v0.1.6"):
        evaluate(
            default=None,
            readme_version="0.1.6",
            release_tag="",
            local_action_yml_exists=True,
            fetch_pypi_releases=lambda: _releases("0.1.6"),
            tag_has_action_yml=lambda version: False,
        )


def test_unpublished_readme_ref_outside_a_matching_release_fails() -> None:
    """(d) README ref not on PyPI and not the release tag -> fail."""
    with pytest.raises(ValueError, match="not a published"):
        evaluate(
            default=None,
            readme_version="0.1.8",
            release_tag="",
            local_action_yml_exists=True,
            fetch_pypi_releases=lambda: _releases("0.1.7"),
            tag_has_action_yml=_unreachable,
        )


def test_readme_and_default_mismatch_fails_before_any_network_call() -> None:
    """(e) README ref != a *present* action.yml default -> fail."""
    with pytest.raises(ValueError, match="does not match"):
        evaluate(
            default="0.1.7",
            readme_version="0.1.6",
            release_tag="",
            local_action_yml_exists=True,
            fetch_pypi_releases=_unreachable,
            tag_has_action_yml=_unreachable,
        )


def test_empty_default_skips_the_mismatch_check_entirely() -> None:
    """An absent/empty default never fails the mismatch check, whatever the README says."""
    message = evaluate(
        default=None,
        readme_version="0.1.6",
        release_tag="",
        local_action_yml_exists=True,
        fetch_pypi_releases=lambda: _releases("0.1.6"),
        tag_has_action_yml=lambda version: True,
    )
    assert "PASS" in message


def test_pypi_network_error_fails_closed() -> None:
    """(f) a network error looking up PyPI fails closed with a readable message."""

    def broken_fetch():
        raise ValueError("network error fetching https://pypi.org/pypi/hermes-gate/json: timed out")

    with pytest.raises(ValueError, match="network error"):
        evaluate(
            default=None,
            readme_version="0.1.7",
            release_tag="",
            local_action_yml_exists=True,
            fetch_pypi_releases=broken_fetch,
            tag_has_action_yml=_unreachable,
        )


def test_github_contents_network_error_fails_closed() -> None:
    """A network error checking the tag tree also fails closed, not just the PyPI leg."""

    def broken_tag_check(_version):
        raise ValueError("network error fetching https://api.github.com/...: timed out")

    with pytest.raises(ValueError, match="network error"):
        evaluate(
            default=None,
            readme_version="0.1.7",
            release_tag="",
            local_action_yml_exists=True,
            fetch_pypi_releases=lambda: _releases("0.1.7"),
            tag_has_action_yml=broken_tag_check,
        )


def test_release_tag_checkout_without_action_yml_fails_closed() -> None:
    """Releasing vX whose own checked-out tree has no action.yml must still fail."""
    with pytest.raises(ValueError, match="action.yml"):
        evaluate(
            default=None,
            readme_version="0.1.8",
            release_tag="v0.1.8",
            local_action_yml_exists=False,
            fetch_pypi_releases=_unreachable,
            tag_has_action_yml=_unreachable,
        )


# --- check_published / tag_has_action_yml / fetch_pypi_releases: unit-level ---


def test_check_published_rejects_unpublished_version() -> None:
    with pytest.raises(ValueError, match="not a published"):
        check_published("0.1.8", _releases("0.1.7"))


def test_check_published_ignores_empty_release_files() -> None:
    with pytest.raises(ValueError, match="not a published"):
        check_published("0.1.7", {"0.1.7": []})


def test_check_published_accepts_a_published_version() -> None:
    check_published("0.1.7", _releases("0.1.7"))


def test_tag_has_action_yml_true_on_200() -> None:
    assert tag_has_action_yml("0.1.7", fetch_status=lambda url: 200) is True


def test_tag_has_action_yml_false_on_404() -> None:
    assert tag_has_action_yml("0.1.6", fetch_status=lambda url: 404) is False


def test_tag_has_action_yml_names_the_tag_in_the_request() -> None:
    seen = {}

    def fake_fetch(url: str) -> int:
        seen["url"] = url
        return 404

    tag_has_action_yml("0.1.6", fetch_status=fake_fetch)
    assert "ref=v0.1.6" in seen["url"]


def test_tag_has_action_yml_raises_on_unexpected_status() -> None:
    with pytest.raises(ValueError, match="HTTP 500"):
        tag_has_action_yml("0.1.7", fetch_status=lambda url: 500)


def test_fetch_pypi_releases_returns_the_releases_mapping() -> None:
    def fake_get(url: str, *, headers: dict) -> tuple:
        assert url == _MODULE["PYPI_URL"]
        return 200, b'{"releases": {"0.1.7": [{}]}}'

    # `fetch_pypi_releases` looks up `_get` as a module global. `runpy.run_path`
    # hands back a *copy* of the finished globals dict, so patch the function's
    # own live `__globals__` instead of `_MODULE` to reach that seam.
    original_get = fetch_pypi_releases.__globals__["_get"]
    fetch_pypi_releases.__globals__["_get"] = fake_get
    try:
        releases = fetch_pypi_releases()
    finally:
        fetch_pypi_releases.__globals__["_get"] = original_get
    assert releases == {"0.1.7": [{}]}


def test_fetch_pypi_releases_fails_closed_on_non_200() -> None:
    original_get = fetch_pypi_releases.__globals__["_get"]
    fetch_pypi_releases.__globals__["_get"] = lambda url, *, headers: (500, b"")
    try:
        with pytest.raises(ValueError, match="HTTP 500"):
            fetch_pypi_releases()
    finally:
        fetch_pypi_releases.__globals__["_get"] = original_get


# --- _input_default: structural, indentation-aware action.yml parsing ---

_MANIFEST_VERSION_FIRST = """\
inputs:
  command:
    description: Hermes Gate command to run
    required: false
    default: fast
  version:
    description: Published hermes-gate version to install from PyPI
    required: false
    default: 0.1.7
  python-version:
    description: Python version used to install and run Hermes Gate
    required: false
    default: "3.11"
"""

_MANIFEST_VERSION_REORDERED = """\
inputs:
  command:
    description: Hermes Gate command to run
    required: false
    default: fast
  python-version:
    description: Python version used to install and run Hermes Gate
    required: false
    default: "3.11"
  version:
    description: Published hermes-gate version to install from PyPI
    required: false
    default: 0.1.7
"""

_MANIFEST_EMPTY_DEFAULT = """\
inputs:
  version:
    description: Published hermes-gate version to install from PyPI
    required: false
    default: ""
"""

_MANIFEST_NO_DEFAULT_KEY = """\
inputs:
  version:
    description: Published hermes-gate version to install from PyPI
    required: false
"""


def test_input_default_reads_the_version_input() -> None:
    assert _input_default(_MANIFEST_VERSION_FIRST, "version") == "0.1.7"


def test_input_default_survives_reordered_inputs() -> None:
    """The old single regex captured python-version's default here because it
    matched the substring "version:" inside "python-version:" first. This must
    resolve the exact `version:` key regardless of where it sits in the list."""
    assert _input_default(_MANIFEST_VERSION_REORDERED, "version") == "0.1.7"


def test_input_default_reads_python_version_default_unquoted() -> None:
    assert _input_default(_MANIFEST_VERSION_FIRST, "python-version") == "3.11"


def test_input_default_empty_string_is_none() -> None:
    assert _input_default(_MANIFEST_EMPTY_DEFAULT, "version") is None


def test_input_default_missing_key_is_none() -> None:
    assert _input_default(_MANIFEST_NO_DEFAULT_KEY, "version") is None


def test_input_default_missing_input_is_none() -> None:
    assert _input_default(_MANIFEST_NO_DEFAULT_KEY, "nonexistent") is None


def test_read_action_default_matches_current_main() -> None:
    """End-to-end sanity check against the real action.yml on disk."""
    read_action_default = _MODULE["read_action_default"]
    default = read_action_default()
    assert default is None or isinstance(default, str)
