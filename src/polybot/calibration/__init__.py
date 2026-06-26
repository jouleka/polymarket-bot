"""Calibration, base-rate priors, and the Anchor Gate (S5 / POL-7).

The L3 GO/NO-GO sizing gate (does the bot's OWN forecasting beat the market well enough to
risk money) + the anti-overconfidence Anchor Gate (clamp Hermes's p so it cannot run away into
a confident-wrong narrative). Dormant in production until S6 feeds forecasts and markets resolve.
"""
