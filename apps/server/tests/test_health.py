import pytest
from httpx import ASGITransport, AsyncClient
from vds_server.main import create_app
from vds_server.settings import Settings


@pytest.mark.parametrize("base", ["/", "/espace/affichage"])
async def test_health(base: str) -> None:
    app = create_app(Settings(base_path=base))
    prefix = "" if base == "/" else base
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(f"{prefix}/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
