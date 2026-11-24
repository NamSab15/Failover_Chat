# Deployment guide

Both tiers must be public and reachable without a login. The easiest pairing
that costs nothing: **frontend on Vercel** + **backend on Render or Railway**.

## 0. Push the repo

```bash
git init && git add -A && git commit -m "LLM chatbot with key failover and caching"
git remote add origin https://github.com/<you>/llm-chatbot.git
git push -u origin main
```

## 1. Get two LLM API keys

The pool needs at least 2 keys. With Gemini (free tier, no credit card):

1. Go to https://aistudio.google.com/ and create API key A (project 1).
2. Create a second project (gear icon -> project -> create) and create API
   key B there.
3. Keys must come from **two different projects** so rate limits are separate.

Keep the model default `gemini/gemini-3.5-flash-lite` (set in `LLM_MODEL`).
Any LiteLLM-supported provider works the same way (OpenAI, Groq, OpenRouter,
Anthropic — change `LLM_MODEL` accordingly, e.g. `groq/llama-3.3-70b-versatile`).

## 2. Deploy the backend

### Option A — Render (Docker, easiest)

1. Render dashboard -> **New -> Web Service** -> connect your repo.
2. **Root Directory:** `backend`, **Runtime:** Docker.
3. **Environment variables:**

   | Variable | Value |
   | --- | --- |
   | `LLM_API_KEYS` | `keyA,keyB` |
   | `LLM_MODEL` | `gemini/gemini-3.5-flash-lite` |
   | `DATABASE_URL` | `sqlite:////data/app.db` |
   | `ALLOWED_ORIGINS` | `http://localhost:3000,https://<your-vercel-app>.vercel.app` |
   | `ENABLE_FAILURE_SIMULATION` | `true` |

   (The Dockerfile binds to `$PORT`, which Render injects automatically.)
4. Deploy. Health check path: `/health`.
   > Free-tier Render instances have an ephemeral disk: SQLite survives process
   > restarts but not redeploys. For persistent history attach a paid disk at
   > `/data`, or use Railway/Fly with a volume (below).

### Option B — Railway (persistent volume)

1. New project -> Deploy from repo -> select the repo, service root `backend`
   (Railway detects the Dockerfile).
2. Add a **Volume** mounted at `/data`.
3. Set the same environment variables as above (Railway injects `$PORT`).
4. Generate a public domain for the service.

### Option C — Fly.io

```bash
fly launch --dockerfile backend/Dockerfile --name llm-chatbot-backend
fly volumes create appdata --size 1
fly deploy
```

Then add secrets: `fly secrets set LLM_API_KEYS="keyA,keyB" LLM_MODEL=... \
DATABASE_URL=sqlite:////data/app.db ALLOWED_ORIGINS=... ENABLE_FAILURE_SIMULATION=true`.
Mount the volume in `fly.toml`:

```toml
[mounts]
  source = "appdata"
  destination = "/data"
```

### Option D — Hugging Face Spaces

Create a Docker Space, add the backend folder contents, set the same env
variables as Space secrets, and make sure the app listens on port 7860
(set env `PORT=7860`).

## 3. Deploy the frontend

### Vercel

1. Import the repo. **Root Directory:** `frontend` (framework auto-detects Vite).
2. Environment variable: `VITE_API_BASE_URL = https://<your-backend-host>`
   (the Render/Railway/Fly URL from step 2, no trailing slash).
3. Deploy. Then set the backend's `ALLOWED_ORIGINS` to the final Vercel URL and
   redeploy the backend (CORS is an allowlist).

### Netlify / Cloudflare Pages

- Build command: `npm run build`, publish directory: `frontend/dist`
  (set base directory / root to `frontend`), env var `VITE_API_BASE_URL` as above.

## 4. Post-deploy checks

```bash
curl -s https://<backend>/health
curl -s -X POST https://<backend>/chat -H 'Content-Type: application/json' \
  -d '{"session_id":"prod-1","message":"What is the capital of France?"}'
curl -s -X POST https://<backend>/chat -H 'Content-Type: application/json' \
  -H 'X-Simulate-Failure: key_1=429' \
  -d '{"session_id":"prod-2","message":"What is the capital of France?"}'
```

1. Open the frontend, send a message -> `LIVE_INFERENCE`.
2. Press **New chat**, resend the same first message -> `LLM_CACHE` (faster).
3. Replay the last request with the `X-Simulate-Failure: key_1=429` header ->
   answered by `key_2` with a "failed over" chip.
4. Restart the backend service -> session history is still there (SQLite on the
   volume).

Fill the URLs into README section 1, then record the demo video
(`docs/demo-video-script.md`).
