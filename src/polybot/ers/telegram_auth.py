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
