"""DELETE /screens/{id} : aucun orphelin, player révoqué, audit conservé (2 VDS_BASE_PATH)."""
# ruff: noqa: S608

from typing import Any

from fastapi import FastAPI
from httpx import AsyncClient
from test_agents import sql
from test_auth import SAME, admin, anon, base, clean, editor, new_client, p  # noqa: F401
from test_pairing import DEVICE, hdr, pair  # noqa: F401
from test_provisioning import _queue_clean, app, online, setup, start  # noqa: F401

PER_SCREEN = (
    "SELECT (SELECT count(*) FROM screen_device), (SELECT count(*) FROM screen_provision),"
    " (SELECT count(*) FROM pairing_request WHERE screen_id IS NOT NULL),"
    " (SELECT count(*) FROM remote_command WHERE screen_id IS NOT NULL),"
    " (SELECT count(*) FROM schedule WHERE target_type = 'screen'),"
    " (SELECT count(*) FROM config_override WHERE screen_id IS NOT NULL),"
    " (SELECT count(*) FROM power_exception WHERE scope_level = 'screen'),"
    " (SELECT count(*) FROM incident WHERE subject_type = 'screen')"
)


async def test_delete_paired_provisioned_scheduled(
    admin: AsyncClient,  # noqa: F811
    app: FastAPI,  # noqa: F811
    p: str,  # noqa: F811
    setup: dict[str, Any],  # noqa: F811
    migrated_db_url: str,
) -> None:
    s = setup
    screen = s["screen"]
    ws = await online(app, p, s)
    await start(admin, p, s, device_owner="skip")
    assert (await ws.recv()) is not None  # commande en file/envoyée visant l'écran
    async with new_client(app) as player:
        token = await pair(admin, player, p, screen)
        hb = f"{p}/api/v1/player/heartbeat"
        assert (
            await player.post(hb, json={"revision": "x"}, headers=hdr(token))
        ).status_code == 200
        pl = await admin.post(f"{p}/api/v1/playlists", json={"name": "P"}, headers=SAME)
        assert pl.status_code == 201, pl.text
        sch = await admin.post(
            f"{p}/api/v1/schedules",
            json={
                "name": "S",
                "playlist_id": pl.json()["id"],
                "target_type": "screen",
                "target_id": screen,
            },
            headers=SAME,
        )
        assert sch.status_code == 201, sch.text
        await admin.put(
            f"{p}/api/v1/config/screen/{screen}/core/timezone",
            json={"value": "Europe/Paris"},
            headers=SAME,
        )
        await sql(
            migrated_db_url,
            "INSERT INTO power_exception (scope_level, scope_id, label, starts_on, ends_on, state)"
            f" VALUES ('screen', '{screen}', 'x', '2026-01-01', '2026-01-02', 'off')",
        )
        await sql(
            migrated_db_url,
            "INSERT INTO incident (kind, severity, subject_type, subject_id, opened_at)"
            f" VALUES ('screen.offline', 'warning', 'screen', '{screen}', now())",
        )
        agent_seqs = "SELECT max(seq), count(*) FROM remote_command WHERE target_kind = 'agent'"
        seq_before = tuple((await sql(migrated_db_url, agent_seqs))[0])
        assert seq_before[0] >= 1
        before = (await sql(migrated_db_url, PER_SCREEN))[0]
        assert all(n >= 1 for n in before[:5]) and before[6] == before[7] == 1, before

        r = await admin.delete(f"{p}/api/v1/screens/{screen}", headers=SAME)
        assert r.status_code == 204, r.text

        assert tuple((await sql(migrated_db_url, PER_SCREEN))[0]) == (0, 0, 0, 0, 0, 0, 0, 0)
        # file append-only : max(seq) ne régresse pas (sinon l'agent ignore les nouvelles commandes)
        after = tuple((await sql(migrated_db_url, agent_seqs))[0])
        assert after[0] >= seq_before[0] and after[1] >= seq_before[1]  # (plan.updated en plus)
        left = await sql(
            migrated_db_url,
            "SELECT count(*) FROM remote_command WHERE name = 'screen.provision'"
            " AND status IN ('queued','sent')",
        )
        assert left[0][0] == 0  # plus rien d'exécutable pour l'écran supprimé
        gone = await player.post(hb, json={}, headers=hdr(token))
        assert gone.status_code == 401
        assert (await admin.get(f"{p}/api/v1/screens/{screen}")).status_code == 404
        assert (await admin.delete(f"{p}/api/v1/screens/{screen}", headers=SAME)).status_code == 404
    audit = await sql(
        migrated_db_url,
        "SELECT details::text FROM audit_log WHERE action = 'screen.deleted'"
        f" AND target_id = '{screen}'",
    )
    assert len(audit) == 1
    assert token not in audit[0][0] and '"devices_revoked": 1' in audit[0][0].replace('":1', '": 1')
