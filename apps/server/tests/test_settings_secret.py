"""VDS_SECRET_KEY : vide (docker compose) = non définie ; trop courte = refusée."""

import pytest
from pydantic import ValidationError
from vds_server.settings import Settings


def test_empty_secret_key_is_none() -> None:
    assert Settings(secret_key="").secret_key is None  # noqa: S106
    assert Settings(secret_key="  ").secret_key is None  # noqa: S106


def test_short_secret_key_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(secret_key="court")  # noqa: S106


def test_long_secret_key_kept() -> None:
    assert Settings(secret_key="k" * 40).secret_key == "k" * 40  # noqa: S106
