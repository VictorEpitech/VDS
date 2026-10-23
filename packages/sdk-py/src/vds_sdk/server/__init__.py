"""Kinds de module côté serveur (sans dépendance FastAPI)."""

from .auth import AuthProvider, AuthRequest, Identity, ShellLink

__all__ = ["AuthProvider", "AuthRequest", "Identity", "ShellLink"]
