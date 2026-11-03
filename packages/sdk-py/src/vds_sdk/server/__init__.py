"""Kinds de module côté serveur (sans dépendance FastAPI)."""

from .auth import AuthProvider, AuthRequest, Identity, ShellLink
from .feeds import FeedRequest, FeedsApi
from .http import SafeHttp, SafeHttpError, SafeResponse
from .notifier import DeliveryResult, Notification, Notifier, Recipient
from .screens import ScreenCommandRequest, ScreensApi
from .storage import StorageBackend, StoredObject

__all__ = [
    "AuthProvider",
    "AuthRequest",
    "DeliveryResult",
    "FeedRequest",
    "FeedsApi",
    "Identity",
    "Notification",
    "Notifier",
    "Recipient",
    "SafeHttp",
    "SafeHttpError",
    "SafeResponse",
    "ScreenCommandRequest",
    "ScreensApi",
    "ShellLink",
    "StorageBackend",
    "StoredObject",
]
