def test_history_is_returned_oldest_first_with_assistant_metadata(client, provider):
    client.post("/chat", json={"session_id": "s-hist-1", "message": "First"})
    client.post("/chat", json={"session_id": "s-hist-1", "message": "Second"})

    body = client.get("/sessions/s-hist-1/messages").json()

    assert body["session_id"] == "s-hist-1"
    roles = [m["role"] for m in body["messages"]]
    assert roles == ["user", "assistant", "user", "assistant"]
    assert body["messages"][0]["content"] == "First"
    assert body["messages"][2]["content"] == "Second"

    # Assistant turns carry metadata; user turns do not.
    assert "metadata" not in body["messages"][0]
    meta = body["messages"][1]["metadata"]
    assert meta["resolution_source"] == "LIVE_INFERENCE"
    assert meta["input_tokens"] > 0
    assert meta["output_tokens"] > 0


def test_history_never_contains_the_system_prompt(client):
    client.post(
        "/chat",
        json={
            "session_id": "s-nosys",
            "message": "Hi",
            "system_prompt": "SECRET-SYSTEM-PROMPT",
        },
    )

    body = client.get("/sessions/s-nosys/messages").json()

    assert all(m["role"] in ("user", "assistant") for m in body["messages"])
    assert "SECRET-SYSTEM-PROMPT" not in str(body)


def test_multi_turn_sends_prior_history_to_the_model(client, provider):
    client.post("/chat", json={"session_id": "s-multi", "message": "My name is Ada"})
    client.post("/chat", json={"session_id": "s-multi", "message": "What is my name?"})

    second_payload = provider.calls[1]["messages"]
    contents = [m["content"] for m in second_payload]

    # system prompt + both earlier turns + the new message
    assert contents[0] == "You are a test assistant."
    assert "My name is Ada" in contents
    assert contents[-1] == "What is my name?"
    assert len(second_payload) == 4


def test_sessions_stay_separate(client, provider):
    client.post("/chat", json={"session_id": "s-alpha", "message": "Alpha secret"})
    client.post("/chat", json={"session_id": "s-beta", "message": "Beta secret"})

    alpha = client.get("/sessions/s-alpha/messages").json()["messages"]
    beta = client.get("/sessions/s-beta/messages").json()["messages"]

    assert len(alpha) == 2 and len(beta) == 2
    assert "Alpha secret" not in str(beta)
    assert "Beta secret" not in str(alpha)

    # The second session's payload carried no trace of the first.
    assert "Alpha secret" not in str(provider.calls[1]["messages"])


def test_unknown_session_returns_404(client):
    response = client.get("/sessions/s-does-not-exist/messages")

    assert response.status_code == 404
    assert response.json() == {"detail": "session not found"}


def test_history_survives_a_restart(settings, provider):
    """History lives in SQLite on disk, so a new app instance still sees it."""
    from fastapi.testclient import TestClient

    from app.main import build_app

    with TestClient(build_app(settings)) as first:
        first.post("/chat", json={"session_id": "s-restart", "message": "Remember me"})

    with TestClient(build_app(settings)) as second:
        body = second.get("/sessions/s-restart/messages").json()

    assert body["messages"][0]["content"] == "Remember me"
