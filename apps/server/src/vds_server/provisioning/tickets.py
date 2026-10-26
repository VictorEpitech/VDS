"""Ticket d'appairage : usage unique, haché en base, chiffré (Fernet) dans la commande."""

import base64
import hashlib
import secrets

from cryptography.fernet import Fernet, InvalidToken

from vds_server.agents.cache import hkdf_sha256

TICKET_TTL_S = 30 * 60
ARGS_KEY = "pairing_ticket_enc"
# Ticket de forme valide mais jamais émis : remis à l'agent si le chiffré n'est plus lisible
# (clé changée, session close). Le player échoue alors proprement vers le code manuel.
UNUSABLE_TICKET = "A" * 43


def new_ticket() -> str:
    return secrets.token_urlsafe(32)  # 43 caractères base64url


def ticket_hash(ticket: str) -> bytes:
    return hashlib.sha256(ticket.encode()).digest()


class TicketBox:
    """Chiffrement du ticket au repos ; clé dérivée de `VDS_SECRET_KEY` (HKDF)."""

    def __init__(self, secret: str) -> None:
        key = hkdf_sha256(secret.encode(), b"vds-provision-ticket")
        self._fernet = Fernet(base64.urlsafe_b64encode(key))

    def encrypt(self, ticket: str) -> str:
        return self._fernet.encrypt(ticket.encode()).decode()

    def decrypt(self, token: str) -> str | None:
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except (InvalidToken, ValueError):
            return None
