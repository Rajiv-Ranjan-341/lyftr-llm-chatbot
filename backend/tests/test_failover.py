import pytest
from fastapi.testclient import TestClient

from app.main import build_app
from tests.conftest import KEY_ONE, KEY_TWO


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_simulated_failure_on_key_1_falls_over_to_key_2(client, provider, status):
    response = client.post(
        "/chat",
        json={"session_id": f"s-fo-{status}", "message": f"Failover check {status}"},
        headers={"X-Simulate-Failure": f"key_1={status}"},
    )

    assert response.status_code == 200
    meta = response.json()["metadata"]
    assert meta["key_id"] == "key_2"
    assert meta["fallback_attempts"] == 1
    assert meta["resolution_source"] == "LIVE_INFERENCE"
    # A real answer, not an error wrapped in a 200.
    assert response.json()["reply"] == provider.reply

    # key_1 was skipped entirely; key_2 did the work.
    assert provider.calls[-1]["api_key"] == KEY_TWO


def test_all_keys_failing_returns_a_clean_503(client, provider):
    response = client.post(
        "/chat",
        json={"session_id": "s-down", "message": "Everything is on fire"},
        headers={"X-Simulate-Failure": "*=429"},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "all providers unavailable"}
    assert provider.calls == []


def test_a_failed_request_adds_nothing_to_history(client):
    client.post(
        "/chat",
        json={"session_id": "s-empty", "message": "Everything is on fire"},
        headers={"X-Simulate-Failure": "*=429"},
    )

    assert client.get("/sessions/s-empty/messages").status_code == 404


def test_explicit_per_key_statuses_exhaust_the_pool(client):
    response = client.post(
        "/chat",
        json={"session_id": "s-both", "message": "Both keys down"},
        headers={"X-Simulate-Failure": "key_1=429,key_2=500"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "all providers unavailable"


def test_real_provider_errors_also_trigger_failover(client, provider):
    """No simulation header - key_1's provider genuinely rate-limits."""
    provider.fail_for_keys = {KEY_ONE: 429}

    response = client.post(
        "/chat", json={"session_id": "s-real", "message": "Real rate limit"}
    )

    assert response.status_code == 200
    assert response.json()["metadata"]["key_id"] == "key_2"
    assert response.json()["metadata"]["fallback_attempts"] == 1


def test_every_real_key_failing_returns_503(client, provider):
    provider.fail_for_keys = {KEY_ONE: 429, KEY_TWO: 503}

    response = client.post(
        "/chat", json={"session_id": "s-real-down", "message": "Both really down"}
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "all providers unavailable"}


def test_simulation_bypasses_the_cache_so_failover_is_observable(client, provider):
    """Without the bypass the second call would be an LLM_CACHE hit with
    key_id=null, and failover could not be demonstrated on demand."""
    client.post("/chat", json={"session_id": "s-bypass-1", "message": "Cached question"})

    response = client.post(
        "/chat",
        json={"session_id": "s-bypass-2", "message": "Cached question"},
        headers={"X-Simulate-Failure": "key_1=429"},
    )

    meta = response.json()["metadata"]
    assert meta["resolution_source"] == "LIVE_INFERENCE"
    assert meta["key_id"] == "key_2"


def test_header_is_ignored_when_the_switch_is_off(settings, provider):
    settings = settings.model_copy(update={"enable_failure_simulation": False})

    with TestClient(build_app(settings)) as client:
        response = client.post(
            "/chat",
            json={"session_id": "s-off", "message": "Simulation disabled"},
            headers={"X-Simulate-Failure": "*=429"},
        )

    assert response.status_code == 200
    assert response.json()["metadata"]["key_id"] == "key_1"


@pytest.mark.parametrize(
    "header", ["", "garbage", "key_1=418", "key_1", "=429", "key_1=abc"]
)
def test_malformed_simulation_headers_are_ignored(client, header):
    # Distinct message per case, so none of these resolve from the cache and
    # every one of them reports the key that actually answered.
    response = client.post(
        "/chat",
        json={"session_id": "s-bad", "message": f"Still works despite {header!r}"},
        headers={"X-Simulate-Failure": header} if header else {},
    )

    assert response.status_code == 200
    assert response.json()["metadata"]["key_id"] == "key_1"
