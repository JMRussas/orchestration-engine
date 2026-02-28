#  Orchestration Engine - Auth Middleware
#
#  FastAPI dependencies for JWT authentication.
#  get_current_user: validates Bearer token, returns user dict.
#  require_admin: wraps get_current_user + role check.
#  get_user_from_sse_token: validates short-lived SSE query-param token.
#
#  Depends on: backend/services/auth.py, backend/container.py
#  Used by:    app.py, routes/*

import logging

import jwt
from dependency_injector.wiring import inject, Provide
from fastapi import Depends, HTTPException, Path, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.container import Container
from backend.services.auth import AuthService

logger = logging.getLogger("orchestration.auth")

_bearer_scheme = HTTPBearer(auto_error=False)


async def _validate_token(auth: AuthService, raw_token: str, expected_type: str = "access") -> dict:
    """Decode a JWT, check its type, and return the active user.

    Shared logic for Bearer header auth and SSE query-param auth.
    Raises HTTPException on any failure.
    """
    try:
        payload = auth.decode_token(raw_token)
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if payload.get("type") != expected_type:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type",
        )

    user = await auth.get_user(payload["sub"])
    if not user or not user["is_active"]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or disabled",
        )

    return user, payload


@inject
async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    auth: AuthService = Depends(Provide[Container.auth]),
) -> dict:
    """Validate Bearer token and return user dict. Raises 401 on failure."""
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user, _payload = await _validate_token(auth, credentials.credentials, "access")
    return user


async def require_admin(user: dict = Depends(get_current_user)) -> dict:
    """Require the current user to have admin role."""
    if user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return user


@inject
async def get_user_from_sse_token(
    project_id: str = Path(...),
    token: str = Query(...),
    auth: AuthService = Depends(Provide[Container.auth]),
) -> dict:
    """Validate a short-lived SSE token scoped to a single project.

    Only accepts type="sse" tokens and verifies the project_id claim
    matches the route parameter.
    """
    try:
        payload = auth.decode_token(token)
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    if payload.get("type") != "sse":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type — SSE token required",
        )

    if payload.get("project_id") != project_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="SSE token not valid for this project",
        )

    user, _ = await _validate_token(auth, token, "sse")
    return user
