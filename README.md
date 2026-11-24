# LLM Chatbot with Key Failover and Response Caching

A full-stack AI chatbot: **React (Vite + TypeScript)** frontend, **FastAPI** backend,
**SQLite** storage, and a **LiteLLM** gateway with an API key pool, automatic
failover and response caching.

- Every reply is labeled `LIVE_INFERENCE` or `LLM_CACHE`, with the model name,
  server-measured latency and per-request token usage shown in the UI.
- If a key is rate-limited (429), quota-exhausted or returns a 5xx, the backend
  automatically fails over to the next key in the pool. Only if **every** key
  fails does the user see a clean `503 {"detail": "all providers unavailable"}`.
- Conversation history is stored per session in SQLite (Docker volume), so the
  bot remembers earlier messages and history survives container restarts.

## 1. Live URLs

| Tier | URL |
| --- | --- |
| Frontend (React, Vercel) | https://failoverchat.vercel.app |
| Backend (FastAPI, Render) | https://failover-chat.onrender.com |
| Health check | https://failover-chat.onrender.com/health |

> The backend runs on Render's free plan, which sleeps after ~15 minutes idle;
> the first request after that can take ~50 seconds while it wakes up. Chat
> history survives restarts but is reset on redeploys (no persistent disk on
> the free plan). See `docs/deployment.md` for the deployment steps.

> The deployed backend is started with `ENABLE_FAILURE_SIMULATION=true`, so the
> `X-Simulate-Failure` failover hook is testable against the live URL without
> burning real keys.

## 2. Run locally

### With Docker (recommended)

```bash
cp .env.example .env
# edit .env: put at least 2 real API keys in LLM_API_KEYS
make up
```

- Frontend: http://localhost:3000
- Backend: http://localhost:8000 (health: http://localhost:8000/health)
- Stop: `make down` (add `-v` / `make clean` to also wipe chat history)

The SQLite file lives at `/data/app.db` inside a named Docker volume
(`sqlite-data`), so history survives `make down` / `make up` and container
restarts. The schema (`messages`, `sessions` tables) is created automatically
on startup.

### Without Docker

Backend (Python 3.12):

```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt   # runtime + test deps
export LLM_API_KEYS="key-one,key-two"               # at least 2 keys
export DATABASE_URL="sqlite:///./app.db"            # local file, not /data
uvicorn app.main:app --reload --port 8000
```

Frontend (Node 20+):

```bash
cd frontend
npm install
VITE_API_BASE_URL=http://localhost:8000 npm run dev   # http://localhost:3000
```

### Tests

```bash
cd backend && python -m pytest        # or: make test
```

45 tests cover the API contract, validation, failover (real HTTP 429/5xx via an
OpenAI-compatible stub server), cache labeling/latency, multi-turn context,
token accounting, history persistence across restarts and the
`X-Simulate-Failure` hook. No real API keys are needed to run them.

## 3. Design decisions

### Key fallback

- `LLM_API_KEYS` is parsed into an ordered pool (`key_1`, `key_2`, ...). Each
  attempt is a real `litellm.acompletion(..., api_key=key_i)` call, so LiteLLM
  handles provider auth, params and errors uniformly across providers.
- `gateway.complete()` walks the pool in order. On **any** provider failure
  (429, quota, 5xx, timeout, connection, auth) it immediately tries the next
  key and counts `fallback_attempts`. First success wins; the answering key's
  id is returned as `key_id` (never the key itself).
- **Cooldown:** after a *real* retryable failure a key is benched for
  `KEY_COOLDOWN_SECONDS` (default 30), so following requests don't waste a call
  on a dead key. If every key is benched, all are retried anyway. Simulated
  failures never trigger cooldowns, so demos stay deterministic.
- **Fail-fast:** `num_retries=0` per attempt — retrying *inside* a dead key
  would add latency without improving resilience; failover across keys is the
  recovery strategy.
- Errors are classified via the exception's HTTP status (`litellm` exceptions
  expose `status_code`); unknown failures are treated as retryable rather than
  crashing the request.

### Caching

- LiteLLM's built-in cache is enabled globally
  (`litellm.cache = Cache(type="local")`, in-memory) and each call passes
  `caching=True`. The cache key is derived from **model + the full message list
  sent to the LLM** (system prompt + history + new message) — exactly the spec's
  rule. LiteLLM does not include credentials in the cache key, so the same
  conversation text always hits the same entry regardless of which key serves
  it (verified by tests).
- A hit returns the identical stored reply **and the usage stored with it**;
  the backend then reports `resolution_source="LLM_CACHE"`, `key_id=null`,
  `fallback_attempts=0`. Latency is measured per request at the route level, so
  cache hits (single dict lookup, ~1–50 ms) are visibly faster than live calls.
- A different message (or different history — e.g. the same text asked again
  inside the same session) produces a different cache key and can never come
  from the cache.
- Requests carrying `X-Simulate-Failure` bypass the cache entirely
  (`caching=False`): the hook exists to demonstrate *live* failover, and a
  cache short-circuit would hide it (and would make `*=429` return a cached
  200 instead of the specified 503).

### SQLite session storage

- Exactly the required `messages` table (plus a `sessions` table for the
  per-session system prompt and an index on `(session_id, id)`), created
  automatically on startup. WAL journaling + `busy_timeout` keep concurrent
  requests safe on a single file.
- The client sends only `session_id` + new message; the backend reassembles
  `system + history + new message` from SQLite for every call. Assistant rows
  store their metadata JSON; user rows don't.
- A turn is written **in one transaction, only after a successful completion**.
  Failed requests (422/503) leave no trace, and a session that never had a
  successful turn stays "unknown" (`404` on the history endpoint).
- `DATABASE_URL` points at `/data/app.db` (Docker volume) so history survives
  restarts; no Redis, no Postgres, no external services.

### API contract & safety

- Exactly three routes (`GET /health`, `POST /chat`,
  `GET /sessions/{session_id}/messages`) with the required fields — extra
  fields are added nowhere except `health`.
- Every error is JSON with a `detail` field. A global exception handler turns
  anything unhandled into `500 {"detail": "internal server error"}` — stack
  traces never reach the client.
- CORS is an allowlist (`ALLOWED_ORIGINS`); secrets (real keys) never leave the
  backend, appear in logs, or in any response; the container runs as a
  non-root user.

## 4. Verification

All commands below assume the backend is reachable at `$BASE` (e.g.
`BASE=http://localhost:8000`, or your deployed URL).

**Health & key pool**

```bash
curl -s $BASE/health
# {"status":"ok","keys_configured":2,"cache_enabled":true,"model":"...","failure_simulation":true}
```

**Live inference, metadata and per-request tokens**

```bash
curl -s -X POST $BASE/chat -H 'Content-Type: application/json' \
  -d '{"session_id":"demo-1","message":"What is the capital of France?"}'
# metadata.resolution_source = "LIVE_INFERENCE", key_id = "key_1",
# input_tokens/output_tokens are for THIS request only
```

**Cache hit** — send the same first message in a *new* session:

```bash
curl -s -X POST $BASE/chat -H 'Content-Type: application/json' \
  -d '{"session_id":"demo-2","message":"What is the capital of France?"}'
# metadata.resolution_source = "LLM_CACHE", key_id = null, fallback_attempts = 0,
# identical reply text, same stored usage, latency_ms much smaller
```

**Failover** — make key_1 fail inside one request:

```bash
curl -s -X POST $BASE/chat -H 'Content-Type: application/json' \
  -H 'X-Simulate-Failure: key_1=429' \
  -d '{"session_id":"demo-3","message":"Tell me a fun fact about space."}'
# 200, metadata.key_id = "key_2", fallback_attempts = 1, LIVE_INFERENCE
```

**Every key failed** — clean 503, nothing stored:

```bash
curl -s -X POST $BASE/chat -H 'Content-Type: application/json' \
  -H 'X-Simulate-Failure: *=429' \
  -d '{"session_id":"demo-4","message":"hello"}'
# 503 {"detail": "all providers unavailable"}
curl -s -i $BASE/sessions/demo-4/messages
# 404 {"detail": "session not found"}   <- nothing was added to history
```

**Multi-turn memory** — ask a follow-up; `input_tokens` grows because the
backend re-sends system + history + new message:

```bash
curl -s -X POST $BASE/chat -H 'Content-Type: application/json' \
  -d '{"session_id":"demo-1","message":"What is its most famous landmark?"}'
curl -s $BASE/sessions/demo-1/messages    # full stored history, oldest first
```

**Validation** — `422` with `{"detail": ...}` for empty/oversized messages,
empty `session_id`, malformed JSON:

```bash
curl -s -X POST $BASE/chat -H 'Content-Type: application/json' \
  -d '{"session_id":"demo-5","message":"   "}'
```

In the UI, every assistant reply has a metadata line (`LIVE_INFERENCE` /
`LLM_CACHE` tag, model, latency, tokens in/out, and the answering key when a
failover happened). Clicking it opens the reply in the **Request** panel on the
right, which also shows the **Key pool** (answered / failed / skipped for the
cache). The **Failure simulation** menu in that panel sends `X-Simulate-Failure`
(`key_1=429`, `key_1=500`, `key_1=429,key_2=500`, `*=429`) with each message
while it is on. The same header can also be sent from the browser console:

```js
fetch(`${API}/chat`, {method:"POST", headers:{"Content-Type":"application/json",
  "X-Simulate-Failure":"key_1=429"},
  body: JSON.stringify({session_id: "browser-demo", message: "hi"})}).then(r=>r.json()).then(console.log)
```

## 5. Setup Used

- **Editor:** VS Code (Python + ESLint/Prettier extensions), terminal-driven
  workflow.
- **AI tools:** built with Claude Code (Anthropic, Claude Opus 5.5) in the VS
  Code extension — used for scaffolding, the UI redesign, test generation and
  code review. LiteLLM behavior was verified empirically before wiring the
  gateway (cache-hit flag, cache-key composition, exception classes). The
  stack was then checked end to end in Docker against real Gemini keys with a
  35-check contract script (caching, failover via `X-Simulate-Failure`,
  validation, history, CORS, persistence across restarts).
- **Model:** `gemini/gemini-3.5-flash-lite` through LiteLLM, with two keys in
  the pool.

## 6. Bonus features

- **Per-session system prompt UI** (`system-prompt-input`): override the
  persona; the override persists for the session (stored in `sessions`), so it
  applies to follow-ups without re-sending it.
- **Request inspector:** a side panel with the selected reply's full metadata
  and the key pool's state for that request (answered / failed / cache), plus
  live backend status from `/health` in the top bar.
- **Failure simulation in the UI:** `X-Simulate-Failure` presets, so failover
  and the all-keys-down 503 can be shown without curl.
- **"New chat" button:** makes session separation visible (a
  new `session_id` is generated per page load, as the contract requires) and
  lets you demo cache hits (new session + same first message) in one click.
- **Key cooldown across requests:** keys that genuinely fail (429/5xx) are
  benched for `KEY_COOLDOWN_SECONDS` so healthy keys answer first — real
  resilience beyond per-request fallback.
- **Failure simulation bypasses the cache** (explained above) so failover demos
  are deterministic even for previously-asked questions.
- **45-test backend suite** that exercises the real LiteLLM path against a
  local OpenAI-compatible stub server (real 429/5xx responses, real cache
  read/write) — no API keys required in CI.
- **CI workflow** (`.github/workflows/tests.yml`) running the backend suite on
  every push.
- **Ops extras:** Docker healthchecks, `$PORT` binding for PaaS deploys,
  non-root container user, `docs/deployment.md` (Render/Railway/Fly/Vercel)
  and `docs/demo-video-script.md` (2–3 minute demo script).

## Repository structure

```
backend/
  app/
    main.py      # FastAPI app factory, CORS, JSON error handlers
    config.py    # env-driven settings (pydantic-settings)
    gateway.py   # LiteLLM key pool, failover, cache, X-Simulate-Failure
    db.py        # SQLite schema + history/sessions persistence
    routes.py    # /health, /chat, /sessions/{id}/messages
    schemas.py   # request/response models (exact API contract)
  tests/         # 45 pytest tests incl. OpenAI-compatible stub LLM
  Dockerfile     # multi-stage, non-root, healthcheck
frontend/
  src/           # React (Vite + TS) chat UI with data-testid contract
  Dockerfile     # multi-stage: node build -> nginx:alpine
  nginx.conf     # SPA fallback on :3000
docker-compose.yml   # backend + frontend + named volume at /data
Makefile             # up, down (plus logs/ps/test/build/clean/dev targets)
.env.example         # every variable with placeholder values
```
