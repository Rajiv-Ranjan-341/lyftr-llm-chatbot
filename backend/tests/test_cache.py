def test_same_first_message_in_a_new_session_hits_the_cache(client, provider):
    """Cache key is model + the full message list, so an identical opening
    message in a *different* session must resolve from cache."""
    first = client.post(
        "/chat", json={"session_id": "s-cache-1", "message": "What is the capital of France?"}
    ).json()
    second = client.post(
        "/chat", json={"session_id": "s-cache-2", "message": "What is the capital of France?"}
    ).json()

    assert first["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert second["metadata"]["resolution_source"] == "LLM_CACHE"

    # Identical reply text, and only one call ever reached the provider.
    assert second["reply"] == first["reply"]
    assert len(provider.calls) == 1


def test_cache_hit_reports_stored_usage_and_null_key_id(client):
    first = client.post(
        "/chat", json={"session_id": "s-usage-1", "message": "Ping"}
    ).json()["metadata"]
    second = client.post(
        "/chat", json={"session_id": "s-usage-2", "message": "Ping"}
    ).json()["metadata"]

    assert second["input_tokens"] == first["input_tokens"]
    assert second["output_tokens"] == first["output_tokens"]
    assert second["key_id"] is None
    assert second["fallback_attempts"] == 0
    assert second["model"] == first["model"]


def test_cache_hit_is_faster_than_the_live_call(client):
    first = client.post(
        "/chat", json={"session_id": "s-speed-1", "message": "How fast is this?"}
    ).json()["metadata"]
    second = client.post(
        "/chat", json={"session_id": "s-speed-2", "message": "How fast is this?"}
    ).json()["metadata"]

    assert second["latency_ms"] < first["latency_ms"]


def test_a_different_message_never_comes_from_cache(client, provider):
    client.post("/chat", json={"session_id": "s-diff-1", "message": "Question one"})
    second = client.post(
        "/chat", json={"session_id": "s-diff-2", "message": "Question two"}
    ).json()

    assert second["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert len(provider.calls) == 2


def test_history_changes_the_cache_key(client, provider):
    """Turn two of a session carries different history, so it is a live call
    even though the same text was asked in a different position before."""
    client.post("/chat", json={"session_id": "s-hist", "message": "Hello"})
    second = client.post(
        "/chat", json={"session_id": "s-hist", "message": "Hello"}
    ).json()

    assert second["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert len(provider.calls) == 2


def test_a_different_system_prompt_changes_the_cache_key(client, provider):
    client.post("/chat", json={"session_id": "s-sp-1", "message": "Same text"})
    second = client.post(
        "/chat",
        json={
            "session_id": "s-sp-2",
            "message": "Same text",
            "system_prompt": "You are a pirate.",
        },
    ).json()

    assert second["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert len(provider.calls) == 2
