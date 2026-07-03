"""L8 TelegramController (S4.6b/c/d / POL-6) -- the structurally-bounded remote safety surface.

Mirrors ProposeOnlyFacade: composes (never subclasses) a name-mangled SafetyController + store +
transport + auth, and exposes EXACTLY {drain, notify}. There is structurally NO order-entry or
approval path of any kind -- the command map dispatches ONLY the six safety-INCREASING verbs, so
a compromised channel can at worst STOP the bot. drain() runs on the serial runloop (S4.6d seam),
so set_state/swap_caps stay single-threaded. See DESIGN-S4.6-TELEGRAM.md SS2/SS3/SS6.
"""
from polybot.ers.ramp import step_weekly
from polybot.ers.safety import (
    FLATTENING, HALTED, PAUSED, RUNNING,
    REASON_L8_ALERTS_DOWN, REASON_L8_BLACKLIST, REASON_L8_KILL, REASON_L8_LOWER_CAPS,
    REASON_L8_PAUSED, REASON_L8_RESUME, REASON_OP_FLATTEN,
)

# The op-audit reason each authenticated verb records on its l8_command row (the state
# transition itself is audited by set_state/swap_caps; this is the command-level provenance).
_REASON_FOR_COMMAND = {
    "KILL": REASON_L8_KILL,
    "PAUSE": REASON_L8_PAUSED,
    "RESUME": REASON_L8_RESUME,
    "FLATTEN": REASON_OP_FLATTEN,
    "LOWER_CAPS": REASON_L8_LOWER_CAPS,
    "BLACKLIST": REASON_L8_BLACKLIST,
}


class TelegramController:
    def __init__(self, controller, store, transport, auth, *, alerts_down_threshold=3):
        self.__ctl = controller           # name-mangled -> _TelegramController__ctl
        self.__store = store
        self.__transport = transport
        self.__auth = auth
        self.__threshold = alerts_down_threshold
        self.__notify_fails = 0

    def drain(self):
        for raw in self.__transport.poll():
            result = self.__auth.authenticate(raw)
            if not result.ok:
                self.__store.record_op_event(
                    kind="l8_refused", reason=result.reason, detail=result.chat_id)
                continue
            try:
                self.__apply(result)
                self.__store.record_op_event(
                    kind="l8_command", reason=self.__reason_for(result.command),
                    detail=result.command)
            except Exception as exc:
                self.__store.record_op_event(
                    kind="l8_command", reason="l8_apply_error",
                    detail=f"{result.command}:{exc}")

    def notify(self, text):
        """Best-effort fire-and-forget alert over the (fake) transport. NEVER raises into the
        caller/loop: a send that returns False OR raises is counted as ONE consecutive failure.
        A success resets the run to zero. When the CONSECUTIVE-failure run reaches the
        alerts-down threshold, the top-level fail-safe fires: HALTED(l8_alerts_down)
        (persistent alerts-down means the operator is blind -> stop the bot). Returns None."""
        try:
            ok = self.__transport.send(text)
        except Exception:
            ok = False
        if ok:
            self.__notify_fails = 0
        else:
            self.__notify_fails += 1
            if self.__notify_fails >= self.__threshold:
                self.__ctl.set_state(HALTED, reason=REASON_L8_ALERTS_DOWN)

    def __reason_for(self, command):
        return _REASON_FOR_COMMAND[command]

    def __apply(self, result):
        command = result.command
        if command == "KILL":
            self.__ctl.set_state(HALTED, reason=REASON_L8_KILL)
        elif command == "PAUSE":
            self.__ctl.set_state(PAUSED, reason=REASON_L8_PAUSED)
        elif command == "RESUME":
            self.__ctl.set_state(RUNNING, reason=REASON_L8_RESUME)
        elif command == "FLATTEN":
            self.__ctl.set_state(FLATTENING, reason=REASON_OP_FLATTEN)
        elif command == "LOWER_CAPS":
            self.__ctl.swap_caps(step_weekly(self.__ctl.active_caps()), reason=REASON_L8_LOWER_CAPS)
        elif command == "BLACKLIST":
            # Payload is the already-neutralized "kind:value" (split on the FIRST colon so a
            # value may itself contain colons). Fail closed on a malformed payload: an unknown
            # kind OR an empty value raises ValueError, caught by drain's per-message isolation
            # -> l8_apply_error audit, op-state untouched, no junk row. The store is dumb
            # (records any kind); the validation lives HERE.
            target_kind, _, target_value = result.payload.partition(":")
            if target_kind not in ("wallet", "market", "source"):
                raise ValueError(f"unknown blacklist kind: {target_kind!r}")
            if not target_value:
                raise ValueError(f"empty blacklist value for kind: {target_kind!r}")
            self.__store.record_blacklist(target_kind=target_kind, target_value=target_value)
        else:  # pragma: no cover -- unreachable: _COMMAND_SET gates command before dispatch.
            raise ValueError(f"unmapped command: {command!r}")
