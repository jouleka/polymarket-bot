"""S8 / POL-10 — maker tracker + gate (binary GO/NO-GO over the honest net-of-cost sample)."""

from decimal import Decimal

import pytest

from polybot.core.clock import MonotonicStamper
from polybot.maker import quote_policy
from polybot.maker.config import DEFAULT_FEE_SCHEDULE, MakerConfig
from polybot.maker.gate import MakerGate, MakerTracker
from polybot.maker.ledger import MakerLedger
from polybot.maker.quote_policy import PULL, QUOTE


def _ledger(path):
    return MakerLedger(str(path), MonotonicStamper())


def _cfg(**kw):
    kw.setdefault("fee_schedule", DEFAULT_FEE_SCHEDULE)
    return MakerConfig(**kw)


_VALUE = {"WON": Decimal("1"), "LOST": Decimal("0"), "DISPUTED": None, "VOID": None}


def _fill(ledger, fill_id, *, category, side, shares, price, mid, reward, status=None):
    ledger.record_fill(fill_id, token_id=f"tok-{fill_id}", condition_id="c", category=category,
                       side=side, shares=Decimal(shares), price_exec=Decimal(price),
                       fill_mid=Decimal(mid), reward_accrued=Decimal(reward))
    if status is not None:
        ledger.record_settlement(fill_id, status=status, resolution_value=_VALUE[status])


def test_cold_category_reports_none_and_no_go(tmp_path):
    with _ledger(tmp_path / "m.db") as l:
        r = MakerTracker(l, _cfg()).report_for("sports")
    assert r.category == "sports"
    assert r.n_settled == 0 and r.n_disputed == 0 and r.n_void == 0
    assert r.reward is None and r.rebate is None and r.spread_capture is None
    assert r.adverse_selection is None and r.fees is None
    assert r.lockup_cost is None and r.dispute_haircut is None
    assert r.net is None and r.go is False


def test_full_report_hand_computed_on_active_sports(tmp_path):
    """Every leg of the pinned derivation, hand-computed over 3 settled sports fills.

    Fills (sports: active, fee_rate 0.03, exponent 1):
      f1 BUY  10 @ 0.40, mid 0.42, reward 0.05, WON  (mark 1)
      f2 BUY  20 @ 0.60, mid 0.61, reward 0.07, LOST (mark 0)
      f3 SELL 10 @ 0.50, mid 0.48, reward 0.03, WON  (mark 1)
    Config: rebate_fraction 0.20 (default), forced_taker_exit_p 0.10, lockup_rate 0.01,
            dispute_p 0.02.
    Arithmetic (each checked by hand, twice):
      reward  = 0.05 + 0.07 + 0.03                                   = 0.15
      cf_1    = 10*0.03*0.40*(1-0.40) = 0.12*0.60                    = 0.072
      cf_2    = 20*0.03*0.60*(1-0.60) = 0.36*0.40                    = 0.144
      cf_3    = 10*0.03*0.50*(1-0.50) = 0.15*0.50                    = 0.075
      sum cf  = 0.072 + 0.144 + 0.075                                = 0.291
      rebate  = 0.20 * 0.291                                         = 0.0582
      spread  = +10*(0.42-0.40) + 20*(0.61-0.60) - 10*(0.48-0.50)
              = 0.20 + 0.20 + 0.20                                   = 0.60
      adverse = +10*(0.40-1) + 20*(0.60-0) - 10*(0.50-1)
              = -6.00 + 12.00 + 5.00                                 = 11.00
      fees    = 0.10 * 0.291                                         = 0.0291
      notional= 10*0.40 + 20*0.60 + 10*0.50 = 4 + 12 + 5             = 21.00
      lockup  = 0.01 * 21.00                                         = 0.21
      dispute = 0.02 * 21.00                                         = 0.42
      net     = 0.15 + 0.0582 + 0.60 - 11.00 - 0.0291 - 0.21 - 0.42
              = 0.8082 - 11.6591                                     = -10.8509
    """
    with _ledger(tmp_path / "m.db") as l:
        _fill(l, "f1", category="sports", side="BUY", shares="10", price="0.40", mid="0.42",
              reward="0.05", status="WON")
        _fill(l, "f2", category="sports", side="BUY", shares="20", price="0.60", mid="0.61",
              reward="0.07", status="LOST")
        _fill(l, "f3", category="sports", side="SELL", shares="10", price="0.50", mid="0.48",
              reward="0.03", status="WON")
        cfg = _cfg(min_samples=3, forced_taker_exit_p=Decimal("0.10"),
                   lockup_rate=Decimal("0.01"), dispute_p=Decimal("0.02"))
        r = MakerTracker(l, cfg).report_for("sports")
    assert r.n_settled == 3 and r.n_disputed == 0 and r.n_void == 0
    assert r.reward == Decimal("0.15")
    assert r.rebate == Decimal("0.0582")
    assert r.spread_capture == Decimal("0.60")
    assert r.adverse_selection == Decimal("11.00")
    assert r.fees == Decimal("0.0291")
    assert r.lockup_cost == Decimal("0.21")
    assert r.dispute_haircut == Decimal("0.42")
    assert r.net == Decimal("-10.8509")
    # the identity is structural -- re-assert it over the report's own legs:
    assert r.net == (r.reward + r.rebate + r.spread_capture - r.adverse_selection
                     - r.fees - r.lockup_cost - r.dispute_haircut)
    assert r.go is False


# A passing geopolitics (FREE fee category -> cf/rebate/fees all 0) seed, hand-computed:
#   g1 BUY  10 @ 0.40, mid 0.41, reward 0.02, WON  (mark 1): spread +0.10, adverse 10*(0.40-1) = -6.0
#   g2 BUY  10 @ 0.55, mid 0.56, reward 0.02, LOST (mark 0): spread +0.10, adverse 10*(0.55-0) = +5.5
#   g3 SELL 10 @ 0.70, mid 0.68, reward 0.02, LOST (mark 0): spread -10*(0.68-0.70) = +0.20,
#                                                            adverse -10*(0.70-0)   = -7.0
#   reward 0.06 ; rebate 0 ; spread 0.40 ; adverse -7.5 ; fees/lockup/dispute 0 (defaults)
#   net = 0.06 + 0 + 0.40 - (-7.5) = 7.96
def _seed_passing(ledger):
    _fill(ledger, "g1", category="geopolitics", side="BUY", shares="10", price="0.40",
          mid="0.41", reward="0.02", status="WON")
    _fill(ledger, "g2", category="geopolitics", side="BUY", shares="10", price="0.55",
          mid="0.56", reward="0.02", status="LOST")
    _fill(ledger, "g3", category="geopolitics", side="SELL", shares="10", price="0.70",
          mid="0.68", reward="0.02", status="LOST")


def test_no_go_below_min_samples_despite_positive_net(tmp_path):
    with _ledger(tmp_path / "m.db") as l:
        _seed_passing(l)
        r = MakerTracker(l, _cfg(min_samples=4)).report_for("geopolitics")
    assert r.n_settled == 3 and r.net == Decimal("7.96")
    assert r.go is False  # 3 < 4: the sample floor gates even a healthy net


def test_go_when_sample_clears_and_net_exceeds_margin(tmp_path):
    with _ledger(tmp_path / "m.db") as l:
        _seed_passing(l)
        r = MakerTracker(l, _cfg(min_samples=3)).report_for("geopolitics")
    assert r.n_settled == 3 and r.net == Decimal("7.96")
    assert r.go is True  # n >= min_samples AND net 7.96 > net_margin_min 0


def test_net_exactly_at_margin_is_no_go(tmp_path):
    # m1 BUY  10 @ 0.40, mid 0.40, reward 0.03, WON (mark 1): spread 0, adverse 10*(0.40-1) = -6
    # m2 SELL 15 @ 0.60, mid 0.60, reward 0.03, WON (mark 1): spread 0, adverse -15*(0.60-1) = +6
    # adverse sums to 0; free category -> every cost leg 0 -> net = reward = 0.06 EXACTLY.
    with _ledger(tmp_path / "m.db") as l:
        _fill(l, "m1", category="geopolitics", side="BUY", shares="10", price="0.40",
              mid="0.40", reward="0.03", status="WON")
        _fill(l, "m2", category="geopolitics", side="SELL", shares="15", price="0.60",
              mid="0.60", reward="0.03", status="WON")
        at = MakerTracker(l, _cfg(min_samples=2,
                                  net_margin_min=Decimal("0.06"))).report_for("geopolitics")
        above = MakerTracker(l, _cfg(min_samples=2,
                                     net_margin_min=Decimal("0.05"))).report_for("geopolitics")
    assert at.net == Decimal("0.06") and at.go is False  # strict >: AT the margin is NO-GO
    assert above.net == Decimal("0.06") and above.go is True


def test_go_reads_net_only_not_reward_gross(tmp_path):
    """The "bleeds invisibly" honesty pin (design §5 invariant 1): a category with a LARGE
    positive reward whose adverse selection drags net <= margin must be NO-GO. A mutation that
    makes go read reward (or reward+rebate+spread_capture, or any gross leg) instead of .net
    MUST be killed by this test."""
    # sports, one fill: BUY 100 @ 0.60, mid 0.60, reward_accrued 5.00, LOST (mark 0).
    #   cf = 100*0.03*0.60*(1-0.60) = 1.8*0.40 = 0.72 ; rebate = 0.20*0.72 = 0.144
    #   spread = 0 (mid == exec) ; adverse = 100*(0.60-0) = 60 ; fees/lockup/dispute 0 (defaults)
    #   net = 5.00 + 0.144 + 0 - 60 = -54.856  (reward-gross 5.144 looks GREAT; the truth bleeds)
    with _ledger(tmp_path / "m.db") as l:
        _fill(l, "x1", category="sports", side="BUY", shares="100", price="0.60", mid="0.60",
              reward="5.00", status="LOST")
        r = MakerTracker(l, _cfg(min_samples=1)).report_for("sports")
    assert r.reward == Decimal("5.00")
    assert r.reward + r.rebate + r.spread_capture == Decimal("5.144")  # gross is positive...
    assert r.net == Decimal("-54.856")                                 # ...the net is not
    assert r.go is False


def test_disputed_counted_and_excluded_from_every_leg(tmp_path):
    """Whale-flip immunity, proven by the NET VALUE: were the DISPUTED row scored (its mark is
    None, so fail-closed worst-case adverse would be 20*0.90 = 18), net would flip
    6.05 -> 6.05 - 18 = -11.95 and go would flip to False. Exclusion keeps net at 6.05."""
    # honest: h1 BUY 10 @ 0.40, mid 0.40, reward 0.05, WON (mark 1)
    #   spread 0 ; adverse 10*(0.40-1) = -6 ; free category -> other legs 0
    #   net = 0.05 + 0 + 0 - (-6) = 6.05
    with _ledger(tmp_path / "m.db") as l:
        _fill(l, "h1", category="geopolitics", side="BUY", shares="10", price="0.40",
              mid="0.40", reward="0.05", status="WON")
        _fill(l, "d1", category="geopolitics", side="BUY", shares="20", price="0.90",
              mid="0.90", reward="0", status="DISPUTED")
        r = MakerTracker(l, _cfg(min_samples=1)).report_for("geopolitics")
    assert r.n_settled == 1 and r.n_disputed == 1 and r.n_void == 0
    assert r.net == Decimal("6.05") and r.go is True


def test_void_counted_and_excluded(tmp_path):
    with _ledger(tmp_path / "m.db") as l:
        _fill(l, "h1", category="geopolitics", side="BUY", shares="10", price="0.40",
              mid="0.40", reward="0.05", status="WON")
        _fill(l, "v1", category="geopolitics", side="BUY", shares="20", price="0.90",
              mid="0.90", reward="0", status="VOID")
        r = MakerTracker(l, _cfg(min_samples=1)).report_for("geopolitics")
    assert r.n_settled == 1 and r.n_disputed == 0 and r.n_void == 1
    assert r.net == Decimal("6.05")


def test_only_disputed_and_void_rows_is_cold(tmp_path):
    with _ledger(tmp_path / "m.db") as l:
        _fill(l, "d1", category="geopolitics", side="BUY", shares="20", price="0.90",
              mid="0.90", reward="0", status="DISPUTED")
        _fill(l, "v1", category="geopolitics", side="BUY", shares="20", price="0.90",
              mid="0.90", reward="0", status="VOID")
        r = MakerTracker(l, _cfg(min_samples=1)).report_for("geopolitics")
    assert r.n_settled == 0 and r.n_disputed == 1 and r.n_void == 1
    assert r.net is None and r.go is False


def test_an_unhandled_settlement_status_fails_loud(tmp_path):
    # A status neither honest nor DISPUTED/VOID (DB corruption, or a future 5th VALID_STATUSES
    # not taught to the tracker) must NOT silently vanish from the accounting. The ledger's own
    # guard blocks writing it, so corrupt the row through the raw sqlite3 connection underneath
    # (mirrors test_calibration_tracker.test_an_unhandled_resolution_status_fails_loud).
    with _ledger(tmp_path / "m.db") as l:
        _fill(l, "h1", category="geopolitics", side="BUY", shares="10", price="0.40",
              mid="0.40", reward="0.05", status="WON")
        l._conn.execute("UPDATE maker_fills SET status='WEIRD' WHERE fill_id='h1'")
        l._conn.commit()
        with pytest.raises(ValueError, match="status"):
            MakerTracker(l, _cfg(min_samples=1)).report_for("geopolitics")


def test_gate_go_for_matches_report(tmp_path):
    with _ledger(tmp_path / "m.db") as l:
        _seed_passing(l)
        g = MakerGate(l, _cfg(min_samples=3))
        assert g.go_for("geopolitics") is True
        assert g.go_for("geopolitics") == g.report_for("geopolitics").go
        assert g.report_for("geopolitics").net == Decimal("7.96")
        assert g.go_for("sports") is False  # cold category through the facade


def test_gate_decide_quote_delegates_with_injected_config(tmp_path):
    with _ledger(tmp_path / "m.db") as l:
        g = MakerGate(l, _cfg())
        kw = dict(pull_quotes=False, recent_adverse=Decimal("0"), break_even=Decimal("0.05"),
                  locked_effective=Decimal("0"), locked_cap=Decimal("100"))
        assert g.decide_quote(**kw) == QUOTE
        assert g.decide_quote(**{**kw, "pull_quotes": True}) == PULL


def test_gate_caller_cannot_pass_config(tmp_path):
    # the gate injects its OWN config; a caller supplying one is a bug -> TypeError
    # (duplicate keyword), never a silent override.
    with _ledger(tmp_path / "m.db") as l:
        g = MakerGate(l, _cfg())
        with pytest.raises(TypeError):
            g.decide_quote(pull_quotes=False, recent_adverse=Decimal("0"),
                           break_even=Decimal("0.05"), locked_effective=Decimal("0"),
                           locked_cap=Decimal("100"), config=_cfg())


def test_gate_decide_quote_injects_its_own_config_object(tmp_path, monkeypatch):
    """The injected VALUE, not just the duplicate-keyword TypeError: decide_quote must forward
    the gate's OWN config object. Pins the sole S8d mutation survivor -- passing config=None
    (or any other object) instead of self._config is invisible while quote_policy's config is
    inert, so assert identity on the captured kwarg."""
    captured = {}

    def _spy(**kw):
        captured.update(kw)
        return QUOTE

    monkeypatch.setattr(quote_policy, "decide_quote", _spy)
    with _ledger(tmp_path / "m.db") as l:
        g = MakerGate(l, _cfg())
        g.decide_quote(pull_quotes=False, recent_adverse=Decimal("0"), break_even=Decimal("1"),
                       locked_effective=Decimal("0"), locked_cap=Decimal("1"))
    assert captured["config"] is g._config  # the gate's own config, by identity
