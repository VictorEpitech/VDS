"""Contrat vds-core 1.14.0 : relance de l'application (additif sur 1.13)."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError
from vds_sdk import CORE_API_VERSION
from vds_sdk.agent import (
    AppRestarter,
    AppRestartOutcome,
    AppRestartRequest,
    check_app_restarter,
)
from vds_sdk.errors import ContractError
from vds_sdk.wire import AGENT_COMMANDS, Cmd
from vds_sdk.wire.messages import AGENT_COMMANDS_1_2, RestartArgs


class Good:
    platforms = frozenset({"android"})

    async def restart_app(self, req: AppRestartRequest) -> AppRestartOutcome:
        return AppRestartOutcome(ok=True)


def test_version_and_command() -> None:
    assert CORE_API_VERSION == "1.14.0"
    assert "app.restart" in AGENT_COMMANDS
    assert set(AGENT_COMMANDS_1_2) <= set(AGENT_COMMANDS)


def test_restart_args_via() -> None:
    assert RestartArgs(scope="app").via == "auto"
    assert RestartArgs(scope="app", via="agent").via == "agent"
    with pytest.raises(ValidationError):
        RestartArgs(scope="device", via="agent")
    with pytest.raises(ValidationError):
        RestartArgs(scope="app", via="x")  # type: ignore[arg-type]


def test_agent_cmd_args() -> None:
    now = datetime.now(UTC)
    ok = Cmd(
        cmd_id=uuid4(),
        name="app.restart",
        args={"request_id": str(uuid4()), "platform": "android"},
        expires_at=now + timedelta(seconds=30),
    )
    ok.validate_for("agent")
    bad = ok.model_copy(update={"args": {"request_id": str(uuid4()), "platform": "A b"}})
    with pytest.raises(ValueError):
        bad.validate_for("agent")


def test_check_app_restarter() -> None:
    assert isinstance(Good(), AppRestarter)
    check_app_restarter(Good(), ["app.restart.android"])
    check_app_restarter(object(), ["power.set"])
    with pytest.raises(ContractError):
        check_app_restarter(object(), ["app.restart.android"])
    with pytest.raises(ContractError):
        check_app_restarter(Good(), ["app.restart.tizen"])


def test_outcome_code_pattern() -> None:
    AppRestartOutcome(ok=False, code="app.restart.busy")
    with pytest.raises(ValidationError):
        AppRestartOutcome(ok=False, code="Bad Code")
