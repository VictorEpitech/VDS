"""Validation du paramètre return_to (anti open-redirect)."""

from urllib.parse import urlsplit

from vds_server.settings import Settings


def safe_return_to(value: str | None, settings: Settings) -> str:
    """Renvoie value si c'est un chemin sous <base>/, sinon la racine du préfixe."""
    default = settings.url("/")
    if not value:
        return default
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in value) or "\\" in value:
        return default
    if not value.startswith(default) or value.startswith("//"):
        return default
    parts = urlsplit(value)
    if parts.scheme or parts.netloc:
        return default
    if ".." in parts.path.split("/"):
        return default
    return value
