"""Trame JSON commune aux canaux /ws/agent et /ws/player (docs/architecture-lot3.md § 1.1)."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

MAX_FRAME_BYTES = 256 * 1024
MAX_UNACKED = 500
SUBPROTOCOL_AGENT = "vds.agent.v1"
SUBPROTOCOL_PLAYER = "vds.player.v1"

CLOSE_BAD_MESSAGE = 4400
CLOSE_UNAUTHORIZED = 4401
CLOSE_FORBIDDEN = 4403
CLOSE_HELLO_TIMEOUT = 4408
CLOSE_REPLACED = 4409
CLOSE_REVOKED = 4410
CLOSE_VERSION_UNSUPPORTED = 4426
CLOSE_RATE_LIMITED = 4429

CLOSE_CODES: dict[int, str] = {
    CLOSE_BAD_MESSAGE: "bad_message",
    CLOSE_UNAUTHORIZED: "unauthorized",
    CLOSE_FORBIDDEN: "forbidden",
    CLOSE_HELLO_TIMEOUT: "hello_timeout",
    CLOSE_REPLACED: "replaced",
    CLOSE_REVOKED: "revoked",
    CLOSE_VERSION_UNSUPPORTED: "version_unsupported",
    CLOSE_RATE_LIMITED: "rate_limited",
}

# Trames fiables : portent un seq et sont rejouées jusqu'à ack.
RELIABLE_TYPES = frozenset({"cmd", "cmd.result", "event"})


class WireError(ValueError):
    """Trame invalide ; `close_code` indique le code de fermeture à utiliser."""

    def __init__(self, code: str, close_code: int = CLOSE_BAD_MESSAGE) -> None:
        super().__init__(code)
        self.code = code
        self.close_code = close_code


class Frame(BaseModel):
    model_config = ConfigDict(extra="forbid")

    v: Literal[1] = 1
    type: str = Field(min_length=1, max_length=64)
    id: UUID = Field(default_factory=uuid4)
    seq: int | None = Field(default=None, ge=1)
    ack: int | None = Field(default=None, ge=0)
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    data: dict[str, Any] = Field(default_factory=dict)

    @property
    def reliable(self) -> bool:
        return self.type in RELIABLE_TYPES


def encode_frame(frame: Frame) -> str:
    text = frame.model_dump_json(exclude_none=True)
    if len(text.encode()) > MAX_FRAME_BYTES:
        raise WireError("frame_too_large")
    return text


def decode_frame(raw: str | bytes) -> Frame:
    if isinstance(raw, bytes):
        raise WireError("binary_not_allowed")
    if len(raw.encode()) > MAX_FRAME_BYTES:
        raise WireError("frame_too_large")
    try:
        frame = Frame.model_validate_json(raw)
    except ValidationError as exc:
        raise WireError("invalid_frame") from exc
    if frame.reliable and frame.seq is None:
        raise WireError("missing_seq")
    if not frame.reliable and frame.seq is not None:
        raise WireError("unexpected_seq")
    return frame


def pending_after(frames: Iterable[Frame], acked: int) -> list[Frame]:
    """Trames fiables à rejouer (seq > acked), triées par seq."""
    return sorted(
        (f for f in frames if f.seq is not None and f.seq > acked), key=lambda f: f.seq or 0
    )


class SeqReceiver:
    """Réception : dédoublonnage et ack cumulatif (doublons ignorés)."""

    def __init__(self, last_seq: int = 0) -> None:
        self.last_seq = last_seq

    def accept(self, frame: Frame) -> bool:
        """True si la trame est nouvelle ; WireError si trou de séquence."""
        if frame.seq is None:
            return True
        if frame.seq <= self.last_seq:
            return False
        if frame.seq != self.last_seq + 1:
            raise WireError("seq_gap")
        self.last_seq = frame.seq
        return True


class SeqSender:
    """Émission : numérotation, conservation jusqu'à ack, fenêtre bornée."""

    def __init__(self, last_seq: int = 0, acked: int = 0) -> None:
        self.last_seq = last_seq
        self.acked = acked
        self._unacked: dict[int, Frame] = {}

    def stamp(self, frame: Frame) -> Frame:
        if len(self._unacked) >= MAX_UNACKED:
            raise WireError("window_full", CLOSE_RATE_LIMITED)
        self.last_seq += 1
        out = frame.model_copy(update={"seq": self.last_seq})
        self._unacked[out.seq or 0] = out
        return out

    def on_ack(self, ack: int) -> None:
        if ack > self.last_seq:
            raise WireError("ack_ahead")
        self.acked = max(self.acked, ack)
        for s in [s for s in self._unacked if s <= self.acked]:
            del self._unacked[s]

    def replay(self, resume_from: int) -> list[Frame]:
        self.on_ack(min(resume_from, self.last_seq))
        return pending_after(self._unacked.values(), resume_from)
