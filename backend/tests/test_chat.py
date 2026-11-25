import pytest

from tests.conftest import KEY_ONE


def test_chat_returns_contract_shape(client, provider):
    response = client.post(
        "/chat", json={"session_id": "s-shape", "message": "What is the capital of France?"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "s-shape"
    assert body["reply"] == provider.reply

    meta = body["metadata"]
    assert meta["resolution_source"] == "LIVE_INFERENCE"
    assert meta["model"] == "groq/openai/gpt-oss-120b"
    assert isinstance(meta["latency_ms"], int) and meta["latency_ms"] >= 0
    assert meta["input_tokens"] > 0
    assert meta["output_tokens"] > 0
    assert meta["key_id"] == "key_1"
    assert meta["fallback_attempts"] == 0

    # key_1 is tried first and its secret is never echoed back
    assert provider.calls[0]["api_key"] == KEY_ONE
    assert KEY_ONE not in response.text


def test_default_system_prompt_is_sent_but_never_stored_in_history(client, provider):
    client.post("/chat", json={"session_id": "s-sys", "message": "Hello"})

    sent = provider.calls[0]["messages"]
    assert sent[0] == {"role": "system", "content": "You are a test assistant."}

    history = client.get("/sessions/s-sys/messages").json()["messages"]
    assert [m["role"] for m in history] == ["user", "assistant"]


def test_custom_system_prompt_persists_for_the_session(client, provider):
    client.post(
        "/chat",
        json={
            "session_id": "s-custom",
            "message": "Hello",
            "system_prompt": "You only speak in haiku.",
        },
    )
    client.post("/chat", json={"session_id": "s-custom", "message": "Again please"})

    # The second turn did not resend the prompt, but it still leads the payload.
    assert provider.calls[1]["messages"][0] == {
        "role": "system",
        "content": "You only speak in haiku.",
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"session_id": "s-1"},                      # missing message
        {"message": "hello"},                        # missing session_id
        {"session_id": "s-1", "message": "   "},     # blank after trimming
        {"session_id": "", "message": "hello"},      # blank session id
        {"session_id": "s-1", "message": "x" * 4001},  # too long
        {"session_id": "y" * 129, "message": "hi"},  # session id too long
    ],
)
def test_invalid_requests_return_422_with_detail(client, provider, payload):
    response = client.post("/chat", json=payload)

    assert response.status_code == 422
    assert "detail" in response.json()
    assert provider.calls == []  # never reached the provider


def test_malformed_json_returns_422(client):
    response = client.post(
        "/chat",
        content=b"{not json",
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 422
    assert "detail" in response.json()


def test_failed_request_is_not_written_to_history(client):
    client.post("/chat", json={"session_id": "s-reject", "message": "  "})

    assert client.get("/sessions/s-reject/messages").status_code == 404


def test_tokens_are_per_request_not_a_running_total(client, provider):
    first = client.post(
        "/chat", json={"session_id": "s-tok", "message": "First question here"}
    ).json()["metadata"]
    second = client.post(
        "/chat", json={"session_id": "s-tok", "message": "Second question here"}
    ).json()["metadata"]

    # Output tokens describe only this reply, so two identical replies match.
    assert first["output_tokens"] == second["output_tokens"] == provider.completion_tokens
    # Input grows with the carried history, but is not a sum of past requests.
    assert second["input_tokens"] > first["input_tokens"]
    assert second["input_tokens"] < first["input_tokens"] * 2


def test_equal_length_first_messages_report_similar_input_tokens(client):
    first = client.post(
        "/chat", json={"session_id": "s-a", "message": "Tell me about Rome!"}
    ).json()["metadata"]
    second = client.post(
        "/chat", json={"session_id": "s-b", "message": "Tell me about Lima!"}
    ).json()["metadata"]

    assert abs(first["input_tokens"] - second["input_tokens"]) <= 1
