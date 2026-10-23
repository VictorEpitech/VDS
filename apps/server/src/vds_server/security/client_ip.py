"""Adresse IP du client, avec la même règle de proxy de confiance que le contrôle de même origine.

Le serveur d'application (uvicorn `--proxy-headers`, `--forwarded-allow-ips`) ne réécrit
`request.client` que si le proxy est de confiance ; sinon les en-têtes `X-Forwarded-*` sont
ignorés. On ne lit donc jamais ces en-têtes ici (forgeables).
"""

from fastapi import Request


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"
