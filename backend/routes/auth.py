#  Orchestration Engine - Auth Routes
#
#  Registration, login, token refresh, and user profile endpoints.
#
#  Depends on: container.py, services/auth.py, middleware/auth.py
#  Used by:    app.py

from dependency_injector.wiring import inject, Provide
from fastapi import APIRouter, Depends, HTTPException
from starlette.requests import Request

from backend.container import Container
from backend.rate_limit import limiter
from backend.middleware.auth import get_current_user
from backend.models.schemas import (
    LoginRequest,
    LoginResponse,
    RefreshRequest,
    RefreshResponse,
    RegisterRequest,
    UserOut,
)
from backend.services.auth import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", status_code=201)
@limiter.limit("5/minute")
@inject
async def register(
    request: Request,
    body: RegisterRequest,
    auth: AuthService = Depends(Provide[Container.auth]),
) -> UserOut:
    """Register a new user. First user becomes admin."""
    try:
        user = await auth.register(body.email, body.password, body.display_name)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return UserOut(**user)


@router.post("/login")
@limiter.limit("5/minute")
@inject
async def login(
    request: Request,
    body: LoginRequest,
    auth: AuthService = Depends(Provide[Container.auth]),
) -> LoginResponse:
    """Authenticate and receive access + refresh tokens."""
    try:
        result = await auth.login(body.email, body.password)
    except (ValueError, PermissionError) as e:
        raise HTTPException(status_code=401, detail=str(e))
    return LoginResponse(**result)


@router.post("/refresh")
@limiter.limit("10/minute")
@inject
async def refresh(
    request: Request,
    body: RefreshRequest,
    auth: AuthService = Depends(Provide[Container.auth]),
) -> RefreshResponse:
    """Exchange a refresh token for new access + refresh tokens."""
    try:
        result = await auth.refresh_tokens(body.refresh_token)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    return RefreshResponse(**result)


@router.get("/me")
async def get_me(
    user: dict = Depends(get_current_user),
) -> UserOut:
    """Get the current authenticated user's profile."""
    return UserOut(**user)
