"""Fail-closed POL-15 exception hierarchy."""


class ResolutionError(Exception):
    """Base resolution-feed failure."""


class ResolutionUnavailable(ResolutionError):
    """Retryable provider or per-condition authority failure."""


class ConditionAlreadyTerminal(ResolutionError):
    """A target ledger already has an immutable receipt for the condition."""


class SettlementConflict(ResolutionError):
    """Terminal authority contradicts immutable stored state."""
