from pathlib import Path

from vds_sdk import load_manifest


def test_manifest() -> None:
    m = load_manifest(Path(__file__).parents[1] / "module.json")
    assert m.id == "power.cec"
    assert m.kind == "power-driver"
    assert m.targets == ["player"]  # code livré dans le bundle du player (apps/player/src/power)
