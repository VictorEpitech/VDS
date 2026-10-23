import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from vds_sdk.wire import (
    AGENT_COMMANDS,
    MAX_FRAME_BYTES,
    PLAYER_COMMANDS,
    AgentHello,
    AgentWelcome,
    Cmd,
    Frame,
    SeqReceiver,
    SeqSender,
    WireError,
    decode_frame,
    encode_frame,
    parse_data,
)
from vds_sdk.wire.schema import PATH, render

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def cmd_frame(name: str = "power.set", args: dict[str, Any] | None = None) -> Frame:
    return Frame(
        type="cmd",
        seq=1,
        data={
            "cmd_id": str(uuid4()),
            "name": name,
            "args": args or {},
            "expires_at": NOW.isoformat(),
        },
    )


def test_roundtrip() -> None:
    f = Frame(type="event", seq=3, data={"name": "power.changed", "occurred_at": NOW.isoformat()})
    assert decode_frame(encode_frame(f)) == f


@pytest.mark.parametrize(
    "raw",
    [
        "{",
        "[]",
        '{"type":"hello","v":2,"id":"x"}',
        b"xx",
        '{"type":"cmd","id":"{u}","ts":"2026-01-01T00:00:00Z"}'.replace("{u}", str(uuid4())),
        '{"type":"heartbeat","seq":1,"id":"{u}","ts":"2026-01-01T00:00:00Z"}'.replace(
            "{u}", str(uuid4())
        ),
        '{"type":"ack","extra":1,"id":"{u}","ts":"2026-01-01T00:00:00Z"}'.replace(
            "{u}", str(uuid4())
        ),
    ],
)
def test_invalid(raw: str | bytes) -> None:
    with pytest.raises(WireError) as e:
        decode_frame(raw)
    assert e.value.close_code == 4400


def test_too_large() -> None:
    with pytest.raises(WireError):
        decode_frame(json.dumps({"pad": "x" * MAX_FRAME_BYTES}))


def test_hello_welcome() -> None:
    hello = Frame(
        type="hello",
        data={"agent_version": "1", "core": "1.2.0", "host": {"os": "linux", "arch": "x86_64"}},
    )
    assert isinstance(parse_data(hello, "agent", "to_server"), AgentHello)
    with pytest.raises(WireError):
        parse_data(hello, "agent", "to_peer")
    w = Frame(
        type="welcome",
        data={
            "agent_id": str(uuid4()),
            "site_id": str(uuid4()),
            "session_id": str(uuid4()),
            "server_time": NOW.isoformat(),
            "resume_from": 4,
        },
    )
    assert isinstance(parse_data(w, "agent", "to_peer"), AgentWelcome)
    with pytest.raises(WireError):
        parse_data(Frame(type="hello", data={}), "agent", "to_server")


def test_closed_command_lists() -> None:
    assert "power.detect" not in PLAYER_COMMANDS and "reload" not in AGENT_COMMANDS
    c = Cmd.model_validate(cmd_frame("power.set", {"state": "on", "request_id": "r"}).data)
    assert c.validate_for("agent")
    with pytest.raises(ValueError, match="unknown_command"):
        Cmd.model_validate(cmd_frame("reload").data).validate_for("agent")
    with pytest.raises(ValueError, match="unknown_command"):
        Cmd.model_validate(cmd_frame("rm").data).validate_for("player")
    with pytest.raises(ValueError):
        Cmd.model_validate(cmd_frame("identify", {"seconds": 31}).data).validate_for("player")
    with pytest.raises(ValueError):
        Cmd.model_validate(
            cmd_frame("power.set", {"state": "on", "request_id": "r", "x": 1}).data
        ).validate_for("agent")


def test_player_event_whitelist_and_auth() -> None:
    ev = Frame(
        type="event", seq=1, data={"name": "screen.deleted", "occurred_at": NOW.isoformat()}
    )
    with pytest.raises(WireError):
        parse_data(ev, "player", "to_server")
    auth = parse_data(Frame(type="auth", data={"token": "vds_dev_x"}), "player", "to_server")
    assert auth.token
    with pytest.raises(WireError):
        parse_data(Frame(type="auth", data={}), "player", "to_server")


def test_resume() -> None:
    s = SeqSender()
    frames = [s.stamp(Frame(type="event", data={})) for _ in range(5)]
    assert [f.seq for f in frames] == [1, 2, 3, 4, 5]
    s.on_ack(2)
    assert [f.seq for f in s.replay(2)] == [3, 4, 5]
    assert [f.seq for f in s.replay(4)] == [5]
    with pytest.raises(WireError):
        s.on_ack(9)


def test_receiver_dedup_gap() -> None:
    r = SeqReceiver(last_seq=2)
    assert r.accept(Frame(type="cmd", seq=2)) is False
    assert r.accept(Frame(type="cmd", seq=3)) is True
    with pytest.raises(WireError):
        r.accept(Frame(type="cmd", seq=5))
    assert r.last_seq == 3


def test_window_full() -> None:
    s = SeqSender()
    for _ in range(500):
        s.stamp(Frame(type="event"))
    with pytest.raises(WireError) as e:
        s.stamp(Frame(type="event"))
    assert e.value.close_code == 4429


def test_schema_up_to_date() -> None:
    assert PATH.read_text() == render()
