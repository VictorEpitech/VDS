"""Bandes de priorité d'affichage."""

import pytest
from vds_server.diffusion.priority import DEFAULT_PRIORITY, priority_level


@pytest.mark.parametrize(
    ("p", "level"),
    [
        (-1000, "background"),
        (-1, "background"),
        (0, "normal"),
        (99, "normal"),
        (100, "high"),
        (1000, "high"),
    ],
)
def test_bands(p: int, level: str) -> None:
    assert priority_level(p) == level


def test_defaults_land_in_their_band() -> None:
    assert all(priority_level(v) == k for k, v in DEFAULT_PRIORITY.items())
