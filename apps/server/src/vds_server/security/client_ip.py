"""Adresse IP du client, avec la même règle de proxy de confiance que le contrôle de même origine.

Le serveur d'application (uvicorn `--proxy-headers`, `--forwarded-allow-ips`) ne réécrit
`request.client` que si le proxy est de confiance ; sinon les en-têtes `X-Forwarded-*` sont
ignorés. On ne lit donc jamais ces en-têtes ici (forgeables).
"""

import ipaddress

from fastapi import Request


def client_ip(request: Request) -> str:
    return limiter_key(request.client.host if request.client else "unknown")


def limiter_key(host: str) -> str:
    """IPv4 telle quelle ; IPv6 réduite à son préfixe /64 (un client en tient des milliards)."""
    try:
        addr = ipaddress.ip_address(host.split("%")[0])
    except ValueError:
        return host
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped is not None:
            return str(addr.ipv4_mapped)
        return str(ipaddress.ip_network(f"{addr}/64", strict=False))
    return str(addr)
