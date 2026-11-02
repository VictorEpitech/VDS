"""Tags libres des médias et playlists : normalisation, bornes, échappement des préfixes."""

from vds_server.core.errors import ApiError

MAX_TAGS = 20
MAX_TAG_LEN = 32
AUTOCOMPLETE_MAX = 50


def _valid(tag: str) -> bool:
    # équivalent de ^[\p{L}\p{N}][\p{L}\p{N} _-]{0,31}$
    if not tag or len(tag) > MAX_TAG_LEN or not tag[0].isalnum():
        return False
    return all(c.isalnum() or c in " _-" for c in tag)


def normalize_tag(raw: str) -> str:
    tag = raw.strip().lower()
    if not _valid(tag):
        raise ApiError("tags.invalid", 422)
    return tag


def normalize_tags(raw: list[str]) -> list[str]:
    """Trim, minuscules, dédoublonnés, triés ; 422 `tags.invalid` / `tags.too_many`."""
    tags = sorted({normalize_tag(t) for t in raw})
    if len(tags) > MAX_TAGS:
        raise ApiError("tags.too_many", 422, max=MAX_TAGS)
    return tags


def escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
