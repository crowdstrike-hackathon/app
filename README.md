# Sentinel — Agentic Red vs Blue SOC Simulation (backend)

Charlotte-style defender AI, continuously pressure-tested by an adversary AI, at scale.

A simulation engine runs a **red agent** (adversary) against a **blue agent** (defender)
turn by turn over a fictional company's logs:

- **Red** picks the next MITRE ATT&CK technique and emits *synthetic log telemetry* —
  mixing real attack signal with **planted decoys** (spoofed sources, tampered timestamps,
  lures) and benign noise. It never touches real systems and never emits exploit code; it
  produces log lines only. In `live` mode it is driven by a local uncensored model on an
  A100 (OpenAI-compatible endpoint) — a real adversary, not a canned script.
- **Blue** (the product) ingests the stream *without* ground truth, reasons through the
  deception, reaches a verdict, and proposes **governed response actions** gated behind
  human-in-the-loop approval. In `live` mode it is Claude.

Every run is scored (detection speed, decoys seen through, real signal wrongly distrusted,
false positives), owned by an authenticated user, and persisted to Postgres.

## Stack

FastAPI · SQLAlchemy 2 (async) · Postgres (asyncpg) · JWT auth (bcrypt) · SSE streaming.

## Run

```bash
cd app/app
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

# Postgres (recommended):
docker compose up -d db          # starts postgres on :5432 matching .env

uvicorn app.main:app --reload    # http://localhost:8000
```

Tables are created automatically on startup. Without Docker, leave `DATABASE_URL` unset
to fall back to a local SQLite file (`sentinel.db`) so the app still boots for a demo.

`SIM_MODE=mock` (default) runs fully offline and deterministic. Flip to `SIM_MODE=live`
and fill in `ANTHROPIC_API_KEY` (blue) and `RED_MODEL_*` (red) to use real models.

## API

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/api/auth/register` | — | create account, returns JWT |
| POST | `/api/auth/login` | — | sign in, returns JWT |
| GET  | `/api/auth/me` | ✓ | current user |
| GET  | `/api/meta` | — | mode, providers, environment, scenarios |
| POST | `/api/sessions` | ✓ | start a scenario `{scenario_id, auto?, seed?}` |
| GET  | `/api/sessions/{id}/stream?token=` | ✓ | **SSE**: `red_turn`/`blue_turn`/`score`/`action_update`/`finished` |
| POST | `/api/sessions/{id}/step` | ✓ | advance one turn |
| POST | `/api/sessions/{id}/run` | ✓ | auto-play remaining turns |
| POST | `/api/sessions/{id}/actions/{id}/{approve\|deny\|execute}` | ✓ | human-in-the-loop gate |
| GET  | `/api/history` | ✓ | past runs for this user |
| GET  | `/api/stats` | ✓ | aggregate detection rate, avg score, avg detect turn |
| POST | `/api/ingest` | ✓ | blue-only analysis of uploaded logs `{logs:[...]}` |

The SSE endpoint authenticates via `?token=` because browsers' `EventSource` cannot send
headers; all other endpoints use the `Authorization: Bearer` header.

## Layout

```
app/
  main.py            FastAPI app, CORS, startup table creation
  config.py          pydantic-settings (mock/live, DB, JWT)
  db.py / orm.py     async engine + User/Run/Event/Investigation/Action tables
  auth.py            bcrypt hashing, JWT, get_current_user dependency
  models.py          pydantic schemas (LogEvent, Investigation, ResponseAction, Score)
  llm/provider.py    Claude (blue) + local OpenAI-compatible (red) behind one interface
  sim/               environment, ATT&CK scenarios, decoy/noise generators
  agents/            red_agent (emits telemetry), blue_agent (reasons -> verdict)
  engine/session.py  turn loop, SSE pub/sub, scoring, persistence, HITL state
  api/               auth_routes + routes
```

## Safety

Red output is log telemetry and narration only; the catalog contains ATT&CK-tagged
*descriptions* of observable activity, never runnable payloads. All response actions are
simulated and require explicit analyst approval. `mock` mode removes any dependency on a
live uncensored endpoint.
