"""Filtrage des textes libres saisis par l'admin (motifs) avant stockage, journaux et audit."""

import unicodedata

MAX_REASON = 200
_FORBIDDEN = frozenset({"Cc", "Cf", "Zl", "Zp"})  # contrôle, format (bidi), séparateurs ligne/para


def clean_reason(value: str | None) -> str | None:
    """strip ; vide -> None ; refuse contrôle/bidi (ValueError, code stable) ; borne à 200."""
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if len(text) > MAX_REASON:
        raise ValueError("reason.too_long")
    if any(unicodedata.category(ch) in _FORBIDDEN for ch in text):
        raise ValueError("reason.invalid_chars")
    return text
