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
from polybot.resolution.errors import SettlementConflict


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


def make_mark_for(ledger, *, book_for):
    """Return terminal-first marks for canonical Maker or Shadow inventory."""
    def _mark(token_id):
        rows = [row for row in ledger.all() if row.token_id == token_id]
        if not rows:
            return None

        # POL-16 marks only canonical rows. Legacy rows remain deliberately
        # unsettleable and therefore fail closed rather than borrowing a live mark.
        if any(
                row.event_id is None or row.outcome_slot is None
                or row.sibling_token_ids is None
                for row in rows):
            return None

        terminal_rows = [row for row in rows if row.terminal_id is not None]
        if terminal_rows:
            if len(terminal_rows) != len(rows):
                raise SettlementConflict("token has mixed pending and terminal shadow rows")
            authority = {
                (
                    row.terminal_id, row.status, row.resolution_value,
                    row.resolution_numerator, row.resolution_denominator,
                )
                for row in terminal_rows
            }
            if len(authority) != 1:
                raise SettlementConflict("token has contradictory terminal shadow marks")
            _terminal_id, status, value, numerator, denominator = authority.pop()
            if (isinstance(numerator, bool) or not isinstance(numerator, int)
                    or isinstance(denominator, bool) or not isinstance(denominator, int)
                    or numerator < 0 or denominator <= 0 or numerator > denominator):
                raise SettlementConflict("token has invalid terminal shadow payout")
            if status in ("WON", "LOST", "SETTLED"):
                if (value is None or not value.is_finite()
                        or not (Decimal(0) <= value <= Decimal(1))):
                    raise SettlementConflict("token has invalid terminal shadow value")
                return value
            if status in ("DISPUTED", "VOID"):
                if value is not None:
                    raise SettlementConflict("excluded terminal shadow mark carries a value")
                return None
            raise SettlementConflict(f"token has unknown terminal shadow status {status!r}")

        if any(row.status is not None for row in rows):
            raise SettlementConflict("pending canonical shadow row has settlement without terminal")
        book = book_for(token_id)
        if book is None:
            return None
        midpoint = book.midpoint()
        if (midpoint is None or not midpoint.is_finite()
                or not (Decimal(0) <= midpoint <= Decimal(1))):
            return None
        return midpoint

    return _mark
