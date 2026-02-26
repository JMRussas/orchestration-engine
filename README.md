# orchestration-engine

**AI-powered task orchestration: describe what you want, get a plan, approve it, watch it execute.**

Most AI coding tools operate as a single conversation — one model, one context window, one task at a time. Orchestration Engine takes a different approach: it decomposes requirements into a dependency graph of focused tasks, routes each to the cheapest capable model (free local Ollama → Haiku → Sonnet), and executes them in parallel with budget controls, tool use, and real-time progress streaming.

One config file. Multi-model. Budget-aware. Self-monitoring.

```
Requirements ──→ Claude Sonnet ──→ Plan JSON ──→ User Approval ──→ Task DAG
                  (planning)        (structured)   (review/edit)       │
                                                                      ▼
                                                    ┌─── Executor Loop ───┐
                                                    │ Check budget        │
                                                    │ Resolve dependencies│
                                                    │ Check resource health│
                                                    │ Route to model tier │
                                                    │ Execute + tools     │
                                                    │ Record cost         │
                                                    │ Emit SSE events     │
                                                    └─────────────────────┘
```

## Why This?

| | Orchestration Engine | Single-model chat | LangChain agents |
|---|---|---|---|
| **Cost control** | Per-task model routing, budget limits, reservation system | One model for everything | Manual, per-chain |
| **Parallelism** | Dependency-aware DAG, semaphore-controlled workers | Sequential | Requires custom orchestration |
| **Observability** | Real-time SSE, per-task cost tracking, event log | Conversation history | Callbacks, varies |
| **Tool use** | RAG search, local LLM, image gen, file I/O — per-task | Single toolset | Per-chain toolsets |
| **Infrastructure** | SQLite + Ollama, no cloud DB required | Cloud API only | Depends on stack |

## Quick Start

### Prerequisites

- Python 3.11+
- Node.js 18+
- `ANTHROPIC_API_KEY` environment variable
- [Ollama](https://ollama.com) running locally (optional, for free-tier tasks)

### Backend

```bash
pip install -r requirements.txt
cp config.example.json config.json   # Edit with your settings — especially auth.secret_key
python run.py                        # http://localhost:5200
```

### Frontend (development)

```bash
cd frontend && npm install && npm run dev   # http://localhost:5173
```

### Frontend (production — served by FastAPI)

```bash
cd frontend && npm run build   # outputs to frontend/dist/, served automatically
```

### Docker

```bash
docker build -t orchestration .
docker run -p 5200:5200 \
  -e ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY \
  -v ./config.json:/app/config.json \
  orchestration
```

### Tests

```bash
# Backend (390+ tests, 85% coverage)
pip install -r requirements-dev.txt
python -m pytest tests/                     # all tests
python -m pytest tests/ --cov=backend       # with coverage

# Frontend (118 tests)
cd frontend && npm test
```

## How It Works

1. **Plan** — Describe what you want. Claude generates a structured plan with tasks, dependencies, complexity ratings, and tool assignments.
2. **Review** — Inspect the plan. Adjust model tiers, edit descriptions, change priorities. Then approve.
3. **Execute** — The async executor dispatches tasks respecting the dependency DAG, budget limits, and resource availability. Tasks run in parallel where the graph allows.
4. **Monitor** — Watch progress in real-time via SSE events. Pause, resume, or cancel at any point.

### Model Routing

Each task is routed to the cheapest model that can handle it:

| Task Type | Complexity | Model | Cost |
|-----------|-----------|-------|------|
| Research / Analysis | Simple | Ollama (local) | Free |
| Asset generation | Any | Ollama + ComfyUI | Free |
| Code / Integration | Simple | Haiku | $1 / $5 per MTok |
| Code / Integration | Medium+ | Sonnet | $3 / $15 per MTok |
| Planning | — | Sonnet | $3 / $15 per MTok |

### Budget System

- **Reserve before execute** — TOCTOU-safe reservation prevents overspend under concurrency
- **Three-tier limits** — Daily, monthly, and per-project caps (all configurable)
- **Per-round checks** — Mid-tool-loop budget verification with graceful partial results
- **Full cost tracking** — Every API call recorded with provider, model, tokens, and USD cost

### Tool System

Tools are Python classes registered via an injectable `ToolRegistry`. Each task can request any combination:

| Tool | What it does |
|------|-------------|
| `search_knowledge` | Semantic RAG search over code/docs (Ollama embeddings + cosine similarity) |
| `lookup_type` | Exact keyword/type lookup via FTS5 full-text search |
| `local_llm` | Free local inference via Ollama (drafts, summaries, sub-tasks) |
| `generate_image` | Image generation via ComfyUI workflow submission + polling |
| `read_file` / `write_file` | Sandboxed file I/O scoped to project workspace |

## Architecture

```
backend/
├── app.py              FastAPI app, lifespan, CORS, rate limiting
├── config.py           Config loader (all values from config.json, never hardcoded)
├── container.py        dependency-injector DI container
├── exceptions.py       Typed exception hierarchy
├── rate_limit.py       Shared slowapi limiter instance
├── db/
│   ├── connection.py   Async SQLite (aiosqlite, WAL mode, transactions)
│   ├── migrate.py      Programmatic Alembic runner
│   └── models_metadata.py  SQLAlchemy Table defs for Alembic autogenerate
├── migrations/         Alembic migration versions
├── middleware/
│   └── auth.py         JWT auth (Bearer tokens + short-lived SSE tokens)
├── models/             Pydantic schemas + status enums
├── routes/             REST endpoints (projects, tasks, usage, services, events, auth)
├── services/
│   ├── planner.py      Claude-powered plan generation
│   ├── decomposer.py   Plan → task rows + dependency DAG (with cycle detection)
│   ├── executor.py     Async worker pool with tool loop and retry backoff
│   ├── budget.py       Spend tracking and limit enforcement
│   ├── model_router.py Model tier selection and cost calculation
│   ├── progress.py     SSE broadcast and event persistence
│   └── resource_monitor.py  Background health checks (Ollama, ComfyUI, Claude)
└── tools/              Tool implementations (RAG, Ollama, ComfyUI, file I/O)

frontend/               React 19 + TypeScript + Vite
├── src/api/            REST client with auto token refresh and 401 retry
├── src/components/     AuthGuard, ErrorBoundary, Layout
├── src/hooks/          useAuth, useSSE
└── src/pages/          Dashboard, ProjectDetail, TaskDetail, Usage, Services
```

### Key Design Decisions

- **Config-driven everything.** All hosts, models, budgets, timeouts, and auth settings come from `config.json`. Zero hardcoded values.
- **Dependency injection.** `dependency-injector` wires all singletons. Routes use `@inject` + `Depends(Provide[...])`. Tests override providers cleanly.
- **Async SQLite.** aiosqlite with WAL mode. Explicit transaction support with same-task nesting detection. Alembic migrations in production, inline schema in tests.
- **Budget reservation.** `reserve_spend()` acquires budget atomically before execution. Prevents two concurrent tasks from both checking "is there budget?" and both spending past the limit.
- **Partial tool registration.** A broken ComfyUI import doesn't take down RAG search. Failed tools are logged and skipped; the rest load normally.
- **SSE via query-param tokens.** EventSource can't send headers, so SSE auth uses short-lived project-scoped tokens (60s TTL) issued via a dedicated endpoint.

## Configuration

All settings live in `config.json` (gitignored). See [config.example.json](config.example.json) for the full template.

| Section | Key settings |
|---------|-------------|
| `server` | Host, port, CORS origins, rate limit |
| `anthropic` | Planning model, tier models, timeout, max concurrent |
| `ollama` | Local + server hosts, default model, timeouts |
| `comfyui` | Hosts, default checkpoint, timeout |
| `rag` | Database paths, embedding dimensions |
| `budget` | Daily / monthly / per-project USD limits, warning threshold |
| `execution` | Max concurrent tasks, tick interval, tool rounds, retry limit |
| `model_pricing` | Per-model input/output cost per million tokens |
| `auth` | JWT secret (min 32 chars), token expiry, registration toggle |

## API

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/api/health` | GET | No | Liveness probe (Docker/k8s) |
| `/api/auth/register` | POST | No | Create account (first user → admin) |
| `/api/auth/login` | POST | No | Get access + refresh tokens |
| `/api/auth/refresh` | POST | No | Rotate tokens |
| `/api/auth/me` | GET | Bearer | Current user profile |
| `/api/projects` | CRUD | Bearer | Project management |
| `/api/projects/{id}/plan` | POST | Bearer | Generate plan via Claude |
| `/api/projects/{id}/plans/{pid}/approve` | POST | Bearer | Approve plan → create tasks |
| `/api/projects/{id}/execute` | POST | Bearer | Start task execution |
| `/api/tasks/project/{id}` | GET | Bearer | List tasks for a project |
| `/api/tasks/{id}` | GET/PATCH | Bearer | Task detail / edit |
| `/api/usage/daily` | GET | Admin | Budget and usage stats |
| `/api/services` | GET | Bearer | External service health |
| `/api/events/{id}?token=...` | GET (SSE) | Token | Real-time progress stream |

## Limitations

- **SQLite single-writer** — WAL mode allows concurrent reads, but writes are serialized. Fine for small-to-medium workloads; not designed for high-throughput multi-user.
- **In-memory embeddings** — RAG tool loads embedding matrices into memory. Practical up to ~50k chunks.
- **No incremental re-indexing** — RAG databases are built externally (via [mcp-rag](https://github.com/JMRussas/mcp-rag)).
- **No persistent task output storage** — Task outputs are stored in SQLite text columns, not as files. Large outputs (images, long documents) should use the file tools.
- **Single Ollama instance per tier** — Falls back to first available host, no load balancing.

## Tech Stack

**Backend:** Python 3.11+ · FastAPI · aiosqlite · Alembic · dependency-injector · anthropic SDK · httpx · PyJWT · bcrypt · slowapi

**Frontend:** React 19 · TypeScript · Vite · react-router-dom · vitest

**Infrastructure:** SQLite (WAL mode) · [Ollama](https://ollama.com) · ComfyUI

## License

MIT
