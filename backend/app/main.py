"""FastAPI application exposing /health, /chat and /sessions/{id}/messages."""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

from fastapi import FastAPI, Header, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import Settings, get_settings
from .db import Database
from .llm_gateway import AllProvidersUnavailable, LLMGateway
from .schemas import (
    ChatMetadata,
    ChatRequest,
    ChatResponse,
    HealthResponse,
    HistoryResponse,
)
from .simulation import parse_simulate_header

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

# httpx logs every outbound request URL at INFO. Some providers pass the API
# key in the query string rather than a header - Google AI Studio uses
# `?key=<secret>` - so leaving this on writes real keys straight into the
# platform's log viewer. Groq uses a bearer header and is not affected, but the
# pool is provider-agnostic and the cost of being wrong here is a leaked key.
# Warnings and errors still come through.
for _noisy in ("httpx", "httpcore", "LiteLLM"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

logger = logging.getLogger("app")


def _serialisable_errors(errors: Any) -> list:
    """Strip Pydantic validation errors down to JSON-safe primitives.

    ``exc.errors()`` carries a ``ctx`` entry holding the original exception
    object whenever a custom field validator raises, and that cannot be
    serialised. Keeping only type/loc/msg (plus a scalar input) preserves
    FastAPI's default error shape without the unserialisable payload.
    """
    cleaned = []
    for error in errors or []:
        item: Dict[str, Any] = {
            "type": str(error.get("type", "value_error")),
            "loc": [str(part) for part in error.get("loc", ())],
            "msg": str(error.get("msg", "invalid value")),
        }
        received = error.get("input")
        if received is None or isinstance(received, (str, int, float, bool)):
            item["input"] = received
        cleaned.append(item)
    return cleaned


def build_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or get_settings()
    database = Database(settings.sqlite_path)
    gateway = LLMGateway(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database.init_schema()
        logger.info(
            "startup: db=%s keys=%d cache=%s simulation=%s",
            settings.sqlite_path,
            gateway.keys_configured,
            gateway.cache.kind,
            settings.enable_failure_simulation,
        )
        yield

    app = FastAPI(
        title="LLM Chatbot with Key Failover and Response Caching",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.state.settings = settings
    app.state.db = database
    app.state.gateway = gateway

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------- error handlers
    # Every error leaves as JSON with a `detail` field - never a stack trace,
    # never an HTML page.
    @app.exception_handler(RequestValidationError)
    async def _validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": _serialisable_errors(exc.errors())},
        )

    @app.exception_handler(Exception)
    async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error on %s", request.url.path)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "internal server error"},
        )

    # -------------------------------------------------------------- routes
    @app.get("/health", response_model=HealthResponse)
    async def health(response: Response) -> Dict[str, Any]:
        db_ok = database.healthy()
        keys = gateway.keys_configured
        ok = db_ok and keys > 0
        if not ok:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "ok" if ok else "degraded",
            "keys_configured": keys,
            "cache_enabled": gateway.cache_enabled,
        }

    @app.post("/chat", response_model=ChatResponse)
    async def chat(
        payload: ChatRequest,
        x_simulate_failure: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        started = time.perf_counter()

        session_id = payload.session_id.strip()
        user_message = payload.message.strip()

        # A per-session system prompt: set it once and it keeps applying.
        if payload.system_prompt:
            database.set_system_prompt(session_id, payload.system_prompt)
            system_prompt = payload.system_prompt
        else:
            system_prompt = (
                database.get_system_prompt(session_id) or settings.default_system_prompt
            )

        history = database.get_history(session_id)
        messages = gateway.build_messages(system_prompt, history, user_message)

        simulate: Dict[str, int] = {}
        if settings.enable_failure_simulation:
            simulate = parse_simulate_header(x_simulate_failure)
        elif x_simulate_failure:
            logger.info("ignoring X-Simulate-Failure: ENABLE_FAILURE_SIMULATION is off")

        try:
            result = await gateway.generate(messages, simulate=simulate)
        except AllProvidersUnavailable as exc:
            logger.warning(
                "all providers unavailable after %d attempt(s)", len(exc.attempts)
            )
            # Nothing is written to history for a failed request.
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"detail": "all providers unavailable"},
            )

        latency_ms = int(round((time.perf_counter() - started) * 1000))

        metadata = ChatMetadata(
            resolution_source=result.resolution_source,
            model=result.model,
            latency_ms=latency_ms,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            key_id=result.key_id,
            fallback_attempts=result.fallback_attempts,
        )

        database.append_turn(
            session_id=session_id,
            user_message=user_message,
            assistant_message=result.reply,
            assistant_metadata=metadata.model_dump(),
        )

        return {
            "session_id": session_id,
            "reply": result.reply,
            "metadata": metadata.model_dump(),
        }

    @app.get(
        "/sessions/{session_id}/messages",
        response_model=HistoryResponse,
        response_model_exclude_none=True,
    )
    async def session_messages(session_id: str) -> Any:
        session_id = session_id.strip()
        if not session_id or not database.session_exists(session_id):
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"detail": "session not found"},
            )
        return {"session_id": session_id, "messages": database.get_history(session_id)}

    return app


app = build_app()
