"""Génère schemas/module.schema.json depuis le modèle : `python -m vds_sdk.gen_schema`."""

import json
from pathlib import Path

from .manifest import ModuleManifest

PATH = Path(__file__).parent / "schemas" / "module.schema.json"


def render() -> str:
    return json.dumps(ModuleManifest.model_json_schema(by_alias=True), indent=2) + "\n"


if __name__ == "__main__":
    PATH.write_text(render(), encoding="utf-8")
