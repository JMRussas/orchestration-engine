#  Orchestration Engine - Internal Routes
#
#  Unauthenticated endpoints for internal use: chat proxy for editor
#  integration, multi-model routing across CLI providers and Ollama.
#
#  Depends on: (none — standalone, no DB or DI required)
#  Used by:    app.py

import asyncio
import logging
import os
import shutil
import sys
from typing import Optional

import httpx

from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger("orchestration.internal")

router = APIRouter(prefix="/internal", tags=["internal"])


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class ChatMessageEntry(BaseModel):
    """A single message in a conversation history."""

    role: str  # "user", "assistant", "system"
    content: str


class ChatRequest(BaseModel):
    prompt: str
    context: Optional[str] = None
    provider: Optional[str] = None
    messages: Optional[list[ChatMessageEntry]] = None
    model: Optional[str] = None


class ChatResponse(BaseModel):
    response: str
    provider: Optional[str] = None
    model_used: Optional[str] = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/chat")
async def chat(request: ChatRequest):
    """Chat endpoint for editor integration with conversation history support.

    Routes to CLI providers (Claude, Gemini, Codex) via subprocess or to
    Ollama via HTTP API. Supports conversation history and model selection.
    """
    # Build prompt with optional context and conversation history
    parts: list[str] = []

    if request.context:
        parts.append(f"Context: {request.context}")

    if request.messages:
        # Include last 20 messages to keep prompt size manageable
        recent = request.messages[-20:]
        history_lines = [f"[{m.role}]: {m.content}" for m in recent]
        parts.append("Previous conversation:\n" + "\n".join(history_lines))

    parts.append(f"Current request:\n{request.prompt}" if request.messages else request.prompt)

    full_prompt = "\n\n".join(parts)

    # Determine provider (default to gemini)
    provider = (request.provider or "gemini").lower()

    # Ollama: use HTTP API directly (supports messages natively)
    if provider == "ollama":
        return await _chat_ollama(request, full_prompt)

    # CLI-based providers
    model_used: Optional[str] = None

    if provider == "claude":
        cmd_args = ["claude", "-p", full_prompt, "--output-format", "text"]
        # Claude -p mode doesn't support model selection
    elif provider == "codex":
        cmd_args = ["codex", "exec"]
        if request.model:
            cmd_args.extend(["--model", request.model])
            model_used = request.model
        cmd_args.extend(["--", full_prompt])
    else:  # gemini default
        cmd_args = ["gemini"]
        if request.model:
            cmd_args.extend(["-m", request.model])
            model_used = request.model
        cmd_args.extend(["-p", full_prompt])

    # On Windows, npm global binaries are .cmd — resolve to full path
    if sys.platform == "win32":
        resolved = shutil.which(cmd_args[0])
        if resolved:
            cmd_args[0] = resolved

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return ChatResponse(
                response="Request timed out",
                provider=provider,
                model_used=model_used,
            )

        stdout_text = stdout.decode().strip()
        stderr_text = stderr.decode().strip()

        if proc.returncode != 0:
            error_msg = stderr_text or f"Command exited with code {proc.returncode}"
            return ChatResponse(
                response=f"Error: {error_msg}",
                provider=provider,
                model_used=model_used,
            )

        return ChatResponse(
            response=stdout_text,
            provider=provider,
            model_used=model_used,
        )
    except FileNotFoundError:
        return ChatResponse(
            response=f"Provider '{provider}' CLI not found",
            provider=provider,
            model_used=model_used,
        )


async def _chat_ollama(request: ChatRequest, full_prompt: str) -> ChatResponse:
    """Route chat to Ollama HTTP API with native message support."""
    ollama_url = os.environ.get("OLLAMA_URL", "http://localhost:11434")
    ollama_model = request.model or os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:14b")

    # Build Ollama messages array if conversation history provided
    if request.messages:
        messages: list[dict] = []

        if request.context:
            messages.append({"role": "system", "content": request.context})

        for m in request.messages[-20:]:
            messages.append({"role": m.role, "content": m.content})

        # Add current prompt as the latest user message
        messages.append({"role": "user", "content": request.prompt})

        payload = {
            "model": ollama_model,
            "messages": messages,
            "stream": False,
        }
        api_path = "/api/chat"
    else:
        # Simple generate mode (no history)
        payload = {
            "model": ollama_model,
            "prompt": full_prompt,
            "stream": False,
        }
        api_path = "/api/generate"

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(f"{ollama_url}{api_path}", json=payload)
            resp.raise_for_status()
            data = resp.json()

        # /api/chat returns {"message": {"content": "..."}},
        # /api/generate returns {"response": "..."}
        if "message" in data:
            response_text = data["message"].get("content", "")
        else:
            response_text = data.get("response", "")

        return ChatResponse(
            response=response_text.strip(),
            provider="ollama",
            model_used=ollama_model,
        )
    except httpx.HTTPStatusError as exc:
        return ChatResponse(
            response=f"Ollama error: {exc.response.status_code} {exc.response.text}",
            provider="ollama",
            model_used=ollama_model,
        )
    except httpx.ConnectError:
        return ChatResponse(
            response=f"Cannot connect to Ollama at {ollama_url}",
            provider="ollama",
            model_used=ollama_model,
        )
