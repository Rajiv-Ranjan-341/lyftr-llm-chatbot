def test_health_reports_key_pool_and_cache(client):
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["keys_configured"] == 2
    assert body["cache_enabled"] is True


def test_health_is_degraded_without_keys(settings, provider):
    from fastapi.testclient import TestClient

    from app.main import build_app

    settings = settings.model_copy(update={"llm_api_keys": ""})
    with TestClient(build_app(settings)) as client:
        response = client.get("/health")

    assert response.status_code == 503
    assert response.json()["keys_configured"] == 0
