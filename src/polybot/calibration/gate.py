"""CalibrationGate facade (S5 / POL-7) -- the single seam S6 plugs into the ERS.

Composes the calibration tracker (the `k` sizing multiplier the validator consumes) and the
Anchor Gate (clamp Hermes's p, with the prior classified from the market text). Deliberately the
only calibration object the S6 wiring needs to know about. The deep wiring into ``service.py``
(recording forecasts on a real proposal, supplying the real category + corroboration from the
citation truth-gate) is S6 -- until then this is built + tested standalone.
"""

from polybot.calibration.anchor import anchor_gate
from polybot.calibration.tracker import CalibrationTracker


class CalibrationGate:
    def __init__(self, ledger, prior_engine, config):
        self._tracker = CalibrationTracker(ledger, config)
        self._prior = prior_engine
        self._config = config

    def k_for(self, category):
        """The binary GO/NO-GO sizing multiplier for a category (-> the validator's calib_score)."""
        return self._tracker.k_for(category)

    def report_for(self, category):
        return self._tracker.report_for(category)

    def clamp_p(self, p, market_mid, *, question_text, seconds_to_resolution, corroborated):
        """Clamp Hermes's posterior p via the Anchor Gate, anchoring on the market mid plus the
        base-rate prior classified from ``question_text`` (None class -> market-only). Returns the
        full ``AnchorResult`` so the caller can log the reason / whether it shrank."""
        prior = self._prior.prior_for(question_text)
        return anchor_gate(p, market_mid, prior, seconds_to_resolution=seconds_to_resolution,
                           corroborated=corroborated, config=self._config)
