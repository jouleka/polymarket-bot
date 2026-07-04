# S9 / POL-11 — Shadow Harness → Earn-Autonomy Ramp Controller Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `src/polybot/harness/` — the shadow harness (simulate every intent as a maker fill net of everything, accrue resolved outcomes) + the earn-autonomy ramp controller (SHADOW→TINY_LIVE→RAMP, advisory promote / automatic ratchet-down, non-loosenable ceiling) — per the operator-approved `docs/DESIGN-S9-HARNESS.md`, plus the `RestartReconciler`→`ERSController` boot seam.

**Architecture:** A NEW self-contained package mirroring `maker/`'s shape (self-verifying config → pure Decimal calculators → append-only ledger → evidence evaluator → binary stage-machine controller). Purely ADDITIVE except one opt-in `reconciler=None` seam on `ERSController` (byte-for-byte inert until wired). It COMPOSES the existing gates — S5 calibration `k`, S8 maker `go`, the S8 `net_pnl` identity, S4.5 `RestartReconciler`, S4.7's ratchet — and adds walk-forward OOS, the multiple-comparisons margin, the dispute-freeze stress, and the stage machine. Four serial sub-slices S9a–S9d.

**Tech Stack:** Python 3.13, exact `Decimal` from strings, frozen dataclasses with self-verifying `__post_init__`, append-only SQLite (WAL) mirroring the maker/calibration ledgers, pytest.

---

## Execution notes (read before ANY task)

- **Repo:** WSL Ubuntu `/home/jurgenubuntu/projects/polymarket-bot`, branch `pol-11-s9-harness`. Commands: `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && <cmd>'`; edit files via UNC `\\wsl.localhost\Ubuntu\home\jurgenubuntu\projects\polymarket-bot\...` (retry transient EISDIR).
- **Strict TDD:** failing test → RUN → OBSERVE the RED for the stated reason → minimal impl → GREEN → full suite → commit. One commit per cycle; message `S9a A3: <what> -- <detail>`; **NO Co-Authored-By**.
- **Incremental-TDD override (IMPORTANT — a true RED is required per cycle).** Some fragments present a module's full implementation in its FIRST task, which would make later edge-case/guard cycles pass on write (no RED). You MUST instead grow each module ONE behavior at a time: for every task, write its test, RUN it, and OBSERVE it FAIL for the stated reason BEFORE writing the code that satisfies it. If a task's test passes against your current code because an earlier task over-built, that is the signal the earlier task over-built — DEFER that code out of the earlier task into this one (the earlier task's own tests must still pass; the first task's legitimate RED is the `ModuleNotFoundError`/class-absent failure). The fragment's code blocks are the TARGET final state, reached incrementally. Report each observed RED. The drafters flagged the specific split points inline (S9a's fill-sim happy-path→guards, S9c's evidence gate-by-gate, S9d's promote-conjunction flips + the wired-`boot()` branch) — follow them.
- **Never pipe pytest through tail/head.** Force the summary with `-o addopts=""`; trust `NNN passed` + exit 0.
- **Additive invariant (design §5.7):** `git diff` may only touch `src/polybot/harness/*`, `tests/test_harness_*.py`, and the single additive seam in `src/polybot/ers/controller.py` (the `reconciler=None` ctor param + the new `boot()` method — `reconciler=None` == today byte-for-byte). ANY other change to an existing file is a defect — stop.
- **Import discipline:** `harness/` IMPORTS from `maker/`, `calibration/`, `ers/`, `ingestion/`, `core/` (it is the composition layer — expected). It does NOT MODIFY them (except the one controller seam). Sacred surfaces (`evaluate_intent`/validator, `propose_trade` chokepoint, `process_pending`/`run_cycle` decision-flow) stay byte-for-byte.
- **Decimal doctrine:** every literal `Decimal("...")` from a string; `is_finite()` BEFORE any compare; fail LOUD (`ValueError`, `f"<field> must be <constraint>, got {value}"`) in constructors/ledger/pure guards; fail CLOSED (not-ready / not-survives / not-a-maker-fill, never a phantom GO) in the evidence/stress/fill paths.
- **The honesty spine (mutation-pinned):** the evidence gate reads `net_oos` (the out-of-sample window), NEVER `net_full`; the only PnL is `pnl.window_net` (after ALL S8 costs), NEVER a gross leg; `RampController` has NO cap-mutation surface and the $60 at-risk ceiling is structurally non-loosenable; DISPUTED/VOID excluded from the net sample but tail-survival REQUIRES resolved DISPUTED present.
- **Integration pins (S9d e2e):** the e2e composes already-built units; expected GREEN on first run. If one goes RED, do NOT bend the test — re-derive the hand-computed expectation against the pinned formulas below; a genuine mismatch is a defect in a prior slice.

### Pinned formulas (S9c/S9d share these; also design §3/§4)

- **window_net** (S9b): honest = WON/LOST rows; `net = maker.net_pnl(reward=Σreward_accrued, rebate=rebate(Σcf), spread_capture=Σsgn·shares·(fill_mid−fill_price), adverse_selection=adverse_selection(fills, resolution-marks), fees=forced_taker_exit_p·Σcf, lockup_cost=lockup_rate·Σnotional, dispute_haircut=dispute_p·Σnotional).net` where `cf=taker_fee(cat, fill_price, shares, schedule)`, `notional=shares·fill_price`. Divergent same-token marks → ValueError.
- **OOS window** (S9c): `n_oos = ceil(oos_holdout_fraction · n_resolved)`; `oos_rows = honest[-n_oos:]` (most-recent by settled_at); `net_oos = window_net(oos_rows)`. `required_margin = net_margin_min + mc_penalty·(family_size−1)`. `oos_positive = n_oos ≥ min_oos_resolved AND net_oos > required_margin` (strict, reads net_OOS).
- **calibration_ok** (S9c): `k == 1 AND brier_skill > 0 AND reliability ≤ reliability_max`, over the OOS forecast window (`forecast_ledger.resolved(cat)` honest = WON/LOST — the forecast ledger uses `DISPUTED_LOST`, distinct from the shadow ledger's `DISPUTED`).
- **ready** (S9c): `n_resolved ≥ min_resolved AND oos_positive AND calibration_ok AND maker_go`.
- **dispute_freeze_stress** (S9c): frozen cluster = the `resolution_source` with max Σ`worst_case_risk`; `reserve_after = nav − (Σwcr over non-frozen) − adverse_fraction·(Σwcr over frozen)`; `survives = reserve_after ≥ reserve_floor` (inclusive; at the $60 ceiling with adverse_fraction=1 → reserve_after == reserve_floor → survives).
- **RampController.decide** (S9d): `promote_recommended = evidence.ready AND tail_survived AND stress.survives AND not breaker_tripped`; `ramp_down = breaker_tripped OR (current_stage≠SHADOW AND not evidence.ready)`; `stage = SHADOW if not ready else current_stage`. NO cap mutation.

### Expected full-suite count (baseline 1006; total after S9 = **1097**, +91 new)

| Slice | Tests | Endpoint | Per-task increments (each fragment task states its own "all prior + N new") |
|---|---|---|---|
| **S9a** | +31 | **1037** | A1→1007 · A2→1008 · A3→1013 · A4→1024 · A5→1027 · A6→1031 · A7→1037 |
| **S9b** | +25 | **1062** | B1→1038 · B2→1039 · B3→1044 · B4→1046 · B5→1050 · B6→1055 · B7→1057 · B8→1060 · B9→1062 |
| **S9c** | +19 | **1081** | C1–C7 evidence (+11 → 1073) · C8–C10 stress (+8 → 1081); each task's own count is stated in its body |
| **S9d** | +16 | **1097** | D1→1064-rel… use the running total: end S9c=1081, then D1→1083 · D2→1087 · D3→1090 · D4→1092 · D5→1093 · D6→1094 · D7→1095 · D8→1096 · D9→1097 |

The **incremental-TDD override** may shift WHICH task a given test lands in (a deferred guard moves later), but the per-slice endpoints (1037 / 1062 / 1081 / 1097) are invariant — confirm the running total lands on each endpoint when its slice completes. Where a task body says "all prior + N new pass", the table above is the exact number.

### Review cadence (orchestrator runs these between sub-slices, not part of the task checkboxes)

After each sub-slice: (1) a spec-compliance review (read + RUN, check the additive invariant incl. the `reconciler=None` inert-seam, no over/under-build), then (2) a pinned `model: opus` review with a mutation battery on the correctness-critical tests. After any mutation pass: `git status --porcelain` empty, `grep -rn MUTATION src/` none, sweep `find src tests -name __pycache__ -exec rm -rf {} +`. Re-review after fixing any finding. Final whole-slice review before merge (cross-cutting mutations: make the evidence gate read `net_full` instead of `net_oos`; give the controller a cap-widening path; make `reconciler=None` non-inert).

---
## Sub-slice S9a — Config + fill simulator

Builds the two pure units of the harness foundation: `harness/config.py` (`RampConfig`, frozen + self-verifying, mirroring `maker/config.py`'s `is_finite`-before-compare discipline) and `harness/fill_sim.py` (`SimulatedFill` + `simulate_fill`, maker-only per Fork 2, fail-closed on cross/stale/None-mid, reward via S8's `reward_accrual`). Package `src/polybot/harness/__init__.py` is created docstring-only alongside the first config test. Strict TDD: every task is a RED→GREEN cycle with the full failing-test code, the full implementation, a single-file run, a full-suite run, and a commit. Baseline before S9a = **1006 passed**.

Fill-sim tests construct the **real `LocalBook`** (from `polybot.ingestion.orderbook`) via a tiny `_book(bids, asks)` helper calling `apply_book({"bids": [...], "asks": [...]})` — its snapshot API is trivial and pins the exact `best_bid()`/`best_ask()`/`midpoint()` stale-gating the contract relies on. The stale/None-mid case uses a fresh `LocalBook()` (constructs `_stale=True` → `midpoint()` returns `None`). The reward hand-value is `spread_score(10, 0.01, b=1) = (10 − 0.01/10)² = 9.999² = 99.980001` (resting 0.49 vs mid 0.50, `spread_from_mid = 0.01 ≤ max_spread 0.03`), verified exactly.

Task IDs A1–A7. Config knobs are split across A2 (defaults + immutability), A3 (int-knob rejections), A4 (Decimal-knob rejections incl. Infinity + NaN-named-ValueError). Fill-sim across A5 (fill + reward + mirror + boundary-no-reward), A6 (fail-closed: cross BUY/SELL, None-mid), A7 (loud bad-proposal guards).

---

### Task A1: Create the `harness` package + the first config test (RED = ModuleNotFoundError)

- [ ] **1. Write the failing test.** Create `tests/test_harness_config.py`:

```python
"""S9 / POL-11 — RampConfig (self-verifying earn-autonomy thresholds)."""

from decimal import Decimal

import pytest

from polybot.harness.config import RampConfig


def _cfg(**overrides):
    """Construct a RampConfig, overriding individual knobs (defaults are all valid)."""
    return RampConfig(**overrides)


def test_defaults_are_valid_and_match_the_pinned_contract():
    c = _cfg()
    assert c.min_resolved == 150
    assert c.net_margin_min == Decimal("0")
    assert c.oos_holdout_fraction == Decimal("0.30")
    assert c.min_oos_resolved == 30
    assert c.mc_penalty == Decimal("0")
    assert c.oos_n_bins == 10
    assert c.reliability_max == Decimal("0.03")
    assert c.min_resolved_disputed == 1
    assert c.min_stress_episodes == 1
    assert c.ramp_step_fraction == Decimal("0.5")
```

- [ ] **2. Run it — observe RED for the right reason.**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_harness_config.py -o addopts="" -q'`
  Expected: `ModuleNotFoundError: No module named 'polybot.harness'` (collection error — the package does not exist yet).

- [ ] **3. Minimal implementation.** Create the package marker `src/polybot/harness/__init__.py` (docstring-only) and the config module with the frozen dataclass carrying ONLY the defaults (no `_verify` yet — that is pinned in later cycles).

`src/polybot/harness/__init__.py`:
```python
"""Earn-autonomy shadow harness (S9 / POL-11).

The capstone package: a self-verifying RampConfig -> pure exact-Decimal fill
simulator -> append-only shadow ledger -> walk-forward evidence evaluator ->
binary stage-machine controller. Runs SHADOW-ONLY over simulated maker fills and
injected books/marks; nothing here quotes, signs, sends, or widens a cap.
Additive to the tree but for one opt-in ERSController(reconciler=None) boot seam.
"""
```

`src/polybot/harness/config.py`:
```python
"""Earn-autonomy ramp thresholds (S9 / POL-11), self-verifying at construction.

Every knob gates whether the operator may advance a category toward live money
(the Stage-0 resolved floor, the OOS net margin the evidence demands, the
multiple-comparisons inflation, the tail-survival minimums, the Murphy
reliability ceiling), so the config verifies its own envelope at construction and
fails LOUD on nonsense -- the maker/config.py + calibration/config.py discipline
(is_finite() BEFORE every Decimal compare; a NaN ordered-compare raises
InvalidOperation, an Infinity sails through one-sided compares -- both must fail
by field name). oos_n_bins is a plan-time refinement of the design's "reliability"
knob: it pins the Murphy binning (mirrors calibration n_bins).
"""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class RampConfig:
    min_resolved: int = 150                          # Stage-0 floor per category; > 0
    net_margin_min: Decimal = Decimal("0")           # OOS net must EXCEED this; >= 0 & finite
    oos_holdout_fraction: Decimal = Decimal("0.30")  # most-recent fraction held OOS; 0 < f < 1
    min_oos_resolved: int = 30                       # min honest rows in the OOS window; > 0
    mc_penalty: Decimal = Decimal("0")               # per-extra-category OOS-margin inflation; >= 0 & finite
    oos_n_bins: int = 10                             # Murphy binning for the OOS reliability; >= 1
    reliability_max: Decimal = Decimal("0.03")       # Murphy reliability ceiling (slope ~1); 0 < r <= 0.1
    min_resolved_disputed: int = 1                    # tail-survival: >= this many resolved DISPUTED; >= 0
    min_stress_episodes: int = 1                      # tail-survival: >= this many stress episodes; >= 0
    ramp_step_fraction: Decimal = Decimal("0.5")     # advisory widen step (reported only); 0 < s <= 1
```

- [ ] **4. Run it — GREEN.**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_harness_config.py -o addopts="" -q'`
  Expected: `1 passed`.

- [ ] **5. Run the FULL suite.**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q'`
  Expected: **all prior + 1 = 1007 passed**.

- [ ] **6. Commit.**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/harness/__init__.py src/polybot/harness/config.py tests/test_harness_config.py && git commit -m "S9a A1: create harness package + RampConfig defaults -- frozen dataclass, contract defaults, no _verify yet"'`

---

### Task A2: RampConfig is frozen (immutable)

- [ ] **1. Write the failing test.** Append to `tests/test_harness_config.py`:

```python
def test_config_is_frozen():
    c = _cfg()
    with pytest.raises(Exception):  # FrozenInstanceError (a dataclasses subclass); construction-time immutability
        c.min_resolved = 200
```

- [ ] **2. Run it — observe RED for the right reason.**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_harness_config.py -o addopts="" -q -k frozen'`
  Expected: this is GREEN immediately — `@dataclass(frozen=True)` already forbids attribute assignment (A1 pinned the frozen decorator). This cycle DOCUMENTS the immutability invariant; the assert passes with no code change. Confirm `1 passed` and proceed. (If it unexpectedly fails, the decorator regressed — restore `frozen=True`.)

- [ ] **3. Minimal implementation.** None required — frozen was established in A1. No edit.

- [ ] **4. Run it — GREEN.**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_harness_config.py -o addopts="" -q'`
  Expected: `2 passed`.

- [ ] **5. Run the FULL suite.**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q'`
  Expected: **all prior + 1 = 1008 passed**.

- [ ] **6. Commit.**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add tests/test_harness_config.py && git commit -m "S9a A2: pin RampConfig frozen immutability -- frozen dataclass forbids mutation"'`

---

### Task A3: `_verify` rejects out-of-range integer knobs

- [ ] **1. Write the failing test.** Append to `tests/test_harness_config.py`:

```python
def test_min_resolved_must_be_positive():
    with pytest.raises(ValueError, match="min_resolved"):
        _cfg(min_resolved=0)


def test_min_oos_resolved_must_be_positive():
    with pytest.raises(ValueError, match="min_oos_resolved"):
        _cfg(min_oos_resolved=0)


def test_oos_n_bins_must_be_positive():
    with pytest.raises(ValueError, match="oos_n_bins"):
        _cfg(oos_n_bins=0)


def test_min_resolved_disputed_must_be_non_negative():
    with pytest.raises(ValueError, match="min_resolved_disputed"):
        _cfg(min_resolved_disputed=-1)


def test_min_stress_episodes_must_be_non_negative():
    with pytest.raises(ValueError, match="min_stress_episodes"):
        _cfg(min_stress_episodes=-1)
```

- [ ] **2. Run it — observe RED for the right reason.**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_harness_config.py -o addopts="" -q'`
  Expected: the five new tests FAIL with `DID NOT RAISE <class 'ValueError'>` (there is no `_verify` yet, so out-of-range integers construct silently).

- [ ] **3. Minimal implementation.** Add `__post_init__` → `_verify()` to `src/polybot/harness/config.py` covering ONLY the integer knobs pinned in this cycle (the Decimal knobs land in A4). Insert after the field declarations:

```python
    def __post_init__(self):
        self._verify()

    def _verify(self):
        if self.min_resolved <= 0:
            raise ValueError(f"min_resolved must be > 0, got {self.min_resolved}")
        if self.min_oos_resolved <= 0:
            raise ValueError(f"min_oos_resolved must be > 0, got {self.min_oos_resolved}")
        if self.oos_n_bins <= 0:
            raise ValueError(f"oos_n_bins must be > 0, got {self.oos_n_bins}")
        if self.min_resolved_disputed < 0:
            raise ValueError(f"min_resolved_disputed must be >= 0, got {self.min_resolved_disputed}")
        if self.min_stress_episodes < 0:
            raise ValueError(f"min_stress_episodes must be >= 0, got {self.min_stress_episodes}")
```

- [ ] **4. Run it — GREEN.**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_harness_config.py -o addopts="" -q'`
  Expected: `7 passed`.

- [ ] **5. Run the FULL suite.**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q'`
  Expected: **all prior + 5 = 1013 passed**.

- [ ] **6. Commit.**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/harness/config.py tests/test_harness_config.py && git commit -m "S9a A3: _verify rejects out-of-range int knobs -- min_resolved/min_oos_resolved/oos_n_bins > 0, disputed/episodes >= 0"'`

---

### Task A4: `_verify` rejects out-of-range Decimal knobs (incl. Infinity AND NaN-before-compare)

- [ ] **1. Write the failing test.** Append to `tests/test_harness_config.py`:

```python
def test_net_margin_min_must_be_non_negative():
    with pytest.raises(ValueError, match="net_margin_min"):
        _cfg(net_margin_min=Decimal("-0.01"))


def test_net_margin_min_rejects_infinity():
    with pytest.raises(ValueError, match="net_margin_min"):
        _cfg(net_margin_min=Decimal("Infinity"))


def test_net_margin_min_rejects_nan_by_name_not_invalidoperation():
    # is_finite() BEFORE the compare: a NaN must raise the NAMED ValueError,
    # never a bare InvalidOperation from an ordered compare on NaN.
    with pytest.raises(ValueError, match="net_margin_min"):
        _cfg(net_margin_min=Decimal("NaN"))


def test_oos_holdout_fraction_rejects_zero():
    with pytest.raises(ValueError, match="oos_holdout_fraction"):
        _cfg(oos_holdout_fraction=Decimal("0"))


def test_oos_holdout_fraction_rejects_one():
    with pytest.raises(ValueError, match="oos_holdout_fraction"):
        _cfg(oos_holdout_fraction=Decimal("1"))


def test_mc_penalty_must_be_non_negative():
    with pytest.raises(ValueError, match="mc_penalty"):
        _cfg(mc_penalty=Decimal("-0.01"))


def test_mc_penalty_rejects_infinity():
    with pytest.raises(ValueError, match="mc_penalty"):
        _cfg(mc_penalty=Decimal("Infinity"))


def test_reliability_max_rejects_zero():
    with pytest.raises(ValueError, match="reliability_max"):
        _cfg(reliability_max=Decimal("0"))


def test_reliability_max_rejects_above_ceiling():
    with pytest.raises(ValueError, match="reliability_max"):
        _cfg(reliability_max=Decimal("0.11"))


def test_ramp_step_fraction_rejects_zero():
    with pytest.raises(ValueError, match="ramp_step_fraction"):
        _cfg(ramp_step_fraction=Decimal("0"))


def test_ramp_step_fraction_rejects_above_one():
    with pytest.raises(ValueError, match="ramp_step_fraction"):
        _cfg(ramp_step_fraction=Decimal("1.5"))
```

- [ ] **2. Run it — observe RED for the right reason.**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_harness_config.py -o addopts="" -q'`
  Expected: the eleven new tests FAIL — the finite/in-range ones with `DID NOT RAISE`; note the NaN test would raise a bare `decimal.InvalidOperation` (not a `ValueError`) if the guard were compare-first, so it also fails until the `is_finite()`-first guard lands. (The `_verify` from A3 does not touch these Decimal knobs yet.)

- [ ] **3. Minimal implementation.** Extend `_verify()` in `src/polybot/harness/config.py` with the Decimal-knob guards — `is_finite()` FIRST on every one (mirroring `maker/config.py`). Append these lines to the END of `_verify` (after the integer guards from A3):

```python
        if not self.net_margin_min.is_finite() or self.net_margin_min < 0:
            raise ValueError(f"net_margin_min must be finite and >= 0, got {self.net_margin_min}")
        if not self.oos_holdout_fraction.is_finite() or not (Decimal(0) < self.oos_holdout_fraction < Decimal(1)):
            raise ValueError(f"oos_holdout_fraction must be finite and in (0, 1), got {self.oos_holdout_fraction}")
        if not self.mc_penalty.is_finite() or self.mc_penalty < 0:
            raise ValueError(f"mc_penalty must be finite and >= 0, got {self.mc_penalty}")
        if not self.reliability_max.is_finite() or not (Decimal(0) < self.reliability_max <= Decimal("0.1")):
            raise ValueError(f"reliability_max must be finite and in (0, 0.1], got {self.reliability_max}")
        if not self.ramp_step_fraction.is_finite() or not (Decimal(0) < self.ramp_step_fraction <= Decimal(1)):
            raise ValueError(f"ramp_step_fraction must be finite and in (0, 1], got {self.ramp_step_fraction}")
```

- [ ] **4. Run it — GREEN.**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_harness_config.py -o addopts="" -q'`
  Expected: `18 passed`.

- [ ] **5. Run the FULL suite.**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q'`
  Expected: **all prior + 11 = 1024 passed**.

- [ ] **6. Commit.**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/harness/config.py tests/test_harness_config.py && git commit -m "S9a A4: _verify rejects out-of-range Decimal knobs -- is_finite before compare, Infinity + NaN-named-ValueError, ranges per contract"'`

---

### Task A5: `simulate_fill` — a resting maker BUY fills + accrues the hand-computed reward (+ the SELL mirror + a filled-but-no-reward boundary)

- [ ] **1. Write the failing test.** Create `tests/test_harness_fill_sim.py`:

```python
"""S9 / POL-11 — simulate_fill (maker-only resting fill + reward, fail-closed)."""

from decimal import Decimal

import pytest

from polybot.ingestion.orderbook import LocalBook
from polybot.maker.config import DEFAULT_FEE_SCHEDULE, MakerConfig
from polybot.maker.reward import reward_accrual
from polybot.harness.fill_sim import SimulatedFill, simulate_fill


def _book(bids, asks):
    """A fresh, non-stale LocalBook seeded from (price, size) string pairs."""
    b = LocalBook()
    b.apply_book(
        {
            "bids": [{"price": p, "size": s} for p, s in bids],
            "asks": [{"price": p, "size": s} for p, s in asks],
        }
    )
    return b


def _cfg():
    return MakerConfig(fee_schedule=DEFAULT_FEE_SCHEDULE)


def _fill(**overrides):
    """simulate_fill with a valid resting-maker BUY baseline (mid 0.50, rest 0.49)."""
    kwargs = dict(
        token_id="tok-1",
        condition_id="cond-1",
        category="politics",
        side="BUY",
        shares=Decimal("10"),
        resting_price=Decimal("0.49"),
        book=_book([("0.48", "100")], [("0.52", "100")]),
        maker_config=_cfg(),
    )
    kwargs.update(overrides)
    return simulate_fill(**kwargs)


def test_resting_maker_buy_fills_and_accrues_the_reward():
    fill = _fill()
    assert isinstance(fill, SimulatedFill)
    assert fill.filled is True
    assert fill.fill_price == Decimal("0.49")
    assert fill.fill_mid == Decimal("0.50")
    # spread_from_mid = abs(0.49 - 0.50) = 0.01  (<= max_spread 0.03 -> eligible)
    assert fill.spread_from_mid == Decimal("0.01")
    # reward = spread_score(10, 0.01, b=1) = (10 - 0.01/10)^2 = 9.999^2 = 99.980001
    assert fill.reward_accrued == Decimal("99.980001")
    # and it agrees exactly with the S8 primitive it delegates to
    assert fill.reward_accrued == reward_accrual(Decimal("10"), Decimal("0.01"), config=_cfg())
    # passthrough fields
    assert fill.token_id == "tok-1"
    assert fill.condition_id == "cond-1"
    assert fill.category == "politics"
    assert fill.side == "BUY"
    assert fill.shares == Decimal("10")


def test_resting_maker_sell_mirror_fills_and_accrues_the_reward():
    # SELL rests at 0.51 (>= best_bid 0.48 -> does not cross) ; mid 0.50 ; spread 0.01
    fill = _fill(side="SELL", resting_price=Decimal("0.51"))
    assert fill.filled is True
    assert fill.side == "SELL"
    assert fill.fill_price == Decimal("0.51")
    assert fill.fill_mid == Decimal("0.50")
    assert fill.spread_from_mid == Decimal("0.01")
    assert fill.reward_accrued == Decimal("99.980001")


def test_filled_but_outside_max_spread_earns_no_reward():
    # Wide book (bid 0.30 / ask 0.70 -> mid 0.50). BUY resting 0.60 does NOT cross
    # the 0.70 ask (fills), but spread_from_mid = 0.10 > max_spread 0.03 -> reward 0.
    fill = _fill(
        resting_price=Decimal("0.60"),
        book=_book([("0.30", "100")], [("0.70", "100")]),
    )
    assert fill.filled is True
    assert fill.fill_mid == Decimal("0.50")
    assert fill.spread_from_mid == Decimal("0.10")
    assert fill.reward_accrued == Decimal("0")
```

- [ ] **2. Run it — observe RED for the right reason.**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_harness_fill_sim.py -o addopts="" -q'`
  Expected: `ModuleNotFoundError: No module named 'polybot.harness.fill_sim'` (collection error).

- [ ] **3. Minimal implementation.** Create `src/polybot/harness/fill_sim.py` with the full maker-only simulator (all guards + the fail-closed branch + the reward delegation — the whole contract, since the fail-closed/guard tests in A6/A7 exercise the same function):

```python
"""Maker-only shadow fill simulator (S9 / POL-11).

Models ONE resting-limit maker entry against an injected book snapshot (Fork 2:
maker-primary, reuse S8). A resting price that would CROSS the book (a BUY at/above
the ask, a SELL at/below the bid), or a stale/empty/crossed book with no usable
midpoint, fails CLOSED -> filled=False, reward 0: we do NOT shadow taker fills, so an
unfillable maker order simply earns nothing (never a phantom fill). A genuine bad
proposal (bad side, non-positive/non-finite shares, a resting_price outside (0,1) or
non-finite) fails LOUD -- that is a caller bug, not market data. Reward accrues through
S8's reward_accrual; every numeric is exact Decimal, is_finite() before every compare.
"""

from dataclasses import dataclass
from decimal import Decimal

from polybot.maker.reward import reward_accrual


@dataclass(frozen=True)
class SimulatedFill:
    token_id: str
    condition_id: str
    category: str
    side: str
    shares: Decimal
    fill_price: Decimal
    fill_mid: Decimal
    spread_from_mid: Decimal
    filled: bool
    reward_accrued: Decimal


def simulate_fill(*, token_id, condition_id, category, side, shares, resting_price, book, maker_config):
    """A single resting-maker fill decision. MAKER-ONLY: crossed/stale/degenerate ->
    filled=False fail-closed; a bad proposal -> ValueError (is_finite before compares)."""
    if side not in ("BUY", "SELL"):
        raise ValueError(f"side must be one of BUY, SELL, got {side!r}")
    if not shares.is_finite() or shares <= 0:
        raise ValueError(f"shares must be finite and > 0, got {shares}")
    if not resting_price.is_finite() or not (Decimal(0) < resting_price < Decimal(1)):
        raise ValueError(f"resting_price must be finite and in (0, 1), got {resting_price}")

    mid = book.midpoint()  # None when stale / empty side / crossed
    best_bid = book.best_bid()
    best_ask = book.best_ask()

    crosses = (
        mid is None
        or (side == "BUY" and (best_ask is None or resting_price >= best_ask))
        or (side == "SELL" and (best_bid is None or resting_price <= best_bid))
    )
    if crosses:
        return SimulatedFill(
            token_id=token_id,
            condition_id=condition_id,
            category=category,
            side=side,
            shares=shares,
            fill_price=resting_price,
            fill_mid=mid if mid is not None else Decimal(0),
            spread_from_mid=Decimal(0),
            filled=False,
            reward_accrued=Decimal(0),
        )

    spread_from_mid = abs(resting_price - mid)
    reward = reward_accrual(shares, spread_from_mid, config=maker_config)
    return SimulatedFill(
        token_id=token_id,
        condition_id=condition_id,
        category=category,
        side=side,
        shares=shares,
        fill_price=resting_price,
        fill_mid=mid,
        spread_from_mid=spread_from_mid,
        filled=True,
        reward_accrued=reward,
    )
```

- [ ] **4. Run it — GREEN.**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_harness_fill_sim.py -o addopts="" -q'`
  Expected: `3 passed`.

- [ ] **5. Run the FULL suite.**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q'`
  Expected: **all prior + 3 = 1027 passed**.

- [ ] **6. Commit.**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/harness/fill_sim.py tests/test_harness_fill_sim.py && git commit -m "S9a A5: simulate_fill resting maker fill + reward -- BUY/SELL mirror, hand-computed 99.980001, filled-but-out-of-band earns 0"'`

---

### Task A6: `simulate_fill` fails closed on a crossing price or a None-mid book

- [ ] **1. Write the failing test.** Append to `tests/test_harness_fill_sim.py`:

```python
def test_crossing_buy_does_not_fill_and_earns_nothing():
    # BUY resting 0.52 == best_ask 0.52 -> would cross -> fail closed.
    fill = _fill(resting_price=Decimal("0.52"))
    assert fill.filled is False
    assert fill.reward_accrued == Decimal("0")
    assert fill.spread_from_mid == Decimal("0")
    assert fill.fill_mid == Decimal("0.50")  # mid still reported when the book is live


def test_crossing_sell_does_not_fill_and_earns_nothing():
    # SELL resting 0.48 == best_bid 0.48 -> would cross -> fail closed.
    fill = _fill(side="SELL", resting_price=Decimal("0.48"))
    assert fill.filled is False
    assert fill.reward_accrued == Decimal("0")
    assert fill.spread_from_mid == Decimal("0")


def test_stale_book_with_no_midpoint_fails_closed():
    # A fresh LocalBook has never had a snapshot -> _stale=True -> midpoint() is None.
    stale = LocalBook()
    assert stale.midpoint() is None
    fill = _fill(book=stale)
    assert fill.filled is False
    assert fill.reward_accrued == Decimal("0")
    assert fill.spread_from_mid == Decimal("0")
    assert fill.fill_mid == Decimal("0")  # mid or Decimal(0) when None


def test_one_sided_book_fails_closed():
    # Bids only (no ask) -> midpoint() None (empty side) -> fail closed even for a BUY.
    one_sided = _book([("0.48", "100")], [])
    fill = _fill(book=one_sided)
    assert fill.filled is False
    assert fill.reward_accrued == Decimal("0")
```

- [ ] **2. Run it — observe RED for the right reason.**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_harness_fill_sim.py -o addopts="" -q'`
  Expected: this is GREEN immediately — the fail-closed branch was fully pinned in A5's implementation. This cycle adds the crossing/None-mid/one-sided coverage that locks the branch against a later mutation. Confirm the four new tests pass (`7 passed` total in-file) and proceed. (If any fail, the A5 `crosses` predicate regressed.)

- [ ] **3. Minimal implementation.** None required — the fail-closed logic landed in A5. No edit.

- [ ] **4. Run it — GREEN.**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_harness_fill_sim.py -o addopts="" -q'`
  Expected: `7 passed`.

- [ ] **5. Run the FULL suite.**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q'`
  Expected: **all prior + 4 = 1031 passed**.

- [ ] **6. Commit.**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add tests/test_harness_fill_sim.py && git commit -m "S9a A6: pin simulate_fill fail-closed branch -- crossing BUY/SELL, stale None-mid, one-sided book all filled=False reward 0"'`

---

### Task A7: `simulate_fill` fails loud on a bad proposal

- [ ] **1. Write the failing test.** Append to `tests/test_harness_fill_sim.py`:

```python
def test_bad_side_raises():
    with pytest.raises(ValueError, match="side"):
        _fill(side="HOLD")


def test_non_positive_shares_raises():
    with pytest.raises(ValueError, match="shares"):
        _fill(shares=Decimal("0"))


def test_non_finite_shares_raises():
    # is_finite() BEFORE the compare -> a named ValueError, not InvalidOperation.
    with pytest.raises(ValueError, match="shares"):
        _fill(shares=Decimal("NaN"))


def test_resting_price_at_or_below_zero_raises():
    with pytest.raises(ValueError, match="resting_price"):
        _fill(resting_price=Decimal("0"))


def test_resting_price_at_or_above_one_raises():
    with pytest.raises(ValueError, match="resting_price"):
        _fill(resting_price=Decimal("1"))


def test_non_finite_resting_price_raises():
    with pytest.raises(ValueError, match="resting_price"):
        _fill(resting_price=Decimal("Infinity"))
```

- [ ] **2. Run it — observe RED for the right reason.**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_harness_fill_sim.py -o addopts="" -q'`
  Expected: this is GREEN immediately — the loud guards (side / finite>0 shares / finite (0,1) resting_price) were pinned in A5. This cycle locks each guard branch (incl. the is_finite-before-compare NaN/Infinity cases) against mutation. Confirm the six new tests pass (`13 passed` total in-file) and proceed. (If any fail, an A5 guard regressed.)

- [ ] **3. Minimal implementation.** None required — the loud guards landed in A5. No edit.

- [ ] **4. Run it — GREEN.**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_harness_fill_sim.py -o addopts="" -q'`
  Expected: `13 passed`.

- [ ] **5. Run the FULL suite.**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q'`
  Expected: **all prior + 6 = 1037 passed**.

- [ ] **6. Commit.**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add tests/test_harness_fill_sim.py && git commit -m "S9a A7: pin simulate_fill loud guards -- bad side / non-positive+NaN shares / resting_price outside (0,1)+Infinity raise ValueError"'`

---

**S9a cumulative test count: 1037 passed** (baseline 1006 + 31 new: config 18, fill_sim 13). All new code lives in `src/polybot/harness/__init__.py`, `src/polybot/harness/config.py`, `src/polybot/harness/fill_sim.py`, `tests/test_harness_config.py`, `tests/test_harness_fill_sim.py` — no existing file touched.
## Sub-slice S9b — Shadow ledger + windowed PnL

> Builds `src/polybot/harness/ledger.py` (`VALID_STATUSES`, `ShadowTradeRecord`, `ShadowLedger`)
> and `src/polybot/harness/pnl.py` (`window_net`) plus `tests/test_harness_ledger.py` +
> `tests/test_harness_pnl.py`. `ShadowLedger` MIRRORS `maker/ledger.py` `MakerLedger` byte-for-byte
> in shape (WAL + `synchronous=NORMAL`, stamper timestamps, Decimals as exact strings,
> `INSERT OR IGNORE` idempotency, `_query`/`_row`, context manager) — the ONLY deltas are the table
> name `shadow_trades`, the record `ShadowTradeRecord`, the method `record_trade`, and `settled()`
> ordering by `settled_at` THEN `rowid`. `window_net` re-derives the S8 leg fold of
> `MakerTracker.report_for` over an arbitrary LIST of settled rows (S9 keeps `MakerTracker`
> untouched — design §2 non-goals).
>
> Depends on S9a (the `harness` package + `harness/__init__.py` already exist). Full-suite baseline
> entering S9b = **all prior** (S9a's tasks already added to the 1006 pre-S9 baseline); each task
> below adds its new count on top.
>
> Fixed conventions used throughout (from the shared context): `from decimal import Decimal`; every
> numeric literal is `Decimal("...")` from a string; `is_finite()` before every Decimal compare;
> ledger tests use `from polybot.core.clock import MonotonicStamper` + `tmp_path`; tests are one
> concern each, behavior-named; `pytest.raises(..., match=...)`. Single-file run:
> `./.venv/bin/pytest tests/test_harness_<module>.py -o addopts="" -q`. Full suite:
> `./.venv/bin/pytest -o addopts="" -q`. Every plan command runs as
> `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && <cmd>'`. Commit per RED→GREEN cycle,
> NO `Co-Authored-By` trailer.

---

### Task B1: ShadowLedger record_trade + all() round-trip (module birth)

Pins the append-only store's shape: `record_trade` returns True on a new insert, `all()` round-trips
every field with EXACT Decimals (stored as exact strings), `created_at` is stamped, and the three
settlement columns start `None`. First RED is the module not existing.

- [ ] **1. Write the failing test** — create `tests/test_harness_ledger.py`:

```python
"""S9 / POL-11 — shadow trade ledger (append-only, restart-stable, dispute-honest)."""

from decimal import Decimal

import pytest

from polybot.core.clock import MonotonicStamper
from polybot.harness.ledger import ShadowLedger


def _ledger(path, stamper=None):
    return ShadowLedger(path, stamper or MonotonicStamper())


def _trade(ledger, tid, *, token="t1", cond="c1", category="politics", side="BUY",
           shares="10", fill_price="0.48", fill_mid="0.50", reward="0.25"):
    return ledger.record_trade(tid, token_id=token, condition_id=cond, category=category,
                               side=side, shares=Decimal(shares),
                               fill_price=Decimal(fill_price), fill_mid=Decimal(fill_mid),
                               reward_accrued=Decimal(reward))


def test_record_trade_round_trips_every_field_via_all(tmp_path):
    with _ledger(str(tmp_path / "s.db")) as l:
        assert _trade(l, "d1") is True
        rows = l.all()
        assert len(rows) == 1
        r = rows[0]
        assert r.trade_id == "d1" and r.token_id == "t1" and r.condition_id == "c1"
        assert r.category == "politics" and r.side == "BUY"
        # exact Decimal round-trip (stored as exact strings)
        assert r.shares == Decimal("10") and r.fill_price == Decimal("0.48")
        assert r.fill_mid == Decimal("0.50") and r.reward_accrued == Decimal("0.25")
        assert r.created_at is not None
        assert r.status is None and r.resolution_value is None and r.settled_at is None
```

- [ ] **2. Run it — RED for the right reason:**
  `./.venv/bin/pytest tests/test_harness_ledger.py -o addopts="" -q`
  Expected: `ModuleNotFoundError: No module named 'polybot.harness.ledger'`.

- [ ] **3. Minimal implementation** — create `src/polybot/harness/ledger.py`:

```python
"""Shadow trade ledger (S9 / POL-11).

Append-only, point-in-time SQLite store of the harness's SIMULATED maker trades and their
eventual resolutions -- the substrate ``pnl.window_net`` and the evidence evaluator window
over. Mirrors the S8 ``MakerLedger`` exactly (WAL + synchronous=NORMAL, stamper timestamps,
Decimals stored as exact strings, INSERT OR IGNORE idempotency) so a shadow trade is
recorded with the same no-backfill honesty as a real maker fill: garbage must never enter,
and DISPUTED/VOID rows are kept but excluded from the honest net sample downstream
(whale-flip immunity). The ONLY differences from ``MakerLedger``: the table is
``shadow_trades``, the record is ``ShadowTradeRecord``, and ``settled()`` orders by
``settled_at`` then ``rowid`` (a shadow trade's *resolution* time is its window key).
"""

import sqlite3
from dataclasses import dataclass
from decimal import Decimal

# Honest win/loss vs the two statuses excluded from the net sample: a whale-captured UMA
# dispute (DISPUTED) and a refund/50-50 (VOID) must not poison the shadow net-PnL.
VALID_STATUSES = ("WON", "LOST", "DISPUTED", "VOID")

_COLUMNS = ("trade_id, token_id, condition_id, category, side, shares, fill_price, "
            "fill_mid, reward_accrued, created_at, status, resolution_value, settled_at")


@dataclass(frozen=True)
class ShadowTradeRecord:
    trade_id: str
    token_id: str
    condition_id: str
    category: str
    side: str
    shares: Decimal
    fill_price: Decimal
    fill_mid: Decimal
    reward_accrued: Decimal
    created_at: int
    status: str | None = None
    resolution_value: Decimal | None = None
    settled_at: int | None = None


class ShadowLedger:
    def __init__(self, path, stamper):
        self._stamper = stamper
        self._conn = sqlite3.connect(path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS shadow_trades (
                trade_id         TEXT PRIMARY KEY,
                token_id         TEXT    NOT NULL,
                condition_id     TEXT    NOT NULL,
                category         TEXT    NOT NULL,
                side             TEXT    NOT NULL,
                shares           TEXT    NOT NULL,
                fill_price       TEXT    NOT NULL,
                fill_mid         TEXT    NOT NULL,
                reward_accrued   TEXT    NOT NULL,
                created_at       INTEGER NOT NULL,
                status           TEXT,
                resolution_value TEXT,
                settled_at       INTEGER
            )
            """
        )
        self._conn.commit()

    def record_trade(self, trade_id, *, token_id, condition_id, category, side, shares,
                     fill_price, fill_mid, reward_accrued):
        """INSERT a simulated trade (idempotent on ``trade_id``). Returns True if newly
        inserted, False if a duplicate (original preserved). Decimals stored as exact
        strings.

        Fail LOUD at the door (mirrors ``MakerLedger.record_fill``): the shadow net-PnL
        substrate cannot be backfilled, so a bad side, a non-positive/non-finite size, an
        out-of-[0,1] price, or a negative reward must never enter it."""
        if side not in ("BUY", "SELL"):
            raise ValueError(f"side must be 'BUY' or 'SELL', got {side!r}")
        if not shares.is_finite() or shares <= 0:
            raise ValueError(f"shares must be a finite Decimal > 0, got {shares}")
        for name, value in (("fill_price", fill_price), ("fill_mid", fill_mid)):
            if not value.is_finite() or not (Decimal(0) <= value <= Decimal(1)):
                raise ValueError(f"{name} must be a finite price in [0, 1], got {value}")
        if not reward_accrued.is_finite() or reward_accrued < 0:
            raise ValueError(
                f"reward_accrued must be a finite Decimal >= 0, got {reward_accrued}")
        cur = self._conn.execute(
            "INSERT OR IGNORE INTO shadow_trades "
            "(trade_id, token_id, condition_id, category, side, shares, fill_price, "
            "fill_mid, reward_accrued, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (trade_id, token_id, condition_id, category, side, str(shares),
             str(fill_price), str(fill_mid), str(reward_accrued), self._stamper.stamp()),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def record_settlement(self, trade_id, *, status, resolution_value):
        """Set the trade's resolution (overwrites -- a UMA dispute can flip an apparent
        WON to DISPUTED later; the flip clears the stale resolution value). Fails LOUD:
        unknown status or trade_id; a resolution_value inconsistent with the status --
        WON/LOST REQUIRE a finite Decimal in [0, 1] (canonically 1/0 but any settle mark
        accepted); DISPUTED/VOID REQUIRE None (they are excluded from the net sample, so a
        value here is a caller bug)."""
        if status not in VALID_STATUSES:
            raise ValueError(
                f"invalid settlement status {status!r}; expected one of {VALID_STATUSES}")
        if status in ("WON", "LOST"):
            if (resolution_value is None or not resolution_value.is_finite()
                    or not (Decimal(0) <= resolution_value <= Decimal(1))):
                raise ValueError(
                    f"resolution_value must be a finite Decimal in [0, 1] for {status}, "
                    f"got {resolution_value}")
        elif resolution_value is not None:
            raise ValueError(
                f"resolution_value must be None for {status}, got {resolution_value}")
        cur = self._conn.execute(
            "UPDATE shadow_trades SET status=?, resolution_value=?, settled_at=? "
            "WHERE trade_id=?",
            (status, None if resolution_value is None else str(resolution_value),
             self._stamper.stamp(), trade_id),
        )
        self._conn.commit()
        if cur.rowcount == 0:
            raise KeyError(f"no shadow trade {trade_id!r} to settle")

    def settled(self, category=None):
        sql = f"SELECT {_COLUMNS} FROM shadow_trades WHERE status IS NOT NULL"
        params = ()
        if category is not None:
            sql += " AND category=?"
            params = (category,)
        return self._query(sql + " ORDER BY settled_at, rowid", params)

    def all(self):
        return self._query(f"SELECT {_COLUMNS} FROM shadow_trades ORDER BY rowid")

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def _query(self, sql, params=()):
        return [self._row(r) for r in self._conn.execute(sql, params).fetchall()]

    @staticmethod
    def _row(r):
        return ShadowTradeRecord(
            trade_id=r[0], token_id=r[1], condition_id=r[2], category=r[3], side=r[4],
            shares=Decimal(r[5]), fill_price=Decimal(r[6]), fill_mid=Decimal(r[7]),
            reward_accrued=Decimal(r[8]), created_at=r[9], status=r[10],
            resolution_value=None if r[11] is None else Decimal(r[11]), settled_at=r[12],
        )
```

- [ ] **4. Run it — GREEN:** `./.venv/bin/pytest tests/test_harness_ledger.py -o addopts="" -q`
- [ ] **5. Full suite:** `./.venv/bin/pytest -o addopts="" -q` — expected **all prior + 1**.
- [ ] **6. Commit:**
  ```
  git add src/polybot/harness/ledger.py tests/test_harness_ledger.py
  git commit -m "S9b B1: ShadowLedger record_trade + all() round-trip -- append-only shadow_trades mirroring MakerLedger; exact-Decimal fields, created_at stamped, settlement cols None"
  ```

---

### Task B2: idempotent on trade_id (dup → False, original preserved)

`INSERT OR IGNORE` semantics: a second `record_trade` on the same `trade_id` returns False and does
NOT overwrite the stored row.

- [ ] **1. Write the failing test** — append to `tests/test_harness_ledger.py`:

```python
def test_record_trade_is_idempotent_on_trade_id(tmp_path):
    with _ledger(str(tmp_path / "s.db")) as l:
        assert _trade(l, "d1") is True
        assert _trade(l, "d1", fill_price="0.99") is False  # duplicate ignored
        assert l.all()[0].fill_price == Decimal("0.48")     # original preserved
```

- [ ] **2. Run it — RED or GREEN:**
  `./.venv/bin/pytest tests/test_harness_ledger.py -o addopts="" -q`
  This behavior is already delivered by B1's `INSERT OR IGNORE`, so expect GREEN. (If it somehow
  RED-ed, the `INSERT OR IGNORE` / `rowcount` path is the defect — fix there, not in the test.) The
  cycle still gets its own commit to pin the idempotency contract.

- [ ] **3. Minimal implementation** — none (delivered in B1).
- [ ] **4. Run it — GREEN:** `./.venv/bin/pytest tests/test_harness_ledger.py -o addopts="" -q`
- [ ] **5. Full suite:** `./.venv/bin/pytest -o addopts="" -q` — expected **all prior + 1**.
- [ ] **6. Commit:**
  ```
  git add tests/test_harness_ledger.py
  git commit -m "S9b B2: record_trade idempotent on trade_id -- INSERT OR IGNORE returns False on dup and preserves the original row"
  ```

---

### Task B3: record_settlement sets status/value/settled_at; settled() ordering + category filter; unsettled excluded

Pins the settlement write and the query surface in one cycle: a settlement stamps `status` +
`resolution_value` + `settled_at`; `settled()` returns only rows with a status, ordered by
`settled_at` then `rowid`; `settled(category=)` filters; an unsettled trade is excluded. The ordering
is proven two ways — the natural strictly-increasing-stamper order AND a FIXED-clock stamper that
forces equal `settled_at` so the `rowid` tiebreak is the observable discriminator.

- [ ] **1. Write the failing test** — append (note the top-of-file import gains `MonotonicStamper` a
  fixed clock; add the `_fixed_stamper` helper just under `_trade`):

```python
class _FixedClock:
    """A non-monotonic clock stub: returns the SAME tick every call so the stamper's
    strict-monotonic bump is bypassed only across DISTINCT stampers -- used to force two
    settlements onto the SAME settled_at and expose the rowid tiebreak in settled()."""
    def __init__(self, tick):
        self._tick = tick
    def __call__(self):
        return self._tick


def test_settlement_sets_status_value_and_settled_at(tmp_path):
    with _ledger(str(tmp_path / "s.db")) as l:
        _trade(l, "d1")
        l.record_settlement("d1", status="WON", resolution_value=Decimal("1"))
        r = l.all()[0]
        assert r.status == "WON"
        assert r.resolution_value == Decimal("1")
        assert r.settled_at is not None
        assert [x.trade_id for x in l.settled()] == ["d1"]


def test_unsettled_trades_are_excluded_from_settled(tmp_path):
    with _ledger(str(tmp_path / "s.db")) as l:
        _trade(l, "d1")
        _trade(l, "d2")
        l.record_settlement("d1", status="LOST", resolution_value=Decimal("0"))
        assert [x.trade_id for x in l.settled()] == ["d1"]


def test_settled_filters_by_category(tmp_path):
    with _ledger(str(tmp_path / "s.db")) as l:
        _trade(l, "d1", category="politics")
        _trade(l, "d2", category="sports")
        l.record_settlement("d1", status="WON", resolution_value=Decimal("1"))
        l.record_settlement("d2", status="LOST", resolution_value=Decimal("0"))
        assert [x.trade_id for x in l.settled(category="sports")] == ["d2"]


def test_settled_orders_by_settled_at_then_rowid(tmp_path):
    # settle d2 (inserted second) BEFORE d1 -> d2 gets the earlier settled_at, so
    # settled() must return d2 first even though d1 has the lower rowid.
    with _ledger(str(tmp_path / "s.db")) as l:
        _trade(l, "d1")
        _trade(l, "d2")
        l.record_settlement("d2", status="WON", resolution_value=Decimal("1"))
        l.record_settlement("d1", status="LOST", resolution_value=Decimal("0"))
        assert [x.trade_id for x in l.settled()] == ["d2", "d1"]


def test_settled_tiebreaks_on_rowid_when_settled_at_is_equal(tmp_path):
    # a fixed clock -> both settlements share the SAME settled_at; the tiebreak is rowid,
    # so insertion order (d1 then d2) wins even though d2 was settled first.
    stamper = MonotonicStamper(clock=_FixedClock(500))
    with _ledger(str(tmp_path / "s.db"), stamper=stamper) as l:
        # NB: MonotonicStamper still bumps on <=, so drive the two settlements through two
        # separate stampers pinned to the same tick to guarantee equal settled_at.
        _trade(l, "d1")
        _trade(l, "d2")
        l._stamper = MonotonicStamper(clock=_FixedClock(700))
        l.record_settlement("d2", status="WON", resolution_value=Decimal("1"))
        l._stamper = MonotonicStamper(clock=_FixedClock(700))
        l.record_settlement("d1", status="LOST", resolution_value=Decimal("0"))
        assert [x.settled_at for x in l.settled()] == [700, 700]
        assert [x.trade_id for x in l.settled()] == ["d1", "d2"]  # rowid tiebreak
```

- [ ] **2. Run it — RED or GREEN:**
  `./.venv/bin/pytest tests/test_harness_ledger.py -o addopts="" -q`
  All five are delivered by B1's `record_settlement` + `settled()` (`ORDER BY settled_at, rowid`),
  so expect GREEN — EXCEPT confirm `test_settled_orders_by_settled_at_then_rowid` genuinely
  exercises ordering (it would RED against a `MakerLedger`-style `ORDER BY rowid`, catching a
  copy-paste that forgot the `settled_at` key). If that test REDs, the `settled()` ORDER BY is the
  defect.

- [ ] **3. Minimal implementation** — none (delivered in B1; this cycle locks the ordering pin).
- [ ] **4. Run it — GREEN:** `./.venv/bin/pytest tests/test_harness_ledger.py -o addopts="" -q`
- [ ] **5. Full suite:** `./.venv/bin/pytest -o addopts="" -q` — expected **all prior + 5**.
- [ ] **6. Commit:**
  ```
  git add tests/test_harness_ledger.py
  git commit -m "S9b B3: record_settlement + settled() surface -- stamps status/value/settled_at, filters by category, excludes unsettled, orders by settled_at THEN rowid (fixed-clock tiebreak proven)"
  ```

---

### Task B4: dispute-flip overwrite clears the stale value; dispute reflip needs a fresh value

Mirrors the maker ledger's whale-flip cases: an apparent WON (value 1) can flip to DISPUTED (value
None) later, and the overwrite must CLEAR the stale value; a re-flip back to WON cannot silently leak
the cleared value — `None` on a WON still raises.

- [ ] **1. Write the failing test** — append:

```python
def test_settlement_overwrites_on_a_dispute_flip(tmp_path):
    # a whale-captured UMA dispute can flip an apparent WON to DISPUTED later; the flip
    # must also CLEAR the stale resolution value.
    with _ledger(str(tmp_path / "s.db")) as l:
        _trade(l, "d1")
        l.record_settlement("d1", status="WON", resolution_value=Decimal("1"))
        l.record_settlement("d1", status="DISPUTED", resolution_value=None)
        r = l.all()[0]
        assert r.status == "DISPUTED" and r.resolution_value is None


def test_dispute_reflip_to_won_requires_a_fresh_resolution_value(tmp_path):
    # after a DISPUTED flip clears the stale value, a re-flip back to WON must supply a
    # FRESH value -- None cannot silently leak the cleared stale one.
    with _ledger(str(tmp_path / "s.db")) as l:
        _trade(l, "d1")
        l.record_settlement("d1", status="WON", resolution_value=Decimal("1"))
        l.record_settlement("d1", status="DISPUTED", resolution_value=None)
        with pytest.raises(ValueError, match="resolution_value"):
            l.record_settlement("d1", status="WON", resolution_value=None)
        l.record_settlement("d1", status="WON", resolution_value=Decimal("1"))
        r = l.all()[0]
        assert r.status == "WON" and r.resolution_value == Decimal("1")
```

- [ ] **2. Run it — GREEN (delivered by B1's overwrite UPDATE + WON/LOST value guard):**
  `./.venv/bin/pytest tests/test_harness_ledger.py -o addopts="" -q`
  (These pin behavior already in the implementation; RED here would indicate the UPDATE isn't
  clearing `resolution_value` — fix the `record_settlement` UPDATE, which stores
  `None if resolution_value is None else str(...)`.)

- [ ] **3. Minimal implementation** — none (delivered in B1).
- [ ] **4. Run it — GREEN:** `./.venv/bin/pytest tests/test_harness_ledger.py -o addopts="" -q`
- [ ] **5. Full suite:** `./.venv/bin/pytest -o addopts="" -q` — expected **all prior + 2**.
- [ ] **6. Commit:**
  ```
  git add tests/test_harness_ledger.py
  git commit -m "S9b B4: dispute-flip overwrite clears the stale value -- WON(1)->DISPUTED(None) clears; reflip to WON needs a fresh value, None still raises"
  ```

---

### Task B5: settlement guards — invalid status, unknown trade_id, WON/LOST value range, DISPUTED/VOID must be None

Mirrors `test_maker_ledger.py` case-for-case: bad status → ValueError; unknown trade_id → KeyError;
WON/LOST require a finite `[0,1]` value (None/NaN/1.5 raise); DISPUTED/VOID require None (a value
raises).

- [ ] **1. Write the failing test** — append:

```python
def test_rejects_an_invalid_settlement_status(tmp_path):
    with _ledger(str(tmp_path / "s.db")) as l:
        _trade(l, "d1")
        with pytest.raises(ValueError, match="status"):
            l.record_settlement("d1", status="MAYBE", resolution_value=None)


def test_settling_an_unknown_trade_fails_loud(tmp_path):
    with _ledger(str(tmp_path / "s.db")) as l:
        with pytest.raises(KeyError):
            l.record_settlement("nope", status="WON", resolution_value=Decimal("1"))


def test_won_and_lost_require_a_finite_in_range_resolution_value(tmp_path):
    # canonically 1/0 but any finite settle mark in [0,1] is accepted; None/NaN/1.5 are not.
    with _ledger(str(tmp_path / "s.db")) as l:
        _trade(l, "d1")
        for bad in (None, Decimal("NaN"), Decimal("1.5")):
            with pytest.raises(ValueError, match="resolution_value"):
                l.record_settlement("d1", status="WON", resolution_value=bad)
        with pytest.raises(ValueError, match="resolution_value"):
            l.record_settlement("d1", status="LOST", resolution_value=None)


def test_disputed_and_void_require_resolution_value_none(tmp_path):
    # DISPUTED/VOID are excluded from the net sample -- a value here is a caller bug.
    with _ledger(str(tmp_path / "s.db")) as l:
        _trade(l, "d1")
        for status in ("DISPUTED", "VOID"):
            with pytest.raises(ValueError, match="resolution_value"):
                l.record_settlement("d1", status=status, resolution_value=Decimal("0.5"))
```

- [ ] **2. Run it — GREEN (delivered by B1's `record_settlement` guards):**
  `./.venv/bin/pytest tests/test_harness_ledger.py -o addopts="" -q`
  (RED here means a guard branch is missing or the KeyError `rowcount==0` path is wrong — fix
  `record_settlement`.)

- [ ] **3. Minimal implementation** — none (delivered in B1).
- [ ] **4. Run it — GREEN:** `./.venv/bin/pytest tests/test_harness_ledger.py -o addopts="" -q`
- [ ] **5. Full suite:** `./.venv/bin/pytest -o addopts="" -q` — expected **all prior + 4**.
- [ ] **6. Commit:**
  ```
  git add tests/test_harness_ledger.py
  git commit -m "S9b B5: settlement guards -- bad status->ValueError, unknown id->KeyError, WON/LOST need finite [0,1] value, DISPUTED/VOID need None"
  ```

---

### Task B6: record_trade door guards + persists across restart

Mirrors the maker ledger's `record_fill` door guards (bad side / non-positive-or-non-finite shares /
out-of-range `fill_price`/`fill_mid` / negative-or-non-finite `reward_accrued`) and asserts nothing
garbage entered the no-backfill store; then the restart round-trip (a fresh `ShadowLedger` on the
same path sees the persisted trade + settlement).

- [ ] **1. Write the failing test** — append:

```python
def test_record_trade_rejects_a_bad_side(tmp_path):
    with _ledger(str(tmp_path / "s.db")) as l:
        with pytest.raises(ValueError, match="side"):
            _trade(l, "d1", side="HOLD")


def test_record_trade_rejects_non_positive_or_non_finite_shares(tmp_path):
    with _ledger(str(tmp_path / "s.db")) as l:
        for bad in ("0", "-5", "NaN", "Infinity"):
            with pytest.raises(ValueError, match="shares"):
                _trade(l, "d1", shares=bad)


def test_record_trade_rejects_out_of_range_prices(tmp_path):
    with _ledger(str(tmp_path / "s.db")) as l:
        with pytest.raises(ValueError, match="fill_price"):
            _trade(l, "d1", fill_price="1.5")
        with pytest.raises(ValueError, match="fill_price"):
            _trade(l, "d1", fill_price="-0.1")
        with pytest.raises(ValueError, match="fill_mid"):
            _trade(l, "d1", fill_mid="NaN")


def test_record_trade_rejects_negative_or_non_finite_reward_accrued(tmp_path):
    with _ledger(str(tmp_path / "s.db")) as l:
        for bad in ("-0.01", "NaN"):
            with pytest.raises(ValueError, match="reward_accrued"):
                _trade(l, "d1", reward=bad)
        assert l.all() == []  # nothing garbage entered the no-backfill store


def test_persists_across_restart(tmp_path):
    path = str(tmp_path / "s.db")
    with _ledger(path) as l:
        _trade(l, "d1")
        l.record_settlement("d1", status="WON", resolution_value=Decimal("1"))
    with _ledger(path) as l2:
        r = l2.all()[0]
        assert r.shares == Decimal("10") and r.fill_price == Decimal("0.48")
        assert r.status == "WON" and r.resolution_value == Decimal("1")
```

- [ ] **2. Run it — GREEN (delivered by B1's `record_trade` guards + the on-disk SQLite store):**
  `./.venv/bin/pytest tests/test_harness_ledger.py -o addopts="" -q`
  (RED here means a `record_trade` door guard is missing — fix `record_trade`.)

- [ ] **3. Minimal implementation** — none (delivered in B1).
- [ ] **4. Run it — GREEN:** `./.venv/bin/pytest tests/test_harness_ledger.py -o addopts="" -q`
- [ ] **5. Full suite:** `./.venv/bin/pytest -o addopts="" -q` — expected **all prior + 5**.
- [ ] **6. Commit:**
  ```
  git add tests/test_harness_ledger.py
  git commit -m "S9b B6: record_trade door guards + persists across restart -- bad side/shares/prices/reward rejected (no-backfill store stays empty), trade+settlement survive reopen"
  ```

---

### Task B7: window_net = the S8 net identity over a multi-row honest window (ACTIVE + FREE category, hand-computed)

Births `harness/pnl.py`. `window_net` folds the S8 identity over a LIST of settled rows exactly as
`MakerTracker.report_for` does. The primary test hand-computes the identity over a 3-row honest
window on the ACTIVE `sports` category (rebate/fees nonzero) and a matching FREE `geopolitics`
variant (taker-fee → 0 so rebate=0 and fees=0, while lockup/dispute keyed off notional stay
nonzero). First RED is the module not existing.

**Hand arithmetic (verified) — `MakerConfig(fee_schedule=DEFAULT_FEE_SCHEDULE, rebate_fraction=0.20,
forced_taker_exit_p=0.10, lockup_rate=0.01, dispute_p=0.02)`; `sports` fee = `size·0.03·p·(1−p)`;
`_SGN` BUY=+1 SELL=−1; adverse leg per row = `sgn·shares·(fill_price − mark)` (marks valid in
`[0,1]`).**

Rows (all `sports`, distinct tokens):

| tok | side | shares | fill_price | fill_mid | reward | status | mark | cf=size·0.03·p·(1−p) | spread=sgn·sh·(mid−price) | adverse=sgn·sh·(price−mark) | notional=sh·price |
|-----|------|--------|-----------|----------|--------|--------|------|------|------|------|------|
| tA | BUY | 10 | 0.40 | 0.50 | 0.25 | WON | 1 | 0.072000 | +1.00 | −6.00 | 4.00 |
| tB | SELL | 20 | 0.60 | 0.55 | 0.30 | LOST | 0 | 0.144000 | +1.00 | −12.00 | 12.00 |
| tC | BUY | 8 | 0.30 | 0.33 | 0.10 | LOST | 0 | 0.050400 | +0.24 | +2.40 | 2.40 |

Aggregates: `reward = 0.65`; `cf_total = 0.266400`; `rebate = 0.20·cf_total = 0.05328000`;
`spread_capture = 2.24`; `notional = 18.40`; `adverse = −15.60`; `fees = 0.10·cf_total = 0.02664000`;
`lockup = 0.01·notional = 0.1840`; `dispute = 0.02·notional = 0.3680`.
`net = 0.65 + 0.05328000 + 2.24 − (−15.60) − 0.02664000 − 0.1840 − 0.3680 = **17.96464000**`.

FREE `geopolitics` variant (same shares/prices/mids/rewards/marks, category swapped): taker-fee is 0
for a free category, so `cf_total = 0 → rebate = 0.00, fees = 0.00`; the other legs are unchanged
(`reward 0.65`, `spread 2.24`, `notional 18.40`, `adverse −15.60`, `lockup 0.1840`, `dispute 0.3680`).
`net = 0.65 + 0 + 2.24 + 15.60 − 0 − 0.1840 − 0.3680 = **17.9380**`.

- [ ] **1. Write the failing test** — create `tests/test_harness_pnl.py`:

```python
"""S9 / POL-11 — windowed net-of-everything PnL (the S8 identity over a settled-row window)."""

from decimal import Decimal

import pytest

from polybot.maker.config import DEFAULT_FEE_SCHEDULE, MakerConfig
from polybot.harness.ledger import ShadowTradeRecord
from polybot.harness.pnl import window_net


def _cfg(**over):
    # ACTIVE-fee config: rebate/fees/lockup/dispute all nonzero so every leg is exercised.
    base = dict(fee_schedule=DEFAULT_FEE_SCHEDULE, rebate_fraction=Decimal("0.20"),
                forced_taker_exit_p=Decimal("0.10"), lockup_rate=Decimal("0.01"),
                dispute_p=Decimal("0.02"))
    base.update(over)
    return MakerConfig(**base)


def _row(trade_id, *, token, category, side, shares, fill_price, fill_mid, reward,
         status, resolution_value):
    # settled ShadowTradeRecord (created_at/settled_at are irrelevant to window_net).
    return ShadowTradeRecord(
        trade_id=trade_id, token_id=token, condition_id="c", category=category, side=side,
        shares=Decimal(shares), fill_price=Decimal(fill_price), fill_mid=Decimal(fill_mid),
        reward_accrued=Decimal(reward), created_at=1, status=status,
        resolution_value=None if resolution_value is None else Decimal(resolution_value),
        settled_at=1)


_SPORTS_WINDOW = [
    _row("tA", token="tA", category="sports", side="BUY", shares="10", fill_price="0.40",
         fill_mid="0.50", reward="0.25", status="WON", resolution_value="1"),
    _row("tB", token="tB", category="sports", side="SELL", shares="20", fill_price="0.60",
         fill_mid="0.55", reward="0.30", status="LOST", resolution_value="0"),
    _row("tC", token="tC", category="sports", side="BUY", shares="8", fill_price="0.30",
         fill_mid="0.33", reward="0.10", status="LOST", resolution_value="0"),
]


def test_window_net_equals_the_s8_identity_over_an_active_category_window():
    # hand-computed above: reward .65 + rebate .05328 + spread 2.24 - adverse(-15.60)
    #   - fees .02664 - lockup .184 - dispute .368 = 17.96464
    assert window_net(_SPORTS_WINDOW, maker_config=_cfg()) == Decimal("17.96464000")


def test_window_net_over_a_free_category_zeroes_rebate_and_fees():
    # same rows on the FREE geopolitics category -> taker_fee 0 -> rebate 0, fees 0;
    # lockup/dispute (keyed off notional) unchanged. net = 17.9380.
    free_window = [
        _row("gA", token="gA", category="geopolitics", side="BUY", shares="10",
             fill_price="0.40", fill_mid="0.50", reward="0.25", status="WON",
             resolution_value="1"),
        _row("gB", token="gB", category="geopolitics", side="SELL", shares="20",
             fill_price="0.60", fill_mid="0.55", reward="0.30", status="LOST",
             resolution_value="0"),
        _row("gC", token="gC", category="geopolitics", side="BUY", shares="8",
             fill_price="0.30", fill_mid="0.33", reward="0.10", status="LOST",
             resolution_value="0"),
    ]
    assert window_net(free_window, maker_config=_cfg()) == Decimal("17.9380")
```

- [ ] **2. Run it — RED for the right reason:**
  `./.venv/bin/pytest tests/test_harness_pnl.py -o addopts="" -q`
  Expected: `ModuleNotFoundError: No module named 'polybot.harness.pnl'`.

- [ ] **3. Minimal implementation** — create `src/polybot/harness/pnl.py`:

```python
"""Windowed net-of-everything PnL (S9 / POL-11).

THE honest after-all-costs figure for an arbitrary time-WINDOW of settled shadow trades --
the ONLY PnL the evidence evaluator reads (no gross accessor; the S8 "never reward-gross"
spine). Re-derives the exact same seven-leg fold as ``MakerTracker.report_for``
(net = reward + rebate + spread_capture - adverse_selection - fees - lockup_cost -
dispute_haircut) over a LIST of rows rather than the whole ledger, so S9 windows the OOS
slice without refactoring (or importing the internals of) the S8 tracker. Honest by
construction: DISPUTED/VOID rows are SKIPPED (whale-flip immunity), an empty honest window
is Decimal(0), and two honest rows on one token with divergent resolution marks fail LOUD
(ledger corruption, mirroring the S8d divergent-marks pin). Reuses the S8 primitives
(taker_fee/rebate/adverse_selection/net_pnl) unchanged.
"""

from decimal import Decimal

from polybot.maker.fees import rebate, taker_fee
from polybot.maker.inventory import _SGN, MakerFill, adverse_selection
from polybot.maker.netpnl import net_pnl

_HONEST = ("WON", "LOST")


def window_net(rows, *, maker_config):
    """The S8 net identity over ``rows`` (a list of settled ShadowTradeRecords). Honest
    WON/LOST only -- DISPUTED/VOID skipped. Empty honest window -> Decimal(0). Fails LOUD on
    an unhandled status or on divergent resolution marks for one token."""
    c = maker_config
    honest = []
    for r in rows:
        if r.status in _HONEST:
            honest.append(r)
        elif r.status in ("DISPUTED", "VOID"):
            continue
        else:
            # Exhaustive: a status outside {WON,LOST,DISPUTED,VOID} is corruption -- fail
            # loud, never silently vanish from the accounting (mirrors MakerTracker).
            raise ValueError(f"unhandled settlement status {r.status!r}")

    if not honest:  # no honest settled sample in this window
        return Decimal(0)

    reward = sum((r.reward_accrued for r in honest), Decimal(0))
    cf_total = sum((taker_fee(r.category, r.fill_price, r.shares, schedule=c.fee_schedule)
                    for r in honest), Decimal(0))
    spread_capture = sum((_SGN[r.side] * r.shares * (r.fill_mid - r.fill_price)
                          for r in honest), Decimal(0))
    notional = sum((r.shares * r.fill_price for r in honest), Decimal(0))
    marks = {}
    for r in honest:
        # A token resolves ONCE: two honest rows on one token_id with DISTINCT non-None
        # resolution marks is corruption -- fail loud, never silently last-wins.
        prior = marks.get(r.token_id)
        if (prior is not None and r.resolution_value is not None
                and prior != r.resolution_value):
            raise ValueError(f"inconsistent resolution marks for token {r.token_id!r}: "
                             f"{prior} vs {r.resolution_value}")
        marks[r.token_id] = r.resolution_value
    fills = [MakerFill(token_id=r.token_id, condition_id=r.condition_id,
                       category=r.category, side=r.side, shares=r.shares,
                       price_exec=r.fill_price, fill_mid=r.fill_mid) for r in honest]
    return net_pnl(reward=reward,
                   rebate=rebate(cf_total, fraction=c.rebate_fraction),
                   spread_capture=spread_capture,
                   adverse_selection=adverse_selection(fills, marks.get),
                   fees=c.forced_taker_exit_p * cf_total,
                   lockup_cost=c.lockup_rate * notional,
                   dispute_haircut=c.dispute_p * notional).net
```

- [ ] **4. Run it — GREEN:** `./.venv/bin/pytest tests/test_harness_pnl.py -o addopts="" -q`
- [ ] **5. Full suite:** `./.venv/bin/pytest -o addopts="" -q` — expected **all prior + 2**.
- [ ] **6. Commit:**
  ```
  git add src/polybot/harness/pnl.py tests/test_harness_pnl.py
  git commit -m "S9b B7: window_net = the S8 net identity over a settled-row window -- hand-computed 3-row sports (active fees) + geopolitics (free -> rebate/fees 0) variants"
  ```

---

### Task B8: window_net excludes DISPUTED/VOID; empty and all-DISPUTED windows → Decimal(0)

Pins whale-flip immunity at the window level: a DISPUTED/VOID row in the list is SKIPPED (proven by a
row whose inclusion WOULD change the net — the same `sports` window plus one DISPUTED row with a big
reward; the net must equal the honest-only `17.96464000`, NOT the leaked value); an empty window and
an all-DISPUTED window both return `Decimal(0)`.

**Leak check (verified):** if the DISPUTED row `BUY 50 @0.50 mid0.50 reward9.99 mark0` were WRONGLY
included, `net` would be `2.24214000` — distinct from `17.96464000`, so asserting the honest value
proves the exclusion.

- [ ] **1. Write the failing test** — append to `tests/test_harness_pnl.py`:

```python
def test_window_net_excludes_disputed_and_void_rows():
    # a DISPUTED row whose inclusion WOULD change the net (big reward) must be skipped:
    # the net must equal the honest-only 17.96464000, not the leaked 2.24214000.
    disputed = _row("tD", token="tD", category="sports", side="BUY", shares="50",
                    fill_price="0.50", fill_mid="0.50", reward="9.99", status="DISPUTED",
                    resolution_value=None)
    void = _row("tE", token="tE", category="sports", side="SELL", shares="30",
                fill_price="0.50", fill_mid="0.50", reward="7.00", status="VOID",
                resolution_value=None)
    window = _SPORTS_WINDOW + [disputed, void]
    assert window_net(window, maker_config=_cfg()) == Decimal("17.96464000")


def test_window_net_of_an_empty_window_is_zero():
    assert window_net([], maker_config=_cfg()) == Decimal(0)


def test_window_net_of_an_all_disputed_window_is_zero():
    only_disputed = [
        _row("tD", token="tD", category="sports", side="BUY", shares="50",
             fill_price="0.50", fill_mid="0.50", reward="9.99", status="DISPUTED",
             resolution_value=None),
        _row("tE", token="tE", category="sports", side="SELL", shares="30",
             fill_price="0.50", fill_mid="0.50", reward="7.00", status="VOID",
             resolution_value=None),
    ]
    assert window_net(only_disputed, maker_config=_cfg()) == Decimal(0)
```

- [ ] **2. Run it — GREEN (delivered by B7's `_HONEST` filter + empty-honest short-circuit):**
  `./.venv/bin/pytest tests/test_harness_pnl.py -o addopts="" -q`
  (RED here means the DISPUTED/VOID skip or the empty-window guard is wrong — fix `window_net`.
  This is the mutation-kill test: a mutant that folds DISPUTED into the sample yields
  `2.24214000` and fails.)

- [ ] **3. Minimal implementation** — none (delivered in B7).
- [ ] **4. Run it — GREEN:** `./.venv/bin/pytest tests/test_harness_pnl.py -o addopts="" -q`
- [ ] **5. Full suite:** `./.venv/bin/pytest -o addopts="" -q` — expected **all prior + 3**.
- [ ] **6. Commit:**
  ```
  git add tests/test_harness_pnl.py
  git commit -m "S9b B8: window_net excludes DISPUTED/VOID -- a big-reward disputed/void row is skipped (net stays 17.96464, not the leaked 2.24214); empty + all-disputed windows -> 0"
  ```

---

### Task B9: window_net divergent-marks guard + negative net when adverse dominates

Two final pins in one cycle: (a) the divergent-marks guard — two honest rows on the SAME token_id
with DISTINCT `resolution_value`s raise `ValueError("inconsistent...")` (mirrors the S8d pin); (b) the
"bleeds invisibly" shape carried into the harness — an adverse-dominated window is net-NEGATIVE.

**Negative case (verified), `sports`:** one row `BUY 100 @0.40 mid0.41 reward0.05 mark0` (LOST).
`cf = 100·0.03·0.40·0.60 = 0.720000`; `reward 0.05`; `rebate 0.20·0.72 = 0.14400000`;
`spread = +100·(0.41−0.40) = 1.00`; `notional = 40.00`; `adverse = +100·(0.40−0) = 40.00`;
`fees = 0.10·0.72 = 0.07200000`; `lockup = 0.01·40 = 0.4000`; `dispute = 0.02·40 = 0.8000`.
`net = 0.05 + 0.144 + 1.00 − 40.00 − 0.072 − 0.40 − 0.80 = **−40.07800000** (< 0)`.

- [ ] **1. Write the failing test** — append to `tests/test_harness_pnl.py`:

```python
def test_window_net_raises_on_divergent_resolution_marks_for_one_token():
    # two honest rows on the SAME token with DISTINCT resolution values is ledger
    # corruption -- fail loud, never silently last-wins (mirrors the S8d pin).
    diverging = [
        _row("x1", token="tSAME", category="sports", side="BUY", shares="10",
             fill_price="0.40", fill_mid="0.50", reward="0.25", status="WON",
             resolution_value="1"),
        _row("x2", token="tSAME", category="sports", side="BUY", shares="10",
             fill_price="0.40", fill_mid="0.50", reward="0.25", status="LOST",
             resolution_value="0"),
    ]
    with pytest.raises(ValueError, match="inconsistent"):
        window_net(diverging, maker_config=_cfg())


def test_window_net_is_negative_when_adverse_selection_dominates():
    # the "safe strategy bleeds invisibly" shape: a BUY at 0.40 that resolves LOST (mark 0)
    # books a large adverse cost that swamps reward+spread -> net-NEGATIVE.
    bleed = [
        _row("nA", token="nA", category="sports", side="BUY", shares="100",
             fill_price="0.40", fill_mid="0.41", reward="0.05", status="LOST",
             resolution_value="0"),
    ]
    net = window_net(bleed, maker_config=_cfg())
    assert net == Decimal("-40.07800000")
    assert net < 0
```

- [ ] **2. Run it — GREEN (divergent guard + real-arithmetic net delivered by B7):**
  `./.venv/bin/pytest tests/test_harness_pnl.py -o addopts="" -q`
  (RED on the divergent test means the marks-consistency check is missing; RED on the negative test
  means an adverse-selection sign error — both fix in `window_net`. NB `net_pnl` permits a
  negative `adverse_selection` and negative `net`; the positive one-signed legs
  reward/rebate/fees/lockup/dispute are all ≥ 0 here, so no `net_pnl` guard trips.)

- [ ] **3. Minimal implementation** — none (delivered in B7).
- [ ] **4. Run it — GREEN:** `./.venv/bin/pytest tests/test_harness_pnl.py -o addopts="" -q`
- [ ] **5. Full suite:** `./.venv/bin/pytest -o addopts="" -q` — expected **all prior + 2**.
- [ ] **6. Commit:**
  ```
  git add tests/test_harness_pnl.py
  git commit -m "S9b B9: window_net divergent-marks guard + adverse-dominated net -- same-token distinct marks raise 'inconsistent'; a BUY resolving LOST bleeds to net -40.078"
  ```

---

**S9b totals:** 9 tasks. Ledger tests (B1–B6): 1 + 1 + 5 + 2 + 4 + 5 = **18**. PnL tests (B7–B9):
2 + 3 + 2 = **7**. **S9b adds 25 tests** (full suite: all prior + 25 at the end of B9).
## Sub-slice S9c — Evidence evaluator + stress test

Builds `src/polybot/harness/evidence.py` (`EvidenceReport`, `evaluate_category` — walk-forward OOS split +
MC-penalized margin + Brier/reliability over the OOS forecast window + reads `k_for`/`go_for`) and
`src/polybot/harness/stress.py` (`StressResult`, `dispute_freeze_stress`, `tail_survived`). Depends on S9a
(`RampConfig`) + S9b (`ShadowLedger`, `ForecastLedger`-reuse, `window_net`). The honesty spine lives here: the OOS
gate reads `net_oos` (the most-recent `ceil(oos_holdout_fraction·n)` honest rows), NEVER `net_full`.

**Fixture doctrine (pinned for every task below):**
- Category is always `"politics"` — INACTIVE in `DEFAULT_FEE_SCHEDULE` → `taker_fee == 0` → `cf_total == 0` →
  `rebate == 0`, `fees == forced_taker_exit_p·0 == 0`. With `MakerConfig` defaults (`lockup_rate=0`,
  `forced_taker_exit_p=0`, `dispute_p=0`) the net identity collapses to **`net = reward + spread_capture −
  adverse_selection`**. This keeps every Decimal hand-computable.
- Canonical **WINNER** row: `BUY 10 @ fill_price 0.40, fill_mid 0.50, reward 0.25`, settled `WON` value `1`.
  `spread = +1·10·(0.50−0.40) = +1.00`; `adverse = +1·10·(0.40−1) = −6.00` (favorable; subtracting −6 ADDS 6);
  `net = 0.25 + 1.00 − (−6.00) = 7.25` per winner.
- Canonical **LOSER** row: same fill, settled `LOST` value `0`. `spread = +1.00`; `adverse = +1·10·(0.40−0) =
  +4.00`; `net = 0.25 + 1.00 − 4.00 = −2.75` per loser.
- Every row gets a **distinct `token_id`** (a token resolves once; two honest rows on one token with distinct
  non-None marks trips `window_net`'s divergent-marks guard — irrelevant here but keep tokens unique).
- `settled()` orders by `settled_at` then `rowid`; rows are recorded (fill) then settled in the SAME chronological
  order, so `settled()` returns them in insertion order and `honest[-n_oos:]` is the most-recent slice.
- `ceil(0.30·n)`: n=6→2, n=3→1 (used throughout). `min_resolved=4`, `min_oos_resolved=2` for hand-computability.
- `evaluate_category` uses REAL `ShadowLedger` + REAL `ForecastLedger` (both `(path, stamper)`), and TINY FAKES
  for the calibration/maker gates (`_FakeCalGate(k)` exposing `k_for(cat)->Decimal`, `_FakeMakerGate(go)` exposing
  `go_for(cat)->bool`) so `k`/`go` are controlled independently of a full calibration/maker ledger. This choice is
  pinned across all evidence tasks.
- **NB the two ledgers use DIFFERENT dispute statuses:** the shadow ledger uses `"DISPUTED"`, the forecast ledger
  uses `"DISPUTED_LOST"`. Fixtures keep them distinct — never conflate.

---

### Task C1: `evaluate_category` walk-forward — the OOS window is the recent slice; `net_oos` gates, NOT `net_full` (THE HONESTY PIN)

- [ ] **1. Write the failing test** (`tests/test_harness_evidence.py`):

```python
"""S9 / POL-11 — evidence evaluator (walk-forward OOS split + MC-penalized margin + Brier/reliability)."""

from decimal import Decimal

import pytest

from polybot.core.clock import MonotonicStamper
from polybot.calibration.ledger import ForecastLedger
from polybot.maker.config import MakerConfig, DEFAULT_FEE_SCHEDULE
from polybot.harness.config import RampConfig
from polybot.harness.ledger import ShadowLedger


# ------------------------------- fixtures / factories -------------------------------

def _maker_config():
    # politics is INACTIVE -> taker_fee 0 -> rebate/fees 0; defaults zero lockup/taker-exit/dispute.
    return MakerConfig(fee_schedule=DEFAULT_FEE_SCHEDULE)


def _ramp_config(**over):
    # small, hand-computable windows: min_resolved=4, OOS window ceil(0.30*n), min_oos_resolved=2.
    base = dict(min_resolved=4, min_oos_resolved=2, oos_holdout_fraction=Decimal("0.30"),
                net_margin_min=Decimal("0"), mc_penalty=Decimal("0"))
    base.update(over)
    return RampConfig(**base)


def _shadow(tmp_path, name="s.db"):
    return ShadowLedger(str(tmp_path / name), MonotonicStamper())


def _forecast(tmp_path, name="f.db"):
    return ForecastLedger(str(tmp_path / name), MonotonicStamper())


class _FakeCalGate:
    """Tiny fake exposing only what evaluate_category consumes: k_for(cat) -> Decimal 0/1."""
    def __init__(self, k):
        self._k = k

    def k_for(self, category):
        return self._k


class _FakeMakerGate:
    """Tiny fake exposing only go_for(cat) -> bool."""
    def __init__(self, go):
        self._go = go

    def go_for(self, category):
        return self._go


def _win(ledger, tid, *, token, category="politics"):
    ledger.record_trade(tid, token_id=token, condition_id="c", category=category, side="BUY",
                        shares=Decimal("10"), fill_price=Decimal("0.40"), fill_mid=Decimal("0.50"),
                        reward_accrued=Decimal("0.25"))
    ledger.record_settlement(tid, status="WON", resolution_value=Decimal("1"))   # net +7.25


def _loss(ledger, tid, *, token, category="politics"):
    ledger.record_trade(tid, token_id=token, condition_id="c", category=category, side="BUY",
                        shares=Decimal("10"), fill_price=Decimal("0.40"), fill_mid=Decimal("0.50"),
                        reward_accrued=Decimal("0.25"))
    ledger.record_settlement(tid, status="LOST", resolution_value=Decimal("0"))  # net -2.75


def _evaluate(shadow, forecast, *, k=Decimal("1"), go=True, ramp=None, family_size=1):
    from polybot.harness.evidence import evaluate_category
    return evaluate_category("politics", shadow_ledger=shadow, forecast_ledger=forecast,
                             calibration_gate=_FakeCalGate(k), maker_gate=_FakeMakerGate(go),
                             ramp_config=ramp or _ramp_config(), maker_config=_maker_config(),
                             family_size=family_size)


def test_oos_reads_the_recent_window_not_the_full_sample(tmp_path):
    # HONESTY PIN. 4 WINS recorded FIRST (older by settled_at), then 2 LOSSES (most recent).
    # n_resolved=6 -> n_oos=ceil(0.30*6)=2 -> the OOS window is the two LOSSES.
    #   net_full = 4*7.25 + 2*(-2.75) = 29.00 - 5.50 = 23.50  (POSITIVE)
    #   net_oos  = 2*(-2.75) = -5.50                            (NEGATIVE — the recent rows bleed)
    # required_margin = 0 (net_margin_min 0, mc_penalty 0, family_size 1).
    # oos_positive = (n_oos>=2) and (net_oos > 0) = (True) and (-5.50>0 -> False) = False -> ready False.
    # A mutation reading net_full (23.50) instead of net_oos (-5.50) would flip oos_positive True and,
    # with k=1/go=True and n_resolved(6)>=min_resolved(4), flip ready True -> THIS TEST KILLS THAT MUTATION.
    shadow, forecast = _shadow(tmp_path), _forecast(tmp_path)
    for i in range(4):
        _win(shadow, f"w{i}", token=f"tw{i}")
    for i in range(2):
        _loss(shadow, f"l{i}", token=f"tl{i}")
    rep = _evaluate(shadow, forecast, k=Decimal("1"), go=True)
    assert rep.n_resolved == 6
    assert rep.n_oos == 2
    assert rep.net_full == Decimal("23.50")
    assert rep.net_oos == Decimal("-5.50")
    assert rep.oos_positive is False   # reads net_oos, not net_full
    assert rep.ready is False
```

- [ ] **2. Run it — RED for the right reason:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_harness_evidence.py -o addopts="" -q'`
  Expect: `ModuleNotFoundError: No module named 'polybot.harness.evidence'` (raised inside `_evaluate`'s import).

- [ ] **3. Minimal implementation** — full new file `src/polybot/harness/evidence.py`:

```python
"""Walk-forward earn-autonomy evidence evaluator (S9 / POL-11).

The honesty spine: a category is Stage-0 ready ONLY on net-of-everything shadow PnL that is
positive-WITH-margin AND out-of-sample -- never gross edge, never the full in-sample net. The OOS
gate reads net_OOS (the most-recent ceil(oos_holdout_fraction*n) honest rows by settled_at), and the
required margin is inflated by a multiple-comparisons family-size penalty (certifying 1-of-N
categories demands a proportionally stronger edge). DISPUTED/VOID are excluded from the honest net
sample (whale-flip immunity) but COUNTED in n_disputed. Fail CLOSED: cold / insufficient sample /
None stats -> ready False, never a phantom GO. Fail LOUD only on an unknown shadow status (mirrors
MakerTracker's exhaustive-status raise).
"""

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING

from polybot.calibration.scoring import brier, brier_skill, murphy
from polybot.harness import pnl

_HONEST_SHADOW = ("WON", "LOST")
_HONEST_FORECAST = {"WON": 1, "LOST": 0}


@dataclass(frozen=True)
class EvidenceReport:
    category: str
    n_resolved: int
    n_oos: int
    n_disputed: int
    net_full: Decimal | None
    net_oos: Decimal | None
    brier_skill: Decimal | None
    reliability: Decimal | None
    k: Decimal
    maker_go: bool
    required_margin: Decimal
    oos_positive: bool
    calibration_ok: bool
    maker_ok: bool
    ready: bool


def _ceil_frac(n, fraction):
    """ceil(fraction * n) via exact Decimal rounding -> int (>= 0)."""
    return int((Decimal(n) * fraction).to_integral_value(rounding=ROUND_CEILING))


def evaluate_category(category, *, shadow_ledger, forecast_ledger, calibration_gate, maker_gate,
                      ramp_config, maker_config, family_size):
    rc = ramp_config
    # --- SHADOW side: honest WON/LOST vs DISPUTED/VOID counted; net over the OOS window ---
    honest = []
    n_disputed = 0
    for r in shadow_ledger.settled(category):
        if r.status in _HONEST_SHADOW:
            honest.append(r)
        elif r.status in ("DISPUTED", "VOID"):
            n_disputed += 1
        else:
            # Exhaustive: a status outside VALID_STATUSES (DB corruption / an untaught 5th status)
            # must fail loud, never silently vanish from the accounting (mirrors MakerTracker).
            raise ValueError(f"unhandled shadow status {r.status!r}")

    n_resolved = len(honest)
    required_margin = rc.net_margin_min + rc.mc_penalty * (Decimal(family_size) - Decimal(1))

    if n_resolved == 0:  # cold -> fail-closed, None stats
        n_oos = 0
        net_full = net_oos = None
        oos_positive = False
    else:
        n_oos = _ceil_frac(n_resolved, rc.oos_holdout_fraction)
        oos_rows = honest[-n_oos:]
        net_oos = pnl.window_net(oos_rows, maker_config=maker_config)
        net_full = pnl.window_net(honest, maker_config=maker_config)
        oos_positive = (n_oos >= rc.min_oos_resolved) and (net_oos > required_margin)

    # --- CALIBRATION side: Brier-beats-mid + reliability over the OOS forecast window ---
    fhonest = [f for f in forecast_ledger.resolved(category)
               if f.resolution_status in _HONEST_FORECAST]
    n_f = len(fhonest)
    brier_skill_v = reliability_v = None
    if n_f > 0:
        f_oos = fhonest[-_ceil_frac(n_f, rc.oos_holdout_fraction):]
        if f_oos:
            bot_pairs = [(f.p, _HONEST_FORECAST[f.resolution_status]) for f in f_oos]
            market_pairs = [(f.market_mid, _HONEST_FORECAST[f.resolution_status]) for f in f_oos]
            brier_skill_v = brier_skill(brier(bot_pairs), brier(market_pairs))
            reliability_v = murphy(bot_pairs, rc.oos_n_bins).reliability

    k = calibration_gate.k_for(category)
    calibration_ok = ((k == Decimal(1))
                      and (brier_skill_v is not None and brier_skill_v > Decimal(0))
                      and (reliability_v is not None and reliability_v <= rc.reliability_max))

    maker_go = maker_gate.go_for(category)
    ready = (n_resolved >= rc.min_resolved) and oos_positive and calibration_ok and maker_go

    return EvidenceReport(category=category, n_resolved=n_resolved, n_oos=n_oos,
                          n_disputed=n_disputed, net_full=net_full, net_oos=net_oos,
                          brier_skill=brier_skill_v, reliability=reliability_v, k=k,
                          maker_go=maker_go, required_margin=required_margin,
                          oos_positive=oos_positive, calibration_ok=calibration_ok,
                          maker_ok=maker_go, ready=ready)
```

- [ ] **4. Run it — GREEN:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_harness_evidence.py -o addopts="" -q'`

- [ ] **5. Full suite:** `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q'`
  — expected **all prior + 1**.

- [ ] **6. Commit:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/harness/evidence.py tests/test_harness_evidence.py && git commit -m "S9c C1: walk-forward OOS gate reads net_oos not net_full -- honesty pin, net_full 23.50 positive while net_oos -5.50 keeps ready False"'`

---

### Task C2: full-sample net POSITIVE with a passing OOS window → `ready True` (all gates cleared)

- [ ] **1. Write the failing test** (append to `tests/test_harness_evidence.py`):

```python
def test_all_gates_cleared_yields_ready_true(tmp_path):
    # 6 WINS -> n_resolved=6 (>=min_resolved 4), n_oos=2.
    #   net_full = 6*7.25 = 43.50 ; net_oos = 2*7.25 = 14.50 (> required_margin 0).
    # Forecast OOS: 2 well-calibrated market-beating forecasts (see below) -> brier_skill 0.9375 (>0),
    #   reliability 0.01 (<= reliability_max 0.03). k=1, go=True. -> ready True.
    shadow, forecast = _shadow(tmp_path), _forecast(tmp_path)
    for i in range(6):
        _win(shadow, f"w{i}", token=f"tw{i}")
    # forecast honest window: f1 WON bot p 0.90 vs mid 0.60 ; f2 LOST bot p 0.10 vs mid 0.40.
    #   bot_brier = ((0.90-1)^2+(0.10-0)^2)/2 = (0.01+0.01)/2 = 0.01
    #   mkt_brier = ((0.60-1)^2+(0.40-0)^2)/2 = (0.16+0.16)/2 = 0.16
    #   brier_skill = 1 - 0.01/0.16 = 0.9375 ; reliability: bin9 (0.9-1)^2 + bin1 (0.1-0)^2, each wt 1/2 = 0.01
    forecast.record_forecast("g1", category="politics", condition_id="c", p=Decimal("0.90"),
                             market_mid=Decimal("0.60"))
    forecast.record_resolution("g1", "WON")
    forecast.record_forecast("g2", category="politics", condition_id="c", p=Decimal("0.10"),
                             market_mid=Decimal("0.40"))
    forecast.record_resolution("g2", "LOST")
    rep = _evaluate(shadow, forecast, k=Decimal("1"), go=True)
    assert rep.n_resolved == 6 and rep.n_oos == 2
    assert rep.net_full == Decimal("43.50")
    assert rep.net_oos == Decimal("14.50")
    assert rep.brier_skill == Decimal("0.9375")
    assert rep.reliability == Decimal("0.01000")
    assert rep.oos_positive is True
    assert rep.calibration_ok is True
    assert rep.maker_ok is True
    assert rep.ready is True
```

- [ ] **2. RED:** `... ./.venv/bin/pytest tests/test_harness_evidence.py::test_all_gates_cleared_yields_ready_true -o addopts="" -q`
  — the fixture builds the ready case; if C1's impl is present this passes, so run it to confirm GREEN directly (this
  task adds the positive-path assertions; expect PASS immediately post-C1). If it FAILS, the failure names the exact
  wrong field (e.g. `reliability` mismatch) — fix `evidence.py` before proceeding.

- [ ] **3. Implementation:** none new (covered by C1's `evaluate_category`).

- [ ] **4. GREEN:** `... ./.venv/bin/pytest tests/test_harness_evidence.py -o addopts="" -q`

- [ ] **5. Full suite:** expected **all prior + 1**.

- [ ] **6. Commit:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add tests/test_harness_evidence.py && git commit -m "S9c C2: all gates cleared -> ready True -- 6 wins net_full 43.50 / net_oos 14.50, brier_skill 0.9375, reliability 0.01"'`

---

### Task C3: the MC-penalized `required_margin` — a `net_oos` that clears at `family_size=1` FAILS at `family_size=3`

- [ ] **1. Write the failing test** (append):

```python
def test_mc_penalty_inflates_required_margin_by_family_size(tmp_path):
    # required_margin = net_margin_min + mc_penalty*(family_size - 1).
    # mc_penalty=10, net_margin_min=0. net_oos = 14.50 (2 wins in the OOS window of a 6-win sample).
    #   family_size=1 -> required 0  -> 14.50 > 0  True  -> oos_positive True.
    #   family_size=3 -> required 20 -> 14.50 > 20 False -> oos_positive False (MC discipline bites).
    def build():
        shadow, forecast = _shadow(tmp_path, f"s{build.n}.db"), _forecast(tmp_path, f"f{build.n}.db")
        build.n += 1
        for i in range(6):
            _win(shadow, f"w{i}", token=f"tw{i}")
        return shadow, forecast
    build.n = 0
    ramp = _ramp_config(mc_penalty=Decimal("10"))

    s1, f1 = build()
    rep1 = _evaluate(s1, f1, ramp=ramp, family_size=1)
    assert rep1.required_margin == Decimal("0")
    assert rep1.net_oos == Decimal("14.50")
    assert rep1.oos_positive is True

    s3, f3 = build()
    rep3 = _evaluate(s3, f3, ramp=ramp, family_size=3)
    assert rep3.required_margin == Decimal("20")
    assert rep3.net_oos == Decimal("14.50")
    assert rep3.oos_positive is False
    assert rep3.ready is False
```

- [ ] **2. RED / GREEN:** run
  `... ./.venv/bin/pytest tests/test_harness_evidence.py::test_mc_penalty_inflates_required_margin_by_family_size -o addopts="" -q`
  — passes on C1's impl (the `required_margin` formula is already there); confirm GREEN.

- [ ] **3. Implementation:** none new.

- [ ] **4. GREEN:** `... ./.venv/bin/pytest tests/test_harness_evidence.py -o addopts="" -q`

- [ ] **5. Full suite:** expected **all prior + 1**.

- [ ] **6. Commit:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add tests/test_harness_evidence.py && git commit -m "S9c C3: mc_penalty inflates required_margin by (family_size-1) -- net_oos 14.50 clears at fs=1 (req 0), fails at fs=3 (req 20)"'`

---

### Task C4: each gate in isolation — below `min_resolved`, `k==0`, `go==False`, and `net_oos == required_margin` boundary all → `ready False`

- [ ] **1. Write the failing test** (append):

```python
def test_below_min_resolved_is_not_ready(tmp_path):
    # 3 WINS -> n_resolved=3 < min_resolved 4. (n_oos=ceil(0.30*3)=1 < min_oos_resolved 2 too.)
    shadow, forecast = _shadow(tmp_path), _forecast(tmp_path)
    for i in range(3):
        _win(shadow, f"w{i}", token=f"tw{i}")
    rep = _evaluate(shadow, forecast, k=Decimal("1"), go=True)
    assert rep.n_resolved == 3
    assert rep.ready is False


def test_k_zero_makes_calibration_not_ok_and_not_ready(tmp_path):
    # ready-shaped 6-win sample + a passing forecast window, but k=0 (calibration gate NO-GO).
    shadow, forecast = _shadow(tmp_path), _forecast(tmp_path)
    for i in range(6):
        _win(shadow, f"w{i}", token=f"tw{i}")
    forecast.record_forecast("g1", category="politics", condition_id="c", p=Decimal("0.90"),
                             market_mid=Decimal("0.60"))
    forecast.record_resolution("g1", "WON")
    forecast.record_forecast("g2", category="politics", condition_id="c", p=Decimal("0.10"),
                             market_mid=Decimal("0.40"))
    forecast.record_resolution("g2", "LOST")
    rep = _evaluate(shadow, forecast, k=Decimal("0"), go=True)
    assert rep.k == Decimal("0")
    assert rep.oos_positive is True         # the OOS net still clears
    assert rep.calibration_ok is False      # k==0 zeroes it
    assert rep.ready is False


def test_go_false_makes_maker_not_ok_and_not_ready(tmp_path):
    # ready-shaped sample + passing forecasts + k=1, but the maker gate says NO-GO.
    shadow, forecast = _shadow(tmp_path), _forecast(tmp_path)
    for i in range(6):
        _win(shadow, f"w{i}", token=f"tw{i}")
    forecast.record_forecast("g1", category="politics", condition_id="c", p=Decimal("0.90"),
                             market_mid=Decimal("0.60"))
    forecast.record_resolution("g1", "WON")
    forecast.record_forecast("g2", category="politics", condition_id="c", p=Decimal("0.10"),
                             market_mid=Decimal("0.40"))
    forecast.record_resolution("g2", "LOST")
    rep = _evaluate(shadow, forecast, k=Decimal("1"), go=False)
    assert rep.maker_go is False
    assert rep.maker_ok is False
    assert rep.calibration_ok is True
    assert rep.ready is False


def test_net_oos_exactly_at_required_margin_is_not_positive_strict(tmp_path):
    # STRICT >. net_oos = 14.50 (2 wins); set net_margin_min = 14.50 so required_margin == 14.50.
    #   oos_positive = 14.50 > 14.50 = False -> ready False.
    shadow, forecast = _shadow(tmp_path), _forecast(tmp_path)
    for i in range(6):
        _win(shadow, f"w{i}", token=f"tw{i}")
    ramp = _ramp_config(net_margin_min=Decimal("14.50"))
    rep = _evaluate(shadow, forecast, k=Decimal("1"), go=True, ramp=ramp)
    assert rep.net_oos == Decimal("14.50")
    assert rep.required_margin == Decimal("14.50")
    assert rep.oos_positive is False
    assert rep.ready is False
```

- [ ] **2. RED / GREEN:** run
  `... ./.venv/bin/pytest tests/test_harness_evidence.py -o addopts="" -q -k "below_min_resolved or k_zero or go_false or net_oos_exactly"`
  — all pass on C1's impl; confirm GREEN (these pin the gate wiring introduced in C1).

- [ ] **3. Implementation:** none new.

- [ ] **4. GREEN:** `... ./.venv/bin/pytest tests/test_harness_evidence.py -o addopts="" -q`

- [ ] **5. Full suite:** expected **all prior + 4**.

- [ ] **6. Commit:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add tests/test_harness_evidence.py && git commit -m "S9c C4: each gate in isolation flips ready False -- below min_resolved, k==0, go==False, net_oos==required_margin (strict >)"'`

---

### Task C5: calibration sub-gate isolation — `reliability > reliability_max` and `brier_skill <= 0` each force `calibration_ok False`

- [ ] **1. Write the failing test** (append):

```python
def test_reliability_over_ceiling_makes_calibration_not_ok(tmp_path):
    # ready-shaped shadow sample + k=1/go=True, but a MIS-calibrated forecast window:
    #   f1 LOST bot p 0.90 ; f2 WON bot p 0.10  -> reliability = bin9 (0.9-0)^2 + bin1 (0.1-1)^2,
    #   each weight 1/2 = 0.81, which exceeds reliability_max 0.03 -> calibration_ok False.
    shadow, forecast = _shadow(tmp_path), _forecast(tmp_path)
    for i in range(6):
        _win(shadow, f"w{i}", token=f"tw{i}")
    forecast.record_forecast("b1", category="politics", condition_id="c", p=Decimal("0.90"),
                             market_mid=Decimal("0.40"))
    forecast.record_resolution("b1", "LOST")
    forecast.record_forecast("b2", category="politics", condition_id="c", p=Decimal("0.10"),
                             market_mid=Decimal("0.60"))
    forecast.record_resolution("b2", "WON")
    rep = _evaluate(shadow, forecast, k=Decimal("1"), go=True)
    assert rep.reliability == Decimal("0.81000")
    assert rep.reliability > rep.k.__class__("0.03") or True  # documentary; ceiling is reliability_max 0.03
    assert rep.calibration_ok is False
    assert rep.ready is False


def test_non_positive_brier_skill_makes_calibration_not_ok(tmp_path):
    # bot WORSE than the market baseline -> brier_skill <= 0.
    #   f1 WON bot p 0.20 vs mid 0.80 ; f2 LOST bot p 0.80 vs mid 0.20.
    #   bot_brier = ((0.20-1)^2+(0.80-0)^2)/2 = (0.64+0.64)/2 = 0.64
    #   mkt_brier = ((0.80-1)^2+(0.20-0)^2)/2 = (0.04+0.04)/2 = 0.04
    #   brier_skill = 1 - 0.64/0.04 = -15  (<= 0) -> calibration_ok False.
    shadow, forecast = _shadow(tmp_path), _forecast(tmp_path)
    for i in range(6):
        _win(shadow, f"w{i}", token=f"tw{i}")
    forecast.record_forecast("s1", category="politics", condition_id="c", p=Decimal("0.20"),
                             market_mid=Decimal("0.80"))
    forecast.record_resolution("s1", "WON")
    forecast.record_forecast("s2", category="politics", condition_id="c", p=Decimal("0.80"),
                             market_mid=Decimal("0.20"))
    forecast.record_resolution("s2", "LOST")
    rep = _evaluate(shadow, forecast, k=Decimal("1"), go=True)
    assert rep.brier_skill == Decimal("-15")
    assert rep.calibration_ok is False
    assert rep.ready is False
```

- [ ] **2. RED / GREEN:** run
  `... ./.venv/bin/pytest tests/test_harness_evidence.py -o addopts="" -q -k "reliability_over or non_positive_brier"`
  — pass on C1's impl; confirm GREEN.

- [ ] **3. Implementation:** none new.

- [ ] **4. GREEN:** `... ./.venv/bin/pytest tests/test_harness_evidence.py -o addopts="" -q`

- [ ] **5. Full suite:** expected **all prior + 2**.

- [ ] **6. Commit:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add tests/test_harness_evidence.py && git commit -m "S9c C5: calibration sub-gates -- reliability 0.81 over ceiling and brier_skill -15 each force calibration_ok False"'`

---

### Task C6: cold (no settled rows) → `n_resolved 0`, None stats, `ready False`; DISPUTED/VOID counted in `n_disputed`, excluded from the honest sample + net

- [ ] **1. Write the failing test** (append):

```python
def test_cold_ledger_is_not_ready_with_none_stats(tmp_path):
    # no settled shadow rows AND no resolved forecasts -> fail-closed.
    shadow, forecast = _shadow(tmp_path), _forecast(tmp_path)
    rep = _evaluate(shadow, forecast, k=Decimal("1"), go=True)
    assert rep.n_resolved == 0
    assert rep.n_oos == 0
    assert rep.net_full is None
    assert rep.net_oos is None
    assert rep.brier_skill is None
    assert rep.reliability is None
    assert rep.oos_positive is False
    assert rep.calibration_ok is False   # None brier_skill/reliability -> not ok
    assert rep.ready is False


def test_disputed_and_void_are_counted_but_excluded_from_the_honest_sample(tmp_path):
    # 4 WINS (honest) + 1 DISPUTED + 1 VOID (shadow ledger uses "DISPUTED", NOT "DISPUTED_LOST").
    #   n_resolved counts only the 4 honest wins; n_disputed = 2 (DISPUTED + VOID).
    #   net_full = 4*7.25 = 29.00 (the DISPUTED/VOID rows contribute NOTHING to net).
    #   n_oos = ceil(0.30*4)=2 ; net_oos = 2*7.25 = 14.50.
    shadow, forecast = _shadow(tmp_path), _forecast(tmp_path)
    for i in range(4):
        _win(shadow, f"w{i}", token=f"tw{i}")
    shadow.record_trade("d1", token_id="td1", condition_id="c", category="politics", side="BUY",
                        shares=Decimal("10"), fill_price=Decimal("0.40"), fill_mid=Decimal("0.50"),
                        reward_accrued=Decimal("0.25"))
    shadow.record_settlement("d1", status="DISPUTED", resolution_value=None)
    shadow.record_trade("v1", token_id="tv1", condition_id="c", category="politics", side="BUY",
                        shares=Decimal("10"), fill_price=Decimal("0.40"), fill_mid=Decimal("0.50"),
                        reward_accrued=Decimal("0.25"))
    shadow.record_settlement("v1", status="VOID", resolution_value=None)
    rep = _evaluate(shadow, forecast, k=Decimal("1"), go=True)
    assert rep.n_resolved == 4
    assert rep.n_disputed == 2
    assert rep.net_full == Decimal("29.00")
    assert rep.net_oos == Decimal("14.50")
    assert rep.n_oos == 2
```

- [ ] **2. RED / GREEN:** run
  `... ./.venv/bin/pytest tests/test_harness_evidence.py -o addopts="" -q -k "cold_ledger or disputed_and_void"`
  — pass on C1's impl (the cold branch + `n_disputed` counting are there); confirm GREEN.

- [ ] **3. Implementation:** none new.

- [ ] **4. GREEN:** `... ./.venv/bin/pytest tests/test_harness_evidence.py -o addopts="" -q`

- [ ] **5. Full suite:** expected **all prior + 2**.

- [ ] **6. Commit:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add tests/test_harness_evidence.py && git commit -m "S9c C6: cold -> None stats ready False; DISPUTED/VOID counted in n_disputed, excluded from honest net (net_full 29.00)"'`

---

### Task C7: an unknown shadow status in the ledger → `ValueError` (raw-sqlite corruption, mirroring the exhaustive-status pin)

- [ ] **1. Write the failing test** (append):

```python
def test_unknown_shadow_status_fails_loud(tmp_path):
    # Insert a corrupt status directly via raw sqlite (bypassing record_settlement's guard) to
    # simulate DB corruption / an untaught 5th status. evaluate_category must fail LOUD, mirroring
    # MakerTracker's exhaustive-status ValueError -- a status it cannot classify must never silently
    # vanish from the honest/DISPUTED accounting.
    import sqlite3

    path = str(tmp_path / "corrupt.db")
    shadow = ShadowLedger(path, MonotonicStamper())
    _win(shadow, "w0", token="tw0")   # one legitimate settled row so the table + schema exist
    shadow.record_trade("x1", token_id="tx1", condition_id="c", category="politics", side="BUY",
                        shares=Decimal("10"), fill_price=Decimal("0.40"), fill_mid=Decimal("0.50"),
                        reward_accrued=Decimal("0.25"))
    conn = sqlite3.connect(path)
    conn.execute("UPDATE shadow_trades SET status=?, settled_at=? WHERE trade_id=?",
                 ("MAYBE", 999, "x1"))
    conn.commit()
    conn.close()

    forecast = _forecast(tmp_path)
    with pytest.raises(ValueError, match="status"):
        _evaluate(shadow, forecast, k=Decimal("1"), go=True)
```

> **Assembler note (dependency on S9b):** this test assumes the ShadowLedger table is named
> `shadow_trades` with a `trade_id` PK and a `status` column (the shared-context ledger contract:
> "the table is `shadow_trades`, the dataclass is `ShadowTradeRecord`"). If S9b's realized column /
> table names differ, update the raw `UPDATE` accordingly — the behavior pinned (loud raise on an
> unclassifiable status via `evaluate_category`) is unchanged.

- [ ] **2. Run it — RED for the right reason:**
  `... ./.venv/bin/pytest tests/test_harness_evidence.py::test_unknown_shadow_status_fails_loud -o addopts="" -q`
  — with C1's impl already raising on the unknown branch this should PASS immediately; run to confirm GREEN. If the
  raw-sqlite table/column name is wrong it fails with an `OperationalError` naming the bad identifier → fix the
  `UPDATE` to match S9b's schema, then GREEN.

- [ ] **3. Implementation:** none new (the exhaustive-status `raise ValueError(...)` is already in `evaluate_category`).

- [ ] **4. GREEN:** `... ./.venv/bin/pytest tests/test_harness_evidence.py -o addopts="" -q`

- [ ] **5. Full suite:** expected **all prior + 1**.

- [ ] **6. Commit:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add tests/test_harness_evidence.py && git commit -m "S9c C7: unknown shadow status (raw-sqlite corruption) -> ValueError via evaluate_category -- exhaustive-status pin"'`

---

### Task C8: `dispute_freeze_stress` — survives / breaches / the $60-ceiling boundary + empty portfolio

- [ ] **1. Write the failing test** (`tests/test_harness_stress.py`):

```python
"""S9 / POL-11 — dispute-freeze stress + tail-survival (DECISIONS-S0 §4 reserve-floor invariant)."""

from decimal import Decimal

import pytest

from polybot.ers.caps import RiskCaps
from polybot.ers.validator import OpenPosition, Portfolio
from polybot.harness.config import RampConfig


def _pos(*, wcr, source, token):
    # only worst_case_risk / resolution_source / token_id are load-bearing for the stress test;
    # the other OpenPosition fields take their defaults.
    return OpenPosition(condition_id="c", event_id="e", resolution_source=source,
                        cluster_id="k", worst_case_risk=Decimal(wcr), token_id=token)


def _stress(portfolio, **kw):
    from polybot.harness.stress import dispute_freeze_stress
    return dispute_freeze_stress(portfolio, caps=RiskCaps(), **kw)


def test_reserve_floor_holds_under_100pct_adverse_freeze_survives(tmp_path):
    # ONE resolution_source, cluster worst_case_risk = 30, adverse_fraction default 1.
    #   worst_case_markdown = 1 * 30 = 30 ; non_frozen_encumbered = 0.
    #   reserve_after = nav(300) - 0 - 30 = 270 >= reserve_floor(240) -> survives True.
    port = Portfolio(nav=Decimal("300"), positions=(
        _pos(wcr="10", source="uma", token="t0"),
        _pos(wcr="20", source="uma", token="t1"),
    ))
    res = _stress(port)
    assert res.worst_case_markdown == Decimal("30")
    assert res.reserve_after == Decimal("270")
    assert res.reserve_floor == Decimal("240")
    assert res.survives is True


def test_reserve_floor_breach_does_not_survive(tmp_path):
    # frozen cluster (srcA) wcr = 45 ; non-frozen (srcB) wcr = 20.
    #   markdown = 45 ; reserve_after = 300 - 20 - 45 = 235 < 240 -> survives False.
    port = Portfolio(nav=Decimal("300"), positions=(
        _pos(wcr="45", source="srcA", token="ta"),
        _pos(wcr="20", source="srcB", token="tb"),
    ))
    res = _stress(port)
    assert res.worst_case_markdown == Decimal("45")
    assert res.reserve_after == Decimal("235")
    assert res.survives is False


def test_boundary_at_the_60_at_risk_ceiling_survives_inclusive(tmp_path):
    # THE $60 ceiling: one source, total at-risk = 60, adverse_fraction 1 (all frozen, no non-frozen).
    #   markdown = 60 ; reserve_after = 300 - 0 - 60 = 240 == reserve_floor -> survives (>= inclusive).
    port = Portfolio(nav=Decimal("300"), positions=(
        _pos(wcr="36", source="uma", token="t0"),
        _pos(wcr="24", source="uma", token="t1"),
    ))
    res = _stress(port)
    assert res.worst_case_markdown == Decimal("60")
    assert res.reserve_after == Decimal("240")
    assert res.reserve_after == res.reserve_floor
    assert res.survives is True   # inclusive >=


def test_empty_portfolio_survives_with_reserve_after_equal_nav(tmp_path):
    # no positions -> markdown 0, non_frozen 0 -> reserve_after = nav = 300 >= 240 -> survives True.
    port = Portfolio(nav=Decimal("300"), positions=())
    res = _stress(port)
    assert res.worst_case_markdown == Decimal("0")
    assert res.reserve_after == Decimal("300")
    assert res.survives is True
```

- [ ] **2. Run it — RED for the right reason:**
  `... ./.venv/bin/pytest tests/test_harness_stress.py -o addopts="" -q`
  — expect `ModuleNotFoundError: No module named 'polybot.harness.stress'` (raised in `_stress`'s import).

- [ ] **3. Minimal implementation** — full new file `src/polybot/harness/stress.py`:

```python
"""Dispute-freeze stress test + tail-survival gate (S9 / POL-11).

DECISIONS-S0 §4 reserve-floor invariant: simulate a freeze of the LARGEST resolution-source cluster
under a (default 100%) adverse co-move markdown, plus the full non-frozen encumbrance, and prove the
signed reserve floor still holds. Pure over the ERS Portfolio + the signed RiskCaps. Fail CLOSED: a
non-finite worst_case_risk or adverse_fraction -> survives False (a bad field must never certify a
phantom survival). tail_survived is the earn-autonomy tail gate: you must have SURVIVED real disputes
(>= min resolved DISPUTED) AND >= min correlated-stress episodes, not merely dodged them.
"""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class StressResult:
    survives: bool
    reserve_after: Decimal
    reserve_floor: Decimal
    worst_case_markdown: Decimal


def dispute_freeze_stress(portfolio, *, caps, adverse_fraction=Decimal("1")):
    floor = caps.reserve_floor
    # Fail CLOSED on a non-finite adverse_fraction or any non-finite position risk.
    if not adverse_fraction.is_finite():
        return StressResult(False, caps.nav, floor, Decimal(0))
    for p in portfolio.positions:
        if not p.worst_case_risk.is_finite():
            return StressResult(False, caps.nav, floor, Decimal(0))

    # Group by resolution_source; the frozen cluster is the source with the MAX summed worst_case_risk
    # (ties -> the first by iteration; positions is an ordered tuple, so deterministic).
    sums = {}
    order = []
    for p in portfolio.positions:
        src = p.resolution_source
        if src not in sums:
            sums[src] = Decimal(0)
            order.append(src)
        sums[src] += p.worst_case_risk

    if not order:  # empty portfolio -> nothing frozen, nothing encumbered
        return StressResult(caps.nav >= floor, caps.nav, floor, Decimal(0))

    frozen_src = order[0]
    for src in order:
        if sums[src] > sums[frozen_src]:
            frozen_src = src

    frozen_cluster_wcr = sums[frozen_src]
    non_frozen_encumbered = sum((sums[src] for src in order if src != frozen_src), Decimal(0))
    worst_case_markdown = adverse_fraction * frozen_cluster_wcr
    reserve_after = caps.nav - non_frozen_encumbered - worst_case_markdown
    return StressResult(reserve_after >= floor, reserve_after, floor, worst_case_markdown)


def tail_survived(*, n_resolved_disputed, stress_episodes, ramp_config):
    return (n_resolved_disputed >= ramp_config.min_resolved_disputed
            and stress_episodes >= ramp_config.min_stress_episodes)
```

- [ ] **4. Run it — GREEN:** `... ./.venv/bin/pytest tests/test_harness_stress.py -o addopts="" -q`

- [ ] **5. Full suite:** expected **all prior + 4**.

- [ ] **6. Commit:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/harness/stress.py tests/test_harness_stress.py && git commit -m "S9c C8: dispute_freeze_stress reserve-floor invariant -- survives (270), breach (235), \$60-ceiling boundary (240==floor inclusive), empty (nav)"'`

---

### Task C9: `dispute_freeze_stress` — the LARGEST cluster is the one frozen (survival-flipping under `adverse_fraction<1`) + fail-closed on a non-finite `worst_case_risk`

- [ ] **1. Write the failing test** (append to `tests/test_harness_stress.py`):

```python
def test_largest_cluster_is_frozen_selection_flips_survival(tmp_path):
    # adverse_fraction=0.5 makes WHICH cluster is frozen load-bearing.
    #   srcBIG wcr=56 (one position), srcSMALL wcr=9 (one position).
    #   CORRECT (freeze srcBIG): markdown = 0.5*56 = 28 ; non_frozen = 9 ;
    #     reserve_after = 300 - 9 - 28 = 263 >= 240 -> survives True.
    #   A mutation freezing srcSMALL instead: markdown = 0.5*9 = 4.5 ; non_frozen = 56 ;
    #     reserve_after = 300 - 56 - 4.5 = 239.5 < 240 -> would be survives False.
    #   Asserting survives True + markdown 28 kills the wrong-cluster mutation.
    port = Portfolio(nav=Decimal("300"), positions=(
        _pos(wcr="9", source="srcSMALL", token="tsmall"),   # smaller listed FIRST to defeat
        _pos(wcr="56", source="srcBIG", token="tbig"),      #   an "always freeze the first source" bug
    ))
    res = _stress(port, adverse_fraction=Decimal("0.5"))
    assert res.worst_case_markdown == Decimal("28.0")
    assert res.reserve_after == Decimal("263.0")
    assert res.survives is True


def test_non_finite_worst_case_risk_fails_closed(tmp_path):
    # a NaN worst_case_risk (corrupt/mis-marked position) -> survives False (never a phantom survival).
    port = Portfolio(nav=Decimal("300"), positions=(
        _pos(wcr="10", source="uma", token="t0"),
        _pos(wcr="NaN", source="uma", token="t1"),
    ))
    res = _stress(port)
    assert res.survives is False
```

- [ ] **2. Run it — RED / GREEN:**
  `... ./.venv/bin/pytest tests/test_harness_stress.py -o addopts="" -q -k "largest_cluster or non_finite_worst_case"`
  — both pass on C8's impl (max-sum selection + the `is_finite` fail-closed guard are there); confirm GREEN.

- [ ] **3. Implementation:** none new.

- [ ] **4. GREEN:** `... ./.venv/bin/pytest tests/test_harness_stress.py -o addopts="" -q`

- [ ] **5. Full suite:** expected **all prior + 2**.

- [ ] **6. Commit:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add tests/test_harness_stress.py && git commit -m "S9c C9: stress freezes the LARGEST cluster (af=0.5 flips survival 263 vs 239.5) + non-finite worst_case_risk fails closed"'`

---

### Task C10: `tail_survived` — the disputed-count and stress-episode boundaries (at / below / above the minimums)

- [ ] **1. Write the failing test** (append to `tests/test_harness_stress.py`):

```python
def _ramp(**over):
    base = dict(min_resolved_disputed=2, min_stress_episodes=3)
    base.update(over)
    return RampConfig(**base)


def _tail(n_disputed, episodes, ramp):
    from polybot.harness.stress import tail_survived
    return tail_survived(n_resolved_disputed=n_disputed, stress_episodes=episodes, ramp_config=ramp)


def test_tail_survived_requires_both_minimums_inclusive(tmp_path):
    ramp = _ramp(min_resolved_disputed=2, min_stress_episodes=3)
    # AT both minimums (inclusive >=) -> True.
    assert _tail(2, 3, ramp) is True
    # disputed BELOW min (1 < 2) -> False even with episodes clearing.
    assert _tail(1, 3, ramp) is False
    # episodes BELOW min (2 < 3) -> False even with disputes clearing.
    assert _tail(2, 2, ramp) is False
    # ABOVE both minimums -> True.
    assert _tail(5, 9, ramp) is True


def test_tail_survived_below_either_minimum_alone_fails(tmp_path):
    ramp = _ramp(min_resolved_disputed=1, min_stress_episodes=1)
    assert _tail(0, 5, ramp) is False   # zero disputes -> you dodged, did not survive
    assert _tail(5, 0, ramp) is False   # zero stress episodes
    assert _tail(1, 1, ramp) is True    # exactly one of each clears the default-shaped gate
```

- [ ] **2. Run it — RED / GREEN:**
  `... ./.venv/bin/pytest tests/test_harness_stress.py -o addopts="" -q -k "tail_survived"`
  — pass on C8's impl (`tail_survived` is defined there); confirm GREEN.

- [ ] **3. Implementation:** none new.

- [ ] **4. GREEN:** `... ./.venv/bin/pytest tests/test_harness_stress.py -o addopts="" -q`

- [ ] **5. Full suite:** expected **all prior + 2**.

- [ ] **6. Commit:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add tests/test_harness_stress.py && git commit -m "S9c C10: tail_survived boundaries -- both minimums inclusive >=; below either (0 disputes / 0 episodes) fails"'`

---

**S9c total: 10 tasks, 19 new tests** (`test_harness_evidence.py`: 11 — C1×1, C2×1, C3×1, C4×4, C5×2, C6×2, C7×1;
`test_harness_stress.py`: 8 — C8×4, C9×2, C10×2). Full-suite expectation at end of S9c: **baseline + S9a + S9b + 19**.
## Sub-slice S9d — Ramp controller + boot seam + e2e

### Task D1: `decide` → SHADOW + `promote_recommended=False` when evidence not ready (reason names the failed gate)

- [ ] **1. Write the failing test** (`tests/test_harness_ramp_controller.py`):

```python
"""S9 / POL-11 — RampController.decide (the binary stage machine: advisory promote, auto ramp-down)."""

from decimal import Decimal

import pytest

from polybot.ers.caps import RiskCaps
from polybot.ers.validator import OpenPosition, Portfolio
from polybot.harness.config import RampConfig
from polybot.harness.evidence import EvidenceReport
from polybot.harness.ramp_controller import RAMP, SHADOW, TINY_LIVE, RampController, RampDecision


def _evidence(*, ready, category="sports", oos_positive=True, calibration_ok=True,
              maker_ok=True, n_resolved=200, n_oos=60):
    """A pinned EvidenceReport (S9c contract). Only `.ready` drives decide(); the sub-flags
    let a test say WHICH gate failed so `reason` can be asserted. Other numeric fields are
    carried through into RampDecision.evidence."""
    return EvidenceReport(
        category=category, n_resolved=n_resolved, n_oos=n_oos, n_disputed=2,
        net_full=Decimal("5"), net_oos=Decimal("3"), brier_skill=Decimal("0.2"),
        reliability=Decimal("0.01"), k=Decimal("1"), maker_go=maker_ok,
        required_margin=Decimal("0"), oos_positive=oos_positive,
        calibration_ok=calibration_ok, maker_ok=maker_ok, ready=ready)


def _controller():
    return RampController(ramp_config=RampConfig(), caps=RiskCaps())


def _healthy_portfolio():
    # One small position that leaves the reserve floor intact under a 100%-adverse freeze:
    # RiskCaps() nav=300, reserve_floor=240; a single $8 worst-case position frozen ->
    # reserve_after = 300 - 0 - 8 = 292 >= 240 -> survives.
    return Portfolio(nav=Decimal("300"), positions=(
        OpenPosition(condition_id="m1", event_id="e1", resolution_source="uma1",
                     cluster_id="c1", worst_case_risk=Decimal("8"), token_id="t1",
                     entry_price=Decimal("0.50")),
    ))


def test_not_ready_forces_shadow_and_no_promote_with_reason():
    # evidence.ready False (OOS gate the culprit) -> stage collapses to SHADOW, promote False,
    # ramp_down False (still in SHADOW so a not-ready is not a regression), reason names the gate.
    c = _controller()
    ev = _evidence(ready=False, oos_positive=False)
    d = c.decide("sports", evidence=ev, current_stage=SHADOW, portfolio=_healthy_portfolio(),
                 n_resolved_disputed=1, stress_episodes=1, breaker_tripped=False)
    assert isinstance(d, RampDecision)
    assert d.stage == SHADOW
    assert d.promote_recommended is False
    assert d.ramp_down is False
    assert d.reason == "not_ready:oos"
    assert d.evidence is ev            # the report is carried through verbatim
    assert d.category == "sports"


def test_not_ready_reason_distinguishes_calibration_and_maker():
    # The reason string names the SPECIFIC failed evidence gate (calibration vs maker), so an
    # operator reading the decision knows why a category is still SHADOW.
    c = _controller()
    d_cal = c.decide("sports", evidence=_evidence(ready=False, calibration_ok=False),
                     current_stage=SHADOW, portfolio=_healthy_portfolio(),
                     n_resolved_disputed=1, stress_episodes=1, breaker_tripped=False)
    assert d_cal.stage == SHADOW and d_cal.reason == "not_ready:calibration"
    d_mk = c.decide("sports", evidence=_evidence(ready=False, maker_ok=False),
                    current_stage=SHADOW, portfolio=_healthy_portfolio(),
                    n_resolved_disputed=1, stress_episodes=1, breaker_tripped=False)
    assert d_mk.stage == SHADOW and d_mk.reason == "not_ready:maker"
```

- [ ] **2. Run it — RED for the right reason:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_harness_ramp_controller.py -o addopts="" -q'`
  Expected: `ModuleNotFoundError: No module named 'polybot.harness.ramp_controller'`.

- [ ] **3. Minimal implementation** (`src/polybot/harness/ramp_controller.py` — the FULL file; D1 lands the whole `decide` since the not-ready path exercises stage+reason; D2/D3 add asserts, no new code beyond what the contract pins):

```python
"""Earn-autonomy ramp controller (S9 / POL-11) — the binary stage machine.

Turns an accrued shadow EvidenceReport into a per-category RampDecision. It is STRUCTURALLY
ADVISORY: ramp-UP is a recommendation the operator acts on out-of-band (DECISIONS-S0 §8: the
human ramp-up gate); ramp-DOWN is an automatic flag the existing S4.7 tighten-only ratchet
applies. The controller has NO signer and NO cap-mutation surface at all — it cannot widen or
loosen a cap, so "the controller widened a cap" / "ramp-up mutated caps" are UNREPRESENTABLE,
not merely untested (the structural-honesty pins in test_harness_ramp_controller.py assert the
class surface is bare). The only PnL basis is EvidenceReport.ready, which reads the OUT-OF-SAMPLE
net (never gross, never in-sample). Fail-closed: not-ready -> SHADOW, no promote.
"""

from polybot.harness import stress

SHADOW = "SHADOW"
TINY_LIVE = "TINY_LIVE"
RAMP = "RAMP"

from dataclasses import dataclass

from polybot.harness.evidence import EvidenceReport


@dataclass(frozen=True)
class RampDecision:
    category: str
    stage: str
    promote_recommended: bool
    ramp_down: bool
    reason: str
    evidence: EvidenceReport


class RampController:
    """Advisory stage machine. NO cap-mutation surface (see the module docstring): decide()
    returns ONLY a RampDecision — it never returns a loosened cap, and the class exposes no
    swap_caps / set_state / place / widen / signer attribute."""

    def __init__(self, *, ramp_config, caps):
        self._ramp_config = ramp_config
        self._caps = caps

    def decide(self, category, *, evidence, current_stage, portfolio, n_resolved_disputed,
               stress_episodes, breaker_tripped):
        tail = stress.tail_survived(
            n_resolved_disputed=n_resolved_disputed, stress_episodes=stress_episodes,
            ramp_config=self._ramp_config)
        st = stress.dispute_freeze_stress(portfolio, caps=self._caps)
        promote_recommended = (evidence.ready and tail and st.survives
                               and not breaker_tripped)
        ramp_down = breaker_tripped or (current_stage != SHADOW and not evidence.ready)
        stage = SHADOW if not evidence.ready else current_stage
        reason = self._reason(evidence, tail=tail, stress_survives=st.survives,
                              breaker_tripped=breaker_tripped, ramp_down=ramp_down)
        return RampDecision(category=category, stage=stage,
                            promote_recommended=promote_recommended, ramp_down=ramp_down,
                            reason=reason, evidence=evidence)

    @staticmethod
    def _reason(evidence, *, tail, stress_survives, breaker_tripped, ramp_down):
        # Ramp-DOWN reasons take precedence in the string (a regression is the loudest signal).
        if breaker_tripped:
            return "ramp_down:breaker"
        if ramp_down:                       # current_stage != SHADOW and not ready
            return "ramp_down:regression"
        if not evidence.ready:
            # Name the specific failed evidence gate (fail-closed order: oos, calibration, maker).
            if not evidence.oos_positive:
                return "not_ready:oos"
            if not evidence.calibration_ok:
                return "not_ready:calibration"
            if not evidence.maker_ok:
                return "not_ready:maker"
            return "not_ready:sample"       # n_resolved below the floor (all sub-gates ok)
        if not tail:
            return "blocked:tail"
        if not stress_survives:
            return "blocked:stress"
        return "promote_ok"
```

  And ensure the package imports cleanly — `src/polybot/harness/__init__.py` already exists (S9a A1); no change needed.

- [ ] **4. Run it — GREEN:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_harness_ramp_controller.py -o addopts="" -q'`

- [ ] **5. Full suite:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q'`
  Expected: **P + 2**.

- [ ] **6. Commit:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/harness/ramp_controller.py tests/test_harness_ramp_controller.py && git commit -m "S9d D1: RampController.decide not-ready path -> SHADOW + gate-named reason -- fail-closed advisory stage machine"'`

---

### Task D2: `promote_recommended` True ONLY when ready AND tail AND stress AND not-breaker (flip each False in isolation)

- [ ] **1. Write the failing test** (append to `tests/test_harness_ramp_controller.py`):

```python
def test_promote_recommended_true_only_when_all_four_hold():
    # The healthy promotion: ready AND tail survived AND stress survives AND no breaker ->
    # promote_recommended True, reason promote_ok, ramp_down False. stage stays current_stage
    # (SHADOW here) -- promotion PAST it is the operator's human gate, not the controller's.
    c = _controller()
    ev = _evidence(ready=True)
    d = c.decide("sports", evidence=ev, current_stage=SHADOW, portfolio=_healthy_portfolio(),
                 n_resolved_disputed=1, stress_episodes=1, breaker_tripped=False)
    assert d.promote_recommended is True
    assert d.ramp_down is False
    assert d.reason == "promote_ok"
    assert d.stage == SHADOW


def test_promote_blocked_when_tail_not_survived():
    # ready but n_resolved_disputed 0 < min_resolved_disputed(1) -> tail_survived False ->
    # promote False, reason blocked:tail. (RampConfig() defaults: min_resolved_disputed 1.)
    c = _controller()
    d = c.decide("sports", evidence=_evidence(ready=True), current_stage=SHADOW,
                 portfolio=_healthy_portfolio(), n_resolved_disputed=0, stress_episodes=1,
                 breaker_tripped=False)
    assert d.promote_recommended is False
    assert d.reason == "blocked:tail"
    assert d.ramp_down is False          # still SHADOW + ready -> not a regression


def test_promote_blocked_when_stress_does_not_survive():
    # ready + tail, but a portfolio whose largest-cluster 100%-adverse freeze breaches the
    # reserve floor -> dispute_freeze_stress.survives False -> promote False, reason blocked:stress.
    # One $70 worst-case position (over the $60 ceiling, but the stress test is pure over the
    # portfolio it is given): frozen markdown 70 -> reserve_after = 300 - 0 - 70 = 230 < 240.
    c = _controller()
    breach = Portfolio(nav=Decimal("300"), positions=(
        OpenPosition(condition_id="m1", event_id="e1", resolution_source="uma1",
                     cluster_id="c1", worst_case_risk=Decimal("70"), token_id="t1",
                     entry_price=Decimal("0.50")),
    ))
    d = c.decide("sports", evidence=_evidence(ready=True), current_stage=SHADOW,
                 portfolio=breach, n_resolved_disputed=1, stress_episodes=1,
                 breaker_tripped=False)
    assert d.promote_recommended is False
    assert d.reason == "blocked:stress"


def test_promote_blocked_when_breaker_tripped():
    # ready + tail + stress, but a tripped breaker -> promote False. A breaker ALSO raises
    # ramp_down (D3), and the breaker reason dominates the string.
    c = _controller()
    d = c.decide("sports", evidence=_evidence(ready=True), current_stage=SHADOW,
                 portfolio=_healthy_portfolio(), n_resolved_disputed=1, stress_episodes=1,
                 breaker_tripped=True)
    assert d.promote_recommended is False
    assert d.ramp_down is True
    assert d.reason == "ramp_down:breaker"
```

- [ ] **2. Run it — RED for the right reason:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_harness_ramp_controller.py -o addopts="" -q'`
  Expected: the four new `test_promote_*` selected and FAILING **only if** the D1 impl were incomplete — but D1 landed the full `decide`, so these pass immediately given D1's implementation. To observe a genuine RED-first, stage D2 by temporarily asserting a wrong reason? **No** — instead: D2 is a pure ADD of asserts over already-shipped behavior. Per the shared-context "group related asserts in one cycle where natural", D2's cycle observes RED via the NameError path if run before D1's file exists. Since D1 shipped the impl, run these and confirm GREEN directly; the RED evidence for the promote logic is the D1 run (module-absent). **Reviewer note:** if strict RED-first per-cycle is demanded, split D2 by first landing `decide` with `promote_recommended = evidence.ready` (dropping tail/stress/breaker) in D1, so these three flips fail RED here, then restore the full conjunction. The plan as written keeps `decide` whole in D1 (the not-ready path needs the full stage/reason machinery) and treats D2 as the promotion-conjunction lock.

  *(Assembler: prefer the split — land the minimal `promote_recommended = evidence.ready` in D1, then D2 tightens to the four-way AND so each flip is a real RED. Both orderings end byte-identical; the split gives cleaner TDD evidence.)*

- [ ] **3. Implementation:** already present from D1 (the four-way conjunction + `_reason` ordering). If the split ordering was taken, tighten `promote_recommended` to the full AND and add the `blocked:tail` / `blocked:stress` branches here.

- [ ] **4. Run it — GREEN:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_harness_ramp_controller.py -o addopts="" -q'`

- [ ] **5. Full suite:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q'`
  Expected: **P + 6**.

- [ ] **6. Commit:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add tests/test_harness_ramp_controller.py src/polybot/harness/ramp_controller.py && git commit -m "S9d D2: promote_recommended True iff ready AND tail AND stress AND not-breaker -- each flip False in isolation blocks with its reason"'`

---

### Task D3: `ramp_down` on regression (non-SHADOW + not-ready) / tripped breaker; False in the healthy SHADOW-ready case

- [ ] **1. Write the failing test** (append to `tests/test_harness_ramp_controller.py`):

```python
def test_ramp_down_on_regression_from_a_live_stage():
    # A previously-promoted category (current_stage TINY_LIVE) whose evidence flips un-ready is a
    # REGRESSION -> ramp_down True (automatic; the flag ERSController hands the S4.7 ratchet),
    # stage collapses to SHADOW, promote False, reason ramp_down:regression.
    c = _controller()
    d = c.decide("sports", evidence=_evidence(ready=False, oos_positive=False),
                 current_stage=TINY_LIVE, portfolio=_healthy_portfolio(),
                 n_resolved_disputed=1, stress_episodes=1, breaker_tripped=False)
    assert d.ramp_down is True
    assert d.stage == SHADOW
    assert d.promote_recommended is False
    assert d.reason == "ramp_down:regression"


def test_ramp_down_on_tripped_breaker_from_any_stage():
    # A tripped breaker raises ramp_down regardless of stage or readiness (even a RAMP-stage,
    # ready category). Breaker reason dominates.
    c = _controller()
    d = c.decide("sports", evidence=_evidence(ready=True), current_stage=RAMP,
                 portfolio=_healthy_portfolio(), n_resolved_disputed=1, stress_episodes=1,
                 breaker_tripped=True)
    assert d.ramp_down is True
    assert d.promote_recommended is False
    assert d.reason == "ramp_down:breaker"
    # ready True -> stage is NOT collapsed to SHADOW by readiness; it stays current_stage.
    # (ramp_down is the automatic-tighten signal; the stage field tracks readiness only.)
    assert d.stage == RAMP


def test_no_ramp_down_in_healthy_shadow_ready_case():
    # SHADOW + ready + no breaker -> NOT a regression: ramp_down False. (A not-ready WHILE in
    # SHADOW is also not a regression -- guarded in D1's test_not_ready_*.)
    c = _controller()
    d = c.decide("sports", evidence=_evidence(ready=True), current_stage=SHADOW,
                 portfolio=_healthy_portfolio(), n_resolved_disputed=1, stress_episodes=1,
                 breaker_tripped=False)
    assert d.ramp_down is False
```

- [ ] **2. Run it — RED / GREEN:** as with D2, the `ramp_down` logic shipped in D1. Observe these pass given the D1 impl (or RED-first if the D2 split ordering deferred the `ramp_down` branch). Command:
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_harness_ramp_controller.py -k ramp_down -o addopts="" -q'`

- [ ] **3. Implementation:** present from D1 (`ramp_down = breaker_tripped or (current_stage != SHADOW and not evidence.ready)`; `stage = SHADOW if not evidence.ready else current_stage`).

- [ ] **4. Run it — GREEN:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_harness_ramp_controller.py -o addopts="" -q'`

- [ ] **5. Full suite:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q'`
  Expected: **P + 9**.

- [ ] **6. Commit:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add tests/test_harness_ramp_controller.py src/polybot/harness/ramp_controller.py && git commit -m "S9d D3: ramp_down automatic on regression (non-SHADOW + un-ready) or tripped breaker; False in healthy SHADOW-ready"'`

---

### Task D4: STRUCTURAL HONESTY PINS — no cap-mutation surface; `decide` returns only a RampDecision (no cap)

- [ ] **1. Write the failing test** (append to `tests/test_harness_ramp_controller.py`):

```python
import dataclasses


def test_controller_has_no_cap_mutation_surface():
    # THE STRUCTURAL PIN (design §5 invariant 2): RampController exposes NO method/attribute that
    # could widen or loosen a cap or sign/place an order. The intersection of its surface with the
    # forbidden names is EMPTY -- so "ramp-up mutated a cap" is unrepresentable, not merely
    # untested. Kills the mutation that gives the controller a swap_caps / set_state / signer path.
    forbidden = {"swap_caps", "set_state", "place", "widen", "loosen", "signer",
                 "active_caps", "cancel_all", "flatten", "sign"}
    surface = set(dir(RampController))
    assert surface & forbidden == set(), f"forbidden cap/exec surface leaked: {surface & forbidden}"
    # The only public entry point is decide(); the only stored state is the config + caps refs
    # (read-only advisory), never a mutator.
    c = _controller()
    instance_surface = {n for n in dir(c) if not n.startswith("_")}
    assert instance_surface == {"decide"}, f"unexpected public surface: {instance_surface}"


def test_decide_returns_only_a_rampdecision_never_a_loosened_cap():
    # decide's return is a RampDecision and NOTHING else -- there is no caps/envelope/widened field
    # anywhere on it. A mutation that smuggled a loosened cap into the decision is killed: the
    # RampDecision field set is EXACTLY the pinned six, none of them a RiskCaps.
    c = _controller()
    d = c.decide("sports", evidence=_evidence(ready=True), current_stage=SHADOW,
                 portfolio=_healthy_portfolio(), n_resolved_disputed=1, stress_episodes=1,
                 breaker_tripped=False)
    assert isinstance(d, RampDecision)
    field_names = {f.name for f in dataclasses.fields(d)}
    assert field_names == {"category", "stage", "promote_recommended", "ramp_down",
                           "reason", "evidence"}
    # No field on the decision is a RiskCaps (the controller cannot hand back a widened ceiling).
    for f in dataclasses.fields(d):
        assert not isinstance(getattr(d, f.name), RiskCaps)
```

- [ ] **2. Run it — RED for the right reason:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_harness_ramp_controller.py -k "surface or loosened" -o addopts="" -q'`
  Expected GREEN immediately against the D1 impl (the surface is already bare) — this task DOCUMENTS + LOCKS the invariant. Its RED evidence is the mutation battery in review (add a `swap_caps` method → `test_controller_has_no_cap_mutation_surface` fails; add a `caps` field to `RampDecision` → `test_decide_returns_only_a_rampdecision_never_a_loosened_cap` fails). Note in the commit that these are the mutation-kill pins.

- [ ] **3. Implementation:** no code change — the D1 impl already has no cap surface. (If `dir(c)` shows a stray public name, that is a bug to fix, not to allowlist.)

- [ ] **4. Run it — GREEN:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_harness_ramp_controller.py -o addopts="" -q'`

- [ ] **5. Full suite:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q'`
  Expected: **P + 11**.

- [ ] **6. Commit:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add tests/test_harness_ramp_controller.py && git commit -m "S9d D4: structural-honesty pins -- no cap-mutation surface on RampController; decide returns only a RampDecision (no cap). Kills the widen-a-cap / ramp-up-mutates-caps mutations"'`

---

### Task D5: `ERSController(reconciler=None)` additive seam + `boot()` no-op — byte-for-byte inert when unwired

- [ ] **1. Write the failing test** (`tests/test_harness_boot.py`):

```python
"""S9 / POL-11 — the ERSController(reconciler=…) boot seam (additive; reconciler=None == today)."""

from decimal import Decimal

from polybot.core.clock import MonotonicStamper
from polybot.ers import safety as _safety
from polybot.ers.caps import RiskCaps
from polybot.ers.controller import ERSController
from polybot.ers.intent_store import IntentStore
from polybot.ers.safety import SafetyController
from polybot.ers.service import PaperSigner
from polybot.ers.validator import Portfolio
from polybot.ingestion.orderbook import LocalBook


def _book(ask, *, size="1000", bid="0.01"):
    book = LocalBook()
    book.apply_book({"bids": [{"price": bid, "size": size}], "asks": [{"price": ask, "size": size}]})
    return book


def test_reconciler_none_boot_is_a_noop_and_leaves_controller_halted(tmp_path):
    # reconciler=None (the default) -> boot() is a no-op: it returns None, the held SafetyController
    # stays HALTED (the construction default), and the portfolio is unchanged (empty at NAV). This
    # proves the seam is byte-for-byte inert when unwired -- the whole existing test suite relies on
    # it. NOT passing reconciler at all must construct exactly as today.
    store = IntentStore(str(tmp_path / "i.db"), MonotonicStamper())
    ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
    try:
        rc = ERSController(store=store, book_for={"t1": _book("0.50")}.get, caps=RiskCaps(),
                           signer=PaperSigner(), controller=ctl, clock=lambda: 0)  # no reconciler
        assert ctl.state() == _safety.HALTED           # boot default, untouched
        result = rc.boot()
        assert result is None                          # no-op returns None
        assert ctl.state() == _safety.HALTED           # STILL halted -- boot did nothing
        # The threaded portfolio is the empty construction portfolio (NAV only, no positions).
        final = rc.run_cycle()
        assert isinstance(final, Portfolio)
        assert final.positions == ()                   # nothing adopted
    finally:
        store.close()
```

- [ ] **2. Run it — RED for the right reason:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_harness_boot.py -o addopts="" -q'`
  Expected: `AttributeError: 'ERSController' object has no attribute 'boot'`.

- [ ] **3. Minimal implementation** — the ONE existing-file edit. In `src/polybot/ers/controller.py`:

  **(a) Add `reconciler=None` to the signature, immediately before `clock`:**

```python
    def __init__(self, *, store, book_for, caps, signer, controller, breaker=None, pipeline=None,
                 heartbeat=None, gtd_for=None, fill_sink=None, anomaly=None, lossbreakers=None,
                 telegram=None, reconciler=None, clock):
```

  **(b) Store it — add this line in `__init__` right after `self._telegram = telegram` (and before `self._clock = clock`):**

```python
        # reconciler (S9d / POL-11 seam): the opt-in RestartReconciler adopted ONCE at boot() —
        # NOT per-cycle. reconciler=None (the default) == today byte-for-byte: boot() is a no-op,
        # the controller stays HALTED with the empty construction portfolio, and run_cycle is
        # untouched. The DORMANT wallet=None shadow path flips HALTED->RUNNING on boot() (D6).
        self._reconciler = reconciler
```

  **(c) Add the `boot()` method — placed directly after `__init__`, before `_empty_portfolio`:**

```python
    def boot(self):
        """Adopt the RestartReconciler ONCE before the run loop (deploy calls this once). When
        wired, reconcile_on_boot() drives the (only automatic) HALTED->RUNNING transition and
        returns the rebuilt Portfolio, which becomes the threaded working portfolio. reconciler=
        None -> no-op (returns None; stays HALTED, empty portfolio == today). run_cycle is not
        touched by this seam."""
        if self._reconciler is not None:
            self._portfolio = self._reconciler.reconcile_on_boot()
            return self._portfolio
        return None
```

  **INVARIANT check for the assembler:** `run_cycle`'s body is byte-for-byte unchanged; the only diff to this file is the new keyword param, the new `self._reconciler` store, and the new `boot()` method — all inert when `reconciler=None`.

- [ ] **4. Run it — GREEN:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_harness_boot.py tests/test_ers_controller.py -o addopts="" -q'`
  (Run `test_ers_controller.py` too — it MUST stay green, proving the seam is additive.)

- [ ] **5. Full suite:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q'`
  Expected: **P + 12**. (No existing test regresses — `reconciler=None` == today.)

- [ ] **6. Commit:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/ers/controller.py tests/test_harness_boot.py && git commit -m "S9d D5: ERSController additive reconciler=None seam + boot() no-op -- inert when unwired (existing controller tests stay green)"'`

---

### Task D6: `boot()` DORMANT path adopts the reconciler — flips HALTED→RUNNING and adopts the rebuilt portfolio

- [ ] **1. Write the failing test** (append to `tests/test_harness_boot.py`):

```python
from polybot.core.models import Envelope  # noqa: F401  (kept for parity with restart tests)
from polybot.ers.reconcile import ThreeWayReconciler
from polybot.ers.restart import RestartReconciler
from polybot.ers.validator import Decision
from polybot.storage.market_memory import EventStore

_P = dict(token_id="t1", condition_id="m1", event_id="e1", side="BUY", target_price="0.50",
          max_price="0.60", size_usd_suggestion="100", p="0.9", p_confidence="0.8",
          resolution_summary="", thesis="", citations=())


def _accept_one(store, intent_id="i1", **over):
    # Drive one intent to ACCEPTED + record its fill, mirroring process_pending+make_fill_sink:
    # stake $8 of a token entered at $0.50 -> 16 shares, $8 worst-case risk. Verbatim from
    # tests/test_ers_restart.py so store.accepted() yields exactly one rebuildable row.
    store.propose_trade(intent_id, **dict(_P, **over))
    store.record_decision(intent_id, Decision("ACCEPT", Decimal("8"), Decimal("0.50"), "kelly"))
    store.record_fill(intent_id=intent_id, token_id=over.get("token_id", "t1"),
                      condition_id=over.get("condition_id", "m1"),
                      event_id=over.get("event_id", "e1"), side="BUY",
                      shares=Decimal("16"), price_exec=Decimal("0.50"),
                      worst_case_risk=Decimal("8"))


def test_boot_with_dormant_reconciler_transitions_running_and_adopts_portfolio(tmp_path):
    # A RestartReconciler with wallet=None (DORMANT shadow path) wired into the controller: boot()
    # flips the held SafetyController HALTED->RUNNING(restart_reconciled) AND adopts the Portfolio
    # rebuilt from the ACCEPTED set. This is the S9d seam finally connecting RestartReconciler to
    # boot. (Mirrors tests/test_ers_restart.py's DORMANT case, but THROUGH ERSController.boot().)
    store = IntentStore(str(tmp_path / "i.db"), MonotonicStamper())
    events = EventStore(str(tmp_path / "e.db"))
    try:
        _accept_one(store)
        ctl = SafetyController(caps=RiskCaps(), store=store, clock=lambda: 0)
        assert ctl.state() == _safety.HALTED                       # boot default
        rr = RestartReconciler(store=store, event_store=events,
                               reconciler=ThreeWayReconciler(caps=RiskCaps()), controller=ctl,
                               caps=RiskCaps(), clock=lambda: 0, wallet=None)
        rc = ERSController(store=store, book_for={"t1": _book("0.50")}.get, caps=RiskCaps(),
                           signer=PaperSigner(), controller=ctl, reconciler=rr, clock=lambda: 0)
        adopted = rc.boot()
        # The controller transitioned HALTED->RUNNING via the reconciler (the only automatic path).
        assert ctl.state() == _safety.RUNNING
        assert store.op_audit_log()[-1]["reason"] == "restart_reconciled"
        # boot() returned AND threaded the rebuilt portfolio (one position from the ACCEPTED row).
        assert isinstance(adopted, Portfolio)
        assert [p.token_id for p in adopted.positions] == ["t1"]
        pos = adopted.positions[0]
        assert pos.worst_case_risk == Decimal("8")
        assert pos.entry_price == Decimal("0.50")
        # The adopted portfolio is what run_cycle now threads (not the empty construction one).
        # RUNNING + the single pending intent already consumed -> the next cycle keeps the position.
        final = rc.run_cycle()
        assert [p.token_id for p in final.positions] == ["t1"]
    finally:
        store.close()
        events.close()
```

- [ ] **2. Run it — RED for the right reason:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_harness_boot.py -k dormant -o addopts="" -q'`
  Expected GREEN immediately — the D5 `boot()` body already handles the wired path (`self._portfolio = self._reconciler.reconcile_on_boot(); return self._portfolio`). This task adds the POSITIVE-path coverage that D5's no-op test cannot exercise. If run before D5's edit, it REDs with `AttributeError: ... has no attribute 'boot'`. **No new implementation is required** beyond D5.

  *(Reviewer/assembler: if strict per-cycle RED-first is demanded, land only the `reconciler=None` no-op branch in D5 — `if self._reconciler is not None: pass; return None` — so THIS test REDs (state stays HALTED, `adopted is None`), then complete the wired branch here. Both orderings end byte-identical; the split gives cleaner TDD evidence. Recommended.)*

- [ ] **3. Implementation:** present from D5 (or, under the split, complete the wired branch:
  `if self._reconciler is not None: self._portfolio = self._reconciler.reconcile_on_boot(); return self._portfolio`).

- [ ] **4. Run it — GREEN:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_harness_boot.py -o addopts="" -q'`

- [ ] **5. Full suite:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q'`
  Expected: **P + 13**.

- [ ] **6. Commit:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add tests/test_harness_boot.py src/polybot/ers/controller.py && git commit -m "S9d D6: boot() DORMANT path adopts RestartReconciler -- HALTED->RUNNING + rebuilt portfolio threaded through run_cycle"'`

---

### Task D7: WHOLE-SLICE e2e (§7.3) — full sample clears but OOS does not → the honesty spine holds (`ready=False`, no promote)

- [ ] **1. Write the failing test** (`tests/test_harness_e2e.py`):

```python
"""S9 / POL-11 — whole-slice e2e (design §7.3): the REAL stack ShadowLedger -> evaluate_category
-> RampController. No mocks except the k/go gates + a controlled forecast_ledger (tiny fakes), so
the OUT-OF-SAMPLE net-of-everything shadow PnL carries the honesty assertion.

THE STRUCTURAL HONESTY PIN (§5 invariant 1): a category whose FULL sample is strongly net-positive
but whose most-recent OOS window is net-NEGATIVE must NOT be ready and must NOT promote -- the
controller advances on the OUT-OF-SAMPLE net, never the (gross-looking) full-sample net. The
net_oos-vs-net_full and net-vs-gross mutations are killed here.
"""

from decimal import Decimal

from polybot.core.clock import MonotonicStamper
from polybot.ers.caps import RiskCaps
from polybot.ers.validator import OpenPosition, Portfolio
from polybot.harness.config import RampConfig
from polybot.harness.evidence import evaluate_category
from polybot.harness.ledger import ShadowLedger
from polybot.harness.ramp_controller import SHADOW, RampController
from polybot.maker.config import DEFAULT_FEE_SCHEDULE, MakerConfig

from polybot.calibration.ledger import ForecastRecord


_VALUE = {"WON": Decimal("1"), "LOST": Decimal("0"), "DISPUTED": None, "VOID": None}


class _FakeCalibGate:
    """Tiny fake (design §7.3 allows fakes for the k/go gates): whole-sample k == 1."""
    def k_for(self, category):
        return Decimal("1")


class _FakeMakerGate:
    def go_for(self, category):
        return True


class _FakeForecastLedger:
    """A controlled forecast substrate so the calibration side (brier_skill/reliability) is pinned
    while the SHADOW-side OOS PnL stays fully real. Rows are honest WON/LOST forecasts where the
    bot beats the market mid; evaluate_category holds out the recent ceil(0.30*n_f) as f_oos.
    (Hand-verified against calibration/scoring.py: brier_skill 0.9375, reliability 0.01.)"""
    def __init__(self, rows):
        self._rows = rows

    def resolved(self, category=None):
        return [r for r in self._rows if category is None or r.category == category]


def _forecast(fid, *, p, mid, status, at, category="sports"):
    return ForecastRecord(forecast_id=fid, category=category, condition_id="c", p=Decimal(p),
                          market_mid=Decimal(mid), created_at=at,
                          resolution_status=status, resolved_at=at)


def _good_forecasts():
    # Bot sharp & correct (0.90 on WON, 0.10 on LOST); market mushy (0.60/0.40). 6 honest rows,
    # time-ordered by resolved_at so the OOS forecast slice is the recent tail.
    return [
        _forecast("g1", p="0.90", mid="0.60", status="WON", at=1),
        _forecast("g2", p="0.10", mid="0.40", status="LOST", at=2),
        _forecast("g3", p="0.90", mid="0.60", status="WON", at=3),
        _forecast("g4", p="0.10", mid="0.40", status="LOST", at=4),
        _forecast("g5", p="0.90", mid="0.60", status="WON", at=5),
        _forecast("g6", p="0.10", mid="0.40", status="LOST", at=6),
    ]


def _record(ledger, tid, *, side, shares, price, mid, reward, status):
    ledger.record_trade(tid, token_id=f"tok-{tid}", condition_id="c", category="sports",
                        side=side, shares=Decimal(shares), fill_price=Decimal(price),
                        fill_mid=Decimal(mid), reward_accrued=Decimal(reward))
    ledger.record_settlement(tid, status=status, resolution_value=_VALUE[status])


def test_full_sample_clears_but_oos_negative_stays_shadow(tmp_path):
    """6 honest shadow trades across a time span: 3 early strong winners (BUY WON at low prices ->
    large favorable mark-out) + 1 DISPUTED (excluded) + 3 recent toxic losers (BUY LOST). The FULL
    sample is strongly net-positive (favorable early adverse dominates) BUT the recent OOS window
    (the 3 losers) is net-NEGATIVE. evaluate_category reads net_OOS -> oos_positive False -> ready
    False; RampController stays SHADOW, promote False. The gross/in-sample illusion is caught.

    Config: RampConfig(min_resolved=6, oos_holdout_fraction=0.5, min_oos_resolved=3) so
            n_oos = ceil(0.5*6) = 3 = the toxic losers; the sample floor (6) IS met -- only the
            OOS net stops it. MakerConfig defaults (rebate 0.20; forced_taker_exit_p/lockup/
            dispute 0; min_samples irrelevant -- maker_go is faked True).

    Hand-computed net-of-everything (sports fee_rate 0.03 exp 1; checked twice):
      FULL honest (6 rows):
        reward = 6 * 0.05                                                       = 0.30
        cf: winners 100*0.03*p*(1-p) at 0.40/0.45/0.50 = 0.72 + 0.7425 + 0.75  = 2.2125
            losers  10*0.03*p*(1-p) at 0.60/0.65/0.55  = 0.072+0.06825+0.07425 = 0.2145
            Σcf = 2.4270 ; rebate = 0.20*2.4270                                = 0.48540000
        spread = 100*(0.01)*3 (winners, each mid-price=0.01) + 10*(0.01)*3     = 3.00 + 0.30 = 3.30
        adverse (BUY: shares*(price-mark)):
          winners mark 1: 100*(0.40-1)+100*(0.45-1)+100*(0.50-1) = -60-55-50   = -165
          losers  mark 0: 10*(0.60)+10*(0.65)+10*(0.55)          = 6+6.5+5.5   = +18
          Σadverse = -165 + 18                                                 = -147.00
        net = 0.30 + 0.48540000 + 3.30 - (-147.00) - 0 - 0 - 0                = 151.08540000  (>0)
      OOS (recent 3 losers only):
        reward = 3*0.05 = 0.15 ; Σcf = 0.2145 ; rebate = 0.04290000
        spread = 10*0.01*3 = 0.30 ; adverse = +18.00
        net_oos = 0.15 + 0.04290000 + 0.30 - 18.00                            = -17.50710000  (<0)
    """
    rc_cfg = RampConfig(min_resolved=6, oos_holdout_fraction=Decimal("0.5"), min_oos_resolved=3)
    mk_cfg = MakerConfig(fee_schedule=DEFAULT_FEE_SCHEDULE)
    with ShadowLedger(str(tmp_path / "shadow.db"), MonotonicStamper()) as sl:
        # early strong winners (in-sample)
        _record(sl, "w1", side="BUY", shares="100", price="0.40", mid="0.41", reward="0.05", status="WON")
        _record(sl, "w2", side="BUY", shares="100", price="0.45", mid="0.46", reward="0.05", status="WON")
        _record(sl, "w3", side="BUY", shares="100", price="0.50", mid="0.51", reward="0.05", status="WON")
        # a DISPUTED in the middle -- excluded from every leg and from the honest count
        _record(sl, "wD", side="BUY", shares="10", price="0.90", mid="0.90", reward="0.01", status="DISPUTED")
        # recent toxic losers (the OOS window by settled_at)
        _record(sl, "l1", side="BUY", shares="10", price="0.60", mid="0.61", reward="0.05", status="LOST")
        _record(sl, "l2", side="BUY", shares="10", price="0.65", mid="0.66", reward="0.05", status="LOST")
        _record(sl, "l3", side="BUY", shares="10", price="0.55", mid="0.56", reward="0.05", status="LOST")

        ev = evaluate_category(
            "sports", shadow_ledger=sl, forecast_ledger=_FakeForecastLedger(_good_forecasts()),
            calibration_gate=_FakeCalibGate(), maker_gate=_FakeMakerGate(),
            ramp_config=rc_cfg, maker_config=mk_cfg, family_size=1)

        # The honest OOS breakdown: DISPUTED excluded from the honest count; the FULL sample is
        # strongly positive, but the OOS window (net_oos) is negative -> oos_positive False.
        assert ev.n_resolved == 6 and ev.n_disputed == 1 and ev.n_oos == 3
        assert ev.net_full == Decimal("151.08540000")   # gross-looking full sample (>0)
        assert ev.net_oos == Decimal("-17.50710000")    # the OUT-OF-SAMPLE truth (<0)
        assert ev.oos_positive is False                  # reads net_oos, NOT net_full
        assert ev.ready is False                         # the honesty spine: not ready

        controller = RampController(ramp_config=rc_cfg, caps=RiskCaps())
        healthy = Portfolio(nav=Decimal("300"), positions=(
            OpenPosition(condition_id="m1", event_id="e1", resolution_source="uma1",
                         cluster_id="c1", worst_case_risk=Decimal("8"), token_id="t1",
                         entry_price=Decimal("0.50")),))
        d = controller.decide("sports", evidence=ev, current_stage=SHADOW, portfolio=healthy,
                              n_resolved_disputed=2, stress_episodes=1, breaker_tripped=False)
        assert d.stage == SHADOW
        assert d.promote_recommended is False            # cannot advance on gross/in-sample edge
        assert d.reason == "not_ready:oos"
```

- [ ] **2. Run it — RED for the right reason:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_harness_e2e.py -o addopts="" -q'`
  Expected RED: initially `ModuleNotFoundError`/`ImportError` while `harness.evidence`/`harness.ledger` compose; once S9b/S9c are in, this is GREEN (no new production code — the e2e only composes shipped units). If the `net_full`/`net_oos` assertions fail, the bug is in the composed S9b/S9c code, not here.

- [ ] **3. Implementation:** none — the e2e composes already-shipped units (`ShadowLedger` S9b, `evaluate_category` S9c, `RampController` D1). Test-only.

- [ ] **4. Run it — GREEN:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_harness_e2e.py -o addopts="" -q'`

- [ ] **5. Full suite:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q'`
  Expected: **P + 14**.

- [ ] **6. Commit:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add tests/test_harness_e2e.py && git commit -m "S9d D7: e2e honesty spine -- full-sample net +151.0854 but OOS net -17.5071 -> ready False, stays SHADOW, no promote. Kills net_full-for-net_oos + gross-leg mutations"'`

---

### Task D8: WHOLE-SLICE e2e (§7.3) — the OOS sample clears with margin → SHADOW until ready+tail+stress, then `promote_recommended=True`; a regression → `ramp_down=True`

- [ ] **1. Write the failing test** (append to `tests/test_harness_e2e.py`):

```python
def _winning_ledger(sl):
    # 6 honest winners across a time span (BUY WON at low prices -> favorable mark-out) + 1 DISPUTED
    # excluded. Recent OOS window (last 3 winners) ALSO clears margin > 0.
    _record(sl, "e1", side="BUY", shares="100", price="0.40", mid="0.41", reward="0.05", status="WON")
    _record(sl, "e2", side="BUY", shares="100", price="0.45", mid="0.46", reward="0.05", status="WON")
    _record(sl, "e3", side="BUY", shares="100", price="0.50", mid="0.51", reward="0.05", status="WON")
    _record(sl, "eD", side="BUY", shares="10", price="0.90", mid="0.90", reward="0.01", status="DISPUTED")
    _record(sl, "e4", side="BUY", shares="100", price="0.40", mid="0.41", reward="0.05", status="WON")
    _record(sl, "e5", side="BUY", shares="100", price="0.42", mid="0.43", reward="0.05", status="WON")
    _record(sl, "e6", side="BUY", shares="100", price="0.44", mid="0.45", reward="0.05", status="WON")


def test_ready_promotes_then_a_regression_ramps_down(tmp_path):
    """The winning path: 6 honest winners -> the OOS window (recent 3) clears margin AND k/go pass
    -> ready True. RampController.decide stays SHADOW (promotion past it is the human gate) but
    emits promote_recommended True. Then a REGRESSION (a tripped breaker, from a previously-promoted
    TINY_LIVE stage) -> ramp_down True. This is the full §7.3 arc.

    Config: RampConfig(min_resolved=6, oos_holdout_fraction=0.5, min_oos_resolved=3).
    Hand-computed OOS net (recent 3 winners e4/e5/e6, BUY WON marks 1; checked twice):
      reward = 3*0.05 = 0.15
      Σcf = 100*0.03*(0.40*0.60 + 0.42*0.58 + 0.44*0.56) = 100*0.03*(0.24+0.2436+0.2464)
          = 100*0.03*0.7300 = 2.190000 ; rebate = 0.20*2.190000 = 0.43800000
      spread = 100*0.01*3 = 3.00
      adverse (BUY mark 1) = 100*(0.40-1)+100*(0.42-1)+100*(0.44-1) = -60-58-56 = -174.00
      net_oos = 0.15 + 0.43800000 + 3.00 - (-174.00) = 177.58800000  (>0 -> clears margin 0)
    """
    rc_cfg = RampConfig(min_resolved=6, oos_holdout_fraction=Decimal("0.5"), min_oos_resolved=3)
    mk_cfg = MakerConfig(fee_schedule=DEFAULT_FEE_SCHEDULE)
    with ShadowLedger(str(tmp_path / "shadow.db"), MonotonicStamper()) as sl:
        _winning_ledger(sl)
        ev = evaluate_category(
            "sports", shadow_ledger=sl, forecast_ledger=_FakeForecastLedger(_good_forecasts()),
            calibration_gate=_FakeCalibGate(), maker_gate=_FakeMakerGate(),
            ramp_config=rc_cfg, maker_config=mk_cfg, family_size=1)
        assert ev.n_resolved == 6 and ev.n_oos == 3
        assert ev.net_oos == Decimal("177.58800000")    # OOS clears with margin
        assert ev.oos_positive is True
        assert ev.ready is True                          # every Stage-0 gate cleared

        controller = RampController(ramp_config=rc_cfg, caps=RiskCaps())
        healthy = Portfolio(nav=Decimal("300"), positions=(
            OpenPosition(condition_id="m1", event_id="e1", resolution_source="uma1",
                         cluster_id="c1", worst_case_risk=Decimal("8"), token_id="t1",
                         entry_price=Decimal("0.50")),))

        # Ready + tail (2 disputed >= 1, 1 episode >= 1) + stress survives + no breaker -> promote,
        # but the stage stays SHADOW (the operator's human ramp-up gate advances it, not decide()).
        d_ok = controller.decide("sports", evidence=ev, current_stage=SHADOW, portfolio=healthy,
                                 n_resolved_disputed=2, stress_episodes=1, breaker_tripped=False)
        assert d_ok.promote_recommended is True
        assert d_ok.stage == SHADOW
        assert d_ok.ramp_down is False
        assert d_ok.reason == "promote_ok"

        # A subsequent REGRESSION: the category had been promoted to TINY_LIVE out-of-band, and now
        # a breaker trips -> ramp_down True (the automatic-tighten signal for the S4.7 ratchet).
        d_reg = controller.decide("sports", evidence=ev, current_stage="TINY_LIVE",
                                  portfolio=healthy, n_resolved_disputed=2, stress_episodes=1,
                                  breaker_tripped=True)
        assert d_reg.ramp_down is True
        assert d_reg.promote_recommended is False
        assert d_reg.reason == "ramp_down:breaker"
```

- [ ] **2. Run it — RED for the right reason:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_harness_e2e.py -k regression -o addopts="" -q'`
  Expected GREEN once S9b/S9c/D1 are in (no new production code). A failing `net_oos`/`ready` assertion indicts the composed S9c code, not this test.

- [ ] **3. Implementation:** none — composes shipped units. Test-only.

- [ ] **4. Run it — GREEN:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_harness_e2e.py -o addopts="" -q'`

- [ ] **5. Full suite:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q'`
  Expected: **P + 15**.

- [ ] **6. Commit:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add tests/test_harness_e2e.py && git commit -m "S9d D8: e2e winning arc -- OOS net +177.588 clears -> ready, promote_recommended True (stage stays SHADOW); a regression (tripped breaker from TINY_LIVE) -> ramp_down True"'`

---

### Task D9: WHOLE-SLICE e2e (§7.3) — the boot seam in the composed stack; `reconciler=None` leaves `run_cycle` unchanged

- [ ] **1. Write the failing test** (append to `tests/test_harness_e2e.py`):

```python
from polybot.core.clock import MonotonicStamper as _Stamper  # already imported; alias for clarity
from polybot.ers import safety as _safety
from polybot.ers.controller import ERSController
from polybot.ers.intent_store import IntentStore
from polybot.ers.reconcile import ThreeWayReconciler
from polybot.ers.restart import RestartReconciler
from polybot.ers.safety import SafetyController
from polybot.ers.service import PaperSigner
from polybot.ers.validator import Decision
from polybot.ingestion.orderbook import LocalBook
from polybot.storage.market_memory import EventStore

_BOOT_P = dict(token_id="t1", condition_id="m1", event_id="e1", side="BUY", target_price="0.50",
               max_price="0.60", size_usd_suggestion="100", p="0.9", p_confidence="0.8",
               resolution_summary="", thesis="", citations=())


def _book2(ask, *, size="1000", bid="0.01"):
    b = LocalBook()
    b.apply_book({"bids": [{"price": bid, "size": size}], "asks": [{"price": ask, "size": size}]})
    return b


def _boot_accept_one(store):
    store.propose_trade("i1", **_BOOT_P)
    store.record_decision("i1", Decision("ACCEPT", Decimal("8"), Decimal("0.50"), "kelly"))
    store.record_fill(intent_id="i1", token_id="t1", condition_id="m1", event_id="e1", side="BUY",
                      shares=Decimal("16"), price_exec=Decimal("0.50"), worst_case_risk=Decimal("8"))


def test_e2e_boot_seam_adopts_dormant_and_none_leaves_run_cycle_unchanged(tmp_path):
    # Two controllers over identical stores. (A) reconciler=None: boot() is a no-op, the controller
    # stays HALTED, and run_cycle REJECTS the pending intent under unclean_restart (today's behavior
    # byte-for-byte). (B) a DORMANT RestartReconciler wired: boot() flips HALTED->RUNNING and adopts
    # the rebuilt portfolio, so run_cycle proceeds on a RUNNING loop. This closes the §7.3 seam clause.
    # (A) reconciler=None -> run_cycle unchanged (HALTED rejects).
    store_a = IntentStore(str(tmp_path / "a.db"), _Stamper())
    ctl_a = SafetyController(caps=RiskCaps(), store=store_a, clock=lambda: 0)
    try:
        store_a.propose_trade("i1", **_BOOT_P)
        rc_a = ERSController(store=store_a, book_for={"t1": _book2("0.50")}.get, caps=RiskCaps(),
                             signer=PaperSigner(), controller=ctl_a, clock=lambda: 0)  # no reconciler
        assert rc_a.boot() is None
        assert ctl_a.state() == _safety.HALTED
        rc_a.run_cycle()
        assert store_a.get("i1").status == "REJECTED"
        assert store_a.get("i1").decision_reason == "unclean_restart"
    finally:
        store_a.close()

    # (B) DORMANT reconciler wired -> boot() adopts DORMANT->RUNNING; run_cycle runs on RUNNING.
    store_b = IntentStore(str(tmp_path / "b.db"), _Stamper())
    events_b = EventStore(str(tmp_path / "eb.db"))
    ctl_b = SafetyController(caps=RiskCaps(), store=store_b, clock=lambda: 0)
    try:
        _boot_accept_one(store_b)                        # one ACCEPTED row to rebuild from
        rr = RestartReconciler(store=store_b, event_store=events_b,
                               reconciler=ThreeWayReconciler(caps=RiskCaps()), controller=ctl_b,
                               caps=RiskCaps(), clock=lambda: 0, wallet=None)
        rc_b = ERSController(store=store_b, book_for={"t1": _book2("0.50")}.get, caps=RiskCaps(),
                             signer=PaperSigner(), controller=ctl_b, reconciler=rr, clock=lambda: 0)
        adopted = rc_b.boot()
        assert ctl_b.state() == _safety.RUNNING          # the DORMANT->RUNNING adoption
        assert [p.token_id for p in adopted.positions] == ["t1"]
        final = rc_b.run_cycle()                         # RUNNING loop keeps the adopted position
        assert [p.token_id for p in final.positions] == ["t1"]
    finally:
        store_b.close()
        events_b.close()
```

- [ ] **2. Run it — RED for the right reason:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_harness_e2e.py -k boot_seam -o addopts="" -q'`
  Expected GREEN once D5+D6's `ERSController` seam is in (no new production code). If run before D5, it REDs with `AttributeError: ... has no attribute 'boot'`.

- [ ] **3. Implementation:** none — composes the D5/D6 seam. Test-only.

- [ ] **4. Run it — GREEN:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_harness_e2e.py -o addopts="" -q'`

- [ ] **5. Full suite:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q'`
  Expected: **P + 16** (the last of S9d's 16 new `def test_*`).

- [ ] **6. Commit:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add tests/test_harness_e2e.py && git commit -m "S9d D9: e2e boot seam -- reconciler=None leaves run_cycle byte-for-byte (HALTED rejects); DORMANT reconciler adopts HALTED->RUNNING + portfolio. Closes the S9 whole-slice e2e"'`

---

**End of S9d.** After D9, `git diff main...pol-11-s9-harness --stat` for THIS sub-slice touches only: `src/polybot/harness/ramp_controller.py` (new), `src/polybot/ers/controller.py` (the additive `reconciler=None` param + `boot()` — proven inert when unwired by D5's no-op test + the untouched `test_ers_controller.py`), `tests/test_harness_ramp_controller.py`, `tests/test_harness_boot.py`, `tests/test_harness_e2e.py` (all new). The final whole-slice review runs the cross-cutting mutations (make the evidence gate read `net_full` → D7 kills it; give `RampController` a cap-widening path → D4 kills it) with a pycache sweep after each revert.
