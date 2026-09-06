#!/usr/bin/env python3
"""Fail closed when a HermesGate release tag or built artifact drifts."""

from __future__ import annotations

import argparse
import ast
import configparser
from email.parser import BytesParser
from pathlib import Path
import tarfile
import tomllib
import zipfile


class ReleaseError(ValueError):
    """The release identity is incomplete or inconsistent."""


def _metadata(raw: bytes, source: str, project: dict) -> tuple[str, str]:
    parsed = BytesParser().parsebytes(raw)
    name = parsed.get("Name")
    version = parsed.get("Version")
    if not name or not version:
        raise ReleaseError(f"{source}: package metadata lacks Name or Version")
    if len(parsed.get_all("Name", [])) != 1 or len(parsed.get_all("Version", [])) != 1:
        raise ReleaseError(f"{source}: duplicate package identity fields")
    expected_python = [project["requires-python"]] if project.get("requires-python") else []
    dependencies = list(project.get("dependencies", []))
    for extra, requirements in project.get("optional-dependencies", {}).items():
        for requirement in requirements:
            dependency, separator, marker = requirement.partition(";")
            condition = (
                f'({marker.strip()}) and extra == "{extra}"' if separator else f'extra == "{extra}"'
            )
            dependencies.append(f"{dependency.strip()}; {condition}")
    if (
        parsed.get_all("Requires-Python", []) != expected_python
        or sorted(parsed.get_all("Requires-Dist", [])) != sorted(dependencies)
        or sorted(parsed.get_all("Provides-Extra", []))
        != sorted(project.get("optional-dependencies", {}))
    ):
        raise ReleaseError(f"{source}: installation metadata differs from reviewed project")
    return name, version


def _literal_version(path: Path, variable: str) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == variable for target in node.targets
            )
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            return node.value.value
    raise ReleaseError(f"{path}: literal {variable} assignment not found")


def _verify_payload(payload: dict[str, bytes], expected: dict[str, bytes], source: str) -> None:
    if payload != expected:
        changed = sorted(
            key for key in payload.keys() | expected.keys() if payload.get(key) != expected.get(key)
        )
        raise ReleaseError(f"{source}: packaged source differs from reviewed source: {changed!r}")


def verify(root: Path, tag: str, dist: Path | None = None) -> str:
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    name = project["name"]
    version = project["version"]
    expected_tag = f"v{version}"
    if tag != expected_tag:
        raise ReleaseError(f"release tag {tag!r} must equal {expected_tag!r}")

    version_paths = {
        "src/hermes_gate/__init__.py __version__": (
            root / "src" / "hermes_gate" / "__init__.py",
            "__version__",
        ),
        "src/hermes_gate/repo_runner.py RUNNER_VERSION": (
            root / "src" / "hermes_gate" / "repo_runner.py",
            "RUNNER_VERSION",
        ),
    }
    for label, (path, variable) in version_paths.items():
        source_version = _literal_version(path, variable)
        if source_version != version:
            raise ReleaseError(f"{label} {source_version!r} must equal {version!r}")

    runner = root / "src" / "hermes_gate" / "repo_runner.py"
    tracked_runner = root / ".hermes" / "hermes_gate_runner.py"
    if runner.read_bytes() != tracked_runner.read_bytes():
        raise ReleaseError("tracked .hermes runner must match src/hermes_gate/repo_runner.py")

    if dist is None:
        return f"PASS: source identity is {name} {version} ({tag})"

    wheel_name = f"hermes_gate-{version}-py3-none-any.whl"
    sdist_name = f"hermes_gate-{version}.tar.gz"
    actual = sorted(path.name for path in dist.iterdir() if path.is_file())
    expected_files = sorted([sdist_name, wheel_name])
    if actual != expected_files:
        raise ReleaseError(
            f"dist must contain exactly {sdist_name!r} and {wheel_name!r}; found {actual!r}"
        )

    wheel = dist / wheel_name
    package = root / "src" / "hermes_gate"
    source_payload = {
        path.relative_to(root / "src").as_posix(): path.read_bytes()
        for path in package.rglob("*")
        if path.is_file() and path.suffix in {".py", ".json"} and "__pycache__" not in path.parts
    }
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ReleaseError(f"{wheel.name}: duplicate archive entries")
        matches = [item for item in archive.namelist() if item.endswith(".dist-info/METADATA")]
        if len(matches) != 1:
            raise ReleaseError(f"{wheel.name}: expected exactly one METADATA file")
        wheel_identity = _metadata(archive.read(matches[0]), wheel.name, project)
        metadata_prefix = matches[0].rsplit("/", 1)[0] + "/"
        entrypoints = configparser.ConfigParser(interpolation=None)
        entrypoints.optionxform = str
        entrypoints.read_string(archive.read(metadata_prefix + "entry_points.txt").decode())
        expected_entries = {"console_scripts": project.get("scripts", {})}
        if project.get("gui-scripts"):
            expected_entries["gui_scripts"] = project["gui-scripts"]
        expected_entries.update(project.get("entry-points", {}))
        if {
            section: dict(entrypoints[section]) for section in entrypoints.sections()
        } != expected_entries or entrypoints.defaults():
            raise ReleaseError(f"{wheel.name}: entry points differ from reviewed project")
        payload = {
            item: archive.read(item)
            for item in names
            if not item.endswith("/") and not item.startswith(metadata_prefix)
        }
        _verify_payload(payload, source_payload, wheel.name)

    sdist = dist / sdist_name
    with tarfile.open(sdist, "r:gz") as archive:
        members = archive.getmembers()
        names = [item.name for item in members]
        if len(names) != len(set(names)):
            raise ReleaseError(f"{sdist.name}: duplicate archive entries")
        root_metadata = f"hermes_gate-{version}/PKG-INFO"
        matches = [item for item in archive.getmembers() if item.name == root_metadata]
        if len(matches) != 1:
            raise ReleaseError(f"{sdist.name}: expected exactly one PKG-INFO file")
        extracted = archive.extractfile(matches[0])
        if extracted is None:
            raise ReleaseError(f"{sdist.name}: could not read PKG-INFO")
        sdist_identity = _metadata(extracted.read(), sdist.name, project)
        archive_root = f"hermes_gate-{version}/"
        for filename in ("pyproject.toml", "setup.py", "setup.cfg", "MANIFEST.in"):
            source = root / filename
            archived = [item for item in members if item.name == archive_root + filename]
            if source.is_file():
                if len(archived) != 1 or not archived[0].isfile():
                    raise ReleaseError(f"{sdist.name}: missing regular build input {filename}")
                stream = archive.extractfile(archived[0])
                if stream is None or stream.read() != source.read_bytes():
                    raise ReleaseError(f"{sdist.name}: build input {filename} differs from source")
            elif filename == "setup.cfg" and archived:
                # setuptools emits this fixed, non-executable sdist-only configuration.
                if not archived[0].isfile():
                    raise ReleaseError(f"{sdist.name}: setup.cfg must be a regular file")
                stream = archive.extractfile(archived[0])
                if stream is None or stream.read() != b"[egg_info]\ntag_build = \ntag_date = 0\n\n":
                    raise ReleaseError(f"{sdist.name}: unexpected generated setup.cfg")
            elif archived:
                raise ReleaseError(f"{sdist.name}: unexpected build input {filename}")
        prefix = f"hermes_gate-{version}/src/hermes_gate/"
        payload = {}
        for item in members:
            if not item.name.startswith(prefix) or item.isdir():
                continue
            if not item.isfile():
                raise ReleaseError(f"{sdist.name}: package entry must be a regular file")
            stream = archive.extractfile(item)
            if stream is None:
                raise ReleaseError(f"{sdist.name}: unreadable package entry")
            payload["hermes_gate/" + item.name[len(prefix) :]] = stream.read()
        _verify_payload(payload, source_payload, sdist.name)

    expected = (name, version)
    if wheel_identity != expected or sdist_identity != expected:
        raise ReleaseError(
            f"artifact identity must be {expected!r}; wheel={wheel_identity!r}, "
            f"sdist={sdist_identity!r}"
        )
    return f"PASS: artifacts are {name} {version} ({tag})"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    parser.add_argument("--dist", type=Path)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        print(verify(args.root, args.tag, args.dist))
    except (
        KeyError,
        OSError,
        ReleaseError,
        configparser.Error,
        tarfile.TarError,
        zipfile.BadZipFile,
    ) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
