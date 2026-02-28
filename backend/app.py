#  Orchestration Engine - FastAPI Application
#
#  Main app setup: lifespan, CORS, router includes.
#  Creates the DI container and manages service lifecycle.
#
#  Depends on: config.py, container.py, routes/*.py, middleware/auth.py
#  Used by:    run.py

import logging
import uuid
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded

from backend.config import CORS_ORIGINS, DB_PATH, PROJECT_ROOT, validate_config
from backend.container import Container
from backend.exceptions import (
    AccountLinkError,
    BudgetExhaustedError,
    CycleDetectedError,
    InvalidStateError,
    NotFoundError,
    OIDCError,
    OrchestrationError,
    PlanParseError,
)
from backend.logging_config import set_request_id
from backend.middleware.auth import get_current_user
from backend.rate_limit import limiter
from backend.routes.admin import router as admin_router
from backend.routes.auth import router as auth_router
from backend.routes.auth_oidc import router as auth_oidc_router
from backend.routes.checkpoints import router as checkpoints_router
from backend.routes.events import router as events_router
from backend.routes.projects import router as projects_router
from backend.routes.rag import router as rag_router
from backend.routes.services import health_router, router as services_router
from backend.routes.tasks import router as tasks_router
from backend.routes.usage import router as usage_router

logger = logging.getLogger("orchestration.app")

# Create and wire the DI container
container = Container()

# Auth dependency for all protected routes
_auth_dep = [Depends(get_current_user)]


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle.

    Uses AsyncExitStack so that if any startup step fails, all previously
    initialized resources are cleaned up in reverse order.
    """
    logger.info("Orchestration Engine starting...")

    # Validate critical config before anything else
    validate_config()

    # Get instances from container
    db = container.db()
    http_client = container.http_client()
    resource_monitor = container.resource_monitor()
    executor = container.executor()

    async with AsyncExitStack() as stack:
        await db.init(DB_PATH, run_migrations=True)
        stack.push_async_callback(db.close)

        # Shared httpx client — close on shutdown
        stack.push_async_callback(http_client.aclose)

        await resource_monitor.start_background()
        stack.push_async_callback(resource_monitor.stop_background)
        logger.info("Resource monitor started")

        await executor.start()
        stack.push_async_callback(executor.stop)

        yield

    logger.info("Orchestration Engine shutting down")


app = FastAPI(
    title="Orchestration Engine",
    version="0.1.0",
    lifespan=lifespan,
)
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"detail": "Rate limit exceeded. Try again later."},
    )


# Global exception handlers — safety net for uncaught business errors
@app.exception_handler(NotFoundError)
async def not_found_handler(request: Request, exc: NotFoundError):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(BudgetExhaustedError)
async def budget_handler(request: Request, exc: BudgetExhaustedError):
    return JSONResponse(status_code=402, content={"detail": str(exc)})


@app.exception_handler(PlanParseError)
async def plan_parse_handler(request: Request, exc: PlanParseError):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(CycleDetectedError)
async def cycle_handler(request: Request, exc: CycleDetectedError):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(InvalidStateError)
async def invalid_state_handler(request: Request, exc: InvalidStateError):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(OIDCError)
async def oidc_error_handler(request: Request, exc: OIDCError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(AccountLinkError)
async def account_link_handler(request: Request, exc: AccountLinkError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(OrchestrationError)
async def orchestration_handler(request: Request, exc: OrchestrationError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception):
    logger.error("Unhandled exception: %s", exc, exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


# Request ID tracing
class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = uuid.uuid4().hex[:12]
        set_request_id(rid)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = rid
            return response
        finally:
            set_request_id(None)

app.add_middleware(RequestIDMiddleware)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

# Health check (public, unauthenticated — for Docker/k8s liveness probes)
app.include_router(health_router, prefix="/api")

# Auth routes (public — no token required)
app.include_router(auth_router, prefix="/api")

# OIDC auth routes (mixed: public endpoints + authenticated link/unlink)
app.include_router(auth_oidc_router, prefix="/api")

# Protected API routes (require valid JWT)
app.include_router(projects_router, prefix="/api", dependencies=_auth_dep)
app.include_router(services_router, prefix="/api", dependencies=_auth_dep)
app.include_router(tasks_router, prefix="/api", dependencies=_auth_dep)
app.include_router(usage_router, prefix="/api", dependencies=_auth_dep)
app.include_router(checkpoints_router, prefix="/api", dependencies=_auth_dep)
app.include_router(admin_router, prefix="/api", dependencies=_auth_dep)
app.include_router(rag_router, prefix="/api", dependencies=_auth_dep)

# Events route uses query-param token auth (EventSource can't send headers)
app.include_router(events_router, prefix="/api")

# Serve frontend build if available
frontend_dist = PROJECT_ROOT / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")
