"""Chat session + message API routes.

    POST   /api/sessions                    — create a new session
    GET    /api/sessions                    — list user's sessions
    GET    /api/sessions/{id}               — get session details
    PATCH  /api/sessions/{id}               — update title / archive
    DELETE /api/sessions/{id}               — delete session + messages
    GET    /api/sessions/{id}/messages      — list messages in session
    POST   /api/sessions/{id}/chat          — send message, get AI reply (persisted)
    POST   /api/sessions/{id}/chat?stream   — same, streamed as SSE events:
                                             meta → token data … → done
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth.dependencies import get_current_user
from ..auth.models import User
from ..database import get_db
from .models import ChatMessage, ChatSession

router = APIRouter(prefix="/api/sessions", tags=["chat"])


# ── request / response schemas ───────────────────────────────────────────────

class SessionCreate(BaseModel):
    title: str = Field(default="New Chat", min_length=1, max_length=300)
    project_id: str | None = None


class SessionUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    is_archived: bool | None = None


class SessionResponse(BaseModel):
    id: str
    user_id: str
    project_id: str | None
    title: str
    is_archived: bool
    message_count: int
    created_at: str | None = None
    updated_at: str | None = None


class MessageResponse(BaseModel):
    id: str
    session_id: str
    seq: int
    role: str
    content: str
    meta: dict
    created_at: str | None = None


class SessionChatRequest(BaseModel):
    query: str = Field(min_length=1)
    stream: bool = False


class SessionChatResponse(BaseModel):
    user_message: MessageResponse
    assistant_message: MessageResponse
    strategy: str | None = None
    rag_used: bool = False
    rag_sources: list = []
    web_used: bool = False
    web_sources: list = []


# ── session CRUD ─────────────────────────────────────────────────────────────

@router.post("", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
def create_session(
    req: SessionCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a new chat session."""
    session = ChatSession(
        user_id=user.id,
        project_id=req.project_id,
        title=req.title,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return SessionResponse(**session.to_dict())


@router.get("", response_model=list[SessionResponse])
def list_sessions(
    archived: bool = False,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List the user's sessions (non-archived by default)."""
    sessions = (
        db.query(ChatSession)
        .filter(ChatSession.user_id == user.id, ChatSession.is_archived == archived)
        .order_by(ChatSession.updated_at.desc())
        .all()
    )
    return [SessionResponse(**s.to_dict()) for s in sessions]


@router.get("/{session_id}", response_model=SessionResponse)
def get_session(
    session_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    session = _get_owned_session(db, session_id, user.id)
    return SessionResponse(**session.to_dict())


@router.patch("/{session_id}", response_model=SessionResponse)
def update_session(
    session_id: str,
    req: SessionUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    session = _get_owned_session(db, session_id, user.id)
    updates = req.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(session, field, value)
    db.commit()
    db.refresh(session)
    return SessionResponse(**session.to_dict())


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_session(
    session_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    session = _get_owned_session(db, session_id, user.id)
    db.delete(session)
    db.commit()


# ── messages ─────────────────────────────────────────────────────────────────

@router.get("/{session_id}/messages", response_model=list[MessageResponse])
def list_messages(
    session_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all messages in a session ordered by sequence."""
    session = _get_owned_session(db, session_id, user.id)
    return [MessageResponse(**m.to_dict()) for m in session.messages]


@router.post("/{session_id}/chat", response_model=SessionChatResponse)
def chat_in_session(
    session_id: str,
    req: SessionChatRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Send a message to a session and get the AI reply. Both are persisted."""
    session = _get_owned_session(db, session_id, user.id)

    # Build conversation history from previous messages
    history = [
        {"role": m.role, "content": m.content}
        for m in session.messages
    ]

    # Persist the user message
    user_msg = ChatMessage(
        session_id=session.id,
        seq=session.message_count,
        role="user",
        content=req.query,
    )
    db.add(user_msg)
    session.message_count += 1
    db.flush()

    # Generate AI response via the assistant
    assistant = request.app.state.assistant
    ask_result, token_stream = assistant.ask_stream(query=req.query, history=history)

    response_meta = {
        "strategy": ask_result.strategy.name,
        "rag_used": ask_result.rag_used,
        "rag_sources": ask_result.rag_sources,
        "web_used": ask_result.web_used,
        "web_sources": ask_result.web_sources,
    }

    # ── streaming branch: SSE with persistence after the stream ends ──
    if req.stream:
        # The Depends(get_db) session may close before streaming finishes,
        # so persistence inside the generator uses a fresh session from
        # app.state.session_factory (set by create_app / overridden in tests).
        if session.title == "New Chat" and session.message_count == 1:
            session.title = req.query[:80]
        db.commit()  # persist the user message now
        session_factory = request.app.state.session_factory
        stream_ctx = {
            "session_id": session.id,
            "assistant_seq": session.message_count,
            "user_message_id": user_msg.id,
        }
        return StreamingResponse(
            _stream_chat_reply(token_stream, response_meta, stream_ctx, session_factory),
            media_type="text/event-stream",
        )

    full_text = "".join(token_stream)

    # Persist the assistant message
    ai_msg = ChatMessage(
        session_id=session.id,
        seq=session.message_count,
        role="assistant",
        content=full_text,
        meta=response_meta,
    )
    db.add(ai_msg)
    session.message_count += 1

    # Auto-title from first user message if title is still default
    if session.title == "New Chat" and session.message_count == 2:
        session.title = req.query[:80]

    db.commit()
    db.refresh(user_msg)
    db.refresh(ai_msg)

    return SessionChatResponse(
        user_message=MessageResponse(**user_msg.to_dict()),
        assistant_message=MessageResponse(**ai_msg.to_dict()),
        **response_meta,
    )


# ── helpers ──────────────────────────────────────────────────────────────────

async def _stream_chat_reply(
    token_stream,
    meta: dict,
    ctx: dict,
    session_factory,
):
    """Stream tokens as SSE, then persist the assistant message.

    SSE protocol (matches POST /api/chat):
        event: meta   → {"strategy": …, "rag_used": …, …}
        data: "<token>" …
        event: done   → {"session_id", "user_message_id", "assistant_message_id"}
    """
    yield f"event: meta\ndata: {json.dumps(meta)}\n\n"

    parts: list[str] = []
    for token in token_stream:
        parts.append(token)
        yield f"data: {json.dumps(token)}\n\n"
    full_text = "".join(parts)

    assistant_message_id = None
    db = session_factory()
    try:
        session = db.query(ChatSession).filter(
            ChatSession.id == ctx["session_id"],
        ).first()
        if session is not None:
            ai_msg = ChatMessage(
                session_id=session.id,
                seq=ctx["assistant_seq"],
                role="assistant",
                content=full_text,
                meta=meta,
            )
            db.add(ai_msg)
            session.message_count += 1
            db.commit()
            db.refresh(ai_msg)
            assistant_message_id = ai_msg.id
    finally:
        db.close()

    done = {
        "session_id": ctx["session_id"],
        "user_message_id": ctx["user_message_id"],
        "assistant_message_id": assistant_message_id,
    }
    yield f"event: done\ndata: {json.dumps(done)}\n\n"


def _get_owned_session(db: Session, session_id: str, user_id: str) -> ChatSession:
    """Fetch a session and verify ownership. Raises 404 if not found."""
    session = db.query(ChatSession).filter(
        ChatSession.id == session_id, ChatSession.user_id == user_id,
    ).first()
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )
    return session
