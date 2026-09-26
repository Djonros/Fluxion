"""FastAPI application wrapping the Fluxion engine.

Endpoints:
    GET  /api/health            — service status & model info
    POST /api/chat              — chat (streaming SSE or JSON)
    POST /api/agent/run         — ReAct agent execution (SSE if stream=true)
    POST /api/rag/index         — index a directory into ChromaDB
    POST /api/auth/*            — register, login, refresh, me
    CRUD /api/projects          — create, list, get, update, delete
    CRUD /api/organizations     — orgs, members (SSO-lite email domain)
    CRUD /api/sessions          — chat sessions + messages
    POST /api/sessions/{id}/chat — send message, get persisted AI reply
                                   (SSE if stream=true in body)
    GET  /api/usage             — usage summary (last N hours)
    GET  /api/usage/breakdown   — per-endpoint stats
    GET  /api/billing/plans     — subscription plans (public)
    GET  /api/billing/subscription — current user's subscription
    POST /api/billing/checkout  — Stripe Checkout session
    POST /api/billing/cancel    — cancel paid subscription
    POST /api/billing/webhook   — Stripe webhook (signature-verified)

Rate limited via in-memory sliding window; paid plans raise per-user
limits via a resolver (billing). All /api/ calls logged.
"""
from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from core.config import Settings
from core.inference import OllamaBackend
from orchestrator import Assistant, CodingAgent
from rag.service import RAGConfig, RAGService
from web.pipeline import WebSearch

from .auth import router as auth_router
from .auth.dependencies import get_current_user
from .auth.models import User
from .billing import router as billing_router
from .billing.service import resolve_user_limits, set_session_factory as billing_set_session_factory
from .chat import router as chat_router
from .database import init_db
from .organizations import router as organizations_router
from .projects import router as projects_router
from .usage import router as usage_router
from .usage.limiter import get_limiter, rate_limit, set_limiter, RateLimiter
from .usage.middleware import UsageLoggingMiddleware
from .security import cors_options, is_saas, resolve_user_path, user_workspace

logger = logging.getLogger("fluxion.server")


# ── request / response models ───────────────────────────────────────────────

class ChatRequest(BaseModel):
    query: str
    history: list[dict[str, str]] = []
    stream: bool = False
    project_id: str | None = None


class AgentRequest(BaseModel):
    task: str
    context: str = ""
    allow_write: bool = False
    project_id: str | None = None
    stream: bool = False


class IndexRequest(BaseModel):
    path: str


# ── app factory ──────────────────────────────────────────────────────────────

def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and return the configured FastAPI application."""
    if settings is None:
        settings = Settings.load()

    app = FastAPI(title="Fluxion", version="0.1.0")

    # Wildcard origins + credentials let any website drive a local server
    # holding the user's session; restrict to localhost / configured origins.
    app.add_middleware(CORSMiddleware, **cors_options())

    # ── Rate limiter (in-memory, per-process) ───────────────────────────
    set_limiter(RateLimiter())
    app.state.rate_limiter = get_limiter()

    # ── Usage logging middleware ────────────────────────────────────────
    app.add_middleware(UsageLoggingMiddleware)

    # ── Database ────────────────────────────────────────────────────────
    init_db()

    from .database import SessionLocal
    app.state.session_factory = SessionLocal

    # ── Billing ↔ limiter wiring (paid plans raise per-user limits) ────
    billing_set_session_factory(SessionLocal)
    get_limiter().set_limit_resolver(resolve_user_limits)

    # ── Auth routes ─────────────────────────────────────────────────────
    app.include_router(auth_router)

    # ── Project routes ──────────────────────────────────────────────────
    app.include_router(projects_router)

    # ── Organization routes ─────────────────────────────────────────────
    app.include_router(organizations_router)

    # ── Chat session routes ─────────────────────────────────────────────
    app.include_router(chat_router)

    # ── Usage stats routes ──────────────────────────────────────────────
    app.include_router(usage_router)

    # ── Billing routes ──────────────────────────────────────────────────
    app.include_router(billing_router)

    backend = OllamaBackend(settings)
    rag_service = RAGService(RAGConfig.from_settings(settings.rag))
    web_search = WebSearch.from_settings(settings.web)
    assistant = Assistant(backend, settings, rag_service=rag_service, web_search=web_search)

    app.state.backend = backend
    app.state.settings = settings
    app.state.rag_service = rag_service
    app.state.web_search = web_search
    app.state.assistant = assistant

    # ── GET /api/health ─────────────────────────────────────────────────

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        ollama_available = backend.is_available()
        return {
            "status": "up",
            "model": settings.model,
            "ollama_available": ollama_available,
            "rag_enabled": settings.rag.enabled,
            "rag_chunks": _safe_count(rag_service),
            "web_enabled": settings.web.enabled,
        }

    # ── POST /api/chat ──────────────────────────────────────────────────

    @app.post("/api/chat")
    async def chat(req: ChatRequest, user: User = Depends(get_current_user)):
        ask_result, token_stream = assistant.ask_stream(query=req.query, history=req.history)

        if req.stream:
            return StreamingResponse(
                _sse_stream(token_stream, ask_result),
                media_type="text/event-stream",
            )

        full_text = "".join(token_stream)
        return {
            "response": full_text,
            "strategy": ask_result.strategy.name,
            "rag_used": ask_result.rag_used,
            "rag_sources": ask_result.rag_sources,
            "web_used": ask_result.web_used,
            "web_sources": ask_result.web_sources,
        }

    # ── POST /api/agent/run ─────────────────────────────────────────────

    @app.post("/api/agent/run")
    async def agent_run(
        req: AgentRequest,
        user: User = Depends(get_current_user),
    ):
        saas = is_saas()
        project_root = str(user_workspace(user.id) if saas else settings.project_root())
        if req.project_id:
            from .projects.models import Project
            from .database import SessionLocal
            db = SessionLocal()
            try:
                proj = db.query(Project).filter(
                    Project.id == req.project_id, Project.user_id == user.id,
                ).first()
                if proj and proj.root_path:
                    project_root = str(resolve_user_path(user.id, proj.root_path))
            finally:
                db.close()
        if saas and req.allow_write:
            raise HTTPException(
                status_code=403,
                detail="Agent write mode is not available on the hosted service",
            )
        agent = CodingAgent(
            backend=backend,
            project_root=project_root,
            rag_service=rag_service,
            web_search=web_search,
            allow_write=req.allow_write and not saas,
            # pytest runs project code (conftest.py): never on a shared server.
            allow_exec=not saas,
        )

        if req.stream:
            return StreamingResponse(
                _agent_sse_stream(agent, req.task, req.context),
                media_type="text/event-stream",
            )

        result = agent.run(req.task, context=req.context)

        return {
            "success": result.success,
            "final_answer": result.final_answer,
            "iterations_used": result.iterations_used,
            "steps": [
                {
                    "iteration": s.iteration,
                    "thought": s.thought,
                    "tool_name": s.tool_name,
                    "tool_args": s.tool_args,
                    "observation": s.observation,
                    "is_final": s.is_final,
                }
                for s in result.steps
            ],
        }

    # ── POST /api/rag/index ─────────────────────────────────────────────

    @app.post("/api/rag/index")
    async def rag_index(req: IndexRequest, user: User = Depends(get_current_user)):
        if is_saas():
            # The RAG index is shared by the whole process: indexing on a
            # multi-tenant server would expose one user's code to others.
            raise HTTPException(
                status_code=403,
                detail="Server-side indexing is disabled on the hosted service",
            )
        target = resolve_user_path(user.id, req.path)
        if not target.exists():
            return {"error": f"Path not found: {req.path}"}
        chunks = rag_service.index(target)
        return {"chunks_indexed": chunks}

    return app


# ── helpers ──────────────────────────────────────────────────────────────────

def _safe_count(rag_service: RAGService) -> int:
    """Return chunk count without triggering lazy init."""
    try:
        return rag_service.count()
    except Exception:
        return 0


async def _sse_stream(token_stream, ask_result) -> AsyncIterator[str]:
    """Wrap token iterator into SSE format."""
    meta = {
        "strategy": ask_result.strategy.name,
        "rag_used": ask_result.rag_used,
        "rag_sources": ask_result.rag_sources,
        "web_used": ask_result.web_used,
        "web_sources": ask_result.web_sources,
    }
    yield f"event: meta\ndata: {json.dumps(meta)}\n\n"

    for token in token_stream:
        yield f"data: {json.dumps(token)}\n\n"

    yield "event: done\ndata: {}\n\n"


async def _agent_sse_stream(agent, task: str, context: str) -> AsyncIterator[str]:
    """Stream ReAct steps as SSE.

    Protocol:
        event: step → one ReAct iteration (thought / action / observation)
        event: done → final result (success, final_answer, iterations_used)
    """
    gen = agent.run_iter(task, context=context)
    while True:
        try:
            step = next(gen)
        except StopIteration as stop:
            result = stop.value or agent.last_result
            break
        payload = {
            "iteration": step.iteration,
            "thought": step.thought,
            "action": step.action,
            "tool_name": step.tool_name,
            "tool_args": step.tool_args,
            "observation": step.observation,
            "is_final": step.is_final,
        }
        yield f"event: step\ndata: {json.dumps(payload)}\n\n"

    done = {
        "success": result.success,
        "final_answer": result.final_answer,
        "iterations_used": result.iterations_used,
    }
    yield f"event: done\ndata: {json.dumps(done)}\n\n"
