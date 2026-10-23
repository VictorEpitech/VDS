"""Modules intégrés content.* : manifeste, schémas, i18n, traitement des médias, to_player."""

import io
import json
import stat
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pypdfium2 as pdfium  # type: ignore[import-untyped]
import pytest
from jsonschema import Draft202012Validator
from PIL import Image
from vds_sdk import ContractError, ModuleContext
from vds_sdk.manifest import ModuleManifest, load_manifest
from vds_sdk.server.content import ContentType, MediaRef
from vds_sdk.server.media import MediaInfo, MediaKind
from vds_sdk.server.storage import StoredObject
from vds_server.diffusion.playlists import media_refs
from vds_server.registry.runtime import load_server_module

MODULES = Path(__file__).resolve().parents[3] / "modules"
IDS = ["content.image", "content.video", "content.pdf", "content.url"]
KIND_BY_ID = {"content.image": "image", "content.video": "video", "content.pdf": "document"}


def _load(mid: str) -> tuple[ModuleManifest, ContentType]:
    manifest = load_manifest(MODULES / mid / "module.json")
    module = load_server_module(MODULES / mid, manifest)
    assert isinstance(module, ContentType)
    return manifest, module


class _Config:
    def __init__(self, values: dict[str, Any]) -> None:
        self._values = values

    async def resolve(self, scope: object = None) -> dict[str, Any]:
        return self._values


class _Ctx:
    def __init__(self, values: dict[str, Any] | None = None) -> None:
        self.config = _Config(values or {})


async def _setup(module: ContentType, values: dict[str, Any] | None = None) -> None:
    await module.setup(cast(ModuleContext, _Ctx(values)))


class FakeStore:
    def __init__(self, original: bytes = b"") -> None:
        self.original = original
        self.variants: dict[str, tuple[bytes, str]] = {}

    async def put_variant(
        self, name: str, chunks: AsyncIterator[bytes], *, mime: str
    ) -> StoredObject:
        data = b"".join([c async for c in chunks])
        self.variants[name] = (data, mime)
        return StoredObject(key=f"00/x/{name}.bin", bytes=len(data), sha256="0" * 64, mime=mime)

    async def _read(self) -> AsyncIterator[bytes]:
        for i in range(0, len(self.original), 4096):
            yield self.original[i : i + 4096]

    def read(self, name: str) -> AsyncIterator[bytes]:
        assert name == "original"
        return self._read()


def _ref(kind: MediaKind, mime: str) -> MediaRef:
    return MediaRef(
        id=uuid4(), kind=kind, mime=mime, bytes=1, sha256="0" * 64, storage_key="00/x/original.bin"
    )


def _info(media_id: UUID, kind: MediaKind, mime: str, **over: Any) -> dict[UUID, MediaInfo]:
    return {
        media_id: MediaInfo(id=media_id, kind=kind, mime=mime, bytes=1, sha256="0" * 64, **over)
    }


# --- manifeste, schémas, i18n -------------------------------------------------


@pytest.mark.parametrize("mid", IDS)
def test_manifest_and_schemas(mid: str) -> None:
    manifest, _ = _load(mid)
    assert manifest.kind == "content-type" and manifest.core == ">=1.1 <2.0"
    assert manifest.content is not None
    assert manifest.content.player.element == "vds-" + mid.replace(".", "-")
    assert manifest.content.requires_network is (mid == "content.url")
    item = json.loads((MODULES / mid / "item.schema.json").read_text())
    Draft202012Validator.check_schema(item)
    media_props = {k: v["x-vds-media"] for k, v in item["properties"].items() if "x-vds-media" in v}
    if mid == "content.url":
        assert manifest.content.media is None and not media_props
    else:
        assert media_props == {"media": KIND_BY_ID[mid]}
        assert manifest.content.media is not None
        # le noyau extrait la référence de média des props
        media_id = uuid4()
        assert media_refs(item, {"media": str(media_id)}) == [("media", media_id, KIND_BY_ID[mid])]


def _flat(obj: dict[str, Any], prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in obj.items():
        if isinstance(v, dict):
            out.update(_flat(v, f"{prefix}{k}."))
        else:
            out[f"{prefix}{k}"] = v
    return out


@pytest.mark.parametrize("mid", IDS)
def test_i18n_covers_schema_keys(mid: str) -> None:
    fr = _flat(json.loads((MODULES / mid / "i18n" / "fr.json").read_text()))
    en = _flat(json.loads((MODULES / mid / "i18n" / "en.json").read_text()))
    assert fr.keys() == en.keys() and all(fr.values()) and all(en.values())
    for name in ("config.schema.json", "item.schema.json"):
        schema = json.loads((MODULES / mid / name).read_text())
        for prop in schema["properties"].values():
            assert prop["title"] in fr and prop["description"] in fr


# --- content.image ------------------------------------------------------------


async def test_image_process_and_player() -> None:
    _, module = _load("content.image")
    await _setup(module, {"thumb_size": 100})
    buf = io.BytesIO()
    Image.new("RGBA", (400, 200), (255, 0, 0, 128)).save(buf, "PNG")
    store = FakeStore(buf.getvalue())
    meta = await module.process_media(_ref("image", "image/png"), store)
    assert (meta.width, meta.height) == (400, 200)
    thumb, mime = store.variants["thumb"]
    assert mime == "image/jpeg" and Image.open(io.BytesIO(thumb)).size == (100, 50)

    media_id = uuid4()
    spec = await module.to_player(
        {"media": str(media_id), "fit": "cover"}, _info(media_id, "image", "image/png")
    )
    key = f"media/{media_id}/original"
    assert spec.props == {"src": key, "fit": "cover"} and spec.assets == [key]
    assert spec.duration_ms is None
    with pytest.raises(ContractError):
        await module.to_player({"media": str(uuid4())}, {})


async def test_image_rejects_garbage() -> None:
    _, module = _load("content.image")
    with pytest.raises(ContractError) as err:
        await module.process_media(_ref("image", "image/png"), FakeStore(b"not an image"))
    assert err.value.code == "content.image.invalid"


# --- content.video ------------------------------------------------------------


def _fake_ffprobe(tmp_path: Path, output: str, code: int = 0, sleep: float = 0) -> str:
    script = tmp_path / "ffprobe"
    script.write_text(f"#!/bin/sh\nsleep {sleep}\ncat <<'EOF'\n{output}\nEOF\nexit {code}\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return str(script)


PROBE = json.dumps(
    {
        "streams": [
            {"codec_type": "audio"},
            {"codec_type": "video", "width": 1080, "height": 1920, "side_data_list": []},
        ],
        "format": {"duration": "12.345000"},
    }
)


async def test_video_process_with_ffprobe(tmp_path: Path) -> None:
    _, module = _load("content.video")
    module.ffprobe_bin = _fake_ffprobe(tmp_path, PROBE)  # type: ignore[attr-defined]
    meta = await module.process_media(_ref("video", "video/mp4"), FakeStore(b"data"))
    assert (meta.width, meta.height, meta.duration_ms) == (1080, 1920, 12345)


async def test_video_rotation_swaps_dimensions(tmp_path: Path) -> None:
    _, module = _load("content.video")
    probe = json.dumps(
        {
            "streams": [
                {
                    "codec_type": "video",
                    "width": 1920,
                    "height": 1080,
                    "side_data_list": [{"rotation": -90}],
                }
            ],
            "format": {},
        }
    )
    module.ffprobe_bin = _fake_ffprobe(tmp_path, probe)  # type: ignore[attr-defined]
    meta = await module.process_media(_ref("video", "video/mp4"), FakeStore(b"data"))
    assert (meta.width, meta.height, meta.duration_ms) == (1080, 1920, None)


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"output": "{}", "code": 1}, "content.video.invalid"),
        ({"output": "garbage"}, "content.video.invalid"),
        ({"output": json.dumps({"streams": [{"codec_type": "audio"}]})}, "content.video.invalid"),
        ({"output": PROBE, "sleep": 5}, "content.video.probe_timeout"),
    ],
)
async def test_video_probe_failures(tmp_path: Path, kwargs: dict[str, Any], code: str) -> None:
    _, module = _load("content.video")
    await _setup(module, {"probe_timeout_s": 5})
    module._timeout_s = 0.3  # type: ignore[attr-defined]
    module.ffprobe_bin = _fake_ffprobe(tmp_path, **kwargs)  # type: ignore[attr-defined]
    with pytest.raises(ContractError) as err:
        await module.process_media(_ref("video", "video/mp4"), FakeStore(b"data"))
    assert err.value.code == code


async def test_video_ffprobe_missing(tmp_path: Path) -> None:
    _, module = _load("content.video")
    module.ffprobe_bin = str(tmp_path / "absent")  # type: ignore[attr-defined]
    with pytest.raises(ContractError) as err:
        await module.process_media(_ref("video", "video/mp4"), FakeStore(b"data"))
    assert err.value.code == "content.video.probe_unavailable"


async def test_video_to_player() -> None:
    _, module = _load("content.video")
    media_id = uuid4()
    info = _info(media_id, "video", "video/mp4", duration_ms=9000)
    spec = await module.to_player({"media": str(media_id), "audio": True}, info)
    key = f"media/{media_id}/original"
    assert spec.props == {"src": key, "audio": True, "fit": "contain", "known_duration_ms": 9000}
    assert spec.assets == [key] and spec.duration_ms is None  # fin sur vds-ended
    muted = await module.to_player({"media": str(media_id)}, info)
    assert muted.props["audio"] is False


# --- content.pdf --------------------------------------------------------------


def _pdf(pages: int) -> bytes:
    doc = pdfium.PdfDocument.new()
    for _ in range(pages):
        doc.new_page(200, 300)
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


async def test_pdf_pages_rendered_and_player() -> None:
    _, module = _load("content.pdf")
    await _setup(module, {"render_width": 1280})
    store = FakeStore(_pdf(3))
    meta = await module.process_media(_ref("document", "application/pdf"), store)
    assert meta.page_count == 3 and meta.width == 1280 and meta.height == 1920
    assert sorted(store.variants) == ["page-1", "page-2", "page-3"]
    data, mime = store.variants["page-2"]
    assert mime == "image/jpeg" and Image.open(io.BytesIO(data)).size == (1280, 1920)

    media_id = uuid4()
    names = ["page-10", "page-2", "page-1", "thumb"]
    info = _info(media_id, "document", "application/pdf", page_count=3, variants=names)
    spec = await module.to_player({"media": str(media_id), "page_duration_ms": 5000}, info)
    expected = [f"media/{media_id}/page-{n}" for n in (1, 2, 10)]
    assert spec.props == {"pages": expected, "page_count": 3, "page_duration_ms": 5000}
    assert spec.assets == expected and spec.duration_ms == 15000


async def test_pdf_invalid_and_too_long() -> None:
    _, module = _load("content.pdf")
    ref = _ref("document", "application/pdf")
    with pytest.raises(ContractError) as err:
        await module.process_media(ref, FakeStore(b"%PDF-broken"))
    assert err.value.code == "content.pdf.invalid"
    await _setup(module, {"max_pages": 2})
    store = FakeStore(_pdf(3))
    with pytest.raises(ContractError) as err2:
        await module.process_media(ref, store)
    assert err2.value.code == "content.pdf.too_many_pages" and not store.variants


# --- content.url --------------------------------------------------------------


async def test_url_to_player() -> None:
    _, module = _load("content.url")
    spec = await module.to_player({"url": "https://example.org/a?b=1", "reload_interval_s": 60}, {})
    assert spec.props == {"url": "https://example.org/a?b=1", "reload_interval_s": 60}
    assert spec.assets == [] and spec.duration_ms is None


@pytest.mark.parametrize(
    "url",
    [
        "http://example.org",
        "javascript:alert(1)",
        "https://u:p@example.org/",
        "https:///x",
        "",
        "ftp://a.b",
    ],
)
async def test_url_rejects_non_https(url: str) -> None:
    _, module = _load("content.url")
    with pytest.raises(ContractError) as err:
        await module.to_player({"url": url}, {})
    assert err.value.code == "content.url.invalid_url"


async def test_url_allowed_hosts() -> None:
    _, module = _load("content.url")
    await _setup(module, {"allowed_hosts": ["Example.org"]})
    await module.to_player({"url": "https://www.example.org/x"}, {})
    await module.to_player({"url": "https://example.org/"}, {})
    with pytest.raises(ContractError) as err:
        await module.to_player({"url": "https://evilexample.org/"}, {})
    assert err.value.code == "content.url.host_not_allowed"


@pytest.mark.parametrize("base", ["/", "/espace/affichage"])
@pytest.mark.parametrize("origin", ["https://vds.example.org", "https://vds.example.org:443"])
async def test_url_same_origin_refused(
    monkeypatch: pytest.MonkeyPatch, base: str, origin: str
) -> None:
    monkeypatch.setenv("VDS_PUBLIC_ORIGIN", origin)
    monkeypatch.setenv("VDS_BASE_PATH", base)
    _, module = _load("content.url")
    await _setup(module)
    prefix = base.rstrip("/")
    for url in (
        f"https://vds.example.org{prefix}/",
        "https://VDS.example.org/anything",
        "https://vds.example.org:443/x?y=1",
    ):
        with pytest.raises(ContractError) as err:
            await module.to_player({"url": url}, {})
        assert err.value.code == "content.url.same_origin"
    await module.to_player({"url": "https://other.example.org/"}, {})
    await module.to_player({"url": "https://vds.example.org:8443/"}, {})


# --- content.pdf : sous-processus jetable ---------------------------------------


def _worker_module(module: ContentType) -> Any:
    return sys.modules[type(module).__module__]


@pytest.mark.parametrize("base", ["/", "/espace/affichage"])
async def test_pdf_runs_in_subprocess_without_secrets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, base: str
) -> None:
    monkeypatch.setenv("VDS_BASE_PATH", base)
    monkeypatch.setenv("VDS_SECRET_TEST", "leak")
    _, module = _load("content.pdf")
    worker = tmp_path / "w.py"
    worker.write_text(
        "import os, sys, json\n"
        "assert not [k for k in os.environ if k.startswith('VDS_')], dict(os.environ)\n"
        "open(sys.argv[2] + '/page-1.jpg', 'wb').write(b'x')\n"
        "print(json.dumps({'count': 1, 'width': 2, 'height': 3}))\n"
    )
    monkeypatch.setattr(_worker_module(module), "WORKER", worker)
    meta = await module.process_media(_ref("document", "application/pdf"), FakeStore(b"%PDF-1.4"))
    assert (meta.width, meta.height, meta.page_count) == (2, 3, 1)


@pytest.mark.parametrize("base", ["/", "/espace/affichage"])
@pytest.mark.parametrize(
    ("body", "code"),
    [
        ("import time; time.sleep(30)", "content.pdf.timeout"),
        ("import sys; sys.exit(139)", "content.pdf.render_failed"),
        ("x = bytearray(4 * 1024**3)", "content.pdf.render_failed"),
        ("print('not json')", "content.pdf.render_failed"),
        ("import sys; sys.exit(3)", "content.pdf.invalid"),
    ],
)
async def test_pdf_worker_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, base: str, body: str, code: str
) -> None:
    monkeypatch.setenv("VDS_BASE_PATH", base)
    _, module = _load("content.pdf")
    worker = tmp_path / "w.py"
    worker.write_text(
        "import resource, sys\n"
        "m = int(sys.argv[6]); resource.setrlimit(resource.RLIMIT_AS, (m, m))\n" + body + "\n"
    )
    monkeypatch.setattr(_worker_module(module), "WORKER", worker)
    module._timeout_s = 0.5  # type: ignore[attr-defined]
    store = FakeStore(b"%PDF-1.4")
    with pytest.raises(ContractError) as err:
        await module.process_media(_ref("document", "application/pdf"), store)
    assert err.value.code == code and not store.variants


async def test_pdf_real_worker_hostile_inputs() -> None:
    """Vrai worker : entrée piégée ou trop gourmande => erreur stable, sans planter."""
    _, module = _load("content.pdf")
    ref = _ref("document", "application/pdf")
    for blob in (b"%PDF-1.7\n" + b"\x00" * 4096, b"%PDF-1.4\n1 0 obj<</Length 99999999>>stream\n"):
        with pytest.raises(ContractError) as err:
            await module.process_media(ref, FakeStore(blob))
        assert err.value.code.startswith("content.pdf.")
    module._mem_bytes = 64 * 1024**2  # type: ignore[attr-defined]
    with pytest.raises(ContractError) as err:
        await module.process_media(ref, FakeStore(_pdf(1)))
    assert err.value.code == "content.pdf.render_failed"


async def test_video_ffprobe_env_is_minimal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VDS_SECRET_TEST", "leak")
    _, module = _load("content.video")
    script = tmp_path / "ffprobe"
    script.write_text(
        f"#!/bin/sh\n[ -z \"$VDS_SECRET_TEST\" ] || exit 9\ncat <<'EOF'\n{PROBE}\nEOF\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    module.ffprobe_bin = str(script)  # type: ignore[attr-defined]
    meta = await module.process_media(_ref("video", "video/mp4"), FakeStore(b"data"))
    assert meta.width == 1080
