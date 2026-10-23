import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from vds_sdk import (
    CORE_API_VERSION,
    ContractError,
    IncompatibleCore,
    load_manifest,
)
from vds_sdk.server.content import ContentType, PlayerItemSpec
from vds_sdk.server.media import MediaFacade, MediaInfo
from vds_sdk.server.storage import StorageBackend


def _write_module(
    tmp_path: Path, data: dict[str, Any], item_schema: dict[str, Any] | None = None
) -> None:
    """Écrit module.json et item.schema.json dans tmp_path."""
    (tmp_path / "module.json").write_text(json.dumps(data))
    if item_schema is not None:
        (tmp_path / "item.schema.json").write_text(json.dumps(item_schema))
    if "content" in data:
        (tmp_path / data["content"]["player"]["script"]).write_text("")


def valid_manifest() -> dict[str, Any]:
    return {
        "id": "power.samsung-ws",
        "version": "1.2.0",
        "core": ">=1.0 <2.0",
        "kind": "power-driver",
        "name": {"fr": "a", "en": "b"},
        "description": {"fr": "a", "en": "b"},
        "targets": ["server", "site-agent"],
        "entrypoints": {"server": "server.module:D", "site-agent": "agent.module:D"},
        "permissions": ["lan.access"],
        "events": {"listens": ["power.requested"], "emits": ["power.changed@2"]},
        "author": "V-Tech",
    }


def valid_content_type() -> dict[str, Any]:
    return {
        "id": "content.video",
        "version": "1.0.0",
        "core": ">=1.1 <2.0",
        "kind": "content-type",
        "name": {"fr": "Vidéo", "en": "Video"},
        "description": {"fr": "Contenu vidéo", "en": "Video content"},
        "targets": ["server"],
        "entrypoints": {"server": "module:Module"},
        "permissions": [],
        "author": "V-Tech",
        "content": {
            "item_schema": "item.schema.json",
            "player": {"element": "vds-content-video-player", "script": "player.js"},
            "media": {"accepts": ["video/mp4"]},
        },
    }


def test_core_version_is_1_1() -> None:
    """CORE_API_VERSION == "1.1.0"."""
    assert CORE_API_VERSION == "1.1.0"


def test_module_1_0_still_valid(tmp_path: Path) -> None:
    """Un manifeste kind "service"-like 1.0 (sans bloc content,."""
    raw = valid_manifest()
    raw["core"] = ">=1.0 <2.0"
    raw["kind"] = "power-driver"
    _write_module(tmp_path, raw)
    assert load_manifest(tmp_path / "module.json", core_version="1.0.0").id == "power.samsung-ws"
    assert load_manifest(tmp_path / "module.json", core_version="1.1.0").id == "power.samsung-ws"


def test_content_type_requires_content_block(tmp_path: Path) -> None:
    """kind "content-type", core ">=1.1 <2.0", sans bloc content -> refusé."""
    raw = valid_manifest()
    raw["core"] = ">=1.1 <2.0"
    raw["kind"] = "content-type"
    _write_module(tmp_path, raw)
    with pytest.raises(ValidationError, match="module.content_type_missing_content"):
        load_manifest(tmp_path / "module.json")


def test_content_block_refused_on_other_kinds(tmp_path: Path) -> None:
    """bloc content sur un kind différent de content-type -> refusé."""
    raw = valid_manifest()
    raw["core"] = ">=1.1 <2.0"
    raw["kind"] = "power-driver"
    raw["content"] = {
        "item_schema": "item.schema.json",
        "player": {"element": "vds-content-video-player", "script": "player.js"},
        "media": {"accepts": ["video/mp4"]},
    }
    _write_module(tmp_path, raw)
    with pytest.raises(ValidationError, match="module.content_type_unexpected"):
        load_manifest(tmp_path / "module.json")


def test_content_type_core_must_exclude_1_0(tmp_path: Path) -> None:
    """kind content-type avec core ">=1.0 <2.0" -> refusé."""
    raw = valid_manifest()
    raw["core"] = ">=1.0 <2.0"
    raw["kind"] = "content-type"
    raw["content"] = {
        "item_schema": "item.schema.json",
        "player": {"element": "vds-content-video-player", "script": "player.js"},
        "media": {"accepts": ["video/mp4"]},
    }
    _write_module(tmp_path, raw)
    with pytest.raises(ValidationError, match="module.content_type_core_incompatible"):
        load_manifest(tmp_path / "module.json")


def test_content_type_1_1_loads(tmp_path: Path) -> None:
    """content-type valide, core ">=1.1 <2.0" -> chargé avec."""
    raw = valid_content_type()
    item_schema = {"type": "object", "properties": {}, "additionalProperties": False}
    _write_module(tmp_path, raw, item_schema)
    assert load_manifest(tmp_path / "module.json", core_version="1.1.0").id == "content.video"
    with pytest.raises(IncompatibleCore):
        load_manifest(tmp_path / "module.json", core_version="1.0.0")


def test_content_element_name_validated(tmp_path: Path) -> None:
    """element "video-player" (sans préfixe vds-content-) -> refusé."""
    raw = valid_content_type()
    raw["content"]["player"]["element"] = "video-player"
    item_schema = {"type": "object", "properties": {}, "additionalProperties": False}
    _write_module(tmp_path, raw, item_schema)
    with pytest.raises(ValidationError, match="module.content_player_element_invalid"):
        load_manifest(tmp_path / "module.json")


def test_content_accepts_mime_validated(tmp_path: Path) -> None:
    """accepts ["PDF"] ou ["application"] -> refusé."""
    raw = valid_content_type()
    raw["content"]["media"]["accepts"] = ["PDF"]
    item_schema = {"type": "object", "properties": {}, "additionalProperties": False}
    _write_module(tmp_path, raw, item_schema)
    with pytest.raises(ValidationError, match="module.content_media_accept_invalid"):
        load_manifest(tmp_path / "module.json")

    raw["content"]["media"]["accepts"] = ["application"]
    _write_module(tmp_path, raw, item_schema)
    with pytest.raises(ValidationError, match="module.content_media_accept_invalid"):
        load_manifest(tmp_path / "module.json")


def test_content_item_schema_missing_refused(tmp_path: Path) -> None:
    """item_schema pointant vers un fichier absent -> refusé."""
    raw = valid_content_type()
    item_schema = {"type": "object", "properties": {}, "additionalProperties": False}
    _write_module(tmp_path, raw, item_schema)
    (tmp_path / "item.schema.json").unlink()
    with pytest.raises(ContractError) as e:
        load_manifest(tmp_path / "module.json")
    assert e.value.code == "module.content_item_schema_missing"


async def test_content_type_defaults(tmp_path: Path) -> None:
    """une sous-classe minimale de ContentType (instanciée avec."""
    raw = valid_content_type()
    item_schema = {"type": "object", "properties": {}, "additionalProperties": False}
    _write_module(tmp_path, raw, item_schema)
    load_manifest(tmp_path / "module.json")

    # Création d'une instance de ContentType
    class VideoContentType(ContentType):
        pass

    content_type = VideoContentType()
    result = await content_type.to_player({"fit": "cover"}, {})
    assert isinstance(result, PlayerItemSpec)
    assert result.props == {"fit": "cover"}
    assert result.assets == []
    assert result.duration_ms is None


def test_storage_backend_is_abstract(tmp_path: Path) -> None:
    """StorageBackend ne peut pas être instancié directement."""
    with pytest.raises(TypeError):
        StorageBackend()  # type: ignore[abstract]
    assert StorageBackend.__abstractmethods__ == frozenset({"put", "open", "local_path", "delete"})


def test_media_facade_is_protocol(tmp_path: Path) -> None:
    """MediaFacade est un Protocol (vérifier."""
    assert getattr(MediaFacade, "_is_protocol", False)
    assert "variants" in MediaInfo.model_fields
