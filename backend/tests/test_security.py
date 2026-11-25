"""Guards against leaking key material."""

import logging

from app.config import ApiKey, Settings


def test_http_client_logging_is_muted():
    """httpx logs full request URLs at INFO, and some providers put the API key
    in the query string (Google AI Studio uses `?key=<secret>`). If that logger
    is ever turned back up, real keys end up in the platform's log viewer."""
    import app.main  # noqa: F401  (import applies the logging configuration)

    for name in ("httpx", "httpcore"):
        assert logging.getLogger(name).level >= logging.WARNING


def test_api_key_repr_masks_the_secret():
    key = ApiKey(key_id="key_1", secret="super-secret-value", model="groq/x")

    assert "super-secret-value" not in repr(key)
    assert "key_1" in repr(key)


def test_secrets_are_never_returned_to_the_client(client, provider):
    secret_bearing_endpoints = [
        client.get("/health"),
        client.post("/chat", json={"session_id": "s-leak", "message": "Leak check"}),
        client.get("/sessions/s-leak/messages"),
    ]

    for response in secret_bearing_endpoints:
        assert "test-secret-one" not in response.text
        assert "test-secret-two" not in response.text


def test_key_pool_ids_are_positional():
    settings = Settings(llm_api_keys=" first , second ,, third ")
    pool = settings.key_pool

    assert [k.key_id for k in pool] == ["key_1", "key_2", "key_3"]
    assert [k.secret for k in pool] == ["first", "second", "third"]


def test_sqlite_path_parsing():
    assert Settings(database_url="sqlite:////data/app.db").sqlite_path == "/data/app.db"
    assert Settings(database_url="sqlite:///./data/app.db").sqlite_path == "./data/app.db"
    assert Settings(database_url="/tmp/plain.db").sqlite_path == "/tmp/plain.db"


def test_cors_origins_parsing():
    assert Settings(allowed_origins="*").cors_origins == ["*"]
    assert Settings(allowed_origins="").cors_origins == ["*"]
    assert Settings(
        allowed_origins="https://a.vercel.app, http://localhost:3000"
    ).cors_origins == ["https://a.vercel.app", "http://localhost:3000"]
