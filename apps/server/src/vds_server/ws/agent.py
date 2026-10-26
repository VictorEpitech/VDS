"""`<base>/ws/agent` : authentification (Bearer sur l'upgrade) puis session d'agent."""

from fastapi import APIRouter, WebSocket
from vds_sdk.wire import (
    CLOSE_REVOKED,
    CLOSE_UNAUTHORIZED,
    CLOSE_VERSION_UNSUPPORTED,
    SUBPROTOCOL_AGENT,
    ModuleEnabled,
)

from vds_server.agents.auth import lookup
from vds_server.agents.cache import cache_info
from vds_server.agents.service import command_frame_data
from vds_server.agents.session import AgentSession
from vds_server.core.runtime import Runtime
from vds_server.player.device_auth import bearer_value


def build_router(rt: Runtime) -> APIRouter:
    router = APIRouter()

    def modules_enabled() -> list[ModuleEnabled]:
        return [ModuleEnabled(id=i, version=v) for i, v in rt.registry.agent_modules()]

    @router.websocket("/ws/agent")
    async def ws_agent(ws: WebSocket) -> None:
        # Un agent n'est pas un navigateur : tout Origin est refusé avant l'upgrade (403).
        if "origin" in ws.headers:
            await ws.close(code=1008)
            return
        offered = [p.strip() for p in ws.headers.get("sec-websocket-protocol", "").split(",")]
        ok = SUBPROTOCOL_AGENT in offered
        await ws.accept(subprotocol=SUBPROTOCOL_AGENT if ok else None)
        if not ok:
            await ws.close(code=CLOSE_VERSION_UNSUPPORTED)
            return
        async with rt.maker() as s:
            agent = await lookup(s, bearer_value(ws.headers.get("authorization", "")))
        if agent is None:
            await ws.close(code=CLOSE_UNAUTHORIZED)
            return
        if agent.revoked_at is not None:
            await ws.close(code=CLOSE_REVOKED)
            return
        await AgentSession(
            ws,
            agent,
            maker=rt.maker,
            bus=rt.bus,
            hub=rt.agent_hub,
            queue=rt.command_queue,
            bridge=rt.agent_bridge,
            modules_enabled=modules_enabled,
            cache_info=lambda row: cache_info(rt.settings.secret_key, row),
            frame_data=lambda cmd: command_frame_data(cmd, rt.ticket_box),
            on_result=rt.provision_events.on_cmd_result,
        ).run()

    return router
