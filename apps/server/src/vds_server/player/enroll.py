"""Ajout d'un écran par code TV : création + réclamation dans une seule transaction."""

from sqlalchemy.ext.asyncio import AsyncSession
from vds_sdk import EventBus

from vds_server.core.auth import Principal
from vds_server.core.errors import ApiError
from vds_server.db.models import PairingRequest, Screen
from vds_server.inventory import schemas as sc
from vds_server.inventory.service import InventoryService
from vds_server.player.service import PairingService, ScreenReplay


def _orientation(row: PairingRequest) -> sc.Orientation:
    dev = row.device if isinstance(row.device, dict) else {}
    w, h = dev.get("screen_w"), dev.get("screen_h")
    if isinstance(w, int) and isinstance(h, int) and h > w:
        return "portrait"
    return "landscape"


async def pair_new_screen(
    session: AsyncSession, bus: EventBus | None, actor: Principal, body: sc.ScreenPair
) -> tuple[Screen, PairingRequest, bool]:
    """Renvoie (écran, demande, rejoué). Aucun écran n'est créé si le code est refusé."""
    pairing = PairingService(session, bus)
    locked = await pairing.lock_code(body.code, actor)
    if isinstance(locked, ScreenReplay):
        screen = await session.get(Screen, locked.row.screen_id)
        if screen is None:  # défensif : lock_code a déjà vérifié l'écran
            raise ApiError("errors.pairing_code_claimed", 409)
        return screen, locked.row, True
    inventory = InventoryService(session, bus, actor)
    try:
        screen = await inventory.add_screen(
            sc.ScreenCreate(
                site_id=body.site_id,
                group_id=body.group_id,
                name=body.name,
                orientation=body.orientation or _orientation(locked),
            ),
            via="screens.pair",
        )
        pairing.mark_claimed(actor, locked, screen.id)
        await session.commit()
    except BaseException:
        await session.rollback()
        raise
    await inventory.publish_pending()
    return screen, locked, False
