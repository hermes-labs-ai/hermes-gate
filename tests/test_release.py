from __future__ import annotations

import importlib.util
import io
import shutil
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
TAG = f"v{VERSION}"
SPEC = importlib.util.spec_from_file_location("verify_release", ROOT / "scripts/verify_release.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
ReleaseError = MODULE.ReleaseError


@pytest.fixture(scope="module")
def built_dist(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("release") / "repo"
    shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns(".git", ".venv", "dist"))
    subprocess.run([sys.executable, "-m", "build", "--outdir", str(root / "dist")], cwd=root, check=True)
    return root


def test_release_identity_accepts_exact_tag_and_built_artifacts(built_dist: Path) -> None:
    assert MODULE.verify(built_dist, TAG, built_dist / "dist").startswith("PASS:")


def test_release_identity_rejects_tag_and_source_version_drift(
    built_dist: Path, tmp_path: Path
) -> None:
    with pytest.raises(ReleaseError, match="release tag"):
        MODULE.verify(built_dist, "v9.9.8")

    root = tmp_path / "repo"
    shutil.copytree(built_dist, root)
    init = root / "src" / "hermes_gate" / "__init__.py"
    init.write_text(init.read_text().replace(f'"{VERSION}"', '"9.9.9"'), encoding="utf-8")
    with pytest.raises(ReleaseError, match="__version__"):
        MODULE.verify(root, TAG)

    runner_root = tmp_path / "runner-repo"
    shutil.copytree(built_dist, runner_root)
    runner = runner_root / "src" / "hermes_gate" / "repo_runner.py"
    runner.write_text(runner.read_text().replace(f'"{VERSION}"', '"9.9.9"'), encoding="utf-8")
    with pytest.raises(ReleaseError, match="RUNNER_VERSION '9.9.9'"):
        MODULE.verify(runner_root, TAG)


def test_release_identity_rejects_tracked_runner_drift(built_dist: Path, tmp_path: Path) -> None:
    root = tmp_path / "repo"
    shutil.copytree(built_dist, root)
    tracked = root / ".hermes" / "hermes_gate_runner.py"
    tracked.write_text(tracked.read_text() + "\n# stale copy\n", encoding="utf-8")
    with pytest.raises(ReleaseError, match="tracked .hermes runner must match"):
        MODULE.verify(root, TAG)


def test_release_identity_rejects_extra_or_mislabeled_artifacts(
    built_dist: Path, tmp_path: Path
) -> None:
    dist = tmp_path / "dist"
    shutil.copytree(built_dist / "dist", dist)
    (dist / "unreviewed.txt").write_text("unexpected", encoding="utf-8")
    with pytest.raises(ReleaseError, match="must contain exactly"):
        MODULE.verify(built_dist, TAG, dist)

    (dist / "unreviewed.txt").unlink()
    wheel = next(dist.glob("*.whl"))
    wheel.rename(dist / "hermes_gate-9.9.8-py3-none-any.whl")
    with pytest.raises(ReleaseError, match="must contain exactly"):
        MODULE.verify(built_dist, TAG, dist)


@pytest.mark.parametrize("artifact", ["wheel", "sdist"])
@pytest.mark.parametrize(
    "mutation", ["metadata", "module", "unexpected-module", "execution", "dependency", "python"]
)
def test_release_identity_rejects_embedded_metadata_drift(
    built_dist: Path, tmp_path: Path, artifact: str, mutation: str
) -> None:
    dist = tmp_path / "dist"
    shutil.copytree(built_dist / "dist", dist)
    if artifact == "wheel":
        path = next(dist.glob("*.whl"))
        with zipfile.ZipFile(path) as archive:
            members = [(item, archive.read(item)) for item in archive.infolist()]
        with zipfile.ZipFile(path, "w") as archive:
            for item, data in members:
                if mutation == "metadata" and item.filename.endswith(".dist-info/METADATA"):
                    data = data.replace(f"Version: {VERSION}".encode(), b"Version: 9.9.9")
                if mutation == "dependency" and item.filename.endswith(".dist-info/METADATA"):
                    data = data.replace(
                        b"Metadata-Version:",
                        b"Requires-Dist: unexpected-package\nMetadata-Version:",
                    )
                if mutation == "python" and item.filename.endswith(".dist-info/METADATA"):
                    data = data.replace(b"Requires-Python: >=3.11", b"Requires-Python: >=3.9")
                if mutation == "module" and item.filename == "hermes_gate/cli.py":
                    data += b"\n# changed package module\n"
                if mutation == "execution" and item.filename.endswith("/entry_points.txt"):
                    data = data.replace(b"hermes_gate.cli:main", b"hermes_gate.cli:parser")
                archive.writestr(item, data)
            if mutation == "unexpected-module":
                archive.writestr("hermes_gate/unexpected.py", b"# unexpected module\n")
    else:
        path = next(dist.glob("*.tar.gz"))
        with tarfile.open(path, "r:gz") as archive:
            members = []
            for item in archive.getmembers():
                stream = archive.extractfile(item) if item.isfile() else None
                members.append((item, stream.read() if stream else None))
        with tarfile.open(path, "w:gz") as archive:
            for item, data in members:
                if data is not None and item.name == f"hermes_gate-{VERSION}/PKG-INFO":
                    if mutation == "dependency":
                        data = data.replace(
                            b"Metadata-Version:",
                            b"Requires-Dist: unexpected-package\nMetadata-Version:",
                        )
                    if mutation == "python":
                        data = data.replace(b"Requires-Python: >=3.11", b"Requires-Python: >=3.9")
                    item.size = len(data)
                if (
                    mutation == "metadata"
                    and item.name == f"hermes_gate-{VERSION}/PKG-INFO"
                    and data is not None
                ):
                    data = data.replace(f"Version: {VERSION}".encode(), b"Version: 9.9.9")
                    item.size = len(data)
                if (
                    mutation == "module"
                    and item.name == f"hermes_gate-{VERSION}/src/hermes_gate/cli.py"
                    and data is not None
                ):
                    data += b"\n# changed package module\n"
                    item.size = len(data)
                if (
                    mutation == "execution"
                    and item.name == f"hermes_gate-{VERSION}/pyproject.toml"
                    and data is not None
                ):
                    data = data.replace(b"setuptools.build_meta", b"unexpected_backend")
                    item.size = len(data)
                archive.addfile(item, io.BytesIO(data) if data is not None else None)
            if mutation == "unexpected-module":
                item = tarfile.TarInfo(f"hermes_gate-{VERSION}/src/hermes_gate/unexpected.py")
                data = b"# unexpected module\n"
                item.size = len(data)
                archive.addfile(item, io.BytesIO(data))
    message = "artifact identity must be" if mutation == "metadata" else "packaged source differs"
    if mutation == "execution":
        message = "entry points differ" if artifact == "wheel" else "build input pyproject.toml"
    if mutation in {"dependency", "python"}:
        message = "installation metadata differs"
    with pytest.raises(ReleaseError, match=message):
        MODULE.verify(built_dist, TAG, dist)
