"""Export JSON Schema : `python -m vds_sdk.wire.schema` -> schemas/wire.schema.json."""

import json
from pathlib import Path

from pydantic import BaseModel
from pydantic.json_schema import models_json_schema

from . import messages
from .frame import Frame

PATH = Path(__file__).parent.parent / "schemas" / "wire.schema.json"


def render() -> str:
    found: set[type[BaseModel]] = {Frame}
    for table in (messages.AGENT_FRAME_DATA, messages.PLAYER_FRAME_DATA):
        found.update(m for m in table.values() if m is not BaseModel)
    models = sorted(found, key=lambda m: m.__name__)
    _, schema = models_json_schema([(m, "validation") for m in models], title="vds wire v1")
    schema["x-agent-commands"] = list(messages.AGENT_COMMANDS)
    schema["x-player-commands"] = list(messages.PLAYER_COMMANDS)
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    PATH.write_text(render(), encoding="utf-8")
