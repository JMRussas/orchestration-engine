#  Orchestration Engine - Auth Service
#
#  Password hashing, JWT encode/decode, register/login, SSE tokens.
#  First registered user becomes admin.
#
#  Depends on: backend/db/connection.py, backend/config.py
#  Used by:    container.py, routes/auth.py, routes/events.py, middleware/auth.py

import logging
import time
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from backend.config import (
    AUTH_ACCESS_TOKEN_EXPIRE_MINUTES,
    AUTH_ALGORITHM,
    AUTH_ALLOW_REGISTRATION,
    AUTH_REFRESH_TOKEN_EXPIRE_DAYS,
    AUTH_SECRET_KEY,
    AUTH_SSE_TOKEN_EXPIRE_SECONDS,
)
from backend.db.connection import Database

logger = logging.getLogger("orchestration.auth")

# Pre-computed dummy hash for timing-safe login (prevents timing side-channel)
_DUMMY_HASH = bcrypt.hashpw(b"dummy-password-for-timing", bcrypt.gensalt()).decode()


class AuthService:
    """Handles user registration, login, and JWT token management."""

    def __init__(self, db: Database):
        self._db = db

    # ------------------------------------------------------------------
    # Password helpers
    # ------------------------------------------------------------------

    @staticmethod
    def hash_password(password: str) -> str:
        return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    @staticmethod
    def verify_password(plain: str, hashed: str) -> bool:
        return bcrypt.checkpw(plain.encode(), hashed.encode())

    # ------------------------------------------------------------------
    # JWT helpers
    # ------------------------------------------------------------------

    @staticmethod
    def create_access_token(user_id: str, role: str) -> str:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=AUTH_ACCESS_TOKEN_EXPIRE_MINUTES
        )
        payload = {
            "sub": user_id,
            "role": role,
            "type": "access",
            "exp": expire,
        }
        return jwt.encode(payload, AUTH_SECRET_KEY, algorithm=AUTH_ALGORITHM)

    @staticmethod
    def create_refresh_token(user_id: str) -> str:
        expire = datetime.now(timezone.utc) + timedelta(
            days=AUTH_REFRESH_TOKEN_EXPIRE_DAYS
        )
        payload = {
            "sub": user_id,
            "type": "refresh",
            "exp": expire,
        }
        return jwt.encode(payload, AUTH_SECRET_KEY, algorithm=AUTH_ALGORITHM)

    @staticmethod
    def create_sse_token(user_id: str, project_id: str) -> str:
        """Create a short-lived token scoped to a single project's SSE stream."""
        expire = datetime.now(timezone.utc) + timedelta(
            seconds=AUTH_SSE_TOKEN_EXPIRE_SECONDS
        )
        payload = {
            "sub": user_id,
            "type": "sse",
            "project_id": project_id,
            "exp": expire,
        }
        return jwt.encode(payload, AUTH_SECRET_KEY, algorithm=AUTH_ALGORITHM)

    @staticmethod
    def decode_token(token: str) -> dict:
        """Decode and validate a JWT. Raises jwt.PyJWTError on failure."""
        return jwt.decode(token, AUTH_SECRET_KEY, algorithms=[AUTH_ALGORITHM])

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    async def register(self, email: str, password: str, display_name: str = "") -> dict:
        """Register a new user. First user becomes admin.

        Uses BEGIN IMMEDIATE transaction to prevent race conditions where
        two concurrent first-registrations both become admin.
        """
        if not AUTH_ALLOW_REGISTRATION:
            raise PermissionError("Registration is disabled")

        user_id = str(uuid.uuid4())
        now = time.time()
        password_hash = self.hash_password(password)
        display = display_name or email.split("@")[0]

        async with self._db.transaction() as conn:
            # Check duplicate — generic error to prevent email enumeration
            existing = await conn.execute(
                "SELECT id FROM users WHERE email = ?", (email,)
            )
            if await existing.fetchone():
                raise ValueError("Registration failed")

            # First user becomes admin (safe: BEGIN IMMEDIATE serializes this)
            count_cursor = await conn.execute("SELECT COUNT(*) as cnt FROM users")
            user_count = await count_cursor.fetchone()
            role = "admin" if user_count[0] == 0 else "user"

            await conn.execute(
                "INSERT INTO users (id, email, password_hash, display_name, role, is_active, created_at) "
                "VALUES (?, ?, ?, ?, ?, 1, ?)",
                (user_id, email, password_hash, display, role, now),
            )

        logger.info("User registered: %s (role=%s)", email, role)
        return {
            "id": user_id,
            "email": email,
            "display_name": display,
            "role": role,
        }

    # ------------------------------------------------------------------
    # Login
    # ------------------------------------------------------------------

    async def login(self, email: str, password: str) -> dict:
        """Authenticate user, return tokens."""
        user = await self._db.fetchone(
            "SELECT * FROM users WHERE email = ?", (email,)
        )

        if not user:
            # Timing-safe: still run bcrypt against dummy hash so response time
            # is indistinguishable from a real user with wrong password
            self.verify_password(password, _DUMMY_HASH)
            raise ValueError("Invalid email or password")

        if not self.verify_password(password, user["password_hash"]):
            raise ValueError("Invalid email or password")

        if not user["is_active"]:
            raise PermissionError("Account is disabled")

        # Update last login
        await self._db.execute_write(
            "UPDATE users SET last_login_at = ? WHERE id = ?",
            (time.time(), user["id"]),
        )

        access_token = self.create_access_token(user["id"], user["role"])
        refresh_token = self.create_refresh_token(user["id"])

        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "user": {
                "id": user["id"],
                "email": user["email"],
                "display_name": user["display_name"],
                "role": user["role"],
            },
        }

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    async def refresh_tokens(self, refresh_token: str) -> dict:
        """Issue new access + refresh tokens from a valid refresh token."""
        try:
            payload = self.decode_token(refresh_token)
        except jwt.PyJWTError as e:
            raise ValueError(f"Invalid refresh token: {e}")

        if payload.get("type") != "refresh":
            raise ValueError("Token is not a refresh token")

        user_id = payload["sub"]
        user = await self._db.fetchone(
            "SELECT * FROM users WHERE id = ? AND is_active = 1", (user_id,)
        )
        if not user:
            raise ValueError("User not found or disabled")

        access_token = self.create_access_token(user["id"], user["role"])
        new_refresh = self.create_refresh_token(user["id"])

        return {
            "access_token": access_token,
            "refresh_token": new_refresh,
            "token_type": "bearer",
        }

    # ------------------------------------------------------------------
    # Get user
    # ------------------------------------------------------------------

    async def get_user(self, user_id: str) -> dict | None:
        """Fetch user by ID. Returns dict or None."""
        row = await self._db.fetchone(
            "SELECT id, email, display_name, role, is_active, created_at, last_login_at "
            "FROM users WHERE id = ?",
            (user_id,),
        )
        if not row:
            return None
        return dict(row)
