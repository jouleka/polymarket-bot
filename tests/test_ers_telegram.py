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


def test_command_set_is_exactly_the_six_safety_increasing_verbs(tmp_path):
    # Structural pin: the auth command set (imported from telegram_auth, the single source of
    # truth) is EXACTLY the six safety-increasing verbs -- no seventh, no open-trade verb.
    # Kills: adding a verb to _COMMAND_SET; dropping one; a typo'd verb string.
    from polybot.ers.telegram_auth import _COMMAND_SET
    assert _COMMAND_SET == frozenset(
        {"KILL", "PAUSE", "RESUME", "FLATTEN", "LOWER_CAPS", "BLACKLIST"})


def test_telegram_module_source_never_references_a_trade_verb():
    # Defense-in-depth over the structural sweep: the module TEXT must not contain a
    # place/propose/sign/submit/open_trade token -- there is no code path, dead or live, that
    # could ever be a trade dispatch. "open_trade" (not bare "open") avoids false hits on the
    # English word 'open' in the docstring.
    # Kills: sneaking a place()/sign()/submit()/propose_trade()/open_trade() into telegram.py.
    import polybot.ers.telegram as _mod
    src = Path(_mod.__file__).read_text(encoding="utf-8")
    for forbidden in ("place", "propose_trade", "sign", "submit", "open_trade"):
        assert forbidden not in src, f"trade-verb token leaked into telegram.py: {forbidden!r}"


def test_drain_refused_message_audits_l8_refused_with_reason_and_chat_and_leaves_state(tmp_path):
    # A message auth rejects (unknown chat-id) is audited kind="l8_refused" with the SPECIFIC
    # refusal reason + the neutralized chat_id, and does NOT mutate op-state (still RUNNING).
    # PAIR with the accept case below (test_drain_accepted_message...).
    # Kills: swallowing the refusal (no audit row); auditing the wrong kind/reason; a refused
    # message reaching __apply and mutating op-state.
    with _store(tmp_path) as store:
        ctl = _running_ctl(store)
        bad = _signed("evil", "KILL", "", "1")   # valid sig, but 'evil' not in the allowlist
        transport = _FakeTransport([bad])
        tc = TelegramController(ctl, store, transport, _auth())
        tc.drain()
        assert ctl.state() == _safety.RUNNING                       # op-state untouched
        # The seed state_change row, then exactly one l8_refused row for the bad chat-id.
        assert [(r["kind"], r["reason"], r["detail"]) for r in store.op_audit_log()] == [
            ("state_change", "clean_reconcile", "RUNNING"),
            ("l8_refused", "l8_bad_chat", "evil"),
        ]


def test_drain_accepted_message_applies_and_audits_l8_command(tmp_path):
    # The accept half of the pair: a fully-authenticated KILL applies (HALTED) and is audited
    # kind="l8_command", reason=l8_kill, detail="KILL" -- AND the set_state adds its own
    # state_change row (l8_kill). Kills: dropping the l8_command audit; wrong reason/detail.
    with _store(tmp_path) as store:
        ctl = _running_ctl(store)
        msg = _signed("chatA", "KILL", "", "1")
        transport = _FakeTransport([msg])
        tc = TelegramController(ctl, store, transport, _auth())
        tc.drain()
        assert ctl.state() == _safety.HALTED
        assert [(r["kind"], r["reason"], r["detail"]) for r in store.op_audit_log()] == [
            ("state_change", "clean_reconcile", "RUNNING"),
            ("state_change", "l8_kill", "HALTED"),          # set_state's own row
            ("l8_command", "l8_kill", "KILL"),              # the drain's command-level row
        ]


def test_drain_isolates_a_raising_apply_audits_l8_apply_error_and_continues(tmp_path):
    # Per-message isolation (mirrors news.poll_all): an authenticated command whose __apply
    # RAISES (BLACKLIST is unbuilt in B -> NotImplementedError) is caught + audited
    # kind="l8_command", reason="l8_apply_error", and the drain CONTINUES to the next message
    # (a following KILL still halts). Two DISTINCT chat-ids so both pass the monotonic nonce.
    # Kills: a raising __apply escaping drain (loop crash); the drain aborting the remaining
    # queue; not auditing the apply error.
    with _store(tmp_path) as store:
        ctl = _running_ctl(store)
        allow = {"chatA": "operator", "chatB": "operator"}
        boom = _signed("chatA", "BLACKLIST", "wallet:0xdead", "1")
        kill = _signed("chatB", "KILL", "", "1")
        transport = _FakeTransport([boom, kill])
        tc = TelegramController(ctl, store, transport, _auth(allowlist=allow))
        tc.drain()
        assert ctl.state() == _safety.HALTED                        # the KILL after the boom applied
        kinds_reasons = [(r["kind"], r["reason"]) for r in store.op_audit_log()]
        assert kinds_reasons == [
            ("state_change", "clean_reconcile"),                    # seed
            ("l8_command", "l8_apply_error"),                       # BLACKLIST raised, isolated
            ("state_change", "l8_kill"),                            # KILL's set_state row
            ("l8_command", "l8_kill"),                              # KILL's command row
        ]


def test_apply_kill_from_running_halts_via_set_state_l8_kill(tmp_path):
    # KILL -> set_state(HALTED, reason=REASON_L8_KILL). The state_change audit carries l8_kill
    # (NOT a generic 'halted'), and the drain's l8_command row detail is "KILL".
    # Kills: KILL routed to PAUSED/FLATTENING; wrong reason on set_state; a swap_caps instead.
    with _store(tmp_path) as store:
        ctl = _running_ctl(store)
        transport = _FakeTransport([_signed("chatA", "KILL", "", "1")])
        TelegramController(ctl, store, transport, _auth()).drain()
        assert ctl.state() == _safety.HALTED
        assert store.op_audit_log()[-2:] == [
            {"at": store.op_audit_log()[-2]["at"], "kind": "state_change",
             "reason": "l8_kill", "detail": "HALTED"},
            {"at": store.op_audit_log()[-1]["at"], "kind": "l8_command",
             "reason": "l8_kill", "detail": "KILL"},
        ]


def test_apply_pause_from_running_pauses_via_set_state_l8_paused(tmp_path):
    # PAUSE -> set_state(PAUSED, reason=REASON_L8_PAUSED). Distinct from KILL: soft halt.
    # Kills: PAUSE routed to HALTED; reason l8_kill instead of l8_paused.
    with _store(tmp_path) as store:
        ctl = _running_ctl(store)
        transport = _FakeTransport([_signed("chatA", "PAUSE", "", "1")])
        TelegramController(ctl, store, transport, _auth()).drain()
        assert ctl.state() == _safety.PAUSED
        assert [(r["kind"], r["reason"], r["detail"]) for r in store.op_audit_log()] == [
            ("state_change", "clean_reconcile", "RUNNING"),
            ("state_change", "l8_paused", "PAUSED"),
            ("l8_command", "l8_paused", "PAUSE"),
        ]
