"""Project CRUD API routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth.dependencies import get_current_user
from ..auth.models import User
from ..database import get_db
from .models import Project

router = APIRouter(prefix="/api/projects", tags=["projects"])


# ── request / response schemas ───────────────────────────────────────────────

class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    root_path: str | None = None
    settings: dict | None = None


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    root_path: str | None = None
    settings: dict | None = None
    is_active: bool | None = None


class ProjectResponse(BaseModel):
    id: str
    user_id: str
    name: str
    description: str | None
    root_path: str | None
    settings: dict
    is_active: bool
    created_at: str | None = None
    updated_at: str | None = None


# ── routes ───────────────────────────────────────────────────────────────────

@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
def create_project(
    req: ProjectCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a new project for the authenticated user."""
    project = Project(
        user_id=user.id,
        name=req.name,
        description=req.description,
        root_path=req.root_path,
        settings=req.settings or {},
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return ProjectResponse(**project.to_dict())


@router.get("", response_model=list[ProjectResponse])
def list_projects(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all projects for the authenticated user."""
    projects = (
        db.query(Project)
        .filter(Project.user_id == user.id)
        .order_by(Project.created_at.desc())
        .all()
    )
    return [ProjectResponse(**p.to_dict()) for p in projects]


@router.get("/{project_id}", response_model=ProjectResponse)
def get_project(
    project_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get a single project by ID (must belong to the user)."""
    project = _get_owned_project(db, project_id, user.id)
    return ProjectResponse(**project.to_dict())


@router.patch("/{project_id}", response_model=ProjectResponse)
def update_project(
    project_id: str,
    req: ProjectUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Partially update a project."""
    project = _get_owned_project(db, project_id, user.id)
    updates = req.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(project, field, value)
    db.commit()
    db.refresh(project)
    return ProjectResponse(**project.to_dict())


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    project_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a project."""
    project = _get_owned_project(db, project_id, user.id)
    db.delete(project)
    db.commit()


# ── helpers ──────────────────────────────────────────────────────────────────

def _get_owned_project(db: Session, project_id: str, user_id: str) -> Project:
    """Fetch a project and verify ownership. Raises 404 if not found or not owned."""
    project = db.query(Project).filter(
        Project.id == project_id, Project.user_id == user_id,
    ).first()
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )
    return project
