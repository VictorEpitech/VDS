"""Contrat vds-core 1.11.0 : commandes player et captures (additif sur 1.10)."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError
from vds_sdk import (
    CORE_API_VERSION,
    CORE_EVENT_PAYLOAD_1_10,
    CORE_EVENT_PAYLOAD_1_11,
    CORE_EVENTS_1_10,
    CORE_EVENTS_1_11,
    parse_event,
    permissions,
)
from vds_sdk.events import CRITICAL_EVENTS_1_10, CRITICAL_EVENTS_1_11
from vds_sdk.server import ScreenCommandRequest, ScreensApi
from vds_sdk.wire import (
    AGENT_COMMANDS,
    PLAYER_CAPABILITIES,
    PLAYER_COMMANDS,
    Cmd,
    PlayerCommandRef,
    PlayerHeartbeatAckExt,
    PlayerHeartbeatExt,
    ScreenshotSchedule,
)
from vds_sdk.wire.messages import PLAYER_COMMANDS_1_2

NEW = {
    "player.command_requested",
    "player.command_completed",
    "screen.screenshot_captured",
}
NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_version() -> None:
    assert CORE_API_VERSION == "1.11.0"


def test_events_1_11_additive() -> None:
    assert NEW == CORE_EVENTS_1_11 - CORE_EVENTS_1_10
    assert CORE_EVENT_PAYLOAD_1_10.items() <= CORE_EVENT_PAYLOAD_1_11.items()
    assert CRITICAL_EVENTS_1_11 == CRITICAL_EVENTS_1_10
    assert CORE_EVENT_PAYLOAD_1_11["player.command_completed"][-2:] == ("status", "code")
    for name in NEW:
        assert parse_event(name + "@1").version == 1


def test_permission_and_facade() -> None:
    assert "screens.command" in permissions.PERMISSIONS
    req = ScreenCommandRequest(screen_id=uuid4(), name="screenshot")
    assert req.ttl_s == 600 and req.args == {}
    for name in ("restart", "cache.resync"):  # interdits aux modules en V1
        with pytest.raises(ValidationError):
            ScreenCommandRequest.model_validate({"screen_id": uuid4(), "name": name})
    assert hasattr(ScreensApi, "send_command")


def test_player_commands_additive() -> None:
    assert set(PLAYER_COMMANDS_1_2) <= set(PLAYER_COMMANDS)
    assert {"restart", "cache.resync"} <= set(PLAYER_COMMANDS)
    assert "restart" not in AGENT_COMMANDS and "cache.resync" not in AGENT_COMMANDS


def _cmd(name: str, args: dict[str, Any]) -> Cmd:
    return Cmd(cmd_id=uuid4(), name=name, args=args, expires_at=NOW)


def test_command_args() -> None:
    assert _cmd("restart", {"scope": "app"}).validate_for("player")
    assert _cmd("cache.resync", {}).validate_for("player")
    for bad in ({"scope": "os"}, {}, {"scope": "app", "x": 1}):
        with pytest.raises(ValueError):
            _cmd("restart", bad).validate_for("player")
    with pytest.raises(ValueError, match="unknown_command"):
        _cmd("restart", {"scope": "app"}).validate_for("agent")


def test_heartbeat_extras_optional_and_bounded() -> None:
    # player 1.10 : aucun champ 1.11 -> valide, capacités absentes
    legacy = PlayerHeartbeatExt.model_validate({})
    assert legacy.capabilities is None and legacy.boot_id is None and legacy.command_results == []
    assert len(PLAYER_CAPABILITIES) == 6 and "cmd.reload" in PLAYER_CAPABILITIES
    full = PlayerHeartbeatExt.model_validate(
        {"capabilities": ["cmd.reload", "cmd.restart.app"], "boot_id": str(uuid4())}
    )
    assert full.capabilities == ["cmd.reload", "cmd.restart.app"]
    with pytest.raises(ValidationError):
        PlayerHeartbeatExt.model_validate({"capabilities": ["cmd.rm"]})
    result = {"cmd_id": str(uuid4()), "status": "ok"}
    with pytest.raises(ValidationError):
        PlayerHeartbeatExt.model_validate({"command_results": [result] * 21})
    ack = PlayerHeartbeatAckExt.model_validate({})
    assert ack.commands == [] and ack.results_acked == []
    ref = {"cmd_id": str(uuid4()), "name": "reload", "expires_at": NOW.isoformat()}
    assert PlayerCommandRef.model_validate(ref).args == {}
    with pytest.raises(ValidationError):
        PlayerHeartbeatAckExt.model_validate({"commands": [ref] * 11})


def test_screenshot_schedule_bounds() -> None:
    assert ScreenshotSchedule(interval_min=5).interval_min == 5
    for bad in (0, 4, 1441):
        with pytest.raises(ValidationError):
            ScreenshotSchedule(interval_min=bad)
