"""Kinds de module côté serveur (sans dépendance FastAPI)."""

from .auth import AuthProvider, AuthRequest, Identity, ShellLink
from .storage import StorageBackend, StoredObject

__all__ = ["StorageBackend", "StoredObject", "AuthProvider", "AuthRequest", "Identity", "ShellLink"]
