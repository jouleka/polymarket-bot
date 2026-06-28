"""MarketMeta stub (S6 / POL-8) — the MVP seam for category / question / seconds-to-resolution.

At MVP there is NO real ``MarketRegistry`` (Gamma metadata) feed (deferred to the
calibration-warming slice, DESIGN §8). ``HermesPipeline`` injects this stub so the calibration
arm always has the three inputs ``CalibrationGate`` needs. It is deliberately neutered toward
safety:

    * ``category_for`` -> a single ``"unknown"`` bucket. ``CalibrationGate.k_for("unknown")``
      has no resolved forecasts, so ``k = 0`` -> paper-only by design. (Do NOT collapse the
      ``k_for`` / ``prior_for`` keyspaces -- DESIGN decision #6.)
    * ``question_text_for`` -> the proposal's own ``resolution_summary`` (the only natural
      language the ERS has at MVP; feeds ``PriorEngine.classify`` inside ``clamp_p``).
    * ``seconds_to_resolution_for`` -> a fixed sentinel STRICTLY past
      ``CalibrationConfig.prior_decay_window_seconds`` (default 86_400) so the prior anchor
      stays active.

  The stub does no I/O, holds no state, and never raises.
  """

# A fixed sentinel far past CalibrationConfig.prior_decay_window_seconds (default 86_400s = 24h),
# matching the seconds_to_resolution=10**9 value used in CalibrationGate.clamp_p throughout the
# tests. "Past the decay window" keeps the prior anchor active (the prior is only dropped WITHIN
# the decay window of resolution). The real per-market value is the MarketRegistry seam below.
SECONDS_TO_RESOLUTION_SENTINEL = 1_000_000_000

# The single MVP category bucket. k_for("unknown") has no resolved history -> k = 0 -> paper-only.
UNKNOWN_CATEGORY = "unknown"


class StubMarketMeta:
    """MVP ``MarketMeta``. Replace with a real ``MarketRegistry`` (condition_id ->
    category/question/seconds, from Gamma metadata) in the calibration-warming slice; the three
    method signatures are the seam HermesPipeline depends on."""

    def category_for(self, intent) -> str:
        return UNKNOWN_CATEGORY

    def question_text_for(self, intent) -> str:
        return intent.resolution_summary

    def seconds_to_resolution_for(self, intent) -> int | None:
        # MarketRegistry seam: a real impl returns (resolution_ts - now) per condition_id from
        # Gamma metadata. The stub returns a fixed sentinel past the prior-decay window.
        return SECONDS_TO_RESOLUTION_SENTINEL
