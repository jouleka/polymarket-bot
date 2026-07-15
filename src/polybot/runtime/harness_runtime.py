"""Periodic evidence and advisory ramp projection for POL-17."""

from __future__ import annotations

from dataclasses import dataclass

from polybot.harness.ramp_controller import SHADOW


@dataclass(frozen=True)
class HarnessSnapshot:
    reports: dict
    decisions: dict


class HarnessEvidenceRuntime:
    """Evaluate evidence and return advice; owns no signer or cap mutation seam."""

    def __init__(self, *, categories, evaluate, ramp_controller, portfolio_for):
        if (not isinstance(categories, tuple) or not categories
                or len(set(categories)) != len(categories)):
            raise ValueError("categories must be a non-empty unique tuple")
        self._categories = categories
        self._evaluate = evaluate
        self._ramp_controller = ramp_controller
        self._portfolio_for = portfolio_for
        self._stages = {category: SHADOW for category in categories}
        self._latest = HarnessSnapshot({}, {})

    def update(self):
        reports = {}
        decisions = {}
        portfolio = self._portfolio_for()
        for category in self._categories:
            report = self._evaluate(category)
            decision = self._ramp_controller.decide(
                category,
                evidence=report,
                current_stage=self._stages[category],
                portfolio=portfolio,
                n_resolved_disputed=report.n_disputed,
                stress_episodes=0,
                breaker_tripped=False,
            )
            reports[category] = report
            decisions[category] = decision
            self._stages[category] = decision.stage if hasattr(decision, "stage") else SHADOW
        self._latest = HarnessSnapshot(reports, decisions)
        return self._latest

    @property
    def latest(self):
        return self._latest
