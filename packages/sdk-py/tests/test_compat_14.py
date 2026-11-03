"""Contrat vds-core 1.4.0 : enrôlement d'agent (additif sur 1.3)."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError
from vds_sdk import (
    CORE_API_VERSION,
    CORE_EVENT_PAYLOAD_1_3,
    CORE_EVENT_PAYLOAD_1_4,
    CORE_EVENTS_1_3,
    CORE_EVENTS_1_4,
    load_manifest,
    parse_event,
)
from vds_sdk.agent import (
    ENROLL_CODE_PREFIX,
    AgentEnrolled,
    EnrollRequest,
    LocalHandoff,
    LocalPresence,
    parse_presence,
)
from vds_sdk.events import CRITICAL_EVENTS_1_3, CRITICAL_EVENTS_1_4

CODE = ENROLL_CODE_PREFIX + "0123456789ABCDEFGHJKMNPQRS"


def req(**over: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "code": CODE,
        "hostname": "pi-hall.local",
        "agent_version": "1.4.0",
        "platform": "linux/arm64",
    }
    raw.update(over)
    return raw


def presence(**over: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "hostname": "h",
        "agent_version": "1.4.0",
        "platform": "linux/amd64",
        "state": "dormant",
        "ts": datetime.now(UTC).isoformat(),
    }
    raw.update(over)
    return raw


def test_version() -> None:
    assert CORE_API_VERSION in {
        "1.4.0",
        "1.5.0",
        "1.6.0",
        "1.7.0",
        "1.8.0",
        "1.9.0",
        "1.10.0",
        "1.11.0",
    }


def test_events_1_4_additive() -> None:
    new = {"agent.enrollment_created", "agent.enrollment_revoked"}
    assert new == CORE_EVENTS_1_4 - CORE_EVENTS_1_3
    assert CORE_EVENT_PAYLOAD_1_3.items() <= CORE_EVENT_PAYLOAD_1_4.items()
    assert "agent.enrolled" in CORE_EVENTS_1_3  # existait déjà : pas un nouveau nom
    assert CRITICAL_EVENTS_1_4 - CRITICAL_EVENTS_1_3 == {"agent.enrolled"}
    for name in CORE_EVENTS_1_4:
        parse_event(name)


def test_enroll_request_valid() -> None:
    EnrollRequest.model_validate(req())
    EnrollRequest.model_validate(req(previous_token=SecretStr("vds_agt_x")))


@pytest.mark.parametrize(
    "over",
    [
        {"code": "vds_enr_short"},
        {"code": CODE + "A"},
        {"code": CODE.lower()},
        {"code": "vds_enr_" + "I" * 26},  # I, L, O, U exclus de Crockford
        {"code": "vds_enr_" + "O" * 26},
        {"code": "xxx_enr_" + "0" * 26},
        {"hostname": "bad host"},
        {"hostname": "a" * 254},
        {"hostname": ""},
        {"agent_version": "1" * 33},
        {"platform": "p" * 33},
        {"extra": 1},
    ],
)
def test_enroll_request_rejected(over: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        EnrollRequest.model_validate(req(**over))


def test_secrets_not_in_repr() -> None:
    r = EnrollRequest.model_validate(req(previous_token=SecretStr("vds_agt_secret")))
    assert isinstance(r.previous_token, SecretStr)
    h = LocalHandoff(code=SecretStr(CODE), enrollment_id=uuid4(), expires_at=datetime.now(UTC))
    assert CODE not in repr(h) and CODE not in str(h.model_dump())


def test_presence_size_limit() -> None:
    ok = json.dumps(presence()).encode()
    assert parse_presence(ok).state == "dormant"
    big = json.dumps(presence(error="e" * 100, pad="x" * 5000)).encode()
    with pytest.raises(ValueError, match="enroll.presence_too_large"):
        parse_presence(big)
    with pytest.raises(ValidationError):
        LocalPresence.model_validate(presence(hostname="h" * 300))
    with pytest.raises(ValidationError):
        LocalPresence.model_validate(presence(state="weird"))


def test_agent_enrolled_payload() -> None:
    p = AgentEnrolled(
        agent_id=uuid4(),
        site_id=uuid4(),
        enrollment_id=uuid4(),
        channel="local",
        hostname="h",
        platform="linux/amd64",
        agent_version="1",
        replaced=False,
    )
    assert p.moved_from_site_id is None


def driver(core: str, provision: bool) -> dict[str, Any]:
    return {
        "id": "power.adb",
        "version": "0.2.0",
        "core": core,
        "kind": "power-driver",
        "name": {"fr": "a", "en": "b"},
        "description": {"fr": "a", "en": "b"},
        "targets": ["site-agent"],
        "entrypoints": {"site-agent": "m:D"},
        "capabilities": ["power.on", *(["provision.android"] if provision else [])],
        "permissions": [
            "lan.access",
            "power.control",
            *(["screens.provision"] if provision else []),
        ],
        "author": "V-Tech",
    }


@pytest.mark.parametrize(
    ("core", "provision"),
    [(">=1.2 <2.0", False), (">=1.3 <2.0", True), (">=1.4 <2.0", True), (">=1.4 <2.0", False)],
)
def test_modules_load_on_1_4(tmp_path: Path, core: str, provision: bool) -> None:
    (tmp_path / "module.json").write_text(json.dumps(driver(core, provision)))
    assert load_manifest(tmp_path / "module.json", core_version="1.4.0").id == "power.adb"
