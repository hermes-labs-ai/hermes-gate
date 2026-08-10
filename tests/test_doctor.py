from hermes_gate.doctor import _provider_auth_status


def test_provider_auth_status_rejects_false_agent_event_even_on_zero_exit() -> None:
    output = '{"type":"status","status":"not_authenticated","authenticated":false}'

    assert _provider_auth_status(0, output) == "AUTH_REQUIRED"


def test_provider_auth_status_accepts_true_agent_event() -> None:
    output = '{"type":"status","status":"authenticated","authenticated":true}'

    assert _provider_auth_status(0, output) == "AUTHENTICATED"


def test_provider_auth_status_falls_back_for_legacy_output() -> None:
    assert _provider_auth_status(0, "Signed in") == "AUTHENTICATED"
    assert _provider_auth_status(1, "Sign-in required") == "AUTH_REQUIRED"
