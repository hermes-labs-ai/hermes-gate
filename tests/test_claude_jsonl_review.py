"""The optional Claude adapter must send only the selected local patch."""

from __future__ import annotations

import json
import runpy
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "src" / "hermes_gate" / "claude_review.py"


def test_selected_diff_includes_untracked_and_excludes_other_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    (tmp_path / "tracked.py").write_text("value = 1\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "base"], check=True)
    (tmp_path / "tracked.py").write_text("value = 2\n")
    (tmp_path / "new.py").write_text("fresh = True\n")
    (tmp_path / "other.py").write_text("secret = 'outside scope'\n")
    monkeypatch.chdir(tmp_path)
    adapter = runpy.run_path(str(SCRIPT))
    diff = adapter["_selected_diff"](["tracked.py", "new.py"])
    assert "+value = 2" in diff
    assert "+fresh = True" in diff
    assert "outside scope" not in diff


def test_saved_diff_rejects_non_patch_payload(tmp_path: Path) -> None:
    adapter = runpy.run_path(str(SCRIPT))
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps([{"filename": "x.py", "diff": "patch"}]))
    assert adapter["_fixture_diff"](fixture) == "patch"
    fixture.write_text(json.dumps([{"filename": "x.py", "diff": 12}]))
    with pytest.raises(ValueError, match="fixture"):
        adapter["_fixture_diff"](fixture)
