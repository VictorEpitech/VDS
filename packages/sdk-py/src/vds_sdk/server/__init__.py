"""Kinds de module côté serveur (sans dépendance FastAPI)."""

from .auth import AuthProvider, AuthRequest, Identity, ShellLink
from .notifier import DeliveryResult, Notification, Notifier, Recipient
from .screens import ScreenCommandRequest, ScreensApi
from .storage import StorageBackend, StoredObject

__all__ = [
    "AuthProvider",
    "AuthRequest",
    "DeliveryResult",
    "Identity",
    "Notification",
    "Notifier",
    "Recipient",
    "ScreenCommandRequest",
    "ScreensApi",
    "ShellLink",
    "StorageBackend",
    "StoredObject",
]
