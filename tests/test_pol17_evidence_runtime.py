"""POL-17 periodic harness evidence remains advisory."""

import inspect
from types import SimpleNamespace

from polybot.runtime.harness_runtime import HarnessEvidenceRuntime


def test_harness_runtime_updates_each_category_and_only_returns_advice():
    trace = []
    reports = {
        "politics": SimpleNamespace(category="politics", n_disputed=1),
        "crypto": SimpleNamespace(category="crypto", n_disputed=0),
    }

    def evaluate(category):
        trace.append(("evidence", category))
        return reports[category]

    class Ramp:
        def decide(self, category, **kwargs):
            trace.append(("ramp", category, kwargs["evidence"]))
            return SimpleNamespace(category=category, promote_recommended=False)

    runtime = HarnessEvidenceRuntime(
        categories=("politics", "crypto"),
        evaluate=evaluate,
        ramp_controller=Ramp(),
        portfolio_for=lambda: "portfolio",
    )

    latest = runtime.update()

    assert trace == [
        ("evidence", "politics"),
        ("ramp", "politics", reports["politics"]),
        ("evidence", "crypto"),
        ("ramp", "crypto", reports["crypto"]),
    ]
    assert latest.reports == reports
    assert set(latest.decisions) == {"politics", "crypto"}
    assert "signer" not in inspect.signature(HarnessEvidenceRuntime).parameters
    assert "caps_mutator" not in inspect.signature(HarnessEvidenceRuntime).parameters
