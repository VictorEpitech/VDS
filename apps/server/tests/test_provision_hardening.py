"""Durcissements mineurs M4 à M7 de la revue provisioning Android."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError
from vds_sdk.agent import ProvisionOutcome
from vds_sdk.wire.messages import ScreenProvisionArgs
from vds_server.agents.schemas import CommandOut
from vds_server.provisioning.tickets import ARGS_KEY
from vds_server.settings import Settings


def test_command_out_masks_encrypted_ticket() -> None:  # M5
    now = datetime.now(UTC)
    out = CommandOut(
        id=uuid4(), seq=1, name="screen.provision", screen_id=None,
        args={ARGS_KEY: "gAAAA-secret", "port": 5555}, status="queued", code=None,
        result=None, created_at=now, sent_at=None, done_at=None, expires_at=now,
    )  # fmt: skip
    assert out.args == {ARGS_KEY: "***", "port": 5555}


def test_outcome_code_is_bounded() -> None:  # M4
    assert ProvisionOutcome(ok=True, kiosk="soft", code="provision.android.no_kiosk")
    with pytest.raises(ValidationError):
        ProvisionOutcome(ok=True, kiosk="soft", code="x" * 200)
    with pytest.raises(ValidationError):
        ProvisionOutcome(ok=True, kiosk="soft", code="<script>")


def test_validation_errors_never_echo_input() -> None:  # M6
    with pytest.raises(ValidationError) as exc:
        ScreenProvisionArgs.model_validate({"pairing_ticket": "TICKET-SECRET-123", "bogus": 1})
    assert "TICKET-SECRET-123" not in str(exc.value)


def test_settings_error_hides_secret_key() -> None:  # M7
    with pytest.raises(ValidationError) as exc:
        Settings(secret_key="court-et-secret")  # noqa: S106
    assert "court-et-secret" not in str(exc.value)
