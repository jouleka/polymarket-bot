"""S4.6b (POL-6) -- the structurally-bounded L8 TelegramController.

Skeleton + name-mangled composition + the structural-sweep/command-set-pin/source-scan,
then drain() (poll -> authenticate -> apply -> audit, per-message isolation), then the
six-verb __apply map (each verb its exact SafetyController primitive + op_audit row).
Runs SHADOW-ONLY over a fake transport. BLACKLIST raises NotImplementedError here (S4.6d
completes it). Clocks are injected 0-arg callables; money is Decimal; helpers copied per
file per convention (no conftest, no test classes).
"""
import inspect
from decimal import Decimal
from pathlib import Path

import pytest

from polybot.core.clock import MonotonicStamper
from polybot.ers import safety as _safety
from polybot.ers.caps import RiskCaps
from polybot.ers.intent_store import IntentStore
from polybot.ers.safety import SafetyController
from polybot.ers.telegram import TelegramController
from polybot.ers.telegram_auth import (
    CommandAuth, RawMessage, SecretHolder, canonical_message, compute_mac,
)

_SECRET = b"s4.6b-test-secret"


def _store(tmp_path):
    return IntentStore(str(tmp_path / "i.db"), MonotonicStamper())


def _running_ctl(store):
    # Boot HALTED -> the operator clean-reconcile RUNNING (one state_change audit row is
    # seeded BEFORE the drain; every op_audit assertion below accounts for it).
    ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
    ctl.set_state(_safety.RUNNING, reason="clean_reconcile")
    return ctl


def _auth(*, allowlist=None):
    # A real CommandAuth over a real SecretHolder (the pure S4.6a core -- not re-faked).
    if allowlist is None:
        allowlist = {"chatA": "operator"}
    return CommandAuth(allowlist=allowlist, secret_holder=SecretHolder(_SECRET))


def _signed(chat_id, command, payload, nonce, secret=_SECRET):
    # Build a VALID signed RawMessage: sig = HMAC over the canonical (neutralized-order)
    # fields. canonical_message takes a RawMessage; the sig field is irrelevant to it, so
    # pass a placeholder sig, compute the mac, then rebuild frozen with the real sig.
    unsigned = RawMessage(chat_id=chat_id, command=command, payload=payload,
                          nonce=nonce, sig=b"")
    sig = compute_mac(canonical_message(unsigned), secret)
    return RawMessage(chat_id=chat_id, command=command, payload=payload, nonce=nonce, sig=sig)


class _FakeTransport:
    # poll() returns a queued list of RawMessages ONCE then []; send() records + returns True.
    def __init__(self, queued=()):
        self._queued = list(queued)
        self.sent = []

    def poll(self):
        out, self._queued = self._queued, []
        return out

    def send(self, text):
        self.sent.append(text)
        return True


def test_structural_sweep_public_surface_is_exactly_drain_and_notify_no_trade_verb(tmp_path):
    # Load-bearing L8 safety guarantee (mirrors test_ers_facade.py's sweep): the controller
    # exposes EXACTLY {drain, notify}, holds ctl/store/transport/auth ONLY under name-mangling,
    # is not callable, is not a SafetyController subclass, and reaches set_state/swap_caps ONLY
    # via the six-verb map -- so a compromised channel can at worst STOP the bot.
    # Kills: leaking a public place/sign/set_state/active_caps/controller attr; a bare/underscore
    # ctl or store handle; subclassing SafetyController; adding __call__.
    with _store(tmp_path) as store:
        ctl = _running_ctl(store)
        transport = _FakeTransport()
        auth = _auth()
        tc = TelegramController(ctl, store, transport, auth)

        # (a) Public surface is EXACTLY the two allowed names.
        allowed = {"drain", "notify"}
        public = {name for name in dir(tc) if not name.startswith("_")}
        assert public == allowed, f"unexpected public surface: {public ^ allowed}"

        # (b) No trade / op-state-mutation / handle attr reachable, bare OR single-underscore.
        for name in ("place", "propose_trade", "open_trade", "sign", "submit",
                     "set_state", "swap_caps", "active_caps", "state", "verdict",
                     "record_op_event", "controller", "store", "transport", "auth"):
            assert not hasattr(tc, name), f"forbidden attr exposed: {name}"
            assert name not in dir(tc), name
            assert not hasattr(tc, "_" + name), f"forbidden single-underscore attr: _{name}"

        # (c) Not callable -- a __call__ would be an unguarded dispatch path outside {drain, notify}.
        assert not callable(tc), "TelegramController must not be callable"

        # (d) Composition-only over the SafetyController (no inherited mutators).
        assert not isinstance(tc, SafetyController)
        assert SafetyController not in type(tc).__mro__

        # (e) The controller/store refs exist ONLY under name-mangling -- no plain dot-in path.
        assert getattr(tc, "_TelegramController__ctl", None) is ctl
        assert getattr(tc, "_TelegramController__store", None) is store
        assert getattr(tc, "_TelegramController__transport", None) is transport
        assert getattr(tc, "_TelegramController__auth", None) is auth
