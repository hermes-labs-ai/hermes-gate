from __future__ import annotations
import hashlib
import json
import subprocess
import sys
from pathlib import Path
import pytest
from hermes_gate.config import load_config
from hermes_gate.engine import fast, review
from hermes_gate.execution import Execution


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "core.hooksPath", "/dev/null")
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.invalid")
    (root / ".hermes").mkdir()
    (root / ".hermes/gate.toml").write_text(
        'version=1\n[review]\nprovider="hermes-pr-review"\nargv=["hermes-pr-review"]\nmodel="claude-sonnet-5"\n'
    )
    profile = root / ".hermes/gate.toml"
    profile.write_text(
        profile.read_text()
        + "\n[[fast]]\nname='test'\nargv="
        + json.dumps([sys.executable, "-c", "pass"])
        + "\ntimeout_seconds=2\n"
    )
    (root / "source.py").write_text("x=1\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "base")
    (root / "source.py").write_text("x=2\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "change")
    return root


def fake_adapter(monkeypatch, defect=None):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if defect == "exit":
            return Execution(tuple(argv), 1, 0, "", "failed")
        output = Path(argv[argv.index("--output-dir") + 1])
        root = kwargs["cwd"]
        base = argv[argv.index("--base") + 1]
        model = argv[argv.index("--model") + 1]
        engine = "claude" if "--run-claude" in argv else "codex"
        raw = {
            "schema_version": 1,
            "reviewed_head_sha": git(root, "rev-parse", "HEAD"),
            "base_sha": base,
            "verdict": "PASS",
            "summary": "Accepted",
            "findings": [],
            "limitations": [],
        }
        receipt = {
            "schema_version": 1,
            "repo": str(root.resolve()),
            "head_sha": raw["reviewed_head_sha"],
            "base_sha": base,
            "validated": True,
            "outer_workflow_status": "VALIDATED",
            "github_mutation": False,
            "engine": engine,
            "model": model,
            "changed_paths": git(root, "diff", "--name-only", base, "HEAD").splitlines(),
            "verdict": "PASS",
        }
        if defect == "unevaluated":
            raw["verdict"] = "UNEVALUATED"
            receipt["verdict"] = "UNEVALUATED"
        if defect == "head":
            receipt["head_sha"] = "0" * 40
        if defect == "model":
            receipt["model"] = "wrong"
        if defect == "engine":
            receipt["engine"] = "wrong"
        if defect == "paths":
            receipt["changed_paths"] = []
        data = json.dumps(raw).encode()
        (output / "review.json").write_bytes(data)
        receipt["review_sha256"] = hashlib.sha256(data).hexdigest()
        if defect == "hash":
            receipt["review_sha256"] = "wrong"
        if defect != "missing":
            (output / "RECEIPT.json").write_text(json.dumps(receipt))
        return Execution(tuple(argv), 0, 0, "", "")

    monkeypatch.setattr("hermes_gate.engine.run_argv", run)
    monkeypatch.setattr("hermes_gate.engine._tool_version", lambda *a: "test")
    return calls


@pytest.mark.parametrize(
    "model,flag", [("claude-sonnet-5", "--run-claude"), ("gpt-5.6-terra", "--run-codex")]
)
def test_declared_adapter_dispatch(repo, monkeypatch, model, flag):
    p = repo / ".hermes/gate.toml"
    p.write_text(p.read_text().replace("claude-sonnet-5", model))
    git(repo, "add", ".")
    git(repo, "commit", "--amend", "--no-edit", "-q")
    assert load_config(repo).review.model == model
    assert fast(repo)["status"] == "PASS"
    calls = fake_adapter(monkeypatch)
    result = review(repo)
    assert result["status"] == "PASS", result
    assert flag in calls[0]
    assert result["receipt"]["provider"] == "hermes-pr-review"
    assert result["receipt"]["fallback_attempted"] is False


@pytest.mark.parametrize(
    "defect", ["exit", "missing", "unevaluated", "head", "model", "engine", "paths", "hash"]
)
def test_adapter_failure_never_passes(repo, monkeypatch, defect):
    assert fast(repo)["status"] == "PASS"
    fake_adapter(monkeypatch, defect)
    assert review(repo)["status"] == "REVIEW_UNAVAILABLE"


def test_dirty_adapter_not_invoked(repo, monkeypatch):
    (repo / "source.py").write_text("x=3\n")
    assert fast(repo)["status"] == "PASS"
    calls = fake_adapter(monkeypatch)
    assert review(repo)["status"] == "REVIEW_UNAVAILABLE"
    assert not calls
