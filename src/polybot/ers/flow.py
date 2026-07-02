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
    (INCLUSIVE old edge). Every row is read in full regardless of window, so a missing key
    propagates KeyError -- callers convert the raise to their fail-closed action."""
    total = Decimal("0")
    for r in rows:
        kind = r["kind"]
        amount = r["amount"]
        if wall_now - r["wall_at"] > window_seconds:
            continue
        if kind == "accept":
            total += amount
        elif kind == "realized" and amount < Decimal("0"):
            total += -amount
    return total
