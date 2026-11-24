# Demo video script (2–3 minutes, voice-over)

Record the deployed app. Keep this on a second screen:

```
BASE=https://<your-backend-url>
FRONT=https://<your-frontend-url>
```

## 0:00 – 0:20 Intro (show repo + live URLs)
"This is my LLM chatbot with key failover and response caching. React and
Vite frontend, FastAPI backend with a LiteLLM gateway, SQLite for session
history — both tiers are deployed live; links are in the README."

Show the repo structure briefly (backend/app/gateway.py is the interesting one).

## 0:20 – 0:50 Live inference, metadata, multi-turn
Open the frontend. Send: "What is the capital of France?"
Point at the reply metadata: LIVE_INFERENCE badge, model, latency in ms,
tokens in/out for that single request.
Follow-up: "What is its most famous landmark?" — "It remembers the context:
the backend stores history per session in SQLite and re-sends it with every
request, so input tokens grow."

## 0:50 – 1:20 Cache hits
Click **New chat** and resend the exact same first message.
"Same message, new session — the full message list is identical, so LiteLLM's
cache answers: LLM_CACHE, identical text, the stored token usage, and you can
see the latency dropped from hundreds of milliseconds to a few dozen."
Then send a different message in that session -> back to LIVE_INFERENCE
("a different message list can never come from cache").

## 1:20 – 2:05 Failover (X-Simulate-Failure)
Switch to a terminal/REST client against the backend URL:

```
curl -s -X POST $BASE/chat -H 'Content-Type: application/json' \
  -H 'X-Simulate-Failure: key_1=429' \
  -d '{"session_id":"video-1","message":"Tell me a fun fact about space."}'
```

"ENABLE_FAILURE_SIMULATION is true on the deployment, so this header makes
key_1 behave as if Google rate-limited it — only inside this request. The
gateway automatically fails over: answered by key_2, fallback_attempts is 1,
and the user gets a real answer, not an error."
Show `*=429` -> clean `503 {"detail":"all providers unavailable"}`, then
`GET /sessions/video-1/messages` -> 404: "nothing from failed requests is
stored". Back in the UI: failed-over replies show a "failed over ×1" chip.

## 2:05 – 2:35 Persistence + close
"Restarting the container..." (Render/Railway restart) "...and the history
endpoint still returns the session: SQLite lives on a Docker volume at
/data/app.db. The whole stack runs locally with `make up`. Everything is
tested — 44 backend tests including a stub LLM server that returns real 429s.
Thanks for watching."

## Tips
- Zoom the metadata chips when pointing at them (Ctrl/Cmd + to enlarge).
- Keep curl commands in a text editor to paste quickly.
- Speak while actions render; cut pauses in editing.
