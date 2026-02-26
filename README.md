# Orchestration Engine

AI-powered task orchestration system that takes natural language requirements, generates structured plans via Claude, decomposes them into a prioritized dependency graph, and executes tasks in parallel with budget controls and real-time progress streaming.

## How It Works

```
Requirements → Claude Sonnet → Plan JSON → User Approval → Task DAG
                                                               ↓
                                         ┌─────── Executor Loop ────────┐
                                         │ 1. Check budget limits       │
                                         │ 2. Find ready tasks (deps met)│
                                         │ 3. Check resource health     │
                                         │ 4. Route to model tier       │
                                         │ 5. Call Claude/Ollama + tools│
                                         │ 6. Record usage + cost       │
                                         │ 7. Store output, emit SSE    │
                                         └──────────────────────────────┘
```

1. **Plan** — Describe what you want built. Claude generates a structured plan with tasks, dependencies, and tool assignments.
2. **Review** — Inspect the plan, adjust model tiers or task descriptions, then approve.
3. **Execute** — The async executor dispatches tasks respecting the dependency DAG, budget limits, and resource availability.
4. **Monitor** — Watch progress in real-time via SSE events. Pause, resume, or cancel at any point.

## Key Features

- **Multi-model routing** — Ollama (free) for research/analysis, Haiku ($) for medium tasks, Sonnet ($$) for complex work
- **Budget enforcement** — Daily, monthly, and per-project spending limits with TOCTOU-safe reservation system
- **Tool system** — RAG search, FTS lookup, local LLM, ComfyUI image generation, sandboxed file I/O
- **Dependency-aware scheduling** — Semaphore-controlled worker pool with automatic retry and backoff
- **Real-time SSE** — Live task progress, tool calls, and completion events streamed to the frontend
- **JWT auth** — Secure authentication with project ownership, admin roles, and timing-safe login

## Architecture

```
backend/
├── app.py              FastAPI app, lifespan, CORS, rate limiting
├── config.py           Config loader (all values from config.json)
├── container.py        dependency-injector DI container
├── exceptions.py       Typed exception hierarchy
├── db/                 Async SQLite (aiosqlite, WAL mode, Alembic migrations)
├── middleware/          JWT auth (Bearer + SSE tokens)
├── models/             Pydantic schemas + status enums
├── routes/             REST endpoints (projects, tasks, usage, services, events, auth)
├── services/           Core logic (planner, decomposer, executor, budget, model router)
└── tools/              Tool implementations (RAG, Ollama, ComfyUI, file I/O)

frontend/               React 19 + TypeScript + Vite
├── src/api/            REST client with auto token refresh
├── src/components/     Layout, AuthGuard, ErrorBoundary
├── src/hooks/          useAuth, useSSE
└── src/pages/          Dashboard, ProjectDetail, TaskDetail, Usage, Services
```

## Quick Start

### Prerequisites

- Python 3.11+
- Node.js 18+
- `ANTHROPIC_API_KEY` environment variable

### Backend

```bash
pip install -r requirements.txt
cp config.example.json config.json   # Edit with your settings
python run.py                        # http://localhost:5200
```

### Frontend (development)

```bash
cd frontend && npm install && npm run dev   # http://localhost:5173
```

### Frontend (production — served by FastAPI)

```bash
cd frontend && npm run build   # outputs to frontend/dist/
```

### Docker

```bash
docker build -t orchestration .
docker run -p 5200:5200 -v ./config.json:/app/config.json orchestration
```

### Tests

```bash
pip install -r requirements-dev.txt
python -m pytest tests/                     # run all tests
python -m pytest tests/ --cov=backend       # with coverage report
```

## Configuration

All settings live in `config.json` (never hardcoded). See [config.example.json](config.example.json) for the full template:

| Section | Key settings |
|---------|-------------|
| `anthropic` | Planning model, timeout, max concurrent |
| `ollama` | Local + server hosts, default model |
| `comfyui` | Hosts, checkpoint, timeout |
| `rag` | Database paths, embedding dimensions |
| `budget` | Daily/monthly/per-project USD limits |
| `execution` | Max concurrent tasks, tick interval, tool rounds |
| `auth` | JWT secret, token expiry, registration toggle |

## Tech Stack

**Backend:** FastAPI, aiosqlite, Alembic, dependency-injector, anthropic SDK, httpx, PyJWT, bcrypt, slowapi

**Frontend:** React 19, TypeScript, Vite, react-router-dom

**Infrastructure:** Ollama (local inference), ComfyUI (image generation), SQLite (WAL mode)
