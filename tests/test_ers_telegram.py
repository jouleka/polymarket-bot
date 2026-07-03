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
    # RAISES (a BLACKLIST with an UNKNOWN kind -> ValueError, S4.6d) is caught + audited
    # kind="l8_command", reason="l8_apply_error", and the drain CONTINUES to the next message
    # (a following KILL still halts). Two DISTINCT chat-ids so both pass the monotonic nonce.
    # Kills: a raising __apply escaping drain (loop crash); the drain aborting the remaining
    # queue; not auditing the apply error.
    with _store(tmp_path) as store:
        ctl = _running_ctl(store)
        allow = {"chatA": "operator", "chatB": "operator"}
        boom = _signed("chatA", "BLACKLIST", "banana:x", "1")   # unknown kind -> __apply raises
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


def test_apply_resume_lifts_a_paused_loop_to_running_l8_resume(tmp_path):
    # RESUME (from PAUSED) -> set_state(RUNNING, reason=REASON_L8_RESUME). Half 1 of the Fork-1
    # pair. Reach PAUSED via a direct set_state (operator PAUSE), THEN drain a RESUME.
    # Kills: RESUME reason wrong; RESUME failing to reach RUNNING from PAUSED.
    with _store(tmp_path) as store:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
        ctl.set_state(_safety.PAUSED, reason=_safety.REASON_L8_PAUSED)
        transport = _FakeTransport([_signed("chatA", "RESUME", "", "1")])
        TelegramController(ctl, store, transport, _auth()).drain()
        assert ctl.state() == _safety.RUNNING
        assert store.op_audit_log()[-2:] == [
            {"at": store.op_audit_log()[-2]["at"], "kind": "state_change",
             "reason": "l8_resume", "detail": "RUNNING"},
            {"at": store.op_audit_log()[-1]["at"], "kind": "l8_command",
             "reason": "l8_resume", "detail": "RESUME"},
        ]


def test_apply_resume_lifts_a_halted_loop_to_running_l8_resume(tmp_path):
    # RESUME (from HALTED) -> RUNNING. Half 2 of the Fork-1 pair: this IS the documented operator
    # override that clears a sticky L5/loss HALT (the ONLY operator HALTED->RUNNING path). The
    # controller boots HALTED (unclean_restart) -- no set_state needed to reach the source state.
    # Kills: gating RESUME on the source being PAUSED only (a HALTED loop would stay stuck).
    with _store(tmp_path) as store:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)  # boot HALTED
        assert ctl.state() == _safety.HALTED
        transport = _FakeTransport([_signed("chatA", "RESUME", "", "1")])
        TelegramController(ctl, store, transport, _auth()).drain()
        assert ctl.state() == _safety.RUNNING
        assert store.op_audit_log()[-1] == {
            "at": store.op_audit_log()[-1]["at"], "kind": "l8_command",
            "reason": "l8_resume", "detail": "RESUME"}


def test_apply_flatten_from_running_sets_flattening_op_flatten(tmp_path):
    # FLATTEN -> set_state(FLATTENING, reason=REASON_OP_FLATTEN). It sets the op-state ONLY; the
    # actual de-risk fires later in verdict() (unchanged S4.1 path), NOT in __apply.
    # Kills: FLATTEN calling signer.flatten directly from __apply; wrong op-state/reason.
    with _store(tmp_path) as store:
        ctl = _running_ctl(store)
        transport = _FakeTransport([_signed("chatA", "FLATTEN", "", "1")])
        TelegramController(ctl, store, transport, _auth()).drain()
        assert ctl.state() == _safety.FLATTENING
        assert [(r["kind"], r["reason"], r["detail"]) for r in store.op_audit_log()] == [
            ("state_change", "clean_reconcile", "RUNNING"),
            ("state_change", "op_flatten", "FLATTENING"),
            ("l8_command", "op_flatten", "FLATTEN"),
        ]


def test_apply_lower_caps_tightens_per_trade_to_six_via_swap_caps(tmp_path):
    # LOWER_CAPS -> swap_caps(step_weekly(active_caps()), reason=REASON_L8_LOWER_CAPS). Default
    # RiskCaps per_trade 12 -> step_weekly -> 6; swap_caps writes a caps_swap audit row
    # (reason=l8_lower_caps) BEFORE the l8_command row.
    # Kills: LOWER_CAPS calling set_state; using step_daily (per_trade 9) instead of step_weekly;
    # wrong swap_caps reason; bypassing swap_caps (no caps_swap audit row).
    with _store(tmp_path) as store:
        ctl = _running_ctl(store)
        assert ctl.active_caps().per_trade == Decimal("12")
        transport = _FakeTransport([_signed("chatA", "LOWER_CAPS", "", "1")])
        TelegramController(ctl, store, transport, _auth()).drain()
        assert ctl.active_caps().per_trade == Decimal("6")
        assert ctl.active_caps().total_open_risk == Decimal("30")
        kinds_reasons = [(r["kind"], r["reason"]) for r in store.op_audit_log()]
        assert kinds_reasons == [
            ("state_change", "clean_reconcile"),
            ("caps_swap", "l8_lower_caps"),          # swap_caps' own audit-before-mutate row
            ("l8_command", "l8_lower_caps"),         # the drain's command row
        ]


def test_apply_lower_caps_a_second_time_is_a_hash_identical_no_op_no_caps_swap_row(tmp_path):
    # The idempotent boundary (pair with the tighten case): step_weekly is idempotent, so a
    # SECOND LOWER_CAPS produces a hash-identical caps -> swap_caps returns False -> NO second
    # caps_swap row -- but the l8_command row IS still written (the command WAS authenticated +
    # applied; the swap was simply a no-op). Two chat-ids so both messages pass the nonce gate.
    # Kills: a compounding step (per_trade < 6 on the 2nd); a spurious 2nd caps_swap audit row;
    # the drain skipping the l8_command row when swap_caps no-ops.
    with _store(tmp_path) as store:
        ctl = _running_ctl(store)
        allow = {"chatA": "operator", "chatB": "operator"}
        first = _signed("chatA", "LOWER_CAPS", "", "1")
        second = _signed("chatB", "LOWER_CAPS", "", "1")
        transport = _FakeTransport([first, second])
        TelegramController(ctl, store, transport, _auth(allowlist=allow)).drain()
        assert ctl.active_caps().per_trade == Decimal("6")        # not compounded below 6
        kinds_reasons = [(r["kind"], r["reason"]) for r in store.op_audit_log()]
        assert kinds_reasons == [
            ("state_change", "clean_reconcile"),
            ("caps_swap", "l8_lower_caps"),      # only the FIRST swap writes a caps_swap row
            ("l8_command", "l8_lower_caps"),     # first command
            ("l8_command", "l8_lower_caps"),     # second command applied (swap no-op'd, still audited)
        ]


# ---------------------------------------------------------------------------
# S4.6c: notify() best-effort + alerts-down halt (tasks C1-C5)
# Module-level helpers copied per file (no conftest / no shared fixtures).
# ---------------------------------------------------------------------------
from polybot.core.clock import MonotonicStamper
from polybot.ers.intent_store import IntentStore
from polybot.ers.caps import RiskCaps
from polybot.ers import safety as _safety
from polybot.ers.telegram import TelegramController


def _c_store(tmp_path):
    return IntentStore(str(tmp_path / "i.db"), MonotonicStamper())


def _c_ctl(store):
    # SafetyController driven to RUNNING so an alerts-down HALT is an observable transition.
    ctl = _safety.SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
    ctl.set_state(_safety.RUNNING, reason="clean_reconcile")
    return ctl


class _CStubAuth:
    """notify() never touches auth; a no-method stub satisfies construction."""


class _CFlakyTransport:
    """poll()->[] (notify never polls). send() replays a scripted sequence of
    True (success), False (soft failure), or the string "raise" (send raises)."""
    def __init__(self, script):
        self._script = list(script)
        self._i = 0
        self.sent = []

    def poll(self):
        return []

    def send(self, text):
        self.sent.append(text)
        step = self._script[self._i]
        self._i += 1
        if step == "raise":
            raise RuntimeError("telegram send exploded")
        return step


def _c_states(store):
    return [(r["kind"], r["reason"], r["detail"]) for r in store.op_audit_log()]


def test_notify_success_returns_none_and_does_not_halt(tmp_path):
    # Kills: notify() returning a truthy/echoed value instead of None; a spurious halt on success.
    with _c_store(tmp_path) as store:
        ctl = _c_ctl(store)
        transport = _CFlakyTransport([True])
        tg = TelegramController(ctl, store, transport, _CStubAuth(), alerts_down_threshold=3)
        result = tg.notify("hello operator")
        assert result is None
        assert transport.sent == ["hello operator"]      # the text was actually sent
        assert ctl.state() == _safety.RUNNING            # a success never halts


def test_notify_success_after_failures_resets_the_consecutive_counter(tmp_path):
    # Kills: a CUMULATIVE (not consecutive) counter -- a success in the middle must RESET it,
    #        so fail,fail,success,fail,fail (4 total, run broken) stays BELOW a threshold of 3.
    with _c_store(tmp_path) as store:
        ctl = _c_ctl(store)
        transport = _CFlakyTransport([False, False, True, False, False])
        tg = TelegramController(ctl, store, transport, _CStubAuth(), alerts_down_threshold=3)
        for _ in range(5):
            tg.notify("beat")
        # 4 total failures but never 3 IN A ROW -> no halt; the success reset the run.
        assert ctl.state() == _safety.RUNNING
        assert _c_states(store) == [("state_change", "clean_reconcile", "RUNNING")]


def test_notify_false_send_does_not_raise_into_caller(tmp_path):
    # Kills: a `raise` on a False send; notify propagating the transport's soft-failure to the loop.
    with _c_store(tmp_path) as store:
        ctl = _c_ctl(store)
        transport = _CFlakyTransport([False])          # one soft failure, below threshold
        tg = TelegramController(ctl, store, transport, _CStubAuth(), alerts_down_threshold=3)
        result = tg.notify("degraded")                 # MUST NOT raise
        assert result is None
        assert transport.sent == ["degraded"]
        assert ctl.state() == _safety.RUNNING          # one failure < threshold -> no halt yet


def test_notify_raising_send_is_caught_and_counted_not_propagated(tmp_path):
    # Kills: an uncaught exception from send() escaping notify(); a raising send NOT counting
    #        toward alerts-down (a bare `try/except: pass` that forgets to increment the counter).
    with _c_store(tmp_path) as store:
        ctl = _c_ctl(store)
        # Two raises with threshold=2 -> the SECOND consecutive raise must trip the halt, proving
        # a raising send is both (a) swallowed and (b) counted exactly like a False send.
        transport = _CFlakyTransport(["raise", "raise"])
        tg = TelegramController(ctl, store, transport, _CStubAuth(), alerts_down_threshold=2)
        tg.notify("boom-1")                            # raise #1 caught, counted (1 < 2, no halt)
        assert ctl.state() == _safety.RUNNING
        tg.notify("boom-2")                            # raise #2 caught, counted (2 >= 2 -> halt)
        assert ctl.state() == _safety.HALTED
        assert transport.sent == ["boom-1", "boom-2"]  # both sends were attempted, neither escaped


def test_notify_two_consecutive_failures_below_default_threshold_do_not_halt(tmp_path):
    # Kills (boundary, the "no" half of the pair): an off-by-one `>= threshold-1` that would
    #        halt at the 2nd failure under the default threshold of 3.
    with _c_store(tmp_path) as store:
        ctl = _c_ctl(store)
        transport = _CFlakyTransport([False, False])   # exactly 2 consecutive failures
        tg = TelegramController(ctl, store, transport, _CStubAuth(), alerts_down_threshold=3)
        tg.notify("f1")
        tg.notify("f2")
        assert ctl.state() == _safety.RUNNING          # 2 < 3 -> NO halt
        # No state_change row beyond the initial RUNNING (the halt would append one).
        assert _c_states(store) == [("state_change", "clean_reconcile", "RUNNING")]


def test_notify_third_consecutive_failure_at_default_threshold_halts_alerts_down(tmp_path):
    # Kills (boundary, the "yes" half): a `> threshold` that would NEVER fire at exactly 3;
    #        the wrong halt reason; the missing state_change audit row.
    with _c_store(tmp_path) as store:
        ctl = _c_ctl(store)
        transport = _CFlakyTransport([False, False, False])   # the 3rd trips it
        tg = TelegramController(ctl, store, transport, _CStubAuth(), alerts_down_threshold=3)
        tg.notify("f1")
        tg.notify("f2")
        assert ctl.state() == _safety.RUNNING          # still running after 2
        tg.notify("f3")                                # the 3rd consecutive failure
        assert ctl.state() == _safety.HALTED           # 3 >= 3 -> HALTED
        # set_state wrote the alerts-down transition row (the ONLY audit trail of the halt).
        assert _c_states(store) == [
            ("state_change", "clean_reconcile", "RUNNING"),
            ("state_change", "l8_alerts_down", "HALTED"),
        ]


def test_notify_custom_threshold_one_halts_on_first_failure(tmp_path):
    # Kills: a HARD-CODED threshold of 3 ignoring the ctor kwarg; the "yes" half at threshold=1.
    with _c_store(tmp_path) as store:
        ctl = _c_ctl(store)
        transport = _CFlakyTransport([False])          # a single failure
        tg = TelegramController(ctl, store, transport, _CStubAuth(), alerts_down_threshold=1)
        tg.notify("only-one")
        assert ctl.state() == _safety.HALTED           # 1 >= 1 -> immediate halt
        assert _c_states(store) == [
            ("state_change", "clean_reconcile", "RUNNING"),
            ("state_change", "l8_alerts_down", "HALTED"),
        ]


def test_notify_custom_threshold_one_success_does_not_halt(tmp_path):
    # Kills (the "no" half): a threshold=1 that halts even on SUCCESS (a mis-wired counter that
    #        increments regardless of ok, or a halt outside the `else` branch).
    with _c_store(tmp_path) as store:
        ctl = _c_ctl(store)
        transport = _CFlakyTransport([True])
        tg = TelegramController(ctl, store, transport, _CStubAuth(), alerts_down_threshold=1)
        tg.notify("healthy")
        assert ctl.state() == _safety.RUNNING          # a success never halts, even at threshold 1
        assert _c_states(store) == [("state_change", "clean_reconcile", "RUNNING")]


def test_notify_default_threshold_is_three(tmp_path):
    # Kills: the ctor default drifting off 3 (a build error would surface here, not only in C3).
    with _c_store(tmp_path) as store:
        ctl = _c_ctl(store)
        transport = _CFlakyTransport([False, False, False])
        tg = TelegramController(ctl, store, transport, _CStubAuth())   # NO threshold kwarg -> default
        tg.notify("d1"); tg.notify("d2")
        assert ctl.state() == _safety.RUNNING          # default 3 not reached at 2
        tg.notify("d3")
        assert ctl.state() == _safety.HALTED           # reached at 3 -> the default IS 3


def test_notify_over_persistently_raising_transport_returns_normally_and_halts(tmp_path):
    # Kills: notify() blocking/crashing the loop on a hostile transport; the halt failing to fire
    #        under a stream of raises.
    with _c_store(tmp_path) as store:
        ctl = _c_ctl(store)
        transport = _CFlakyTransport(["raise", "raise", "raise", "raise", "raise"])
        tg = TelegramController(ctl, store, transport, _CStubAuth(), alerts_down_threshold=3)
        for i in range(5):
            assert tg.notify(f"m{i}") is None          # every call returns normally, never raises
        assert ctl.state() == _safety.HALTED
        assert transport.sent == ["m0", "m1", "m2", "m3", "m4"]   # all five attempted
        # The counter keeps tripping >= threshold on calls 3,4,5 -> one set_state each = 3 rows
        # (the pinned notify body has NO reset-on-halt and NO already-halted guard).
        alerts_rows = [s for s in _c_states(store) if s == ("state_change", "l8_alerts_down", "HALTED")]
        assert len(alerts_rows) == 3


def test_notify_does_not_write_its_own_op_audit_row(tmp_path):
    # Kills: notify() sneaking a record_op_event call (an l8_* audit row) -- the ONLY audit trail
    #        of the alerts-down halt must be set_state's state_change row, nothing else.
    with _c_store(tmp_path) as store:
        ctl = _c_ctl(store)
        transport = _CFlakyTransport([False])          # below threshold -> no halt, no rows at all
        tg = TelegramController(ctl, store, transport, _CStubAuth(), alerts_down_threshold=3)
        tg.notify("quiet-failure")
        # Only the setup RUNNING row exists; notify wrote NOTHING to op_audit on a sub-threshold fail.
        assert _c_states(store) == [("state_change", "clean_reconcile", "RUNNING")]


class _CConnDownTransport:
    """send() raises a NON-RuntimeError (what a real Telegram transport raises when the
    channel is down). poll()->[] (notify never polls)."""
    def __init__(self):
        self.sent = []

    def poll(self):
        return []

    def send(self, text):
        self.sent.append(text)
        raise ConnectionError("channel down")


class _CNoneReturnTransport:
    """send() returns None (a transport with NO explicit return). poll()->[]."""
    def __init__(self):
        self.sent = []

    def poll(self):
        return []

    def send(self, text):
        self.sent.append(text)
        return None


def test_notify_a_non_runtimeerror_send_exception_is_still_caught_and_counted(tmp_path):
    # MUTATION KILLED = narrowing `except Exception` to a specific-type tuple lets a live
    # transport's ConnectionError/TimeoutError propagate into the run loop -- the exact
    # real-world alerts-down failure. The catch MUST be broad (any Exception), and a caught
    # raise MUST count toward the alerts-down threshold exactly like a False/raising send.
    # MUTATION-VERIFY: narrow `except Exception` -> `except RuntimeError` in telegram.py,
    # confirm THIS test fails (ConnectionError propagates), revert + sweep pycache.
    with _c_store(tmp_path) as store:
        ctl = _c_ctl(store)
        transport = _CConnDownTransport()              # send() raises ConnectionError (non-RuntimeError)
        tg = TelegramController(ctl, store, transport, _CStubAuth(), alerts_down_threshold=1)
        result = tg.notify("channel-down")             # MUST NOT propagate the ConnectionError
        assert result is None                          # caught, returned normally
        assert transport.sent == ["channel-down"]      # the send was attempted
        assert ctl.state() == _safety.HALTED           # 1 >= 1 -> counted AND halted
        assert _c_states(store) == [
            ("state_change", "clean_reconcile", "RUNNING"),
            ("state_change", "l8_alerts_down", "HALTED"),
        ]


def test_notify_a_none_returning_send_counts_as_a_failure(tmp_path):
    # MUTATION KILLED = treating a None (no-explicit-return) send as success silently blinds
    # the fail-safe. `if ok:` is correct (None is falsy -> the failure branch); a mutation to
    # `if ok is not False:` would take the reset branch on None and never halt.
    # MUTATION-VERIFY: change `if ok:` to `if ok is not False:`, confirm this test fails
    # (None takes the reset branch, no halt), revert + sweep pycache.
    with _c_store(tmp_path) as store:
        ctl = _c_ctl(store)
        transport = _CNoneReturnTransport()            # send() returns None
        tg = TelegramController(ctl, store, transport, _CStubAuth(), alerts_down_threshold=1)
        tg.notify("no-return")
        assert transport.sent == ["no-return"]         # the send was attempted
        assert ctl.state() == _safety.HALTED           # None is falsy -> counted -> 1 >= 1 -> halt
        assert _c_states(store) == [
            ("state_change", "clean_reconcile", "RUNNING"),
            ("state_change", "l8_alerts_down", "HALTED"),
        ]


# --- S4.6d: the BLACKLIST verb (replacing the S4.6b NotImplementedError stub) -----------------
import hmac as _hmac_d
from polybot.core.clock import MonotonicStamper as _Stamper_d
from polybot.ers.intent_store import IntentStore as _IntentStore_d
from polybot.ers import telegram_auth as _ta_d
from polybot.ers.telegram import TelegramController as _TC_d


def _store_d(tmp_path):
    return _IntentStore_d(str(tmp_path / "i.db"), _Stamper_d())


_SECRET_D = b"s4.6d-secret"


def _signed_d(chat_id, command, payload, nonce, secret=_SECRET_D):
    # Build a VALID signed RawMessage: sig = compute_mac(canonical over the sig-less message).
    unsigned = _ta_d.RawMessage(chat_id=chat_id, command=command, payload=payload,
                                nonce=nonce, sig=b"")
    sig = _ta_d.compute_mac(_ta_d.canonical_message(unsigned), secret)
    return _ta_d.RawMessage(chat_id=chat_id, command=command, payload=payload,
                            nonce=nonce, sig=sig)


class _FakeTransport_d:
    """Duck-typed TelegramTransport: poll() drains a FIFO queue of RawMessages; send() records."""
    def __init__(self, inbound=()):
        self._inbound = list(inbound)
        self.sent = []
        self.send_result = True

    def poll(self):
        out, self._inbound = self._inbound, []
        return out

    def send(self, text):
        self.sent.append(text)
        return self.send_result


def _auth_d(chat_id="ops"):
    return _ta_d.CommandAuth(allowlist={chat_id: "operator"},
                             secret_holder=_ta_d.SecretHolder(_SECRET_D))


def _tc_d(store, ctl, transport, chat_id="ops"):
    return _TC_d(ctl, store, transport, _auth_d(chat_id))


def test_blacklist_verb_records_the_parsed_kind_value_and_audits_l8_blacklist(tmp_path):
    # DESIGN §3 row BLACKLIST: an authenticated "kind:value" payload parses to
    # (target_kind, target_value), records a durable blacklist row, and the drain audits
    # kind="l8_command" reason="l8_blacklist" detail="BLACKLIST". Kills: leaving the
    # NotImplementedError stub, mis-parsing the payload, or the wrong audit reason.
    from polybot.ers.safety import SafetyController
    from polybot.ers.caps import RiskCaps
    with _store_d(tmp_path) as store:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
        transport = _FakeTransport_d([_signed_d("ops", "BLACKLIST", "wallet:0xdead", "1")])
        tc = _tc_d(store, ctl, transport)
        tc.drain()
        # The durable row landed with the parsed kind + value.
        assert [(r["target_kind"], r["target_value"]) for r in store.blacklist_log()] == [
            ("wallet", "0xdead")]
        # The drain audited exactly the l8_command / l8_blacklist row for BLACKLIST.
        assert [(r["kind"], r["reason"], r["detail"]) for r in store.op_audit_log()] == [
            ("l8_command", "l8_blacklist", "BLACKLIST")]


def test_blacklist_unknown_kind_is_isolated_as_l8_apply_error_and_touches_no_state(tmp_path):
    # Refuse-partner of D4: an AUTHENTICATED BLACKLIST with a kind outside {wallet,market,
    # source} makes __apply raise ValueError, which drain's per-message isolation catches +
    # audits kind="l8_command" reason="l8_apply_error" detail="BLACKLIST:<exc>". NO blacklist
    # row is written and the op-state is untouched (still boot HALTED). Kills: over-widening
    # the kind whitelist to accept the bad kind, or letting the raise escape drain (which
    # would crash the runloop), or recording a row before the guard.
    from polybot.ers import safety as _safety
    from polybot.ers.safety import SafetyController
    from polybot.ers.caps import RiskCaps
    with _store_d(tmp_path) as store:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)  # boot: HALTED
        transport = _FakeTransport_d([_signed_d("ops", "BLACKLIST", "banana:x", "1")])
        tc = _tc_d(store, ctl, transport)
        tc.drain()                                   # must NOT raise
        assert store.blacklist_log() == []           # nothing recorded
        assert ctl.state() == _safety.HALTED         # op-state untouched
        rows = store.op_audit_log()
        assert len(rows) == 1
        assert rows[0]["kind"] == "l8_command"
        assert rows[0]["reason"] == "l8_apply_error"
        assert rows[0]["detail"].startswith("BLACKLIST:")


# --- S4.6d: the ERSController(telegram=) seam -------------------------------------------------
from polybot.ers import safety as _safety_seam
from polybot.ers.controller import ERSController as _ERS_seam
from polybot.ers.safety import SafetyController as _SC_seam
from polybot.ers.caps import RiskCaps as _RC_seam
from polybot.ers.service import PaperSigner as _PS_seam
from polybot.ingestion.orderbook import LocalBook as _LB_seam

_P_seam = dict(token_id="t1", condition_id="m1", event_id="e1", side="BUY", target_price="0.50",
               max_price="0.60", size_usd_suggestion="100", p="0.9", p_confidence="0.8",
               resolution_summary="", thesis="", citations=())


def _book_seam(ask, *, size="1000", bid="0.01"):
    book = _LB_seam()
    book.apply_book({"bids": [{"price": bid, "size": size}], "asks": [{"price": ask, "size": size}]})
    return book


def test_telegram_none_default_leaves_the_cycle_exactly_as_today(tmp_path):
    # Dormant-by-default: an ERSController WITHOUT the telegram kwarg (None) trades exactly as
    # before S4.6 -- the intent ACCEPTs, no extra audit rows beyond the setup state_change.
    # Expected GREEN from birth (the 840 baseline is the wider proof). Mirrors
    # test_lossbreakers_none_default_leaves_the_cycle_exactly_as_today. Kills: draining/acting
    # when the seam is None, or requiring the kwarg.
    with _store_d(tmp_path) as store:
        ctl = _SC_seam(caps=_RC_seam(), store=store, clock=lambda: 0)
        ctl.set_state(_safety_seam.RUNNING, reason="clean_reconcile")
        store.propose_trade("i1", **_P_seam)
        signer = _PS_seam()
        rc = _ERS_seam(store=store, book_for={"t1": _book_seam("0.50")}.get, caps=_RC_seam(),
                       signer=signer, controller=ctl, clock=lambda: 0)   # telegram unset
        rc.run_cycle()
        assert store.get("i1").status == "ACCEPTED"
        assert [r["kind"] for r in store.op_audit_log()] == ["state_change"]


def test_run_cycle_drains_first_so_an_authenticated_kill_halts_before_any_intent_processes(tmp_path):
    # DESIGN §2 step 0 + invariant 7 (DOMINANCE): drain is run_cycle's FIRST step, so a queued
    # authenticated KILL flips the loop HALTED at the top -- and the SAME cycle's process_pending
    # then REJECTs the pending intent under l8_kill and places NOTHING (the S4.4/S4.7 drain-at-top
    # pattern, mirroring test_run_cycle_starts_halted_and_blocks). Kills: draining AFTER
    # beat/anomaly/process_pending (the KILL would land a cycle late and the intent would ACCEPT
    # first), or not wiring the seam into run_cycle at all.
    from polybot.ers import safety as _safety
    from polybot.ers.safety import SafetyController
    from polybot.ers.caps import RiskCaps
    from polybot.ers.service import PaperSigner
    with _store_d(tmp_path) as store:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
        ctl.set_state(_safety.RUNNING, reason="clean_reconcile")   # a LIVE loop
        store.propose_trade("i1", **_P_seam)
        signer = PaperSigner()
        transport = _FakeTransport_d([_signed_d("ops", "KILL", "", "1")])
        tc = _tc_d(store, ctl, transport)
        rc = _ERS_seam(store=store, book_for={"t1": _book_seam("0.50")}.get, caps=RiskCaps(),
                       signer=signer, controller=ctl, telegram=tc, clock=lambda: 0)
        rc.run_cycle()
        assert ctl.state() == _safety.HALTED                        # KILL applied at the top
        assert store.get("i1").status == "REJECTED"                 # ...before the intent processed
        assert store.get("i1").decision_reason == "l8_kill"         # under the KILL's stored reason
        assert signer.placed == []                                  # NOTHING was placed this cycle


class _StateSnoopingHeartbeat_d:
    """A heartbeat that records the controller op-state AT THE MOMENT beat() is called --
    proves the KILL drain ran (HALTED) BEFORE the beat, per the DESIGN §2 step-0-before-step-1
    ordering."""
    def __init__(self, ctl):
        self._ctl = ctl
        self.state_at_beat = []

    def beat(self):
        self.state_at_beat.append(self._ctl.state())


def test_drain_runs_before_the_heartbeat_beat(tmp_path):
    # DESIGN §2: step 0 (drain) precedes step 1 (beat). A KILL queued this cycle must ALREADY
    # have flipped the loop HALTED by the time beat() fires. Kills: ordering the drain AFTER
    # beat (state_at_beat would read RUNNING), which would let the out-of-band supervisor beat
    # a loop that should already be dying.
    from polybot.ers import safety as _safety
    from polybot.ers.safety import SafetyController
    from polybot.ers.caps import RiskCaps
    from polybot.ers.service import PaperSigner
    with _store_d(tmp_path) as store:
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
        ctl.set_state(_safety.RUNNING, reason="clean_reconcile")
        signer = PaperSigner()
        transport = _FakeTransport_d([_signed_d("ops", "KILL", "", "1")])
        tc = _tc_d(store, ctl, transport)
        hb = _StateSnoopingHeartbeat_d(ctl)
        rc = _ERS_seam(store=store, book_for={"t1": _book_seam("0.50")}.get, caps=RiskCaps(),
                       signer=signer, controller=ctl, telegram=tc, heartbeat=hb, clock=lambda: 0)
        rc.run_cycle()
        assert hb.state_at_beat == [_safety.HALTED]   # the KILL drained BEFORE the beat
