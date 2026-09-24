"""Projects module: Project model + CRUD routes."""
from .models import Project
from .routes import router

__all__ = ["Project", "router"]
