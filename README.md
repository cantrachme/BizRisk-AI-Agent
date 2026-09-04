# BizRisk AI Agent

Graph-based multi-agent business due-diligence platform. It takes partial
information about a legal entity, researches it across public sources with a
browser-based research agent, resolves the entity's identity, detects
cross-source inconsistencies, computes an explainable **deterministic** risk
score, and produces an evidence-backed report that a QA agent validates before
release.

```
Intake → Discovery → Planner → Browser Research → Evidence Store
      → Entity Resolution → Risk Engine → Report → QA
```

## Stack

| Layer | Tech |
|---|---|
| Backend | FastAPI, LangGraph, SQLAlchemy, Alembic |
| Browser research | direct HTTP + Playwright (Chromium) fallback |
| Risk engine | deterministic rules (`backend/app/risk/config.yaml`) |
| LLM | pluggable provider abstraction — `mock` (default) or `anthropic` |
| DB / cache | PostgreSQL 16, Redis 7 |
| Frontend | Next.js 16, React 19 |

## Prerequisites

- Python 3.13, Node.js 20+
- Docker (for PostgreSQL + Redis) — `docker compose up -d`

## Backend setup

```bash
# from the repo root
docker compose up -d                       # starts postgres:5432 and redis:6379

cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium      # for live browser research
cp .env.example .env                        # then edit as needed

.venv/bin/alembic upgrade head              # create the schema (uses DATABASE_URL)
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Health check: `curl http://127.0.0.1:8000/health` → `{"status":"healthy",...}`.
OpenAPI docs: `http://127.0.0.1:8000/docs`.

## Frontend setup

```bash
cd frontend
npm install
# frontend/.env.local already points at http://127.0.0.1:8000/api/v1
npm run dev            # http://localhost:3000
```

Log in with any identifier string — it becomes your bearer token / user id
(see **Authentication** below).

## Configuration (`backend/.env`)

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | local postgres | `postgres://` and `postgresql://` are auto-normalised |
| `REDIS_URL` | local redis | |
| `LLM_PROVIDER` | `mock` | `mock` (deterministic, offline, used by tests) or `anthropic` |
| `LLM_ANTHROPIC_MODEL` | `claude-opus-5` | model used when `LLM_PROVIDER=anthropic` |
| `ANTHROPIC_API_KEY` | — | read from the environment only, never stored in Settings |
| `ENTITY_RESOLUTION_THRESHOLD` | `0.75` | 0.0–1.0 acceptance threshold; docs recommend `0.85` for production |
| `MAX_QA_LOOPS` | `2` | QA → planner correction loops before release |
| `MAX_RESEARCH_DEPTH` / `MAX_BROWSER_ACTIONS` / `MAX_RESEARCH_TASKS` / `MAX_LLM_CALLS` / `TOKEN_BUDGET` | `3` / `20` / `15` / `50` / `100000` | cost/loop guardrails |
| `PLAYWRIGHT_HEADLESS` | `true` | headed Chromium for CAPTCHA HITL when `false` |
| `ENABLE_TEST_ENDPOINTS` | `true` | gates the unauthenticated `/api/v1/test/*` endpoints — **must be `false` in production** (enforced when `ENVIRONMENT=production`) |
| `CORS_ORIGINS` | localhost:3000 | JSON list or comma-separated |

Risk weights and levels are configured in `backend/app/risk/config.yaml`.

## Authentication

Endpoints require a bearer token and enforce per-user investigation isolation
(`get_owned_investigation`; covered by `test_security_user_isolation.py`). The
token is currently an **opaque identifier used directly as the user id** — there
is no login/JWT flow yet. This is a deliberate MVP simplification; the
authorization model (auth required, user-scoped access, no cross-user reads) is
in place. A production deployment should place a real identity provider /
JWT-verifying gateway in front of `get_current_user_id`.

## LLM

`app/core/llm.py` exposes `get_llm_provider()` returning a `BaseLLMProvider`.
`mock` is fully deterministic and is the default (all tests run on it). Set
`LLM_PROVIDER=anthropic` + `ANTHROPIC_API_KEY` to enable the real provider
(`AnthropicLLMProvider`, official SDK, strict structured output, timeout +
graceful failure). The LLM is used only for candidate reasoning, cross-source
consistency narrative, report narrative, and advisory QA notes — **it never
determines the numeric risk score or the QA PASS/FAIL verdict**, which stay in
the deterministic engine.

## Tests

```bash
# backend — run from the repo root (test modules import `backend.tests.*`)
backend/.venv/bin/python -m pytest backend/tests -q

# frontend
cd frontend && npm test && npm run build
```

## Known limitations

- **Background jobs run in-process** (FastAPI `BackgroundTasks`; `WORKER_MODE`
  and the `rq` dependency are unused). If the API process restarts mid-run an
  investigation stays at a non-terminal status; recover it with
  `POST /api/v1/investigations/{id}/resume` (or list them via
  `GET /api/v1/investigations/incomplete`). There is no automatic
  crash-recovery sweep.
- **Live government-portal research** (GST/MCA/EPFO) is HTTP + regex extraction;
  against real portals it often yields `SOURCE_UNAVAILABLE` / `NOT_FOUND`
  because those sites require JS/search forms/CAPTCHA. Third-party directories
  and company websites are the more reliable live sources.
- The modular research-provider layer (`app/research/{gst,mca,epfo,
  company_website,generic_web}.py`, `ResearchDispatcher`) is unit-tested but not
  wired into the live path; `BrowserResearchAgent` does inline extraction.
