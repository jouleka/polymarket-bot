"""Composite insider score 0..10 + Low/Med/High/Critical bands (S7 / POL-9).

The 0..1 sub-scores (D1-D6) combine into a 0..10 weighted sum. For a DEFENSIVE system, a single
detector in its Critical band (>= critical_subscore) ESCALATES the overall band to >=High, so one
strong signal (e.g. clear D1 toxicity) is never diluted by quiet detectors. Pure.
"""

from dataclasses import dataclass

from polybot.detectors.signals import clamp01

LOW = "LOW"
MED = "MED"
HIGH = "HIGH"
CRITICAL = "CRITICAL"
_ORDER = {LOW: 0, MED: 1, HIGH: 2, CRITICAL: 3}


@dataclass(frozen=True)
class CompositeScore:
    value: float   # 0..10
    band: str


def composite(subscores, config, weights=None):
    if not subscores:
        return CompositeScore(0.0, LOW)
    # Defense-in-depth (review H2/M1): never trust the five sub-score producers -- clamp every
    # sub-score (and the final value) to its contractual range, so one out-of-range/NaN producer
    # can't blow the 0..10 scale, force a spurious Critical, or mask a real signal.
    clamped = {k: clamp01(v) for k, v in subscores.items()}
    if weights is None:
        mean = sum(clamped.values()) / len(clamped)
    else:
        wsum = sum(weights.get(k, 0) for k in clamped)
        mean = (sum(clamped[k] * weights.get(k, 0) for k in clamped) / wsum) if wsum > 0 else 0.0
    value = clamp01(mean) * 10.0
    band = _band(value, config)
    # Single-Critical override: escalate to >=High, never demote.
    if any(s >= float(config.critical_subscore) for s in clamped.values()) and _ORDER[band] < _ORDER[HIGH]:
        band = HIGH
    return CompositeScore(value, band)


def _band(value, config):
    if value < float(config.band_low_max):
        return LOW
    if value < float(config.band_med_max):
        return MED
    if value < float(config.band_high_max):
        return HIGH
    return CRITICAL
