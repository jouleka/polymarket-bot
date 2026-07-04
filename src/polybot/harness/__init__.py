"""Earn-autonomy shadow harness (S9 / POL-11).

The capstone package: a self-verifying RampConfig -> pure exact-Decimal fill
simulator -> append-only shadow ledger -> walk-forward evidence evaluator ->
binary stage-machine controller. Runs SHADOW-ONLY over simulated maker fills and
injected books/marks; nothing here quotes, signs, sends, or widens a cap.
Additive to the tree but for one opt-in ERSController(reconciler=None) boot seam.
"""
