"""POL-16 paper-execution planning and durable target projection.

The planner is the authority boundary between an ERS ACCEPT and S9's maker-only fill
simulator. It never trusts Hermes's proposed price, side, or size: it re-fetches a live
book, joins the best bid as a BUY of the selected outcome token, and sizes shares from
the deterministic ERS-approved stake.
"""

from decimal import Decimal

from polybot.ers.intent_store import ShadowExecutionRecord
from polybot.ers.market_meta import ResolutionSubjectMetadata
from polybot.harness.fill_sim import simulate_fill


def make_shadow_execution_planner(*, book_for, subject_for, maker_config):
    """Return ``(intent, ACCEPT decision) -> canonical execution | None``.

    ``None`` means the freshly observed maker quote did not fill. Bad deterministic
    inputs fail loud so the ERS wiring can reject without signing.
    """
    def _plan(intent, decision):
        if decision.verdict != "ACCEPT":
            raise ValueError("shadow execution planning requires ACCEPT")
        stake = decision.stake_usd
        if stake is None or not stake.is_finite() or stake <= 0:
            raise ValueError("accepted shadow stake must be finite and > 0")

        subject = subject_for(intent)
        if not isinstance(subject, ResolutionSubjectMetadata):
            raise TypeError("subject_for must return ResolutionSubjectMetadata")
        if (subject.event_id != intent.event_id
                or subject.condition_id != intent.condition_id
                or subject.token_id != intent.token_id):
            raise ValueError("shadow resolution subject contradicts intent")

        book = book_for(intent.token_id)
        if book is None:
            return None
        resting_price = book.best_bid()
        if (resting_price is None or not resting_price.is_finite()
                or not (Decimal(0) < resting_price < Decimal(1))):
            return None

        shares = stake / resting_price
        fill = simulate_fill(
            token_id=intent.token_id,
            condition_id=intent.condition_id,
            category=subject.category,
            side="BUY",
            shares=shares,
            resting_price=resting_price,
            book=book,
            maker_config=maker_config,
        )
        if not fill.filled:
            return None
        return ShadowExecutionRecord(
            execution_id=intent.intent_id,
            token_id=intent.token_id,
            condition_id=intent.condition_id,
            event_id=subject.event_id,
            category=subject.category,
            outcome_slot=subject.outcome_slot,
            sibling_token_ids=subject.sibling_token_ids,
            side="BUY",
            shares=fill.shares,
            price_exec=fill.fill_price,
            fill_mid=fill.fill_mid,
            reward_accrued=fill.reward_accrued,
        )

    return _plan


class ShadowExecutionDispatcher:
    """Idempotently fan the atomic ACCEPT outbox into Maker then Shadow."""

    def __init__(self, store, maker_ledger, shadow_ledger):
        self._store = store
        self._targets = {"MAKER": maker_ledger, "SHADOW": shadow_ledger}

    def drain(self, limit):
        acknowledged = 0
        for record in self._store.pending_shadow_executions(limit):
            changed = self._targets[record.role].apply_shadow_execution(record.execution)
            self._after_apply(record, changed)
            self._store.acknowledge_shadow_execution(
                record.sequence, record.execution.execution_id, record.role
            )
            acknowledged += 1
        return acknowledged

    def _after_apply(self, record, changed):
        """Failure-injection seam after the target transaction commits."""
