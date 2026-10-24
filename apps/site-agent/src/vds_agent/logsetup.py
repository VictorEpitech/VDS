"""Journalisation de l'agent : les champs `extra` sont rendus en `clé=valeur`, secrets masqués."""

import logging

_STD = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {"message", "asctime"}
_SECRET = ("token", "secret", "key", "pin", "authorization", "password")


def _fmt(k: str, v: object) -> str:
    if any(s in k.lower() for s in _SECRET):
        return f"{k}=***"
    return f"{k}={str(v)[:200]!r}" if isinstance(v, str) else f"{k}={v}"


class ExtraFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras = " ".join(_fmt(k, v) for k, v in record.__dict__.items() if k not in _STD)
        return f"{base} {extras}" if extras else base


def setup_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(ExtraFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logging.basicConfig(level=level, handlers=[handler], force=True)
