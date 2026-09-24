"""Fluxion server: FastAPI wrapper exposing the full Fluxion stack."""
from .app import create_app

__all__ = ["create_app"]
