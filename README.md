# LLM Chatbot with Key Failover and Response Caching

A multi-turn chat app built on a LiteLLM gateway. It keeps answering when an
API key rate-limits or dies, serves repeat questions from cache, and reports
exactly where every reply came from — source, model, latency and per-request
token usage — next to the reply itself.

React + Vite · FastAPI · LiteLLM · SQLite · Docker

---

## 1. Live URLs

| Tier | URL |
| --- | --- |
| Frontend | `https://<your-app>.vercel.app` |
| Backend | `https://<your-backend>.onrender.com` |
| Health check | `https://<your-backend>.onrender.com/health` |

> Fill these in after deploying. See [Deploying](#deploying) for the steps.
> The backend runs on Render's free plan, which sleeps after ~15 minutes of
> inactivity — the first request after a nap can take 30–50 seconds.

---

## 2. Run locally

### With Docker

```bash
cp .env.example .env     # then put 2 real keys in LLM_API_KEYS
make up
```

- Backend → http://localhost:8000 (`/health`, `/docs`)
- Frontend → http://localhost:3000

`make down` stops it. `make clean` also deletes the SQLite volume.
`make help` lists every target.

SQLite lives on the named volume `lyftr-app-data`, mounted at `/data`, so
history survives `make down && make up`.

### Without Docker

Two terminals.

**Backend**

```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt

export LLM_API_KEYS="key-one,key-two"
export DATABASE_URL="sqlite:///./data/app.db"
export ALLOWED_ORIGINS="http://localhost:3000"
export ENABLE_FAILURE_SIMULATION=true
export CACHE_DIR="./data/llm_cache"

uvicorn app.main:app --reload --port 8000
```

**Frontend**

```bash
cd frontend
npm install
echo "VITE_API_BASE_URL=http://localhost:8000" > .env.local
npm run dev
```

**Tests**

```bash
cd backend && python -m pytest        # or: make test
```

The suite stubs `litellm.acompletion`, so it needs no API keys and makes no
network calls.

---

## 3. Design decisions

### Key fallback

The key pool comes from `LLM_API_KEYS` as a comma-separated list. Position
assigns the public id: the first key is `key_1`, the second `key_2`. Only the
id is ever returned to the client; the secret never leaves the process (and
`ApiKey.__repr__` masks it so it cannot leak into a log line).

`LLMGateway.generate` walks the pool **in order**. A key that raises for any
reason — 429, quota exhaustion, 5xx, timeout, bad credentials, or an empty
completion — is recorded and the next key is tried. The reply reports
`key_id` (who answered) and `fallback_attempts` (how many failed first). Only
when the whole pool is exhausted does the request become a `503` with
`{"detail": "all providers unavailable"}`.

**Why an explicit loop instead of `litellm.Router`.** Router is the obvious
reach for this, and it gives you cooldowns and load balancing for free. But its
routing strategies are round-robin, least-busy or weighted-shuffle — none of
them guarantee a *deterministic* order. Section 4 of the spec requires exactly
that: with `X-Simulate-Failure: key_1=429`, the answer must come from `key_2`.
An ordered loop over `litellm.acompletion` makes that a property of the code
rather than a probability, and it also makes `fallback_attempts` exact. LiteLLM
still owns every call, the provider abstraction, the usage accounting and the
cache; what sits on top is only the ordering policy.

Failing over on *any* provider error, not just the three named statuses, is
deliberate. The requirement is that the user never sees a broken screen, and a
revoked key failing with 401 breaks a screen exactly as thoroughly as a 429.

### Caching

LiteLLM's built-in cache backend does the storing — `disk` by default (under
`CACHE_DIR`, which lives on the `/data` volume, so it outlives a restart) and
`local` in-memory as a fallback when the directory is not writable.

The cache key is computed explicitly rather than left to LiteLLM's default
derivation:

```
sha256(model + the full message list sent to the LLM)
```

Two reasons. First, the contract pins that definition down, and LiteLLM's
default key also folds in call parameters — including the API key, which would
give `key_1` and `key_2` separate caches and make a hit depend on which key
answered. Second, driving `get`/`set` directly lets the cached entry carry the
*original* token usage, so a cache hit reports the usage that was actually
billed instead of a recount or a zero.

Because the key includes the full message list, the behaviour follows for free:
the same opening message in a brand-new session is a hit, the same text at
turn 3 is a miss (different history), and a different system prompt is a miss.
A hit returns byte-identical reply text with `key_id: null` and
`fallback_attempts: 0`, and comes back in single-digit milliseconds against
hundreds for a live call.

One deliberate exception: a request carrying `X-Simulate-Failure` **bypasses the
cache**. The point of the hook is to watch failover happen; a cache hit would
short-circuit the pool and report `key_id: null`, which is precisely what the
hook exists to demonstrate.

### SQLite session storage

Stdlib `sqlite3`, no ORM. The access pattern is a handful of small reads and
writes per request, so SQLAlchemy would add a dependency and an abstraction
layer without buying anything here.

A fresh connection is opened per operation rather than shared. That keeps the
code thread-safe under FastAPI's worker threads without a lock, and SQLite
handles it well; WAL mode keeps readers from blocking the writer.

The schema is created on startup and is idempotent (`CREATE TABLE IF NOT
EXISTS`), so a fresh volume bootstraps itself. Beyond the required `messages`
table there is an index on `(session_id, id)` for the history read, and a small
`sessions` table holding the per-session system prompt — that way a prompt set
once keeps applying to later turns without the client resending it.

**Both rows of a turn are written in one transaction, and only after the model
has answered.** A request that fails validation (422) or exhausts the pool
(503) leaves nothing behind, so a failed send never corrupts the conversation
the next request will replay.

### Error handling

Every error leaves as JSON with a `detail` field — a `RequestValidationError`
handler for 422s and a catch-all handler for anything unexpected. No stack
trace and no HTML error page can reach the client. On the frontend, errors
render as a readable bubble in the chat rather than a blank screen, and they
are deliberately *not* tagged `data-testid="assistant-message"`, since they
carry no per-request metadata.

---

## 4. Verification

With the stack running, `BASE=http://localhost:8000` (or your Render URL).

### Health

```bash
curl $BASE/health
# {"status":"ok","keys_configured":2,"cache_enabled":true}
```

### Cache hits

Send the same first message in two different sessions:

```bash
curl -s -X POST $BASE/chat -H 'Content-Type: application/json' \
  -d '{"session_id":"s-cache-1","message":"What is the capital of France?"}' | jq .metadata

curl -s -X POST $BASE/chat -H 'Content-Type: application/json' \
  -d '{"session_id":"s-cache-2","message":"What is the capital of France?"}' | jq .metadata
```

First → `"resolution_source": "LIVE_INFERENCE"`, `"key_id": "key_1"`, latency in
the hundreds of ms.
Second → `"resolution_source": "LLM_CACHE"`, `"key_id": null`, latency in single
digits, identical `reply` text.

In the UI, the badge beside the reply flips from blue `LIVE_INFERENCE` to green
`LLM_CACHE`.

### Failover

Requires `ENABLE_FAILURE_SIMULATION=true` (it is set on the deployed backend).

```bash
# key_1 rate-limited -> key_2 answers
curl -s -X POST $BASE/chat -H 'Content-Type: application/json' \
  -H 'X-Simulate-Failure: key_1=429' \
  -d '{"session_id":"s-fo","message":"Who answered this?"}' | jq .metadata
# "key_id": "key_2", "fallback_attempts": 1

# key_1 returns a 500 -> key_2 still answers
curl -s -X POST $BASE/chat -H 'Content-Type: application/json' \
  -H 'X-Simulate-Failure: key_1=500' \
  -d '{"session_id":"s-fo","message":"And now?"}' | jq .metadata

# every key down -> clean 503, nothing written to history
curl -i -s -X POST $BASE/chat -H 'Content-Type: application/json' \
  -H 'X-Simulate-Failure: *=429' \
  -d '{"session_id":"s-down","message":"Everything is on fire"}'
# HTTP/1.1 503 ... {"detail":"all providers unavailable"}
```

In the UI, the **Simulate failure** dropdown sends the same header — pick
`key_1=429` and watch the `key` field in the metadata row change to `key_2`
with a `fallbacks 1` badge.

### Per-request token usage

Every reply carries `input_tokens` and `output_tokens` for **that request
only**, shown under the reply in the UI and returned in `metadata`. Two
equal-length opening messages in two new sessions report near-identical
`input_tokens`; `output_tokens` describes only the reply just produced, never a
running total.

### Multi-turn state and history

```bash
curl -s -X POST $BASE/chat -H 'Content-Type: application/json' \
  -d '{"session_id":"s-mem","message":"My name is Ada."}' | jq -r .reply
curl -s -X POST $BASE/chat -H 'Content-Type: application/json' \
  -d '{"session_id":"s-mem","message":"What is my name?"}' | jq -r .reply
# -> the model recalls "Ada"

curl -s $BASE/sessions/s-mem/messages | jq
curl -i -s $BASE/sessions/s-nope/messages   # 404 {"detail":"session not found"}
```

### Validation

```bash
curl -i -s -X POST $BASE/chat -H 'Content-Type: application/json' \
  -d '{"session_id":"s-1","message":"   "}'      # 422
curl -i -s -X POST $BASE/chat -H 'Content-Type: application/json' \
  -d '{not json'                                  # 422
```

### Automated

```bash
cd backend && python -m pytest -v
```

Covers the contract end to end: response shape, 422 cases, ordered failover for
429/500/502/503 (simulated *and* real), the clean 503, cache hit/miss semantics
including history and system-prompt sensitivity, cache-hit latency, per-request
token accounting, multi-turn recall, session isolation, 404s, and that failed
requests write nothing to history.

---

## Deploying

### Backend → Render

1. Push this repo to GitHub.
2. Render → **New → Web Service** → connect the repo.
3. Root directory `backend`, runtime **Docker**, health check path `/health`.
4. Environment variables:
   | Key | Value |
   | --- | --- |
   | `LLM_API_KEYS` | your two real keys, comma-separated |
   | `LLM_MODEL` | `gemini/gemini-2.0-flash` |
   | `DATABASE_URL` | `sqlite:////data/app.db` |
   | `ALLOWED_ORIGINS` | your Vercel URL (add it after step 2 below) |
   | `ENABLE_FAILURE_SIMULATION` | `true` |
   | `CACHE_TYPE` | `disk` |
   | `CACHE_DIR` | `/data/llm_cache` |
5. Deploy, then confirm `/health` returns `keys_configured: 2`.

`render.yaml` at the repo root does the same thing as a Blueprint.

### Frontend → Vercel

1. Vercel → **Add New → Project** → same repo.
2. Root directory `frontend` (framework auto-detects as Vite).
3. Environment variable `VITE_API_BASE_URL` = your Render URL, no trailing
   slash.
4. Deploy, then go back and set `ALLOWED_ORIGINS` on Render to the Vercel URL
   and redeploy the backend.

> `VITE_API_BASE_URL` is inlined at build time, so changing it needs a redeploy,
> not just a restart.

---

## 5. Setup used

- **Editor:** Visual Studio Code
- **AI tooling:** Claude Code (Opus) in the VS Code extension — used for
  scaffolding the FastAPI and React layers, the Docker setup and the test
  suite. The LiteLLM Router-vs-explicit-loop call, the cache-key definition,
  the cache bypass under simulation, and the write-after-success transaction
  boundary were reasoned through against the contract rather than accepted as
  generated.
- **Model provider:** Google AI Studio (Gemini), two keys from two projects.
- **Docs:** LiteLLM caching and exception-handling docs, FastAPI exception
  handlers, Render Docker + Vercel Vite deployment guides.

---

## 6. Bonus features

| Feature | Why |
| --- | --- |
| **Failover dropdown in the UI** | Sends `X-Simulate-Failure` straight from the browser, so failover is demonstrable live in the demo video without curl. |
| **`key_id` and `fallback_attempts` shown per reply** | Makes "which key answered, and how many died first" visible at a glance instead of only in the JSON. |
| **Health strip in the header** | Live `keys_configured` / `cache_enabled` / backend status, so a misconfigured deploy is obvious immediately. |
| **Per-session system prompt** | Stored in a `sessions` table, so it keeps applying to later turns without the client resending it on every request. |
| **Persistent disk cache** | `CACHE_DIR` on the `/data` volume, so cache hits survive a container restart, not just a process lifetime. |
| **Mixed-model key pools** | `LLM_MODELS` optionally assigns a different model per key, so the pool can span providers (e.g. Gemini → Groq) rather than just keys. |
| **Secret-safe logging** | `ApiKey.__repr__` masks the secret, so no accidental `print`/traceback can leak a key. |
| **`Shift+Enter` for newlines, auto-scroll, typing indicator** | Small chat-UX details that make the app feel finished. |
| **Container healthchecks + non-root images** | Both Dockerfiles are multi-stage, run as a non-root user and declare `HEALTHCHECK`. |

---

## Repository layout

```
backend/
  app/
    config.py         env-driven settings, key pool, path parsing
    db.py             SQLite schema, history, transactional turn writes
    llm_gateway.py    LiteLLM calls, ordered failover, response cache
    schemas.py        request/response models (the API contract)
    simulation.py     X-Simulate-Failure parser
    main.py           FastAPI app, routes, error handlers, CORS
  tests/              pytest suite, no network required
  Dockerfile          multi-stage, non-root
frontend/
  src/
    App.jsx           session lifecycle, send flow, error surfacing
    api.js            backend client, readable error messages
    components/       MessageList (metadata row), Composer
  Dockerfile          multi-stage node build -> nginx
docker-compose.yml    both services + named volume at /data
Makefile              up, down, build, logs, test, clean
.env.example
render.yaml
```
