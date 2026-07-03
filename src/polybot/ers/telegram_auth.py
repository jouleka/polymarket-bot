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


# The safety-increasing-ONLY command set -- structurally pinned to EXACTLY these six verbs.
# There is deliberately NO open-trade verb: a compromised channel can at worst STOP the bot.
_COMMAND_SET = frozenset({"KILL", "PAUSE", "RESUME", "FLATTEN", "LOWER_CAPS", "BLACKLIST"})

# The five auth-refusal reasons -- used both as AuthResult.reason and the op-audit reason.
REASON_MALFORMED = "l8_malformed"      # gate 1: empty/absent field OR non-base-10 nonce
REASON_BAD_CHAT = "l8_bad_chat"        # gate 2: chat-id not in the operator allowlist
REASON_UNKNOWN_CMD = "l8_unknown_cmd"  # gate 3: command not in _COMMAND_SET
REASON_BAD_SIG = "l8_bad_sig"          # gate 4: HMAC mismatch (constant-time compare)
REASON_REPLAY = "l8_replay"            # gate 5: nonce <= last-seen for this chat-id


def canonical_message(raw) -> bytes:
    """The HMAC input: b"chat_id|command|payload|nonce" -- fixed field order, "|" separator,
    each field .encode()d. The signature is NEVER part of its own input. The caller feeds a
    RawMessage whose chat_id/command/nonce are already NEUTRALIZED (payload as received)."""
    return b"|".join(
        (
            raw.chat_id.encode(),
            raw.command.encode(),
            raw.payload.encode(),
            raw.nonce.encode(),
        )
    )


def compute_mac(canonical: bytes, secret: bytes) -> bytes:
    """HMAC-SHA256(secret, canonical) raw digest bytes (compared constant-time in gate 4)."""
    return hmac.new(secret, canonical, hashlib.sha256).digest()


class CommandAuth:
    """The five fail-closed gates (§3), IN ORDER. Every plumbing field is neutralize()d;
    a refusal reports the FIRST failing gate. State: a per-chat-id in-memory monotonic
    nonce dict (in-session; per-restart secret rotation defeats cross-restart replay)."""

    def __init__(self, *, allowlist, secret_holder, command_set=_COMMAND_SET):
        self._allow = allowlist                # {chat_id: role}; operator-curated, injected
        self._secret_holder = secret_holder
        self._command_set = command_set
        self._seen = {}                        # {chat_id: last_nonce_int}

    def authenticate(self, raw) -> AuthResult:
        # Gate 1 -- structure. Neutralize the plumbing fields; the signature stays raw bytes.
        nc_chat = neutralize(raw.chat_id)
        nc_cmd = neutralize(raw.command)
        nc_nonce = neutralize(raw.nonce)
        nc_payload = neutralize(raw.payload)
        if not nc_chat or not nc_cmd or not nc_nonce:
            return AuthResult(False, REASON_MALFORMED)
        # Nonce must be a base-10 ASCII integer: str.isdigit() also accepts unicode digits
        # (superscripts etc.) that int() rejects -- gate isascii() too, or gate 5's int() crashes.
        if not (nc_nonce.isascii() and nc_nonce.isdigit()):
            return AuthResult(False, REASON_MALFORMED)
        # Reject the "|" canonical-message delimiter in any plumbing field: neutralize does NOT
        # strip it, and an unescaped "|" makes canonical_message ambiguous (two distinct tuples
        # -> one MAC). nonce is already ASCII-digits-only above, so this bites chat/command.
        if "|" in nc_chat or "|" in nc_cmd or "|" in nc_nonce:
            return AuthResult(False, REASON_MALFORMED)
        # Gate 2 -- chat-id allowlist (the FIRST semantic check).
        if self._allow.get(nc_chat) is None:
            return AuthResult(False, REASON_BAD_CHAT, chat_id=nc_chat)
        # Gate 3 -- command set (structural: no open verb dispatches).
        if nc_cmd not in self._command_set:
            return AuthResult(False, REASON_UNKNOWN_CMD, chat_id=nc_chat)
        # Gate 4 -- HMAC over the NEUTRALIZED plumbing fields + raw payload; constant-time.
        canonical = canonical_message(
            RawMessage(chat_id=nc_chat, command=nc_cmd, payload=raw.payload, nonce=nc_nonce, sig=b"")
        )
        mac = compute_mac(canonical, self._secret_holder.current())
        if not hmac.compare_digest(mac, raw.sig):
            return AuthResult(False, REASON_BAD_SIG, chat_id=nc_chat)
        # Gate 5 -- monotonic per-chat-id nonce.
        n = int(nc_nonce)
        if n <= self._seen.get(nc_chat, -1):
            return AuthResult(False, REASON_REPLAY, chat_id=nc_chat)
        self._seen[nc_chat] = n
        return AuthResult(True, "ok", command=nc_cmd, payload=nc_payload, chat_id=nc_chat)
