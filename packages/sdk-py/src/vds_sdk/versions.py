"""Semver minimal et intervalles `>=1.0 <2.0` (sans dépendance externe)."""

import re
from functools import total_ordering
from typing import Self

_VER = re.compile(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
_CMP = re.compile(r"^(>=|<=|==|>|<|=)?\s*(.+)$")


@total_ordering
class Version:
    __slots__ = ("major", "minor", "patch")

    def __init__(self, major: int, minor: int = 0, patch: int = 0) -> None:
        self.major, self.minor, self.patch = major, minor, patch

    @classmethod
    def parse(cls, text: str, *, strict: bool = False) -> Self:
        m = _VER.match(text.strip())
        if not m or (strict and (m.group(2) is None or m.group(3) is None)):
            raise ValueError(f"invalid version: {text!r}")
        return cls(int(m.group(1)), int(m.group(2) or 0), int(m.group(3) or 0))

    def _key(self) -> tuple[int, int, int]:
        return (self.major, self.minor, self.patch)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Version) and self._key() == other._key()

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self._key() < other._key()

    def __hash__(self) -> int:
        return hash(self._key())

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


def is_semver(text: str) -> bool:
    try:
        Version.parse(text, strict=True)
    except ValueError:
        return False
    return True


def parse_range(text: str) -> list[tuple[str, Version]]:
    """Parse `>=1.0 <2.0` en [(op, version)] ; lève ValueError si invalide."""
    parts = text.replace(",", " ").split()
    if not parts:
        raise ValueError("empty range")
    out: list[tuple[str, Version]] = []
    for part in parts:
        m = _CMP.match(part)
        if not m:
            raise ValueError(f"invalid range: {text!r}")
        op = m.group(1) or "=="
        out.append(("==" if op == "=" else op, Version.parse(m.group(2))))
    return out


def satisfies(version: str | Version, spec: str) -> bool:
    v = Version.parse(version) if isinstance(version, str) else version
    for op, bound in parse_range(spec):
        ok = {
            ">=": v >= bound,
            "<=": v <= bound,
            ">": v > bound,
            "<": v < bound,
            "==": v == bound,
        }[op]
        if not ok:
            return False
    return True
