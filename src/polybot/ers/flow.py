"""Flow-journal recorder + rolling-window helpers (S4.7a / POL-6, DESIGN-S4.7-BREAKERS.md §4).

The flow_journal counts NEW-POSITION flow (kind="accept", amount = the folded position's
worst_case_risk) and realized outcomes (kind="realized", signed PnL: +win / -loss) so the
S4.7 rate caps, daily pending ceiling, and loss breakers survive restarts. Window math uses
the caller-supplied wall clock (``wall_at``, epoch seconds) -- NEVER the monotonic ``at``
stamp, which is not comparable across restarts. Wins never offset pending (conservative).
A malformed row in our own journal is corruption, never skipped: the window helpers RAISE
and each consumer converts the raise into its fail-closed action.
"""

from decimal import Decimal

from polybot.ers.safety import REASON_RATE_HOURLY

_KINDS = ("accept", "realized")


def make_flow_recorder(store, *, wall_clock):
    """Return a fill_sink-shaped callable ``(intent, decision, position)`` appending one
    kind="accept" flow row per ACCEPT: amount = the folded position's worst_case_risk,
    wall_at = wall_clock() (epoch seconds; time.time in the live assembly, injected in tests)."""
    def _rec(intent, decision, position):
        store.record_flow_event(kind="accept", token_id=position.token_id,
                                amount=position.worst_case_risk, wall_at=wall_clock())
    return _rec


def compose_sinks(*sinks):
    """Return ONE fill_sink fanning out to many: each sink is called exactly once per ACCEPT,
    in the given order, with the same ``(intent, decision, position)``. No service.py change --
    the composite plugs into process_pending's existing ``fill_sink=`` seam (fills + flow)."""
    def _sink(intent, decision, position):
        for sink in sinks:
            sink(intent, decision, position)
    return _sink


def accepts_in_window(rows, *, wall_now, window_seconds):
    """Count kind=="accept" rows inside the rolling window: in-window iff
    ``wall_now - wall_at <= window_seconds`` (INCLUSIVE old edge -- the breaker/ApiStorm
    convention; keeping the boundary row is the tighter direction)."""
    return sum(1 for r in rows
               if r["kind"] == "accept" and wall_now - r["wall_at"] <= window_seconds)


def pending_in_window(rows, *, wall_now, window_seconds=86400):
    """Today's pending worst-case-risk FLOW: the sum of accept amounts in the rolling window
    plus abs(amount) for realized LOSSES (amount < 0) in the window. Wins (amount >= 0)
    NEVER offset -- conservative. In-window iff ``wall_now - wall_at <= window_seconds``
    (INCLUSIVE old edge). A malformed row (unknown kind / missing key) RAISES
    (ValueError / KeyError propagate) -- corruption in our own journal is never skipped;
    every row is validated in full regardless of window."""
    total = Decimal("0")
    for r in rows:
        kind = r["kind"]
        if kind not in _KINDS:
            raise ValueError(f"unknown flow kind: {kind!r}")
        amount = r["amount"]
        if wall_now - r["wall_at"] > window_seconds:
            continue
        if kind == "accept":
            total += amount
        elif amount < Decimal("0"):
            total += -amount
    return total


def make_flow_gate(store, caps_provider, *, wall_clock):
    """The per-cycle flow gate (DESIGN-S4.7 SS3 rows 1-2 + SS4): returns a 0-arg callable ->
    None | a REASON_* string, wired into SafetyController.verdict's running-state branch via
    wire_flow_gate.

    store / caps_provider / wall_clock are consulted PER CALL: the gate follows the sliding
    window AND the ramp ratchet (assembly binds caps_provider=controller.active_caps). The
    gate does NOT catch its own exceptions -- verdict wraps a raise into flow_gate_error
    (fail closed, SS6.4). It does NOT filter frozen tokens: it is 0-arg with no portfolio
    view, and unfiltered accepts only count HIGHER = MORE blocking = the conservative
    direction (documented deviation from the breakers' frozen exclusion)."""
    def _gate():
        caps = caps_provider()
        rows = store.flow_log()
        now = wall_clock()
        if accepts_in_window(rows, wall_now=now, window_seconds=3600) >= caps.new_positions_per_hour:
            return REASON_RATE_HOURLY
        return None
    return _gate
