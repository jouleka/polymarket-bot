"""Maker-rewards shadow analytics (S8 / POL-10).

Honest net-of-adverse-selection maker accounting: append-only shadow ledger -> pure
exact-Decimal cost/PnL calculators -> binary GO/NO-GO gate, all data-gated dormant.
Never reward-gross: the only number the gate reads is the net after ALL cost legs.
Purely additive — imports nothing from ers/detectors/calibration at module load.
"""
