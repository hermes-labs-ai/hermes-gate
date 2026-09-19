from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_marketplace_action_metadata_is_a_safe_composite_action() -> None:
    manifest = (ROOT / "action.yml").read_text(encoding="utf-8")

    assert "name: Hermes Gate" in manifest
    assert "using: composite" in manifest
    assert "uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97" in manifest
    assert 'default: 0.1.7' in manifest
    assert 'default: "3.11"' in manifest
    assert 'HERMES_GATE_VERSION: ${{ inputs.version }}' in manifest
    assert 'hermes-gate==${HERMES_GATE_VERSION}' in manifest
    assert 'HERMES_GATE_COMMAND: ${{ inputs.command }}' in manifest
    assert 'fast|full|review|doctor) hermes-gate "${HERMES_GATE_COMMAND}"' in manifest
    assert "command must be one of: fast, full, review, doctor" in manifest
