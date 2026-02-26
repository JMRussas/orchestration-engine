# Orchestration Engine - Architecture

## Core Workflow

```
Requirements → Claude Sonnet → Plan JSON → User Approval → Task Rows + DAG
                                                                  ↓
                                            ┌─────── Executor Loop ────────┐
                                            │ 1. Check budget limits       │
                                            │ 2. Find ready tasks (deps met)│
                                            │ 3. Check resource health     │
                                            │ 4. Route to model tier       │
                                            │ 5. Build small context       │
                                            │ 6. Call Claude/Ollama + tools│
                                            │ 7. Record usage + cost       │
                                            │ 8. Store output, emit SSE    │
                                            └──────────────────────────────┘
```

## Dependency Injection

All singletons are wired via `dependency-injector` in `backend/container.py`:

```
Container
├── config: AppConfig          (loads config.json)
├── db: Database               (async SQLite via aiosqlite)
├── auth: AuthService          (JWT + bcrypt)
├── budget: BudgetManager      (spend tracking)
├── progress: ProgressManager  (SSE broadcast)
├── tool_registry: ToolRegistry
├── anthropic_client: AsyncAnthropic
├── resource_monitor: ResourceMonitor
└── executor: Executor
```

Routes use `@inject` + `Depends(Provide[Container.xxx])` to receive dependencies.
Tests override providers: `container.db.override(providers.Object(test_db))`.

## Database

- **Engine**: async SQLite via `aiosqlite` (WAL mode, foreign keys)
- **Schema management**: Alembic migrations in `backend/migrations/versions/`
- **Dual-mode init**: `Database.init(run_migrations=True)` for production (runs Alembic), `False` for tests (applies inline schema)
- **Pre-Alembic detection**: `migrate.py` checks for existing tables without `alembic_version` and stamps them

### Schema (8 tables)

- **users**: id, email, password_hash, display_name, role, is_active
- **projects**: top-level container (owner_id → users)
- **plans**: Claude-generated plan JSON (versioned)
- **tasks**: individual work units with model_tier, tools, context, output
- **task_deps**: dependency DAG edges
- **usage_log**: every API call (provider, model, tokens, cost)
- **budget_periods**: aggregated daily/monthly spend
- **task_events**: fine-grained event log for SSE

## Authentication

- **Passwords**: bcrypt hashing (direct, not passlib — passlib is incompatible with bcrypt 5.0)
- **Tokens**: PyJWT with HS256. Access tokens (30 min) + refresh tokens (7 days).
- **REST endpoints**: `Authorization: Bearer <token>` header via `HTTPBearer` scheme
- **SSE/EventSource**: token as query parameter (`?token=...`) since EventSource can't send headers
- **First user**: automatically gets `admin` role
- **Protected routes**: all routes except `/api/auth/*` require authentication via `get_current_user` dependency

### Auth Flow

```
Register → POST /api/auth/register → user row + 201
Login    → POST /api/auth/login    → {access_token, refresh_token, user}
Refresh  → POST /api/auth/refresh  → new {access_token, refresh_token}
Me       → GET  /api/auth/me       → user profile (protected)
```

### Frontend Auth

- Tokens in localStorage (`orch_access_token`, `orch_refresh_token`)
- `AuthProvider` context wraps app, auto-refreshes tokens every 25 min
- `AuthGuard` component redirects to `/login` if unauthenticated
- `authFetch` wrapper injects Bearer header, retries once on 401 after refresh

## Model Routing

| Task Type | Complexity | Model | Cost |
|-----------|-----------|-------|------|
| research | simple | Ollama | Free |
| analysis | simple | Ollama | Free |
| asset | any | Ollama + ComfyUI | Free |
| code | simple | Haiku | $1/5 per MTok |
| code | medium+ | Sonnet | $3/15 per MTok |
| integration | any | Haiku | $1/5 per MTok |
| planning | — | Sonnet | $3/15 per MTok |

## Tool System

Tools are Python classes in `backend/tools/`, registered via `ToolRegistry` (injectable DI singleton). Each has:
- `name`, `description`, `parameters` (JSON Schema)
- `async execute(params) -> str`

Available tools:
- `search_knowledge`: semantic RAG search (direct SQLite, Ollama embedding)
- `lookup_type`: FTS5 keyword RAG lookup
- `local_llm`: Ollama generate (free local inference)
- `generate_image`: ComfyUI workflow submission + polling
- `read_file` / `write_file`: sandboxed to `data/projects/{id}/`

## Budget System

- Pre-call check: `budget.can_spend(estimated_cost)` before every API call
- Post-call record: actual tokens → usage_log + budget_periods
- Limits: daily, monthly, per-project (configurable in config.json)
- Hard stop at 100%, warning at 80%

## Rate Limiting

- slowapi middleware with configurable default (`server.rate_limit`, default `60/minute`)
- Plan generation endpoint: `5/minute` (expensive Claude call)

## Resource Monitoring

Background task (30s interval) health-checks:
- Ollama: GET /api/tags (verifies models loaded)
- ComfyUI: GET /system_stats
- Claude API: checks ANTHROPIC_API_KEY presence

Tasks that need unavailable resources stay in queue until next tick.

## Test Architecture

```
tests/
├── conftest.py          # Shared fixtures (tmp_db, app_client, authed_client, mocks)
├── unit/                # Pure function tests (no HTTP, no DB writes except tmp_db)
│   ├── test_model_router.py    (15 tests)
│   ├── test_budget.py          (8 tests)
│   ├── test_decomposer.py      (8 tests)
│   ├── test_file_tool.py       (13 tests)
│   ├── test_auth.py            (17 tests)
│   ├── test_progress.py        (9 tests)
│   └── test_registry.py        (7 tests)
├── integration/         # HTTP endpoint tests via AsyncClient + ASGI transport
│   ├── test_auth_api.py        (12 tests)
│   ├── test_projects_api.py    (13 tests)
│   ├── test_tasks_api.py       (15 tests)
│   └── test_usage_api.py       (5 tests)
└── e2e/                 # Full workflow tests
    └── test_full_workflow.py   (2 tests)
```

- `tmp_db` fixture: fresh async Database with inline schema (no Alembic for speed)
- `app_client`: DI container overrides for db, auth, budget, progress, executor, resource_monitor
- `authed_client`: app_client + registered user + Bearer token header
- Claude/Ollama mocked in all tests (no real API calls)
- Coverage target: 60%+

## Dependency Map

| File | Role | Depends On | Used By |
|------|------|-----------|---------|
| config.py | Config loader | config.json | Everything |
| container.py | DI container | config, all services | app.py, routes |
| db/connection.py | Async SQLite | aiosqlite, config | All services, routes |
| db/migrate.py | Alembic runner | alembic, models_metadata | db/connection.py |
| middleware/auth.py | JWT auth deps | services/auth, container | app.py, routes |
| models/enums.py | Status enums | — | schemas, services, routes |
| models/schemas.py | Pydantic models | enums | Routes |
| services/auth.py | Auth service | db, config, bcrypt, PyJWT | middleware, routes/auth |
| services/planner.py | Plan generation | config, budget, model_router | routes/projects |
| services/decomposer.py | Plan → tasks | db, model_router | routes/projects |
| services/executor.py | Task execution | db, budget, model_router, tools, progress, resource_monitor | app.py |
| services/budget.py | Cost tracking | db, config | executor, routes/usage |
| services/model_router.py | Model selection | config | planner, decomposer, executor |
| services/resource_monitor.py | Health checks | config | executor, routes/services |
| services/progress.py | SSE broadcast | db | executor, routes/events |
| tools/registry.py | Tool registry | tools/* | container, executor |
| tools/rag.py | RAG search | config, tools/base | registry |
| tools/ollama.py | Ollama client | config, tools/base | registry |
| tools/comfyui.py | ComfyUI client | config, tools/base | registry |
| tools/file.py | File I/O | config, tools/base | registry |

## Gotchas & Pitfalls

- Claude may return dependency indices as strings ("2") instead of ints. The decomposer handles both.
- Model IDs must match exactly what the Anthropic API accepts. Current valid IDs: `claude-sonnet-4-6`, `claude-opus-4-6`, `claude-haiku-4-5-20251001`.
- **passlib 1.7.4 is incompatible with bcrypt 5.0.0**. Use `bcrypt.hashpw()`/`bcrypt.checkpw()` directly instead of `passlib.CryptContext`.
- aiosqlite runs SQLite on a background thread — no need for `threading.Lock` or `check_same_thread=False`.
- RAG tool SQLite connections still use sync sqlite3 directly (not via aiosqlite). These need `check_same_thread=False`.
- **Must use `AsyncAnthropic`** in all async functions (planner, executor). Using sync `Anthropic()` blocks the entire event loop and freezes SSE, health checks, and concurrent task dispatch.
- The executor marks tasks as QUEUED before dispatching and tracks dispatched task IDs in `_dispatched` set to prevent duplicate dispatch. Without this, the tick loop can re-dispatch the same PENDING task before the semaphore is acquired.
- Budget `record_spend()` uses `execute_many_write()` for atomic transactions — all three writes (usage_log, daily period, monthly period) commit together.
- RAG embedding matrices are loaded lazily on first use and cached in memory. First search will be slow.
- The `response.content` from Anthropic SDK (list of ContentBlock objects) can be passed directly back into messages — the SDK handles serialization.
- The executor uses a shared `AsyncAnthropic` client (created once at `start()`, closed at `stop()`). Do NOT create per-call clients — they leak connection pools.
- The executor tracks in-flight task handles in `_in_flight` set and cancels them on `stop()` for clean shutdown.
- Dead-project detection: if no tasks are PENDING/QUEUED/RUNNING and some are BLOCKED, the executor marks the project FAILED. This prevents projects stuck in EXECUTING forever.
- File tool sandbox uses `Path.is_relative_to()` for path traversal prevention — NOT `str.startswith()` which has prefix-collision vulnerabilities.
- **EventSource can't send headers** — SSE auth uses query-param token (`?token=...`) validated by `get_user_from_token_param`.
- **Alembic + SQLite**: Must use `render_as_batch=True` in `env.py` because SQLite doesn't support `ALTER TABLE DROP COLUMN` natively.
- **Test DB uses inline schema, not Alembic** — faster and avoids event loop issues. Keep inline `_SCHEMA` in `connection.py` in sync with migration files.
