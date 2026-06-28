"""Tests for the citation truth-gate (S6 / POL-8).

The truth-gate is the ERS-side re-derivation of a Hermes proposal's evidence: it
matches citations against the already-sanitized EventStore (NEVER fetches/executes
them), keeps only allowlisted PRIMARY envelopes, collapses them by publisher_group,
and answers (a) corroborated = >=2 INDEPENDENT primaries, (b) the indirect-prompt-
injection signature: one fresh source moving p while a thin book lets it push the
mid -> same_source_collusion. DISCOVERY tier and non-allowlisted citations never
count and never trigger. Every value that is money/depth is a Decimal from a string.
"""

from decimal import Decimal

import pytest

from polybot.core.clock import MonotonicStamper
from polybot.ingestion.envelope import make_envelope
from polybot.ingestion.news import DISCOVERY, PRIMARY, Source
from polybot.ingestion.orderbook import LocalBook
from polybot.storage.market_memory import EventStore
from polybot.truthgate.gate import (
    REASON_SAME_SOURCE,
    REASON_TRUTH_GATE_REFUSE,
    TruthGateConfig,
    TruthVerdict,
    verify,
)


def test_config_rejects_non_positive_fields():
    # all three fields must be strictly > 0 (fail loud, not a silent default)
    with pytest.raises(ValueError):
        TruthGateConfig(freshness_window_ns=0,
                        thin_book_depth_usd=Decimal("50"),
                        thin_book_move=Decimal("0.05"))
    with pytest.raises(ValueError):
        TruthGateConfig(freshness_window_ns=1,
                        thin_book_depth_usd=Decimal("0"),
                        thin_book_move=Decimal("0.05"))
    with pytest.raises(ValueError):
        TruthGateConfig(freshness_window_ns=1,
                        thin_book_depth_usd=Decimal("50"),
                        thin_book_move=Decimal("0"))
