"""Contrat vds-core 1.8.0 : médiathèque (additif sur 1.7)."""

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from pydantic import TypeAdapter
from vds_sdk import (
    CORE_API_VERSION,
    CORE_EVENT_PAYLOAD_1_7,
    CORE_EVENT_PAYLOAD_1_8,
    CORE_EVENTS_1_7,
    CORE_EVENTS_1_8,
    load_manifest,
    parse_event,
)
from vds_sdk.events import CRITICAL_EVENTS_1_7, CRITICAL_EVENTS_1_8, SCHEDULE_DELETED_REASONS_1_8
from vds_sdk.server.media import MediaInfo

NEW = {
    "media.updated",
    "media.replaced",
    "media.expired",
    "media.restored",
    "media.purged",
    "playlist.expired",
    "playlist.restored",
    "playlist.purged",
}


def test_version() -> None:
    assert CORE_API_VERSION == "1.8.0"


def test_events_1_8_additive() -> None:
    assert NEW == CORE_EVENTS_1_8 - CORE_EVENTS_1_7
    assert CORE_EVENT_PAYLOAD_1_7.items() <= CORE_EVENT_PAYLOAD_1_8.items()
    assert CRITICAL_EVENTS_1_8 - CRITICAL_EVENTS_1_7 == {"media.purged", "playlist.purged"}
    assert CORE_EVENT_PAYLOAD_1_8["schedule.deleted"] == ("schedule_id",)  # reason optionnel
    assert SCHEDULE_DELETED_REASONS_1_8 == {"user", "playlist_purged"}
    for name in NEW:
        assert parse_event(name + "@1").version == 1


def test_media_info_new_fields_optional() -> None:
    base: dict[str, Any] = {
        "id": uuid4(),
        "kind": "image",
        "mime": "image/png",
        "bytes": 1,
        "sha256": "a" * 64,
    }
    info = MediaInfo(**base)
    assert info.revision == 1 and info.expires_at is None
    schema = TypeAdapter(MediaInfo).json_schema()
    assert {"revision", "expires_at"} <= set(schema["properties"])
    assert not {"revision", "expires_at"} & set(schema.get("required", []))
    json.dumps(schema)


@pytest.mark.parametrize("core", [">=1.1 <2.0", ">=1.1 <1.9"])
def test_older_modules_load(tmp_path: Path, core: str) -> None:
    raw = {
        "id": "content.demo",
        "version": "0.1.0",
        "core": core,
        "kind": "content-type",
        "name": {"fr": "a", "en": "b"},
        "description": {"fr": "a", "en": "b"},
        "targets": ["server"],
        "entrypoints": {"server": "m:C"},
        "permissions": [],
        "author": "V-Tech",
        "content": {
            "item_schema": "item.schema.json",
            "player": {"element": "vds-content-demo-player", "script": "player.js"},
            "media": {"accepts": ["image/png"]},
        },
    }
    schema = {"type": "object", "additionalProperties": False, "properties": {}}
    (tmp_path / "item.schema.json").write_text(json.dumps(schema))
    (tmp_path / "player.js").write_text("")
    (tmp_path / "module.json").write_text(json.dumps(raw))
    assert load_manifest(tmp_path / "module.json", core_version="1.8.0").id == "content.demo"
