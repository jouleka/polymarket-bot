"""L8 authenticated-command core (S4.6a / POL-6).

The PURE auth boundary for the remote safety-control channel. An inbound Telegram
message is UNTRUSTED DATA, never instructions: every plumbing field (chat_id /
command / nonce) is neutralize()d (strip Cc/Cf control/bidi/zero-width), then the
FIVE fail-closed gates run IN ORDER -- allowlisted chat-id FIRST, then the
safety-increasing-only command set, then a constant-time HMAC-SHA256 over the
current rotating secret, then a monotonic per-chat-id nonce. Only a message that
passes all five authenticates; every refusal reports the FIRST failing gate.

Nothing here can OPEN a trade -- the command set is EXACTLY the six safety-
increasing verbs, structurally pinned. A compromised channel can at worst STOP
the bot.
"""

import hashlib
import hmac
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from polybot.ingestion.sanitizer import neutralize


@dataclass(frozen=True)
class RawMessage:
    """The untrusted inbound message as received (strings/bytes, un-neutralized)."""
    chat_id: str
    command: str
    payload: str
    nonce: str
    sig: bytes


@dataclass(frozen=True)
class AuthResult:
    """The verdict of CommandAuth.authenticate. On ok=True, command/payload/chat_id
    carry the NEUTRALIZED values the controller applies; on ok=False, reason is the
    first-failing-gate REASON_* and chat_id is the neutralized id where known."""
    ok: bool
    reason: str
    command: str | None = None
    payload: str | None = None
    chat_id: str = ""


@runtime_checkable
class TelegramTransport(Protocol):
    """Non-blocking inbound/outbound seam (the real Telegram bot is deploy-deferred; the
    tests inject a fake). poll() returns pending RawMessages ([] when none); send() is
    best-effort (True on success)."""

    def poll(self) -> list: ...

    def send(self, text) -> bool: ...


class SecretHolder:
    """Holds the CURRENT rotating HMAC secret. The value lives off-repo (deploy-config);
    this is the verify-side holder + the rotate() seam. rotate() is the per-restart +
    operator-triggered swap (cadence is deploy-config, not a code constant)."""

    def __init__(self, secret: bytes):
        self._secret = secret

    def current(self) -> bytes:
        return self._secret

    def rotate(self, new_secret: bytes) -> None:
        self._secret = new_secret
