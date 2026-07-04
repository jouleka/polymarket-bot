# S8 / POL-10 — Maker-Rewards Module Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `src/polybot/maker/` — the honest net-of-adverse-selection maker-economics shadow module (ledger → pure Decimal calculators → binary GO/NO-GO gate → facade + quote-policy) per the operator-approved `docs/DESIGN-S8-MAKER.md`.

**Architecture:** A NEW self-contained shadow-analytics package mirroring `calibration/`'s shape, purely ADDITIVE (zero change to any existing file; nothing imports it yet — S9 consumes it later). The central identity, honest by construction: `net = reward + rebate + spread_capture − adverse_selection − fees − lockup_cost − dispute_haircut`; the gate reads `MakerNetPnL.net` ONLY, never a gross leg. Four serial sub-slices: S8a config+fees → S8b inventory+reward → S8c netpnl+ledger → S8d quote-policy+gate+e2e.

**Tech Stack:** Python 3.13, exact `Decimal` from strings throughout, frozen dataclasses with self-verifying `__post_init__`, append-only SQLite (WAL) mirroring `ForecastLedger`, pytest.

---

## Execution notes (read before ANY task)

- **Repo:** WSL Ubuntu `/home/jurgenubuntu/projects/polymarket-bot`, branch `pol-10-s8-maker`. Run commands as `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && <cmd>'`; edit files via UNC `\\wsl.localhost\Ubuntu\home\jurgenubuntu\projects\polymarket-bot\...` (retry on transient EISDIR).
- **Strict TDD:** write the failing test, RUN it, OBSERVE the RED and confirm it fails for the stated reason, then the minimal implementation, then GREEN, then the full suite, then commit. One commit per cycle; message style `S8a A3: <what> -- <detail>`; **NO Co-Authored-By trailer**.
- **Never pipe pytest through tail/head** (mangles the exit code under `bash -lc`). Force the summary with `-o addopts=""`; trust the `NNN passed` line + exit 0.
- **Purely additive invariant (design §5.7):** `git status`/`git diff` may only ever touch `src/polybot/maker/*` and `tests/test_maker_*.py`. Any diff outside those paths is a defect — stop.
- **Import discipline:** `maker/` source modules import only stdlib (`dataclasses`, `decimal`, `sqlite3`) and each other. `polybot.core.clock.MonotonicStamper` is allowed in TESTS only (the ledger stamper, mirroring `tests/test_calibration_ledger.py`). Nothing from `polybot.ers`/`polybot.detectors`/`polybot.calibration` anywhere in `maker/`.
- **Decimal doctrine:** every literal `Decimal("...")` from a string; `is_finite()` checks BEFORE any comparison (a `Decimal("NaN")` comparison raises `InvalidOperation`); fail LOUD (`ValueError`, message `f"<field> must be <constraint>, got {value}"`) in constructors/pure functions; fail CLOSED (the conservative action) in the mark + quote-policy paths, exactly as each task pins.
- **Integration pins (D8/D9):** the e2e tasks are expected GREEN on first run (they compose already-built units). If one goes RED, do NOT bend the test — stop and re-derive the hand-computed expectation against the pinned leg derivations below; a genuine mismatch is a defect in a prior slice.

### Pinned leg derivations (MakerTracker.report_for — S8c/S8d share these; also design §3)

Over `ledger.settled(category)`: honest sample = WON/LOST rows; DISPUTED/VOID counted in `n_disputed`/`n_void` and EXCLUDED from every leg; any other status → `ValueError` (fail loud). Cold (`n_settled == 0`) → every leg `None`, `go=False`. For honest rows (`notional_i = shares_i·price_exec_i`, `cf_i = taker_fee(category_i, price_exec_i, shares_i, schedule=config.fee_schedule)`):

| Leg | Derivation |
|---|---|
| `reward` | Σ `reward_accrued_i` (accrued at fill time) |
| `rebate` | `rebate(Σ cf_i, fraction=config.rebate_fraction)` |
| `spread_capture` | Σ `sgn(side_i)·shares_i·(fill_mid_i − price_exec_i)` |
| `adverse_selection` | `inventory.adverse_selection(fills, mark_for)` with `mark_for` = the settled rows' `resolution_value` by `token_id` |
| `fees` | `config.forced_taker_exit_p · Σ cf_i` (the DECISIONS-S0 forced-taker-exit hurdle) |
| `lockup_cost` | `config.lockup_rate · Σ notional_i` |
| `dispute_haircut` | `config.dispute_p · Σ notional_i` |
| `net` | via `net_pnl(...)`; `go = n_settled ≥ config.min_samples AND net > config.net_margin_min` (strict `>`) |

### Expected full-suite count after each task (baseline 853; total after S8 = **990**)

| S8a | A1 858 | A2 866 | A3 874 | A4 879 | A5 883 | A6 888 | A7 892 |
|---|---|---|---|---|---|---|---|
| **S8b** | B1 902 | B2 906 | B3 912 | B4 918 | B5 927 | B6 935 | |
| **S8c** | C1 940 | C2 943 | C3 945 | C4 950 | C5 954 | C6 958 | |
| **S8d** | D1 960 | D2 966 | D3 974 | D4 976 | D5 980 | D6 984 | D7 987 · D8 988 · D9 990 |

Where a task body says "all prior + N new pass", this table is the exact number.

### Review cadence (not part of the task checkboxes — the orchestrator runs these between sub-slices)

After each sub-slice: (1) a spec-compliance review (read + RUN, check the untouched-files invariant, no over/under-build), then (2) a pinned `model: opus` code-review with a mutation battery on the correctness-critical tests (break the impl, confirm the named test fails). After any mutation pass: verify `git status --porcelain` is empty, `grep -rn MUTATION src/` returns nothing, and sweep `find src -name __pycache__ -exec rm -rf {} +`. Re-review after fixing any finding. Final whole-slice review before merge (suggested cross-cutting mutations: make the gate read a gross leg instead of `.net`; drop the DISPUTED exclusion).

---
## Sub-slice S8a — Config + fees

Creates the `polybot.maker` package, the self-verifying `MakerConfig` + `FeeCategory` +
`DEFAULT_FEE_SCHEDULE` (design Fork 3: parameterized, dossier-corrected, re-pullable at deploy),
and the pure-Decimal fee model `taker_fee`/`rebate`. Purely additive — no existing file changes.
Baseline before A1: **853 passed**. All commands run from Windows as
`wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && <cmd>'` on branch `pol-10-s8-maker`.

A note the reviewers should hold us to: the contract pins geopolitics as FREE but does not pin its
`fee_rate`/`active` fields. We represent it as `active=True, free=True` with the same conservative
`0.03/1` shape so the `free` FLAG (not a zero rate) is the thing that zeroes the fee — the flag is
load-bearing and separately tested (A5 `free` wins over `active`).

### Task A1: package + FeeCategory + DEFAULT_FEE_SCHEDULE + a valid MakerConfig constructs (defaults, frozen)

- [ ] **1. Write the failing test** — create the docstring-only package `__init__.py` alongside the first test file (so the RED is for `polybot.maker.config`, not the package).

Create `src/polybot/maker/__init__.py` (complete file):

```python
"""Maker-rewards shadow analytics (S8 / POL-10).

Honest net-of-adverse-selection maker accounting: append-only shadow ledger -> pure
exact-Decimal cost/PnL calculators -> binary GO/NO-GO gate, all data-gated dormant.
Never reward-gross: the only number the gate reads is the net after ALL cost legs.
Purely additive — imports nothing from ers/detectors/calibration at module load.
"""
```

Create `tests/test_maker_config.py` (complete file):

```python
"""S8 / POL-10 — MakerConfig + FeeCategory + DEFAULT_FEE_SCHEDULE (self-verifying maker knobs)."""

import dataclasses
from decimal import Decimal

import pytest

from polybot.maker.config import DEFAULT_FEE_SCHEDULE, FeeCategory, MakerConfig


def _config(**overrides):
    return MakerConfig(fee_schedule=DEFAULT_FEE_SCHEDULE, **overrides)


def test_default_fee_schedule_is_the_documented_envelope():
    # Design Fork 3: sports ACTIVE (0.03, exp 1); the seven planned categories INACTIVE
    # (same conservative shape, fee 0 until Polymarket activates them); geopolitics FREE
    # by flag. All of it is a re-pullable deploy seam, never a trusted constant.
    names = [c.name for c in DEFAULT_FEE_SCHEDULE]
    assert names == [
        "sports", "politics", "finance", "tech", "econ",
        "culture", "weather", "crypto", "geopolitics",
    ]
    by_name = {c.name: c for c in DEFAULT_FEE_SCHEDULE}
    sports = by_name["sports"]
    assert sports.active is True and sports.free is False
    assert sports.fee_rate == Decimal("0.03") and sports.exponent == Decimal("1")
    geo = by_name["geopolitics"]
    assert geo.free is True  # free by FLAG — the rate field is irrelevant to its fee
    for planned in ("politics", "finance", "tech", "econ", "culture", "weather", "crypto"):
        entry = by_name[planned]
        assert entry.active is False and entry.free is False
        assert entry.fee_rate == Decimal("0.03") and entry.exponent == Decimal("1")


def test_maker_config_defaults_are_the_documented_envelope():
    c = _config()
    assert c.fee_schedule is DEFAULT_FEE_SCHEDULE
    assert c.rebate_fraction == Decimal("0.20")
    assert c.reward_b == Decimal("1")
    assert c.max_spread == Decimal("0.03")
    assert c.min_samples == 150
    assert c.net_margin_min == Decimal("0")
    assert c.lockup_rate == Decimal("0")
    assert c.forced_taker_exit_p == Decimal("0")
    assert c.dispute_p == Decimal("0")


def test_fee_schedule_is_required():
    # No default: a config without an explicit schedule is a construction error, not a guess.
    with pytest.raises(TypeError):
        MakerConfig()


def test_maker_config_is_frozen():
    c = _config()
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.rebate_fraction = Decimal("0.3")


def test_fee_category_is_frozen():
    entry = DEFAULT_FEE_SCHEDULE[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        entry.fee_rate = Decimal("0.05")
```

- [ ] **2. Run it — RED for the right reason:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_maker_config.py -o addopts="" -q'`
  → collection error: `ModuleNotFoundError: No module named 'polybot.maker.config'` (the package `__init__.py` exists; `config.py` does not).

- [ ] **3. Minimal implementation** — create `src/polybot/maker/config.py` (complete file; `_verify` is a documented no-op stub — its knob/schedule checks are pinned RED-first by A2/A3):

```python
"""Maker knobs + the parameterized fee schedule (S8 / POL-10), self-verifying at construction.

Several knobs gate live money downstream (the GO floor, the net margin the gate demands, the
rebate fraction in the net identity), so the config verifies its own envelope at construction
and fails LOUD on nonsense — the CalibrationConfig/DetectorConfig discipline. The fee schedule
is a conservative RE-PULLABLE seam (design Fork 3): the real live numbers are
documented-UNSPECIFIED and must be re-pulled at deploy; nothing here is a trusted constant.
"""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class FeeCategory:
    name: str
    fee_rate: Decimal
    exponent: Decimal
    active: bool  # False = planned/inactive -> taker_fee 0 until Polymarket activates it
    free: bool    # True = fee-free category -> taker_fee 0 regardless of rate (wins over active)


# Conservative re-pullable defaults: sports is the one ACTIVE fee category (the dossier
# correction: fee_rate 0.03, exponent 1); the other trading categories are planned-INACTIVE
# (same shape, fee 0 until activated); geopolitics is FREE by flag (actively traded, no fee —
# the flag, not a zero rate, is what zeroes it).
DEFAULT_FEE_SCHEDULE: tuple[FeeCategory, ...] = (
    FeeCategory(name="sports", fee_rate=Decimal("0.03"), exponent=Decimal("1"), active=True, free=False),
    FeeCategory(name="politics", fee_rate=Decimal("0.03"), exponent=Decimal("1"), active=False, free=False),
    FeeCategory(name="finance", fee_rate=Decimal("0.03"), exponent=Decimal("1"), active=False, free=False),
    FeeCategory(name="tech", fee_rate=Decimal("0.03"), exponent=Decimal("1"), active=False, free=False),
    FeeCategory(name="econ", fee_rate=Decimal("0.03"), exponent=Decimal("1"), active=False, free=False),
    FeeCategory(name="culture", fee_rate=Decimal("0.03"), exponent=Decimal("1"), active=False, free=False),
    FeeCategory(name="weather", fee_rate=Decimal("0.03"), exponent=Decimal("1"), active=False, free=False),
    FeeCategory(name="crypto", fee_rate=Decimal("0.03"), exponent=Decimal("1"), active=False, free=False),
    FeeCategory(name="geopolitics", fee_rate=Decimal("0.03"), exponent=Decimal("1"), active=True, free=True),
)


@dataclass(frozen=True)
class MakerConfig:
    fee_schedule: tuple                          # tuple[FeeCategory, ...] — REQUIRED, no default
    rebate_fraction: Decimal = Decimal("0.20")   # maker share of taker fees; (0, 0.5]
    reward_b: Decimal = Decimal("1")             # S(v,s) pool constant; > 0 (deploy-calibrated)
    max_spread: Decimal = Decimal("0.03")        # reward eligibility (rest within this of mid); (0, 1)
    min_samples: int = 150                       # GO floor per category; > 0 (mirrors calibration min_n)
    net_margin_min: Decimal = Decimal("0")       # net must EXCEED this to GO; >= 0
    lockup_rate: Decimal = Decimal("0")          # locked-to-resolution opportunity-cost rate; >= 0
    forced_taker_exit_p: Decimal = Decimal("0")  # P(forced taker exit) in the fee hurdle; [0, 1]
    dispute_p: Decimal = Decimal("0")            # ex-ante P(dispute/void) for the haircut leg; [0, 1]

    def __post_init__(self):
        self._verify()

    def _verify(self):
        # Knob-envelope checks pinned RED-first by the A2 cycle; schedule checks by A3.
        pass
```

- [ ] **4. Run it — GREEN:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_maker_config.py -o addopts="" -q'` → `5 passed`.

- [ ] **5. Full suite:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q'` → **858 passed** (853 + 5).

- [ ] **6. Commit:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/maker/__init__.py src/polybot/maker/config.py tests/test_maker_config.py && git commit -m "S8a A1: maker package + FeeCategory/DEFAULT_FEE_SCHEDULE/MakerConfig construct -- frozen defaults envelope pinned"'`

### Task A2: MakerConfig._verify rejects every out-of-range knob

- [ ] **1. Write the failing test** — append to `tests/test_maker_config.py`:

```python
def test_rejects_rebate_fraction_out_of_range():
    # (0, 0.5]: 0 silently disables the rebate leg; > 0.5 is beyond any documented maker share.
    with pytest.raises(ValueError, match="rebate_fraction"):
        _config(rebate_fraction=Decimal("0"))
    with pytest.raises(ValueError, match="rebate_fraction"):
        _config(rebate_fraction=Decimal("0.6"))


def test_rejects_max_spread_out_of_range():
    # (0, 1): 0 makes nothing reward-eligible; 1 makes EVERYTHING eligible (gate toothless).
    with pytest.raises(ValueError, match="max_spread"):
        _config(max_spread=Decimal("0"))
    with pytest.raises(ValueError, match="max_spread"):
        _config(max_spread=Decimal("1"))


def test_rejects_non_positive_min_samples():
    with pytest.raises(ValueError, match="min_samples"):
        _config(min_samples=0)


def test_rejects_negative_net_margin_min():
    with pytest.raises(ValueError, match="net_margin_min"):
        _config(net_margin_min=Decimal("-0.01"))


def test_rejects_negative_lockup_rate():
    with pytest.raises(ValueError, match="lockup_rate"):
        _config(lockup_rate=Decimal("-0.01"))


def test_rejects_forced_taker_exit_p_out_of_range():
    with pytest.raises(ValueError, match="forced_taker_exit_p"):
        _config(forced_taker_exit_p=Decimal("1.01"))
    with pytest.raises(ValueError, match="forced_taker_exit_p"):
        _config(forced_taker_exit_p=Decimal("-0.01"))


def test_rejects_dispute_p_out_of_range():
    with pytest.raises(ValueError, match="dispute_p"):
        _config(dispute_p=Decimal("1.01"))
    with pytest.raises(ValueError, match="dispute_p"):
        _config(dispute_p=Decimal("-0.01"))


def test_rejects_non_positive_reward_b():
    with pytest.raises(ValueError, match="reward_b"):
        _config(reward_b=Decimal("0"))
    with pytest.raises(ValueError, match="reward_b"):
        _config(reward_b=Decimal("-1"))
```

- [ ] **2. Run it — RED for the right reason:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_maker_config.py -o addopts="" -q'`
  → `8 failed, 5 passed`; every failure is `Failed: DID NOT RAISE <class 'ValueError'>` (the `_verify` stub accepts everything).

- [ ] **3. Minimal implementation** — in `src/polybot/maker/config.py`, replace the `_verify` method (exact replacement; the trailing `_verify_schedule` hook stays a stub for A3):

```python
    def _verify(self):
        if not (Decimal(0) < self.rebate_fraction <= Decimal("0.5")):
            raise ValueError(f"rebate_fraction must be in (0, 0.5], got {self.rebate_fraction}")
        if self.reward_b <= 0:
            raise ValueError(f"reward_b must be > 0, got {self.reward_b}")
        if not (Decimal(0) < self.max_spread < Decimal(1)):
            raise ValueError(f"max_spread must be in (0, 1), got {self.max_spread}")
        if self.min_samples <= 0:
            raise ValueError(f"min_samples must be > 0, got {self.min_samples}")
        if self.net_margin_min < 0:
            raise ValueError(f"net_margin_min must be >= 0, got {self.net_margin_min}")
        if self.lockup_rate < 0:
            raise ValueError(f"lockup_rate must be >= 0, got {self.lockup_rate}")
        if not (Decimal(0) <= self.forced_taker_exit_p <= Decimal(1)):
            raise ValueError(f"forced_taker_exit_p must be in [0, 1], got {self.forced_taker_exit_p}")
        if not (Decimal(0) <= self.dispute_p <= Decimal(1)):
            raise ValueError(f"dispute_p must be in [0, 1], got {self.dispute_p}")
        self._verify_schedule()

    def _verify_schedule(self):
        # Schedule-envelope checks pinned RED-first by the A3 cycle.
        pass
```

- [ ] **4. Run it — GREEN:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_maker_config.py -o addopts="" -q'` → `13 passed`.

- [ ] **5. Full suite:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q'` → **866 passed** (858 + 8).

- [ ] **6. Commit:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/maker/config.py tests/test_maker_config.py && git commit -m "S8a A2: MakerConfig._verify rejects out-of-range knobs -- one loud ValueError per knob, both edges tested"'`

### Task A3: MakerConfig._verify validates the fee schedule itself

- [ ] **1. Write the failing test** — append to `tests/test_maker_config.py`:

```python
def _cat(name="sports", fee_rate="0.03", exponent="1", active=True, free=False):
    return FeeCategory(
        name=name, fee_rate=Decimal(fee_rate), exponent=Decimal(exponent),
        active=active, free=free,
    )


def test_rejects_an_empty_fee_schedule():
    with pytest.raises(ValueError, match="fee_schedule"):
        MakerConfig(fee_schedule=())


def test_rejects_a_non_tuple_fee_schedule():
    # a mutable schedule invites in-place edits behind the frozen config's back.
    with pytest.raises(ValueError, match="fee_schedule"):
        MakerConfig(fee_schedule=[_cat()])


def test_rejects_a_non_feecategory_entry():
    with pytest.raises(ValueError, match="FeeCategory"):
        MakerConfig(fee_schedule=(_cat(), "sports"))


def test_rejects_duplicate_category_names():
    # two entries for one name = ambiguous lookup -> which fee applies is undefined.
    with pytest.raises(ValueError, match="unique"):
        MakerConfig(fee_schedule=(_cat(name="sports"), _cat(name="sports", free=True)))


def test_rejects_an_empty_category_name():
    with pytest.raises(ValueError, match="name"):
        MakerConfig(fee_schedule=(_cat(name=""),))


def test_rejects_a_negative_fee_rate_entry():
    with pytest.raises(ValueError, match="fee_rate"):
        MakerConfig(fee_schedule=(_cat(fee_rate="-0.01"),))


def test_rejects_a_non_finite_fee_rate_entry():
    with pytest.raises(ValueError, match="fee_rate"):
        MakerConfig(fee_schedule=(_cat(fee_rate="NaN"),))


def test_rejects_a_negative_exponent_entry():
    with pytest.raises(ValueError, match="exponent"):
        MakerConfig(fee_schedule=(_cat(exponent="-1"),))
```

- [ ] **2. Run it — RED for the right reason:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_maker_config.py -o addopts="" -q'`
  → `8 failed, 13 passed`; every failure `Failed: DID NOT RAISE <class 'ValueError'>` (the `_verify_schedule` stub accepts any schedule).

- [ ] **3. Minimal implementation** — in `src/polybot/maker/config.py`, replace the `_verify_schedule` method (exact replacement; `is_finite()` is checked BEFORE the sign compare — a Decimal NaN comparison raises `InvalidOperation`, and the non-finite doctrine wants a loud named `ValueError` instead):

```python
    def _verify_schedule(self):
        if not isinstance(self.fee_schedule, tuple) or not self.fee_schedule:
            raise ValueError(f"fee_schedule must be a non-empty tuple, got {self.fee_schedule!r}")
        seen = set()
        for entry in self.fee_schedule:
            if not isinstance(entry, FeeCategory):
                raise ValueError(f"fee_schedule entry must be a FeeCategory, got {entry!r}")
            if not entry.name:
                raise ValueError(f"fee_schedule entry name must be non-empty, got {entry!r}")
            if entry.name in seen:
                raise ValueError(f"fee_schedule entry names must be unique, got duplicate {entry.name!r}")
            seen.add(entry.name)
            if not entry.fee_rate.is_finite() or entry.fee_rate < 0:
                raise ValueError(f"fee_rate must be finite and >= 0, got {entry.fee_rate} for {entry.name!r}")
            if not entry.exponent.is_finite() or entry.exponent < 0:
                raise ValueError(f"exponent must be finite and >= 0, got {entry.exponent} for {entry.name!r}")
```

- [ ] **4. Run it — GREEN:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_maker_config.py -o addopts="" -q'` → `21 passed`.

- [ ] **5. Full suite:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q'` → **874 passed** (866 + 8).

- [ ] **6. Commit:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/maker/config.py tests/test_maker_config.py && git commit -m "S8a A3: MakerConfig._verify validates the fee schedule -- non-empty tuple, FeeCategory entries, unique non-empty names, finite non-negative rate/exponent"'`

### Task A4: taker_fee — the active-category formula, hand-computed

- [ ] **1. Write the failing test** — create `tests/test_maker_fees.py` (complete file):

```python
"""S8 / POL-10 — taker_fee + rebate (the parameterized per-category fee model, pure exact-Decimal)."""

from decimal import Decimal

from polybot.maker.config import DEFAULT_FEE_SCHEDULE, FeeCategory
from polybot.maker.fees import taker_fee


def _fee(p, size="100", category="sports", schedule=DEFAULT_FEE_SCHEDULE):
    return taker_fee(category, Decimal(p), Decimal(size), schedule=schedule)


def test_sports_fee_hand_computed_at_the_peak():
    # 100 shares * 0.03 * 0.5 * (1 - 0.5)**1 = 0.75 — the master design's
    # "$0.75 sports per 100 shares" figure, exact.
    assert _fee("0.5") == Decimal("0.75")


def test_sports_fee_hand_computed_off_peak():
    # 100 * 0.03 * 0.2 * 0.8 = 0.48
    assert _fee("0.2") == Decimal("0.48")


def test_fee_peaks_at_p_half():
    # p*(1-p) is maximal at p = 0.5 and symmetric for exponent 1:
    # both wings are 100 * 0.03 * 0.3 * 0.7 = 0.63 < 0.75.
    assert _fee("0.5") > _fee("0.3")
    assert _fee("0.5") > _fee("0.7")
    assert _fee("0.3") == _fee("0.7") == Decimal("0.63")


def test_fee_is_zero_at_the_p_boundaries():
    # a certainty-priced share generates no fee: p or (1-p) is 0.
    assert _fee("0") == Decimal("0")
    assert _fee("1") == Decimal("0")


def test_exponent_is_applied_via_decimal_power():
    quadratic = (
        FeeCategory(name="sports", fee_rate=Decimal("0.03"), exponent=Decimal("2"),
                    active=True, free=False),
    )
    # 100 * 0.03 * 0.5 * (0.5)**2 = 0.375
    assert _fee("0.5", schedule=quadratic) == Decimal("0.375")
```

- [ ] **2. Run it — RED for the right reason:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_maker_fees.py -o addopts="" -q'`
  → collection error: `ModuleNotFoundError: No module named 'polybot.maker.fees'`.

- [ ] **3. Minimal implementation** — create `src/polybot/maker/fees.py` (complete file; the category-disposition branches and input guards are pinned RED-first by A5/A6):

```python
"""Taker-fee model + maker rebate (S8 / POL-10), pure exact-Decimal.

A pure maker pays 0 fees; this model prices the FORCED-taker-exit hurdle (DECISIONS-S0)
and the rebate leg of the net identity. Dossier-corrected per-category shape:
fee = size * fee_rate * p * (1 - p)**exponent, with planned-INACTIVE and FREE categories
paying 0 and an UNKNOWN category failing LOUD — a config gap must never silently price
as free. The schedule numbers are a re-pullable deploy seam (see maker/config.py).
"""

from decimal import Decimal


def taker_fee(category, p, size, *, schedule) -> Decimal:
    entry = None
    for candidate in schedule:
        if candidate.name == category:
            entry = candidate
            break
    # Unknown-category / free / inactive dispositions pinned RED-first by the A5 cycle;
    # p/size input guards by A6.
    return size * entry.fee_rate * p * (Decimal(1) - p) ** entry.exponent
```

- [ ] **4. Run it — GREEN:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_maker_fees.py -o addopts="" -q'` → `5 passed`.

- [ ] **5. Full suite:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q'` → **879 passed** (874 + 5).

- [ ] **6. Commit:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/maker/fees.py tests/test_maker_fees.py && git commit -m "S8a A4: taker_fee active formula -- size*fee_rate*p*(1-p)**exp, hand-computed USD 0.75 sports per 100 shares at p=0.5"'`

### Task A5: taker_fee — free and inactive pay zero; unknown category fails loud

- [ ] **1. Write the failing test** — in `tests/test_maker_fees.py`, add `import pytest` below the `from decimal import Decimal` line, then append:

```python
def test_free_category_pays_zero():
    # geopolitics is FREE by flag — its rate/exponent fields are irrelevant.
    assert _fee("0.5", category="geopolitics") == Decimal("0")


def test_planned_inactive_categories_pay_zero():
    for planned in ("politics", "finance", "tech", "econ", "culture", "weather", "crypto"):
        assert _fee("0.5", category=planned) == Decimal("0")


def test_free_wins_over_active():
    # the free flag short-circuits even an active entry with a nonzero rate.
    schedule = (
        FeeCategory(name="promo", fee_rate=Decimal("0.03"), exponent=Decimal("1"),
                    active=True, free=True),
    )
    assert _fee("0.5", category="promo", schedule=schedule) == Decimal("0")


def test_unknown_category_fails_loud():
    # a config gap must never silently price as free (fail LOUD, design Fork 3).
    with pytest.raises(ValueError, match="category"):
        _fee("0.5", category="esports")
```

- [ ] **2. Run it — RED for the right reason:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_maker_fees.py -o addopts="" -q'`
  → `4 failed, 5 passed`: the free/inactive tests fail on `assert Decimal('0.7500') == Decimal('0')` (the formula runs regardless of flags); the unknown-category test fails with `AttributeError: 'NoneType' object has no attribute 'fee_rate'` instead of the expected `ValueError`.

- [ ] **3. Minimal implementation** — in `src/polybot/maker/fees.py`, replace the whole `taker_fee` function (exact replacement):

```python
def taker_fee(category, p, size, *, schedule) -> Decimal:
    entry = None
    for candidate in schedule:
        if candidate.name == category:
            entry = candidate
            break
    if entry is None:
        raise ValueError(f"unknown fee category {category!r} -- config gap, refusing to price as free")
    if entry.free or not entry.active:
        return Decimal(0)
    # p/size input guards pinned RED-first by the A6 cycle.
    return size * entry.fee_rate * p * (Decimal(1) - p) ** entry.exponent
```

- [ ] **4. Run it — GREEN:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_maker_fees.py -o addopts="" -q'` → `9 passed`.

- [ ] **5. Full suite:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q'` → **883 passed** (879 + 4).

- [ ] **6. Commit:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/maker/fees.py tests/test_maker_fees.py && git commit -m "S8a A5: taker_fee category dispositions -- free/inactive pay 0, unknown category fails loud"'`

### Task A6: taker_fee — input guards (p range/finiteness, size sign/finiteness)

- [ ] **1. Write the failing test** — append to `tests/test_maker_fees.py`:

```python
def test_rejects_p_out_of_range():
    with pytest.raises(ValueError, match="p must"):
        _fee("-0.01")
    with pytest.raises(ValueError, match="p must"):
        _fee("1.01")


def test_rejects_a_non_finite_p():
    # NaN propagates QUIETLY through Decimal arithmetic — without the guard the fee
    # would silently come back NaN. Fail LOUD instead (constructor/pure-fn doctrine).
    with pytest.raises(ValueError, match="p must"):
        _fee("NaN")
    with pytest.raises(ValueError, match="p must"):
        _fee("Infinity")


def test_rejects_a_negative_size():
    with pytest.raises(ValueError, match="size"):
        _fee("0.5", size="-1")


def test_rejects_a_non_finite_size():
    with pytest.raises(ValueError, match="size"):
        _fee("0.5", size="NaN")


def test_zero_size_is_a_zero_fee():
    # size 0 is a valid boundary, not an error.
    assert _fee("0.5", size="0") == Decimal("0")
```

- [ ] **2. Run it — RED for the right reason:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_maker_fees.py -o addopts="" -q'`
  → `4 failed, 10 passed`: the four guard tests fail `Failed: DID NOT RAISE <class 'ValueError'>` (out-of-range p yields a nonsense signed fee; NaN/Infinity propagate quietly through the Decimal arithmetic); `test_zero_size_is_a_zero_fee` already passes (0 falls out of the formula).

- [ ] **3. Minimal implementation** — in `src/polybot/maker/fees.py`, replace the whole `taker_fee` function (exact replacement; `is_finite()` FIRST so a NaN never reaches an ordered compare, which would raise `InvalidOperation` instead of the loud named `ValueError`):

```python
def taker_fee(category, p, size, *, schedule) -> Decimal:
    if not p.is_finite() or not (Decimal(0) <= p <= Decimal(1)):
        raise ValueError(f"p must be a finite Decimal in [0, 1], got {p}")
    if not size.is_finite() or size < 0:
        raise ValueError(f"size must be a finite Decimal >= 0, got {size}")
    entry = None
    for candidate in schedule:
        if candidate.name == category:
            entry = candidate
            break
    if entry is None:
        raise ValueError(f"unknown fee category {category!r} -- config gap, refusing to price as free")
    if entry.free or not entry.active:
        return Decimal(0)
    return size * entry.fee_rate * p * (Decimal(1) - p) ** entry.exponent
```

- [ ] **4. Run it — GREEN:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_maker_fees.py -o addopts="" -q'` → `14 passed`.

- [ ] **5. Full suite:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q'` → **888 passed** (883 + 5).

- [ ] **6. Commit:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/maker/fees.py tests/test_maker_fees.py && git commit -m "S8a A6: taker_fee input guards -- non-finite/out-of-range p and negative/non-finite size fail loud"'`

### Task A7: rebate = fraction × taker fee, guarded

- [ ] **1. Write the failing test** — in `tests/test_maker_fees.py`, change the fees import line to `from polybot.maker.fees import rebate, taker_fee`, then append:

```python
def test_rebate_is_fraction_times_fee():
    # 20% of the hand-computed USD 0.75 sports fee = USD 0.15, exact.
    fee = _fee("0.5")
    assert rebate(fee, fraction=Decimal("0.20")) == Decimal("0.15")


def test_rebate_of_a_zero_fee_is_zero():
    assert rebate(Decimal("0"), fraction=Decimal("0.20")) == Decimal("0")


def test_rebate_rejects_a_negative_fee():
    with pytest.raises(ValueError, match="taker_fee_paid"):
        rebate(Decimal("-0.01"), fraction=Decimal("0.20"))


def test_rebate_rejects_a_non_finite_fee():
    with pytest.raises(ValueError, match="taker_fee_paid"):
        rebate(Decimal("NaN"), fraction=Decimal("0.20"))
```

- [ ] **2. Run it — RED for the right reason:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_maker_fees.py -o addopts="" -q'`
  → collection error: `ImportError: cannot import name 'rebate' from 'polybot.maker.fees'`.

- [ ] **3. Minimal implementation** — append to the end of `src/polybot/maker/fees.py`:

```python
def rebate(taker_fee_paid, *, fraction) -> Decimal:
    if not taker_fee_paid.is_finite() or taker_fee_paid < 0:
        raise ValueError(f"taker_fee_paid must be a finite Decimal >= 0, got {taker_fee_paid}")
    return fraction * taker_fee_paid
```

- [ ] **4. Run it — GREEN:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest tests/test_maker_fees.py -o addopts="" -q'` → `18 passed`.

- [ ] **5. Full suite:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && ./.venv/bin/pytest -o addopts="" -q'` → **892 passed** (888 + 4).

- [ ] **6. Commit:**
  `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && git add src/polybot/maker/fees.py tests/test_maker_fees.py && git commit -m "S8a A7: rebate = fraction * taker fee -- negative/non-finite fee fails loud, USD 0.15 of the USD 0.75 hand case"'`

**S8a exit state:** `src/polybot/maker/__init__.py` (docstring-only), `src/polybot/maker/config.py`
(`FeeCategory`, `DEFAULT_FEE_SCHEDULE`, `MakerConfig` fully self-verifying), `src/polybot/maker/fees.py`
(`taker_fee`, `rebate` fully guarded); `tests/test_maker_config.py` (21 tests) + `tests/test_maker_fees.py`
(18 tests). Full suite **892 passed** (853 baseline + 39 S8a). Zero changes to existing files. Then the
two-stage review (spec-compliance + pinned-opus mutation battery; pycache sweep after each mutation revert)
before S8b.
## Sub-slice S8b — Inventory + reward

Scope: `src/polybot/maker/inventory.py` (MakerFill, `_SGN`, `net_inventory`, `adverse_selection`) +
`src/polybot/maker/reward.py` (`spread_score`, `reward_accrual`) + `tests/test_maker_inventory.py` +
`tests/test_maker_reward.py`. Depends on S8a (the `polybot.maker` package + `MakerConfig`/
`DEFAULT_FEE_SCHEDULE` exist). Pure Decimal, no I/O, no imports from `ers/`/`detectors/`/`calibration/`.
Covers design §7.2: BUY/SELL folding, adverse-selection sign + fail-CLOSED marks, the exact
`S(v,s)=(v−s/v)²·b`, and the `max_spread` eligibility gate. Adds **43 tests** (26 inventory + 17 reward).

All commands run as `wsl -d Ubuntu -- bash -lc 'cd ~/projects/polymarket-bot && <cmd>'` on branch
`pol-10-s8-maker`. Full suite = `./.venv/bin/pytest -o addopts="" -q`; single file =
`./.venv/bin/pytest tests/test_maker_<module>.py -o addopts="" -q`. Never pipe pytest through tail/head.

---

### Task B1: MakerFill — frozen, validated fill record + `_SGN`

- [ ] **1. Write the failing test** — create `tests/test_maker_inventory.py` (full file):

```python
"""S8 / POL-10 — maker inventory (MakerFill validation + BUY/SELL folding + adverse-selection mark-out)."""

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from polybot.maker.inventory import _SGN, MakerFill


def _fill(**over):
    base = dict(
        token_id="tok-yes",
        condition_id="cond-1",
        category="sports",
        side="BUY",
        shares=Decimal("10"),
        price_exec=Decimal("0.50"),
        fill_mid=Decimal("0.50"),
    )
    base.update(over)
    return MakerFill(**base)


def test_valid_fill_constructs():
    fill = _fill()
    assert fill.token_id == "tok-yes"
    assert fill.side == "BUY"
    assert fill.shares == Decimal("10")
    assert fill.price_exec == Decimal("0.50")


def test_fill_is_frozen():
    fill = _fill()
    with pytest.raises(FrozenInstanceError):
        fill.shares = Decimal("1")


def test_sgn_maps_buy_plus_one_sell_minus_one():
    assert _SGN["BUY"] == Decimal(1)
    assert _SGN["SELL"] == Decimal(-1)


def test_rejects_bad_side():
    with pytest.raises(ValueError, match="side"):
        _fill(side="HOLD")


def test_rejects_zero_shares():
    with pytest.raises(ValueError, match="shares"):
        _fill(shares=Decimal("0"))


def test_rejects_negative_shares():
    with pytest.raises(ValueError, match="shares"):
        _fill(shares=Decimal("-1"))


def test_rejects_non_finite_shares():
    with pytest.raises(ValueError, match="shares"):
        _fill(shares=Decimal("NaN"))
    with pytest.raises(ValueError, match="shares"):
        _fill(shares=Decimal("Infinity"))


def test_rejects_price_exec_out_of_range():
    with pytest.raises(ValueError, match="price_exec"):
        _fill(price_exec=Decimal("1.01"))
    with pytest.raises(ValueError, match="price_exec"):
        _fill(price_exec=Decimal("-0.01"))


def test_rejects_fill_mid_out_of_range():
    with pytest.raises(ValueError, match="fill_mid"):
        _fill(fill_mid=Decimal("1.01"))
    with pytest.raises(ValueError, match="fill_mid"):
        _fill(fill_mid=Decimal("-0.01"))


def test_rejects_non_finite_prices():
    with pytest.raises(ValueError, match="price_exec"):
        _fill(price_exec=Decimal("NaN"))
    with pytest.raises(ValueError, match="fill_mid"):
        _fill(fill_mid=Decimal("Infinity"))
```

- [ ] **2. Run it — observe RED for the RIGHT reason:**
  `./.venv/bin/pytest tests/test_maker_inventory.py -o addopts="" -q`
  Expected: collection error `ModuleNotFoundError: No module named 'polybot.maker.inventory'`.
- [ ] **3. Minimal implementation** — create `src/polybot/maker/inventory.py` (full file):

```python
"""Maker inventory + adverse-selection mark-out (S8 / POL-10).

The honest bleed-meter (master design §6: "the 'safe' strategy bleeds invisibly"). Every fill
is a frozen, fail-loud-validated record; net positions fold BUY(+)/SELL(-) per token; adverse
selection is the SIGNED mark-out of inventory (Fork 2 -- mark-to-mid interim, mark-to-resolution
at settle), so a two-sided maker's hit ASK books correctly. A None/NaN/out-of-range mark fails
CLOSED to the worst-case adverse (mark 0 for a BUY, 1 for a SELL) -- a bad feed never books a
phantom gain. Pure; marks arrive as an injected ``mark_for(token_id)`` callable.
"""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class MakerFill:
    token_id: str
    condition_id: str
    category: str
    side: str
    shares: Decimal
    price_exec: Decimal
    fill_mid: Decimal

    def __post_init__(self):
        self._verify()

    def _verify(self):
        # Fail LOUD: a malformed fill is data corruption (mirrors toxicity's negative-size guard).
        if self.side not in ("BUY", "SELL"):
            raise ValueError(f"side must be BUY or SELL, got {self.side}")
        if not self.shares.is_finite() or self.shares <= 0:
            raise ValueError(f"shares must be finite > 0, got {self.shares}")
        for name in ("price_exec", "fill_mid"):
            value = getattr(self, name)
            if not value.is_finite() or value < 0 or value > 1:
                raise ValueError(f"{name} must be finite in [0, 1], got {value}")


_SGN = {"BUY": Decimal(1), "SELL": Decimal(-1)}
```

- [ ] **4. Run it — GREEN:**
  `./.venv/bin/pytest tests/test_maker_inventory.py -o addopts="" -q` → `10 passed`.
- [ ] **5. Run the FULL suite:** `./.venv/bin/pytest -o addopts="" -q` → all prior + 10 new pass
  (853 + S8a + 10), exit 0.
- [ ] **6. Commit:**
  `git add src/polybot/maker/inventory.py tests/test_maker_inventory.py && git commit -m "S8b B1: MakerFill frozen validated fill record -- fail-loud side/shares/price guards + _SGN"`

---

### Task B2: `net_inventory` — BUY/SELL folding per token

- [ ] **1. Write the failing test** — in `tests/test_maker_inventory.py`, update the import line to
  `from polybot.maker.inventory import _SGN, MakerFill, net_inventory` and append:

```python
def test_buy_sell_folding_net_and_avg_cost():
    # BUY 10 @ 0.46 + SELL 4 @ 0.40 on ONE token:
    #   net = 10 - 4 = 6
    #   signed cost = 10*0.46 - 4*0.40 = 4.60 - 1.60 = 3.00
    #   avg_cost = 3.00 / 6 = 0.50   (exact Decimal)
    fills = [
        _fill(side="BUY", shares=Decimal("10"), price_exec=Decimal("0.46")),
        _fill(side="SELL", shares=Decimal("4"), price_exec=Decimal("0.40")),
    ]
    assert net_inventory(fills) == {"tok-yes": (Decimal("6"), Decimal("0.5"))}


def test_two_tokens_stay_separate():
    fills = [
        _fill(token_id="tok-a", side="BUY", shares=Decimal("10"), price_exec=Decimal("0.46")),
        _fill(token_id="tok-b", side="SELL", shares=Decimal("3"), price_exec=Decimal("0.20")),
    ]
    out = net_inventory(fills)
    # tok-b: net = -3 ; cost = -3*0.20 = -0.60 ; avg = -0.60 / -3 = 0.20
    assert out["tok-a"] == (Decimal("10"), Decimal("0.46"))
    assert out["tok-b"] == (Decimal("-3"), Decimal("0.20"))
    assert len(out) == 2


def test_flattened_token_nets_zero_with_zero_avg_cost():
    # Fully flattened: net 0 -> avg_cost is Decimal(0) by definition (no division by zero).
    fills = [
        _fill(side="BUY", shares=Decimal("5"), price_exec=Decimal("0.30")),
        _fill(side="SELL", shares=Decimal("5"), price_exec=Decimal("0.35")),
    ]
    assert net_inventory(fills) == {"tok-yes": (Decimal("0"), Decimal("0"))}


def test_no_fills_empty_inventory():
    assert net_inventory([]) == {}
```

- [ ] **2. Run it — observe RED for the RIGHT reason:**
  `./.venv/bin/pytest tests/test_maker_inventory.py -o addopts="" -q`
  Expected: `ImportError: cannot import name 'net_inventory' from 'polybot.maker.inventory'`.
- [ ] **3. Minimal implementation** — append to `src/polybot/maker/inventory.py` (file is now the B1
  content plus this, in full):

```python
def net_inventory(fills):
    """token_id -> (net_shares, avg_cost) folding BUY(+)/SELL(-).

    net_shares = sum sgn*shares ; avg_cost = (sum sgn*shares*price_exec) / net_shares when
    net_shares != 0 else Decimal(0) (a flattened token has no cost basis left).
    """
    net = {}
    cost = {}
    for fill in fills:
        sgn = _SGN[fill.side]
        net[fill.token_id] = net.get(fill.token_id, Decimal(0)) + sgn * fill.shares
        cost[fill.token_id] = cost.get(fill.token_id, Decimal(0)) + sgn * fill.shares * fill.price_exec
    return {
        token_id: (shares, cost[token_id] / shares if shares != 0 else Decimal(0))
        for token_id, shares in net.items()
    }
```

- [ ] **4. Run it — GREEN:**
  `./.venv/bin/pytest tests/test_maker_inventory.py -o addopts="" -q` → `14 passed`.
- [ ] **5. Run the FULL suite:** `./.venv/bin/pytest -o addopts="" -q` → all prior + 14 new pass
  (853 + S8a + 14), exit 0.
- [ ] **6. Commit:**
  `git add src/polybot/maker/inventory.py tests/test_maker_inventory.py && git commit -m "S8b B2: net_inventory BUY/SELL folding -- per-token net shares + avg cost, flattened -> (0, 0)"`

---

### Task B3: `adverse_selection` — signed mark-out, two-sided sign correctness

- [ ] **1. Write the failing test** — update the import line to
  `from polybot.maker.inventory import _SGN, MakerFill, adverse_selection, net_inventory` and append:

```python
def test_buy_mark_below_fill_is_positive_adverse():
    # BUY 10 @ 0.50, mark 0.40: +1 * 10 * (0.50 - 0.40) = +1.00  (the bleed)
    fills = [_fill(side="BUY", shares=Decimal("10"), price_exec=Decimal("0.50"))]
    assert adverse_selection(fills, lambda token_id: Decimal("0.40")) == Decimal("1.00")


def test_buy_mark_above_fill_is_negative_adverse():
    # BUY 10 @ 0.50, mark 0.60: +1 * 10 * (0.50 - 0.60) = -1.00  (favorable = negative cost)
    fills = [_fill(side="BUY", shares=Decimal("10"), price_exec=Decimal("0.50"))]
    assert adverse_selection(fills, lambda token_id: Decimal("0.60")) == Decimal("-1.00")


def test_sell_mark_above_fill_is_positive_adverse():
    # SELL 10 @ 0.50, mark 0.60: -1 * 10 * (0.50 - 0.60) = +1.00  (a hit ASK bleeds when mark RISES)
    fills = [_fill(side="SELL", shares=Decimal("10"), price_exec=Decimal("0.50"))]
    assert adverse_selection(fills, lambda token_id: Decimal("0.60")) == Decimal("1.00")


def test_sell_mark_below_fill_is_negative_adverse():
    # SELL 10 @ 0.50, mark 0.40: -1 * 10 * (0.50 - 0.40) = -1.00
    fills = [_fill(side="SELL", shares=Decimal("10"), price_exec=Decimal("0.50"))]
    assert adverse_selection(fills, lambda token_id: Decimal("0.40")) == Decimal("-1.00")


def test_two_sided_book_hand_computed():
    # BUY 10 @ 0.50 marked 0.45: +1 * 10 * (0.50 - 0.45) = +0.50
    # SELL 4 @ 0.60 marked 0.70: -1 *  4 * (0.60 - 0.70) = +0.40
    # total adverse = 0.90
    fills = [
        _fill(token_id="tok-buy", side="BUY", shares=Decimal("10"), price_exec=Decimal("0.50")),
        _fill(token_id="tok-sell", side="SELL", shares=Decimal("4"), price_exec=Decimal("0.60")),
    ]
    marks = {"tok-buy": Decimal("0.45"), "tok-sell": Decimal("0.70")}
    assert adverse_selection(fills, lambda token_id: marks[token_id]) == Decimal("0.90")


def test_no_fills_zero_adverse():
    assert adverse_selection([], lambda token_id: Decimal("0.50")) == Decimal(0)
```

- [ ] **2. Run it — observe RED for the RIGHT reason:**
  `./.venv/bin/pytest tests/test_maker_inventory.py -o addopts="" -q`
  Expected: `ImportError: cannot import name 'adverse_selection' from 'polybot.maker.inventory'`.
- [ ] **3. Minimal implementation** — append to `src/polybot/maker/inventory.py`:

```python
def adverse_selection(fills, mark_for):
    """Signed mark-out: sum over fills of sgn(side) * shares * (price_exec - mark_for(token_id)).

    Positive = adverse cost (the identity SUBTRACTS it); may be negative overall (favorable
    marks). mark = LocalBook.midpoint() interim / the resolution value at settle -- injected.
    """
    total = Decimal(0)
    for fill in fills:
        mark = mark_for(fill.token_id)
        total += _SGN[fill.side] * fill.shares * (fill.price_exec - mark)
    return total
```

- [ ] **4. Run it — GREEN:**
  `./.venv/bin/pytest tests/test_maker_inventory.py -o addopts="" -q` → `20 passed`.
- [ ] **5. Run the FULL suite:** `./.venv/bin/pytest -o addopts="" -q` → all prior + 20 new pass
  (853 + S8a + 20), exit 0.
- [ ] **6. Commit:**
  `git add src/polybot/maker/inventory.py tests/test_maker_inventory.py && git commit -m "S8b B3: adverse_selection signed mark-out -- BUY/SELL two-sided sign correctness"`

---

### Task B4: `adverse_selection` — fail-CLOSED on None/NaN/out-of-range marks

- [ ] **1. Write the failing test** — append to `tests/test_maker_inventory.py`:

```python
def test_none_mark_buy_fails_closed_to_full_cost():
    # mark_for -> None: BUY worst case is mark 0 -> shares * price_exec = 10 * 0.50 = 5.00
    fills = [_fill(side="BUY", shares=Decimal("10"), price_exec=Decimal("0.50"))]
    assert adverse_selection(fills, lambda token_id: None) == Decimal("5.00")


def test_none_mark_sell_fails_closed_to_one_minus_price():
    # SELL worst case is mark 1 -> shares * (1 - price_exec) = 10 * 0.40 = 4.00
    fills = [_fill(side="SELL", shares=Decimal("10"), price_exec=Decimal("0.60"))]
    assert adverse_selection(fills, lambda token_id: None) == Decimal("4.00")


def test_nan_mark_fails_closed():
    fills = [_fill(side="BUY", shares=Decimal("10"), price_exec=Decimal("0.50"))]
    assert adverse_selection(fills, lambda token_id: Decimal("NaN")) == Decimal("5.00")


def test_mark_above_one_fails_closed_never_phantom_gain():
    # A naive signed mark-out with mark=1.5 would book +1*10*(0.50-1.5) = -10 (a phantom GAIN).
    # Fail-closed books the worst case instead: +5.00.
    fills = [_fill(side="BUY", shares=Decimal("10"), price_exec=Decimal("0.50"))]
    assert adverse_selection(fills, lambda token_id: Decimal("1.5")) == Decimal("5.00")


def test_mark_below_zero_fails_closed_never_phantom_gain():
    # Naive with mark=-0.2 on a SELL: -1*10*(0.60-(-0.2)) = -8 (phantom gain). Fail-closed: +4.00.
    fills = [_fill(side="SELL", shares=Decimal("10"), price_exec=Decimal("0.60"))]
    assert adverse_selection(fills, lambda token_id: Decimal("-0.2")) == Decimal("4.00")


def test_bad_mark_folds_worst_case_alongside_good_marks():
    # Good BUY 10 @ 0.50 marked 0.55: +1 * 10 * (0.50 - 0.55) = -0.50 (a real gain, kept)
    # SELL 2 @ 0.30 with a None mark: worst case 2 * (1 - 0.30) = +1.40
    # total = 0.90
    fills = [
        _fill(token_id="tok-good", side="BUY", shares=Decimal("10"), price_exec=Decimal("0.50")),
        _fill(token_id="tok-bad", side="SELL", shares=Decimal("2"), price_exec=Decimal("0.30")),
    ]
    marks = {"tok-good": Decimal("0.55"), "tok-bad": None}
    assert adverse_selection(fills, lambda token_id: marks[token_id]) == Decimal("0.90")
```

- [ ] **2. Run it — observe RED for the RIGHT reason:**
  `./.venv/bin/pytest tests/test_maker_inventory.py -o addopts="" -q`
  Expected: the None-mark tests fail with
  `TypeError: unsupported operand type(s) for -: 'decimal.Decimal' and 'NoneType'`; the NaN test
  fails its assert (a NaN total compares unequal); the out-of-range tests fail their asserts with
  the phantom-gain values (`-10` / `-8`). Six failures, 20 prior still green.
- [ ] **3. Minimal implementation** — replace `adverse_selection` in
  `src/polybot/maker/inventory.py` with (final form):

```python
def adverse_selection(fills, mark_for):
    """Signed mark-out: sum over fills of sgn(side) * shares * (price_exec - mark_for(token_id)).

    Positive = adverse cost (the identity SUBTRACTS it); may be negative overall (favorable
    marks). mark = LocalBook.midpoint() interim / the resolution value at settle -- injected.
    A None / non-finite / out-of-[0,1] mark FAILS CLOSED to that fill's worst-case adverse:
    BUY -> shares * price_exec (mark 0); SELL -> shares * (1 - price_exec) (mark 1). A bad
    feed must never book a phantom gain (design §5.4).
    """
    total = Decimal(0)
    for fill in fills:
        mark = mark_for(fill.token_id)
        if mark is None or not mark.is_finite() or mark < 0 or mark > 1:
            if fill.side == "BUY":
                total += fill.shares * fill.price_exec
            else:
                total += fill.shares * (Decimal(1) - fill.price_exec)
            continue
        total += _SGN[fill.side] * fill.shares * (fill.price_exec - mark)
    return total
```

- [ ] **4. Run it — GREEN:**
  `./.venv/bin/pytest tests/test_maker_inventory.py -o addopts="" -q` → `26 passed`.
- [ ] **5. Run the FULL suite:** `./.venv/bin/pytest -o addopts="" -q` → all prior + 26 new pass
  (853 + S8a + 26), exit 0.
- [ ] **6. Commit:**
  `git add src/polybot/maker/inventory.py tests/test_maker_inventory.py && git commit -m "S8b B4: adverse_selection fail-CLOSED marks -- None/NaN/out-of-range -> worst-case, never a phantom gain"`

---

### Task B5: `spread_score` — the exact S(v,s) = (v − s/v)² · b quadratic

- [ ] **1. Write the failing test** — create `tests/test_maker_reward.py` (full file):

```python
"""S8 / POL-10 — maker reward (spread_score S(v,s) quadratic + reward_accrual eligibility gate)."""

from decimal import Decimal

import pytest

from polybot.maker.reward import spread_score


def test_spread_score_exact_hand_computed():
    # S(v=10, s=0.5) with b=2: s/v = 0.05 ; (10 - 0.05)^2 = 9.95^2 = 99.0025 ; * 2 = 198.005
    assert spread_score(Decimal("10"), Decimal("0.5"), b=Decimal("2")) == Decimal("198.005")


def test_spread_score_zero_spread_is_depth_squared_times_b():
    # s = 0: S = v^2 * b = 100 * 3 = 300
    assert spread_score(Decimal("10"), Decimal("0"), b=Decimal("3")) == Decimal("300")


def test_spread_score_scales_linearly_in_b():
    one = spread_score(Decimal("10"), Decimal("0.5"), b=Decimal("1"))
    assert one == Decimal("99.0025")
    assert spread_score(Decimal("10"), Decimal("0.5"), b=Decimal("3")) == one * 3


def test_spread_score_rejects_non_positive_v():
    with pytest.raises(ValueError, match="v must"):
        spread_score(Decimal("0"), Decimal("0.5"), b=Decimal("1"))
    with pytest.raises(ValueError, match="v must"):
        spread_score(Decimal("-1"), Decimal("0.5"), b=Decimal("1"))


def test_spread_score_rejects_non_finite_v():
    with pytest.raises(ValueError, match="v must"):
        spread_score(Decimal("NaN"), Decimal("0.5"), b=Decimal("1"))
    with pytest.raises(ValueError, match="v must"):
        spread_score(Decimal("Infinity"), Decimal("0.5"), b=Decimal("1"))


def test_spread_score_rejects_negative_s():
    with pytest.raises(ValueError, match="s must"):
        spread_score(Decimal("10"), Decimal("-0.01"), b=Decimal("1"))


def test_spread_score_rejects_non_finite_s():
    with pytest.raises(ValueError, match="s must"):
        spread_score(Decimal("10"), Decimal("NaN"), b=Decimal("1"))


def test_spread_score_rejects_negative_b():
    with pytest.raises(ValueError, match="b must"):
        spread_score(Decimal("10"), Decimal("0.5"), b=Decimal("-1"))


def test_spread_score_rejects_non_finite_b():
    with pytest.raises(ValueError, match="b must"):
        spread_score(Decimal("10"), Decimal("0.5"), b=Decimal("NaN"))
```

- [ ] **2. Run it — observe RED for the RIGHT reason:**
  `./.venv/bin/pytest tests/test_maker_reward.py -o addopts="" -q`
  Expected: collection error `ModuleNotFoundError: No module named 'polybot.maker.reward'`.
- [ ] **3. Minimal implementation** — create `src/polybot/maker/reward.py` (full file):

```python
"""Maker reward accrual -- the S(v,s) quadratic (S8 / POL-10).

Models the documented per-market reward score S(v,s) = (v - s/v)^2 * b (v = resting depth,
s = spread-from-mid, b = pool constant), exact Decimal. Resting STRICTLY wider than
config.max_spread earns NOTHING (the eligibility gate); AT the boundary still earns. All
guards fail LOUD -- a bad input here is a caller bug, not market data. The exact Polymarket
pool->score mapping and the real b are deploy calibration (design §6); config-parameterized.
"""

from decimal import Decimal


def spread_score(v, s, *, b):
    """The documented S(v, s) = (v - s/v)^2 * b, exact Decimal."""
    if not v.is_finite() or v <= 0:
        raise ValueError(f"v must be finite > 0, got {v}")
    if not s.is_finite() or s < 0:
        raise ValueError(f"s must be finite >= 0, got {s}")
    if not b.is_finite() or b < 0:
        raise ValueError(f"b must be finite >= 0, got {b}")
    delta = v - s / v
    return delta * delta * b
```

- [ ] **4. Run it — GREEN:**
  `./.venv/bin/pytest tests/test_maker_reward.py -o addopts="" -q` → `9 passed`.
- [ ] **5. Run the FULL suite:** `./.venv/bin/pytest -o addopts="" -q` → all prior + 35 new pass
  (853 + S8a + 35), exit 0.
- [ ] **6. Commit:**
  `git add src/polybot/maker/reward.py tests/test_maker_reward.py && git commit -m "S8b B5: spread_score S(v,s) quadratic -- exact (v - s/v)^2 * b + fail-loud guards"`

---

### Task B6: `reward_accrual` — the max_spread eligibility gate

- [ ] **1. Write the failing test** — in `tests/test_maker_reward.py`, update the imports to:

```python
from polybot.maker.config import DEFAULT_FEE_SCHEDULE, MakerConfig
from polybot.maker.reward import reward_accrual, spread_score
```

  and append:

```python
def _config(**over):
    return MakerConfig(fee_schedule=DEFAULT_FEE_SCHEDULE, **over)


def test_reward_accrual_zero_strictly_outside_max_spread():
    # default max_spread = 0.03; resting strictly wider earns NOTHING
    assert reward_accrual(Decimal("10"), Decimal("0.031"), config=_config()) == Decimal(0)


def test_reward_accrual_eligible_at_max_spread_boundary():
    # AT max_spread is still eligible (the gate is strictly >):
    # s/v = 0.03/10 = 0.003 ; (10 - 0.003)^2 = 9.997^2 = 99.940009 ; * b=1
    assert reward_accrual(Decimal("10"), Decimal("0.03"), config=_config()) == Decimal("99.940009")


def test_reward_accrual_inside_equals_spread_score():
    cfg = _config()
    got = reward_accrual(Decimal("10"), Decimal("0.02"), config=cfg)
    # s/v = 0.002 ; (10 - 0.002)^2 = 9.998^2 = 99.960004 ; * b=1
    assert got == Decimal("99.960004")
    assert got == spread_score(Decimal("10"), Decimal("0.02"), b=cfg.reward_b)


def test_reward_accrual_uses_config_reward_b():
    cfg = _config(reward_b=Decimal("2"))
    # 99.960004 * 2 = 199.920008
    assert reward_accrual(Decimal("10"), Decimal("0.02"), config=cfg) == Decimal("199.920008")


def test_reward_accrual_rejects_non_positive_eligible_size():
    with pytest.raises(ValueError, match="eligible_size"):
        reward_accrual(Decimal("0"), Decimal("0.02"), config=_config())
    with pytest.raises(ValueError, match="eligible_size"):
        reward_accrual(Decimal("-1"), Decimal("0.02"), config=_config())


def test_reward_accrual_rejects_non_finite_eligible_size():
    with pytest.raises(ValueError, match="eligible_size"):
        reward_accrual(Decimal("NaN"), Decimal("0.02"), config=_config())


def test_reward_accrual_rejects_negative_spread():
    with pytest.raises(ValueError, match="spread_from_mid"):
        reward_accrual(Decimal("10"), Decimal("-0.01"), config=_config())


def test_reward_accrual_rejects_non_finite_spread():
    with pytest.raises(ValueError, match="spread_from_mid"):
        reward_accrual(Decimal("10"), Decimal("Infinity"), config=_config())
```

- [ ] **2. Run it — observe RED for the RIGHT reason:**
  `./.venv/bin/pytest tests/test_maker_reward.py -o addopts="" -q`
  Expected: collection error `ImportError: cannot import name 'reward_accrual' from 'polybot.maker.reward'`.
- [ ] **3. Minimal implementation** — append to `src/polybot/maker/reward.py` (final file = B5
  content plus this):

```python
def reward_accrual(eligible_size, spread_from_mid, *, config):
    """Decimal(0) if spread_from_mid > config.max_spread (resting too far from mid earns
    NOTHING); else spread_score(eligible_size, spread_from_mid, b=config.reward_b). AT the
    boundary is eligible -- the gate is strictly >."""
    if not eligible_size.is_finite() or eligible_size <= 0:
        raise ValueError(f"eligible_size must be finite > 0, got {eligible_size}")
    if not spread_from_mid.is_finite() or spread_from_mid < 0:
        raise ValueError(f"spread_from_mid must be finite >= 0, got {spread_from_mid}")
    if spread_from_mid > config.max_spread:
        return Decimal(0)
    return spread_score(eligible_size, spread_from_mid, b=config.reward_b)
```

- [ ] **4. Run it — GREEN:**
  `./.venv/bin/pytest tests/test_maker_reward.py -o addopts="" -q` → `17 passed`.
- [ ] **5. Run the FULL suite:** `./.venv/bin/pytest -o addopts="" -q` → all prior + 43 new pass
  (853 + S8a + 43), exit 0.
- [ ] **6. Commit:**
  `git add src/polybot/maker/reward.py tests/test_maker_reward.py && git commit -m "S8b B6: reward_accrual eligibility gate -- 0 strictly outside max_spread, boundary eligible"`

---

**S8b exit state:** `src/polybot/maker/inventory.py` (MakerFill, `_SGN`, `net_inventory`,
`adverse_selection`) + `src/polybot/maker/reward.py` (`spread_score`, `reward_accrual`) complete per
the pinned §4 contract; 43 new tests (26 + 17); full suite = 853 + S8a + 43, exit 0; zero changes to
any existing file. Ready for the S8b two-stage review, then S8c (netpnl + ledger, which consumes
`adverse_selection` and `reward_accrual` in the tracker legs).
## Sub-slice S8c — Net-PnL + ledger

Depends on S8a + S8b being complete (the `polybot.maker` package and its `__init__.py` already exist).
Files touched (purely additive, nothing else):

- `src/polybot/maker/netpnl.py` — `MakerNetPnL` frozen breakdown + `net_pnl()` (THE honest identity)
- `src/polybot/maker/ledger.py` — `VALID_STATUSES`, `MakerFillRecord`, `MakerLedger` (append-only SQLite, mirrors `calibration/ledger.py` exactly)
- `tests/test_maker_netpnl.py`, `tests/test_maker_ledger.py`

Pinned identity (design §3/§4, invariant 1): `net = reward + rebate + spread_capture − adverse_selection − fees − lockup_cost − dispute_haircut`, computed BY `net_pnl` — never caller-supplied. Ledger doctrine (invariants 3/4/6): no-backfill substrate → garbage fails LOUD at the door; DISPUTED/VOID rows are kept but carry `resolution_value=None` so the tracker can exclude them from the honest sample (whale-flip immunity); a dispute flip OVERWRITES and clears the stale value.

### Task C1: net_pnl — the honest identity (all-legs, negative-net, negative-adverse, zero, structural)

- [ ] **1. Write the failing test** — create `tests/test_maker_netpnl.py`:

```python
"""S8 / POL-10 — net-PnL identity (the honest after-all-costs maker figure)."""

from decimal import Decimal

from polybot.maker.netpnl import MakerNetPnL, net_pnl


def _pnl(**overrides):
    legs = dict(reward=Decimal("3"), rebate=Decimal("0.5"), spread_capture=Decimal("1.25"),
                adverse_selection=Decimal("2"), fees=Decimal("0.4"),
                lockup_cost=Decimal("0.1"), dispute_haircut=Decimal("0.25"))
    legs.update(overrides)
    return net_pnl(**legs)


def test_identity_on_the_hand_computed_all_legs_case():
    # 3 + 0.5 + 1.25 = 4.75 credit; 2 + 0.4 + 0.1 + 0.25 = 2.75 debit; net = 2.00.
    r = _pnl()
    assert isinstance(r, MakerNetPnL)
    assert r.net == Decimal("2.00")
    # every leg round-trips as a named field
    assert r.reward == Decimal("3") and r.rebate == Decimal("0.5")
    assert r.spread_capture == Decimal("1.25") and r.adverse_selection == Decimal("2")
    assert r.fees == Decimal("0.4") and r.lockup_cost == Decimal("0.1")
    assert r.dispute_haircut == Decimal("0.25")


def test_negative_net_when_adverse_selection_dominates():
    # the "bleeds invisibly" number must be representable and honest.
    r = _pnl(adverse_selection=Decimal("6"))
    assert r.net == Decimal("-2.00")


def test_negative_adverse_selection_increases_net():
    # favorable marks = negative adverse cost; subtracting a negative adds.
    assert _pnl(adverse_selection=Decimal("-1")).net == Decimal("5.75")


def test_zero_legs_net_to_zero():
    zero = {name: Decimal("0") for name in ("reward", "rebate", "spread_capture",
            "adverse_selection", "fees", "lockup_cost", "dispute_haircut")}
    assert net_pnl(**zero).net == Decimal("0")


def test_net_is_computed_by_net_pnl_not_caller_supplied():
    # structural honesty: for any leg mix, .net equals the hand identity — there is no
    # public path that lets a caller supply an inconsistent net.
    cases = (
        dict(reward=Decimal("1"), rebate=Decimal("0"), spread_capture=Decimal("-0.5"),
             adverse_selection=Decimal("0.25"), fees=Decimal("0.1"),
             lockup_cost=Decimal("0"), dispute_haircut=Decimal("0")),
        dict(reward=Decimal("0"), rebate=Decimal("0.05"), spread_capture=Decimal("2"),
             adverse_selection=Decimal("-0.75"), fees=Decimal("0"),
             lockup_cost=Decimal("0.2"), dispute_haircut=Decimal("0.1")),
    )
    for legs in cases:
        expected = (legs["reward"] + legs["rebate"] + legs["spread_capture"]
                    - legs["adverse_selection"] - legs["fees"]
                    - legs["lockup_cost"] - legs["dispute_haircut"])
        assert net_pnl(**legs).net == expected
```

- [ ] **2. Run it — RED for the right reason:** `./.venv/bin/pytest tests/test_maker_netpnl.py -o addopts="" -q` → `ModuleNotFoundError: No module named 'polybot.maker.netpnl'`
- [ ] **3. Minimal implementation** — create `src/polybot/maker/netpnl.py` (complete file):

```python
"""Maker net-PnL identity (S8 / POL-10).

THE honest after-all-costs figure -- the ONLY number the maker gate reads. net is computed
HERE, from the seven named legs, never caller-supplied: a report cannot show gross
reward+rebate+spread while adverse selection bleeds the book invisibly. There is
deliberately NO accessor exposing the credit side alone -- the identity is structural.
net_pnl() is the public construction path for MakerNetPnL.
"""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class MakerNetPnL:
    reward: Decimal
    rebate: Decimal
    spread_capture: Decimal
    adverse_selection: Decimal
    fees: Decimal
    lockup_cost: Decimal
    dispute_haircut: Decimal
    net: Decimal  # the after-ALL-costs figure; the ONLY number the gate reads


def net_pnl(*, reward, rebate, spread_capture, adverse_selection, fees, lockup_cost,
            dispute_haircut):
    """net = reward + rebate + spread_capture − adverse_selection − fees − lockup_cost
    − dispute_haircut. adverse_selection may be negative (favorable marks) — subtracting
    a negative INCREASES net; spread_capture likewise two-signed."""
    net = (reward + rebate + spread_capture
           - adverse_selection - fees - lockup_cost - dispute_haircut)
    return MakerNetPnL(reward=reward, rebate=rebate, spread_capture=spread_capture,
                       adverse_selection=adverse_selection, fees=fees,
                       lockup_cost=lockup_cost, dispute_haircut=dispute_haircut, net=net)
```

- [ ] **4. Run it — GREEN:** `./.venv/bin/pytest tests/test_maker_netpnl.py -o addopts="" -q` → `5 passed`
- [ ] **5. Full suite:** `./.venv/bin/pytest -o addopts="" -q` → all prior (853 baseline + S8a + S8b) + 5 new pass, exit 0
- [ ] **6. Commit:**

```
git add src/polybot/maker/netpnl.py tests/test_maker_netpnl.py
git commit -m "S8c C1: net_pnl identity -- 7-leg honest breakdown, net computed in net_pnl not caller-supplied"
```

### Task C2: net_pnl validation — non-finite fails LOUD; one-signed legs reject negatives; two-signed legs allowed

- [ ] **1. Write the failing test** — in `tests/test_maker_netpnl.py`, add `import pytest` so the import block reads:

```python
from decimal import Decimal

import pytest

from polybot.maker.netpnl import MakerNetPnL, net_pnl
```

then append:

```python
def test_rejects_a_non_finite_leg():
    for name in ("reward", "rebate", "spread_capture", "adverse_selection",
                 "fees", "lockup_cost", "dispute_haircut"):
        with pytest.raises(ValueError, match=name):
            _pnl(**{name: Decimal("NaN")})
    with pytest.raises(ValueError, match="reward"):
        _pnl(reward=Decimal("Infinity"))


def test_rejects_negative_one_signed_legs():
    for name in ("reward", "rebate", "fees", "lockup_cost", "dispute_haircut"):
        with pytest.raises(ValueError, match=name):
            _pnl(**{name: Decimal("-0.01")})


def test_allows_negative_two_signed_legs():
    # spread_capture and adverse_selection may be either sign by nature.
    assert _pnl(spread_capture=Decimal("-1")).net == Decimal("-0.25")
    assert _pnl(adverse_selection=Decimal("-2")).net == Decimal("6.75")
```

- [ ] **2. Run it — RED for the right reason:** `./.venv/bin/pytest tests/test_maker_netpnl.py -o addopts="" -q` → 2 failures: `Failed: DID NOT RAISE <class 'ValueError'>` in `test_rejects_a_non_finite_leg` and `test_rejects_negative_one_signed_legs` (`test_allows_negative_two_signed_legs` already passes — allowed behavior existed)
- [ ] **3. Minimal implementation** — `src/polybot/maker/netpnl.py`, replace the whole `net_pnl` function with:

```python
def net_pnl(*, reward, rebate, spread_capture, adverse_selection, fees, lockup_cost,
            dispute_haircut):
    """net = reward + rebate + spread_capture − adverse_selection − fees − lockup_cost
    − dispute_haircut. Fail LOUD ValueError: any non-finite leg; a negative one-signed
    leg (reward/rebate/fees/lockup_cost/dispute_haircut). spread_capture and
    adverse_selection may be either sign — a favorable mark is a negative adverse cost,
    and subtracting it INCREASES net."""
    legs = (("reward", reward), ("rebate", rebate), ("spread_capture", spread_capture),
            ("adverse_selection", adverse_selection), ("fees", fees),
            ("lockup_cost", lockup_cost), ("dispute_haircut", dispute_haircut))
    for name, value in legs:
        if not value.is_finite():
            raise ValueError(f"{name} must be a finite Decimal, got {value}")
    for name, value in legs:
        if name not in ("spread_capture", "adverse_selection") and value < 0:
            raise ValueError(f"{name} must be >= 0, got {value}")
    net = (reward + rebate + spread_capture
           - adverse_selection - fees - lockup_cost - dispute_haircut)
    return MakerNetPnL(reward=reward, rebate=rebate, spread_capture=spread_capture,
                       adverse_selection=adverse_selection, fees=fees,
                       lockup_cost=lockup_cost, dispute_haircut=dispute_haircut, net=net)
```

- [ ] **4. Run it — GREEN:** `./.venv/bin/pytest tests/test_maker_netpnl.py -o addopts="" -q` → `8 passed`
- [ ] **5. Full suite:** `./.venv/bin/pytest -o addopts="" -q` → all prior + 3 new pass, exit 0
- [ ] **6. Commit:**

```
git add src/polybot/maker/netpnl.py tests/test_maker_netpnl.py
git commit -m "S8c C2: net_pnl validation -- non-finite any leg + negative one-signed legs fail LOUD; two-signed legs may be negative"
```

### Task C3: MakerLedger.record_fill — append-only SQLite, exact-Decimal round-trip, idempotent on fill_id

- [ ] **1. Write the failing test** — create `tests/test_maker_ledger.py`:

```python
"""S8 / POL-10 — maker fill/settlement ledger (append-only, restart-stable, dispute-honest)."""

from decimal import Decimal

from polybot.core.clock import MonotonicStamper
from polybot.maker.ledger import MakerLedger


def _ledger(path):
    return MakerLedger(path, MonotonicStamper())


def _fill(ledger, fid, *, token="t1", cond="c1", category="politics", side="BUY",
          shares="10", price_exec="0.48", fill_mid="0.50", reward="0.25"):
    return ledger.record_fill(fid, token_id=token, condition_id=cond, category=category,
                              side=side, shares=Decimal(shares),
                              price_exec=Decimal(price_exec), fill_mid=Decimal(fill_mid),
                              reward_accrued=Decimal(reward))


def test_record_fill_round_trips_every_field_via_all(tmp_path):
    with _ledger(str(tmp_path / "m.db")) as l:
        assert _fill(l, "f1") is True
        rows = l.all()
        assert len(rows) == 1
        r = rows[0]
        assert r.fill_id == "f1" and r.token_id == "t1" and r.condition_id == "c1"
        assert r.category == "politics" and r.side == "BUY"
        # exact Decimal round-trip (stored as exact strings)
        assert r.shares == Decimal("10") and r.price_exec == Decimal("0.48")
        assert r.fill_mid == Decimal("0.50") and r.reward_accrued == Decimal("0.25")
        assert r.created_at is not None
        assert r.status is None and r.resolution_value is None and r.settled_at is None


def test_record_fill_is_idempotent_on_fill_id(tmp_path):
    with _ledger(str(tmp_path / "m.db")) as l:
        assert _fill(l, "f1") is True
        assert _fill(l, "f1", price_exec="0.99") is False  # duplicate ignored
        assert l.all()[0].price_exec == Decimal("0.48")    # original preserved
```

- [ ] **2. Run it — RED for the right reason:** `./.venv/bin/pytest tests/test_maker_ledger.py -o addopts="" -q` → `ModuleNotFoundError: No module named 'polybot.maker.ledger'`
- [ ] **3. Minimal implementation** — create `src/polybot/maker/ledger.py` (complete file):

```python
"""Maker fill/settlement ledger (S8 / POL-10).

Append-only, point-in-time SQLite store of the maker's OWN fills and their eventual
settlements -- the substrate MakerTracker derives every net-PnL leg from. Mirrors the
calibration ForecastLedger exactly (WAL + synchronous=NORMAL, stamper timestamps,
Decimals stored as exact strings). Like that ledger it cannot be backfilled, so garbage
must never enter it; DISPUTED/VOID rows are kept but excluded from the honest net
sample by the tracker (whale-flip immunity).
"""

import sqlite3
from dataclasses import dataclass
from decimal import Decimal

# Honest win/loss vs the two statuses excluded from the net sample: a whale-captured UMA
# dispute (DISPUTED) and a refund/50-50 (VOID) must not poison the maker's net-PnL.
VALID_STATUSES = ("WON", "LOST", "DISPUTED", "VOID")

_COLUMNS = ("fill_id, token_id, condition_id, category, side, shares, price_exec, "
            "fill_mid, reward_accrued, created_at, status, resolution_value, settled_at")


@dataclass(frozen=True)
class MakerFillRecord:
    fill_id: str
    token_id: str
    condition_id: str
    category: str
    side: str
    shares: Decimal
    price_exec: Decimal
    fill_mid: Decimal
    reward_accrued: Decimal
    created_at: int
    status: str | None = None
    resolution_value: Decimal | None = None
    settled_at: int | None = None


class MakerLedger:
    def __init__(self, path, stamper):
        self._stamper = stamper
        self._conn = sqlite3.connect(path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS maker_fills (
                fill_id          TEXT PRIMARY KEY,
                token_id         TEXT    NOT NULL,
                condition_id     TEXT    NOT NULL,
                category         TEXT    NOT NULL,
                side             TEXT    NOT NULL,
                shares           TEXT    NOT NULL,
                price_exec       TEXT    NOT NULL,
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

    def record_fill(self, fill_id, *, token_id, condition_id, category, side, shares,
                    price_exec, fill_mid, reward_accrued):
        """INSERT a fill (idempotent on ``fill_id``). Returns True if newly inserted,
        False if a duplicate (original preserved). Decimals stored as exact strings."""
        cur = self._conn.execute(
            "INSERT OR IGNORE INTO maker_fills "
            "(fill_id, token_id, condition_id, category, side, shares, price_exec, "
            "fill_mid, reward_accrued, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (fill_id, token_id, condition_id, category, side, str(shares),
             str(price_exec), str(fill_mid), str(reward_accrued), self._stamper.stamp()),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def all(self):
        return self._query(f"SELECT {_COLUMNS} FROM maker_fills ORDER BY rowid")

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
        return MakerFillRecord(
            fill_id=r[0], token_id=r[1], condition_id=r[2], category=r[3], side=r[4],
            shares=Decimal(r[5]), price_exec=Decimal(r[6]), fill_mid=Decimal(r[7]),
            reward_accrued=Decimal(r[8]), created_at=r[9], status=r[10],
            resolution_value=None if r[11] is None else Decimal(r[11]), settled_at=r[12],
        )
```

- [ ] **4. Run it — GREEN:** `./.venv/bin/pytest tests/test_maker_ledger.py -o addopts="" -q` → `2 passed`
- [ ] **5. Full suite:** `./.venv/bin/pytest -o addopts="" -q` → all prior + 2 new pass, exit 0
- [ ] **6. Commit:**

```
git add src/polybot/maker/ledger.py tests/test_maker_ledger.py
git commit -m "S8c C3: MakerLedger record_fill -- append-only SQLite, exact-Decimal round-trip, idempotent on fill_id"
```

### Task C4: record_settlement + settled() — status/value/settled_at, category filter, dispute-flip overwrite, restart-stable

- [ ] **1. Write the failing test** — append to `tests/test_maker_ledger.py`:

```python
def test_settlement_sets_status_value_and_settled_at(tmp_path):
    with _ledger(str(tmp_path / "m.db")) as l:
        _fill(l, "f1")
        l.record_settlement("f1", status="WON", resolution_value=Decimal("1"))
        r = l.all()[0]
        assert r.status == "WON"
        assert r.resolution_value == Decimal("1")
        assert r.settled_at is not None
        assert [x.fill_id for x in l.settled()] == ["f1"]


def test_unsettled_fills_are_excluded_from_settled(tmp_path):
    with _ledger(str(tmp_path / "m.db")) as l:
        _fill(l, "f1")
        _fill(l, "f2")
        l.record_settlement("f1", status="LOST", resolution_value=Decimal("0"))
        assert [x.fill_id for x in l.settled()] == ["f1"]


def test_settled_filters_by_category(tmp_path):
    with _ledger(str(tmp_path / "m.db")) as l:
        _fill(l, "f1", category="politics")
        _fill(l, "f2", category="sports")
        l.record_settlement("f1", status="WON", resolution_value=Decimal("1"))
        l.record_settlement("f2", status="LOST", resolution_value=Decimal("0"))
        assert [x.fill_id for x in l.settled(category="sports")] == ["f2"]


def test_settlement_overwrites_on_a_dispute_flip(tmp_path):
    # a whale-captured UMA dispute can flip an apparent WON to DISPUTED later;
    # the flip must also CLEAR the stale resolution value.
    with _ledger(str(tmp_path / "m.db")) as l:
        _fill(l, "f1")
        l.record_settlement("f1", status="WON", resolution_value=Decimal("1"))
        l.record_settlement("f1", status="DISPUTED", resolution_value=None)
        r = l.all()[0]
        assert r.status == "DISPUTED" and r.resolution_value is None


def test_persists_across_restart(tmp_path):
    path = str(tmp_path / "m.db")
    with _ledger(path) as l:
        _fill(l, "f1")
        l.record_settlement("f1", status="WON", resolution_value=Decimal("1"))
    with _ledger(path) as l2:
        r = l2.all()[0]
        assert r.shares == Decimal("10") and r.price_exec == Decimal("0.48")
        assert r.status == "WON" and r.resolution_value == Decimal("1")
```

- [ ] **2. Run it — RED for the right reason:** `./.venv/bin/pytest tests/test_maker_ledger.py -o addopts="" -q` → 5 failures, each `AttributeError: 'MakerLedger' object has no attribute 'record_settlement'`
- [ ] **3. Minimal implementation** — in `src/polybot/maker/ledger.py`, insert these two methods immediately after `record_fill` (before `all`):

```python
    def record_settlement(self, fill_id, *, status, resolution_value):
        """Set the fill's settlement (overwrites -- a UMA dispute can flip an apparent
        WON to DISPUTED later; the flip clears the stale resolution value)."""
        self._conn.execute(
            "UPDATE maker_fills SET status=?, resolution_value=?, settled_at=? "
            "WHERE fill_id=?",
            (status, None if resolution_value is None else str(resolution_value),
             self._stamper.stamp(), fill_id),
        )
        self._conn.commit()

    def settled(self, category=None):
        sql = f"SELECT {_COLUMNS} FROM maker_fills WHERE status IS NOT NULL"
        params = ()
        if category is not None:
            sql += " AND category=?"
            params = (category,)
        return self._query(sql + " ORDER BY rowid", params)
```

- [ ] **4. Run it — GREEN:** `./.venv/bin/pytest tests/test_maker_ledger.py -o addopts="" -q` → `7 passed`
- [ ] **5. Full suite:** `./.venv/bin/pytest -o addopts="" -q` → all prior + 5 new pass, exit 0
- [ ] **6. Commit:**

```
git add src/polybot/maker/ledger.py tests/test_maker_ledger.py
git commit -m "S8c C4: MakerLedger settlement -- status/value/settled_at, settled(category) filter, dispute-flip overwrite, restart-stable"
```

### Task C5: settlement validation — status whitelist, unknown fill KeyError, WON/LOST need in-[0,1] value, DISPUTED/VOID need None

- [ ] **1. Write the failing test** — in `tests/test_maker_ledger.py`, add `import pytest` so the import block reads:

```python
from decimal import Decimal

import pytest

from polybot.core.clock import MonotonicStamper
from polybot.maker.ledger import MakerLedger
```

then append:

```python
def test_rejects_an_invalid_settlement_status(tmp_path):
    with _ledger(str(tmp_path / "m.db")) as l:
        _fill(l, "f1")
        with pytest.raises(ValueError, match="status"):
            l.record_settlement("f1", status="MAYBE", resolution_value=None)


def test_settling_an_unknown_fill_fails_loud(tmp_path):
    with _ledger(str(tmp_path / "m.db")) as l:
        with pytest.raises(KeyError):
            l.record_settlement("nope", status="WON", resolution_value=Decimal("1"))


def test_won_and_lost_require_a_finite_in_range_resolution_value(tmp_path):
    # canonically 1/0 but any finite settle mark in [0,1] is accepted; None/NaN/1.5 are not.
    with _ledger(str(tmp_path / "m.db")) as l:
        _fill(l, "f1")
        for bad in (None, Decimal("NaN"), Decimal("1.5")):
            with pytest.raises(ValueError, match="resolution_value"):
                l.record_settlement("f1", status="WON", resolution_value=bad)
        with pytest.raises(ValueError, match="resolution_value"):
            l.record_settlement("f1", status="LOST", resolution_value=None)


def test_disputed_and_void_require_resolution_value_none(tmp_path):
    # DISPUTED/VOID are excluded from the net sample -- a value here is a caller bug.
    with _ledger(str(tmp_path / "m.db")) as l:
        _fill(l, "f1")
        for status in ("DISPUTED", "VOID"):
            with pytest.raises(ValueError, match="resolution_value"):
                l.record_settlement("f1", status=status, resolution_value=Decimal("0.5"))
```

- [ ] **2. Run it — RED for the right reason:** `./.venv/bin/pytest tests/test_maker_ledger.py -o addopts="" -q` → 4 failures: `Failed: DID NOT RAISE <class 'ValueError'>` in the three ValueError tests and `Failed: DID NOT RAISE <class 'KeyError'>` in `test_settling_an_unknown_fill_fails_loud`
- [ ] **3. Minimal implementation** — in `src/polybot/maker/ledger.py`, replace the whole `record_settlement` method with:

```python
    def record_settlement(self, fill_id, *, status, resolution_value):
        """Set the fill's settlement (overwrites -- a UMA dispute can flip an apparent
        WON to DISPUTED later; the flip clears the stale resolution value). Fails LOUD:
        unknown status or fill_id; a resolution_value inconsistent with the status --
        WON/LOST REQUIRE a finite Decimal in [0, 1] (canonically 1/0 but any settle mark
        accepted); DISPUTED/VOID REQUIRE None (they are excluded from the net sample, so
        a value here is a caller bug)."""
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
            "UPDATE maker_fills SET status=?, resolution_value=?, settled_at=? "
            "WHERE fill_id=?",
            (status, None if resolution_value is None else str(resolution_value),
             self._stamper.stamp(), fill_id),
        )
        self._conn.commit()
        if cur.rowcount == 0:
            raise KeyError(f"no maker fill {fill_id!r} to settle")
```

- [ ] **4. Run it — GREEN:** `./.venv/bin/pytest tests/test_maker_ledger.py -o addopts="" -q` → `11 passed`
- [ ] **5. Full suite:** `./.venv/bin/pytest -o addopts="" -q` → all prior + 4 new pass, exit 0
- [ ] **6. Commit:**

```
git add src/polybot/maker/ledger.py tests/test_maker_ledger.py
git commit -m "S8c C5: settlement validation -- status whitelist, unknown fill KeyError, WON/LOST need in-[0,1] value, DISPUTED/VOID need None"
```

### Task C6: record_fill fail-LOUD guards — side / shares / prices / reward_accrued rejected at the door

- [ ] **1. Write the failing test** — append to `tests/test_maker_ledger.py`:

```python
def test_record_fill_rejects_a_bad_side(tmp_path):
    with _ledger(str(tmp_path / "m.db")) as l:
        with pytest.raises(ValueError, match="side"):
            _fill(l, "f1", side="HOLD")


def test_record_fill_rejects_non_positive_or_non_finite_shares(tmp_path):
    with _ledger(str(tmp_path / "m.db")) as l:
        for bad in ("0", "-5", "NaN", "Infinity"):
            with pytest.raises(ValueError, match="shares"):
                _fill(l, "f1", shares=bad)


def test_record_fill_rejects_out_of_range_prices(tmp_path):
    with _ledger(str(tmp_path / "m.db")) as l:
        with pytest.raises(ValueError, match="price_exec"):
            _fill(l, "f1", price_exec="1.5")
        with pytest.raises(ValueError, match="price_exec"):
            _fill(l, "f1", price_exec="-0.1")
        with pytest.raises(ValueError, match="fill_mid"):
            _fill(l, "f1", fill_mid="NaN")


def test_record_fill_rejects_negative_or_non_finite_reward_accrued(tmp_path):
    with _ledger(str(tmp_path / "m.db")) as l:
        for bad in ("-0.01", "NaN"):
            with pytest.raises(ValueError, match="reward_accrued"):
                _fill(l, "f1", reward=bad)
        assert l.all() == []  # nothing garbage entered the no-backfill store
```

- [ ] **2. Run it — RED for the right reason:** `./.venv/bin/pytest tests/test_maker_ledger.py -o addopts="" -q` → 4 failures, each `Failed: DID NOT RAISE <class 'ValueError'>`
- [ ] **3. Minimal implementation** — in `src/polybot/maker/ledger.py`, replace the whole `record_fill` method with:

```python
    def record_fill(self, fill_id, *, token_id, condition_id, category, side, shares,
                    price_exec, fill_mid, reward_accrued):
        """INSERT a fill (idempotent on ``fill_id``). Returns True if newly inserted,
        False if a duplicate (original preserved). Decimals stored as exact strings.

        Fail LOUD at the door: the maker's net-PnL substrate cannot be backfilled, so a
        bad side, a non-positive/non-finite size, an out-of-[0,1] price, or a negative
        reward must never enter it (mirrors the calibration ledger's H1 guard)."""
        if side not in ("BUY", "SELL"):
            raise ValueError(f"side must be 'BUY' or 'SELL', got {side!r}")
        if not shares.is_finite() or shares <= 0:
            raise ValueError(f"shares must be a finite Decimal > 0, got {shares}")
        for name, value in (("price_exec", price_exec), ("fill_mid", fill_mid)):
            if not value.is_finite() or not (Decimal(0) <= value <= Decimal(1)):
                raise ValueError(f"{name} must be a finite price in [0, 1], got {value}")
        if not reward_accrued.is_finite() or reward_accrued < 0:
            raise ValueError(
                f"reward_accrued must be a finite Decimal >= 0, got {reward_accrued}")
        cur = self._conn.execute(
            "INSERT OR IGNORE INTO maker_fills "
            "(fill_id, token_id, condition_id, category, side, shares, price_exec, "
            "fill_mid, reward_accrued, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (fill_id, token_id, condition_id, category, side, str(shares),
             str(price_exec), str(fill_mid), str(reward_accrued), self._stamper.stamp()),
        )
        self._conn.commit()
        return cur.rowcount > 0
```

- [ ] **4. Run it — GREEN:** `./.venv/bin/pytest tests/test_maker_ledger.py -o addopts="" -q` → `15 passed`
- [ ] **5. Full suite:** `./.venv/bin/pytest -o addopts="" -q` → all prior + 4 new pass, exit 0 (S8c complete: +23 over the pre-S8c count)
- [ ] **6. Commit:**

```
git add src/polybot/maker/ledger.py tests/test_maker_ledger.py
git commit -m "S8c C6: record_fill guards -- side/shares/prices/reward_accrued fail LOUD before insert"
```
## Sub-slice S8d — Quote-policy + gate + e2e

Scope: `src/polybot/maker/quote_policy.py` (QUOTE/WIDEN/PULL + `decide_quote`),
`src/polybot/maker/gate.py` (`MakerReport`/`MakerTracker`/`MakerGate`), plus
`tests/test_maker_quote_policy.py`, `tests/test_maker_gate.py`, `tests/test_maker_e2e.py`
(the design §7.3 whole-slice e2e). Everything below is drafted against the PINNED CONTRACT and
PINNED LEG DERIVATIONS in the shared context — the tracker's legs come from the ledger's settled
WON/LOST rows exactly as pinned, DISPUTED/VOID are counted-and-excluded, and **go reads `.net`
ONLY** (design §5 invariant 1, the "bleeds invisibly" pin). Zero change to any existing file.

---

### Task D1: quote_policy — module + the QUOTE and WIDEN arms

- [ ] **1. Write the failing test** — create `tests/test_maker_quote_policy.py`:

```python
"""S8 / POL-10 — quote policy (QUOTE / WIDEN / PULL over the D1 pull_quotes seam)."""

from decimal import Decimal

import pytest

from polybot.maker.config import DEFAULT_FEE_SCHEDULE, MakerConfig
from polybot.maker.quote_policy import PULL, QUOTE, WIDEN, decide_quote

_CFG = MakerConfig(fee_schedule=DEFAULT_FEE_SCHEDULE)


def _decide(**kw):
    """A benign baseline cycle: no toxic flow, no bleed, locked inventory well under cap."""
    base = dict(pull_quotes=False, recent_adverse=Decimal("0"), break_even=Decimal("0.05"),
                locked_effective=Decimal("0"), locked_cap=Decimal("100"), config=_CFG)
    base.update(kw)
    return decide_quote(**base)


def test_quote_when_no_bleed_and_no_triggers():
    assert _decide() == QUOTE


def test_widen_when_bleeding_under_break_even():
    # bleeding (recent_adverse > 0) but under the break-even adverse move -> widen, don't pull.
    assert _decide(recent_adverse=Decimal("0.01")) == WIDEN
```

- [ ] **2. Run it — RED for the right reason:**
  `./.venv/bin/pytest tests/test_maker_quote_policy.py -o addopts="" -q`
  Expected: collection error `ModuleNotFoundError: No module named 'polybot.maker.quote_policy'`.

- [ ] **3. Minimal implementation** — create `src/polybot/maker/quote_policy.py` (all three
  action constants are part of the pinned contract; only the QUOTE/WIDEN arms are wired yet):

```python
"""Maker quote-policy actions (S8 / POL-10).

Decides QUOTE / WIDEN / PULL for one quoting cycle. Consumes the D1 toxicity ``pull_quotes``
seam as a plain bool plus the CALLER-computed break-even adverse move (daily_reward/order_size,
the master design's tiny number) and the locked-inventory cap. Doctrine: the default under any
ambiguity is the SAFE action -- never "keep quoting". ``config`` is accepted per the pinned
contract (reserved for future policy knobs; unused today -- do not invent behavior for it).
"""

from decimal import Decimal

QUOTE = "QUOTE"
WIDEN = "WIDEN"
PULL = "PULL"


def decide_quote(*, pull_quotes, recent_adverse, break_even, locked_effective, locked_cap,
                 config):
    if recent_adverse > Decimal(0):
        return WIDEN
    return QUOTE
```

- [ ] **4. Run it — GREEN:**
  `./.venv/bin/pytest tests/test_maker_quote_policy.py -o addopts="" -q` → `2 passed`.
- [ ] **5. Full suite:** `./.venv/bin/pytest -o addopts="" -q` — expected: all prior
  (853 + S8a–S8c) + 2 new pass.
- [ ] **6. Commit:**
  `git add src/polybot/maker/quote_policy.py tests/test_maker_quote_policy.py && git commit -m "S8d D1: quote-policy QUOTE/WIDEN arms -- bleed under break-even widens, clean cycle quotes"`

---

### Task D2: quote_policy — each PULL trigger in isolation + the strict boundaries

- [ ] **1. Write the failing tests** — append to `tests/test_maker_quote_policy.py`:

```python
def test_pull_quotes_alone_pulls():
    # the D1 toxicity seam alone (no bleed, locked under cap) is a HARD pull.
    assert _decide(pull_quotes=True) == PULL


def test_adverse_over_break_even_alone_pulls():
    # 0.06 > break_even 0.05, everything else benign.
    assert _decide(recent_adverse=Decimal("0.06")) == PULL


def test_locked_over_cap_alone_pulls():
    # 101 > cap 100, everything else benign.
    assert _decide(locked_effective=Decimal("101")) == PULL


def test_adverse_exactly_at_break_even_widens_not_pulls():
    # strict >: at break-even is NOT a PULL trigger; 0.05 > 0 so it falls to the WIDEN arm.
    assert _decide(recent_adverse=Decimal("0.05"), break_even=Decimal("0.05")) == WIDEN


def test_zero_adverse_quotes_even_at_zero_break_even():
    # recent_adverse == 0 -> the QUOTE arm (0 > 0 is False on BOTH strict comparisons).
    assert _decide(recent_adverse=Decimal("0"), break_even=Decimal("0")) == QUOTE


def test_locked_exactly_at_cap_is_not_pull():
    assert _decide(locked_effective=Decimal("100"), locked_cap=Decimal("100")) == QUOTE
```

- [ ] **2. Run it — RED for the right reason:**
  `./.venv/bin/pytest tests/test_maker_quote_policy.py -o addopts="" -q`
  Expected: the three `*_alone_pulls` tests FAIL (`assert 'QUOTE' == 'PULL'` /
  `assert 'WIDEN' == 'PULL'` — no PULL arm exists yet). The three boundary tests already pass
  against the D1 arms — they are pinned NOW so the PULL arm about to be added cannot overreach
  the strict `>` boundaries.

- [ ] **3. Minimal implementation** — `src/polybot/maker/quote_policy.py`, full content now:

```python
"""Maker quote-policy actions (S8 / POL-10).

Decides QUOTE / WIDEN / PULL for one quoting cycle. Consumes the D1 toxicity ``pull_quotes``
seam as a plain bool plus the CALLER-computed break-even adverse move (daily_reward/order_size,
the master design's tiny number) and the locked-inventory cap. Doctrine: PULL is fail-safe under
ANY trigger (toxic flow / adverse over break-even / locked over cap) -- the default under any
ambiguity is the SAFE action, never "keep quoting". ``config`` is accepted per the pinned
contract (reserved for future policy knobs; unused today -- do not invent behavior for it).
"""

from decimal import Decimal

QUOTE = "QUOTE"
WIDEN = "WIDEN"
PULL = "PULL"


def decide_quote(*, pull_quotes, recent_adverse, break_even, locked_effective, locked_cap,
                 config):
    if pull_quotes or recent_adverse > break_even or locked_effective > locked_cap:
        return PULL
    if recent_adverse > Decimal(0):
        return WIDEN
    return QUOTE
```

- [ ] **4. Run it — GREEN:**
  `./.venv/bin/pytest tests/test_maker_quote_policy.py -o addopts="" -q` → `8 passed`.
- [ ] **5. Full suite:** `./.venv/bin/pytest -o addopts="" -q` — all prior + 6 new pass.
- [ ] **6. Commit:**
  `git add src/polybot/maker/quote_policy.py tests/test_maker_quote_policy.py && git commit -m "S8d D2: PULL fail-safe on any trigger -- pull_quotes/adverse>break_even/locked>cap, strict boundaries pinned"`

---

### Task D3: quote_policy — fail-safe PULL on None/NaN inputs (the loop must not crash)

- [ ] **1. Write the failing test** — append to `tests/test_maker_quote_policy.py`:

```python
@pytest.mark.parametrize("bad", [None, Decimal("NaN")], ids=["none", "nan"])
@pytest.mark.parametrize("field",
                         ["recent_adverse", "break_even", "locked_effective", "locked_cap"])
def test_fail_safe_pull_on_missing_or_nan_numeric_input(field, bad):
    # a quoting loop must NEVER crash on a bad feed, and ambiguity is never a reason to keep
    # quoting: any of the four numeric inputs missing (None) or NaN -> the conservative PULL.
    assert _decide(**{field: bad}) == PULL
```

- [ ] **2. Run it — RED for the right reason:**
  `./.venv/bin/pytest tests/test_maker_quote_policy.py -o addopts="" -q`
  Expected: all 8 new params FAIL — the None cases with
  `TypeError: '>' not supported between instances of ...` and the NaN cases with
  `decimal.InvalidOperation` (Decimal ordering comparisons against NaN raise). That crash IS the
  bug this cycle pins away.

- [ ] **3. Minimal implementation** — `src/polybot/maker/quote_policy.py`, full FINAL content:

```python
"""Maker quote-policy actions (S8 / POL-10).

Decides QUOTE / WIDEN / PULL for one quoting cycle. Consumes the D1 toxicity ``pull_quotes``
seam as a plain bool plus the CALLER-computed break-even adverse move (daily_reward/order_size,
the master design's tiny number) and the locked-inventory cap. Doctrine: PULL is fail-safe under
ANY trigger (toxic flow / adverse over break-even / locked over cap), and a None or non-finite
numeric input also PULLs -- the quoting loop must never crash, and ambiguity is never a reason
to keep quoting. ``config`` is accepted per the pinned contract (reserved for future policy
knobs; unused today -- do not invent behavior for it).
"""

from decimal import Decimal

QUOTE = "QUOTE"
WIDEN = "WIDEN"
PULL = "PULL"


def _unusable(value):
    """None or a non-finite Decimal -- an input the policy must not reason over."""
    return value is None or not value.is_finite()


def decide_quote(*, pull_quotes, recent_adverse, break_even, locked_effective, locked_cap,
                 config):
    if (_unusable(recent_adverse) or _unusable(break_even)
            or _unusable(locked_effective) or _unusable(locked_cap)):
        return PULL  # fail-safe: never crash, never quote into ambiguity
    if pull_quotes or recent_adverse > break_even or locked_effective > locked_cap:
        return PULL
    if recent_adverse > Decimal(0):
        return WIDEN
    return QUOTE
```

- [ ] **4. Run it — GREEN:**
  `./.venv/bin/pytest tests/test_maker_quote_policy.py -o addopts="" -q` → `16 passed`.
- [ ] **5. Full suite:** `./.venv/bin/pytest -o addopts="" -q` — all prior + 8 new pass.
- [ ] **6. Commit:**
  `git add src/polybot/maker/quote_policy.py tests/test_maker_quote_policy.py && git commit -m "S8d D3: fail-safe PULL on None/NaN inputs -- the quoting loop never crashes, never quotes into ambiguity"`

---

### Task D4: gate — MakerTracker cold path + the FULL hand-computed report (active sports)

- [ ] **1. Write the failing test** — create `tests/test_maker_gate.py`:

```python
"""S8 / POL-10 — maker tracker + gate (binary GO/NO-GO over the honest net-of-cost sample)."""

from decimal import Decimal

import pytest

from polybot.core.clock import MonotonicStamper
from polybot.maker.config import DEFAULT_FEE_SCHEDULE, MakerConfig
from polybot.maker.gate import MakerTracker
from polybot.maker.ledger import MakerLedger


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
```

- [ ] **2. Run it — RED for the right reason:**
  `./.venv/bin/pytest tests/test_maker_gate.py -o addopts="" -q`
  Expected: collection error `ModuleNotFoundError: No module named 'polybot.maker.gate'`.

- [ ] **3. Minimal implementation** — create `src/polybot/maker/gate.py` (the leg derivations
  in full; `go` deliberately hardcoded `False` — the go rule is D5's cycle; DISPUTED/VOID
  counting is D6's cycle):

```python
"""Maker GO/NO-GO gate (S8 / POL-10).

Scores the shadow maker sample per category, honestly: every leg of
``net = reward + rebate + spread_capture - adverse_selection - fees - lockup_cost -
dispute_haircut`` is derived from the ledger's settled WON/LOST rows; DISPUTED/VOID are counted
separately and EXCLUDED from every leg (whale-flip immunity); GO reads ``.net`` ONLY -- never a
reward-gross leg (the master design's "bleeds invisibly" pin). Binary and data-gated: cold or
below ``min_samples`` -> no GO. ``lockup_cost`` = ``lockup_rate * total notional``; the
per-day x days-to-resolution folding is deferred deploy calibration.
"""

from dataclasses import dataclass
from decimal import Decimal

from polybot.maker.fees import rebate, taker_fee
from polybot.maker.inventory import _SGN, MakerFill, adverse_selection
from polybot.maker.netpnl import net_pnl

_HONEST = ("WON", "LOST")


@dataclass(frozen=True)
class MakerReport:
    category: str
    n_settled: int
    n_disputed: int
    n_void: int
    reward: Decimal | None
    rebate: Decimal | None
    spread_capture: Decimal | None
    adverse_selection: Decimal | None
    fees: Decimal | None
    lockup_cost: Decimal | None
    dispute_haircut: Decimal | None
    net: Decimal | None
    go: bool


class MakerTracker:
    def __init__(self, ledger, config):
        self._ledger = ledger
        self._config = config

    def report_for(self, category):
        c = self._config
        honest = [r for r in self._ledger.settled(category) if r.status in _HONEST]
        n = len(honest)
        if n == 0:  # cold -- no honest settled sample yet (shadow-only, data-gated dormant)
            return MakerReport(category, 0, 0, 0,
                               None, None, None, None, None, None, None, None, False)

        reward = sum((r.reward_accrued for r in honest), Decimal(0))
        cf_total = sum((taker_fee(r.category, r.price_exec, r.shares, schedule=c.fee_schedule)
                        for r in honest), Decimal(0))
        spread_capture = sum((_SGN[r.side] * r.shares * (r.fill_mid - r.price_exec)
                              for r in honest), Decimal(0))
        notional = sum((r.shares * r.price_exec for r in honest), Decimal(0))
        marks = {r.token_id: r.resolution_value for r in honest}
        fills = [MakerFill(token_id=r.token_id, condition_id=r.condition_id,
                           category=r.category, side=r.side, shares=r.shares,
                           price_exec=r.price_exec, fill_mid=r.fill_mid) for r in honest]
        pnl = net_pnl(reward=reward,
                      rebate=rebate(cf_total, fraction=c.rebate_fraction),
                      spread_capture=spread_capture,
                      adverse_selection=adverse_selection(fills, marks.get),
                      fees=c.forced_taker_exit_p * cf_total,
                      lockup_cost=c.lockup_rate * notional,
                      dispute_haircut=c.dispute_p * notional)
        return MakerReport(category, n, 0, 0, pnl.reward, pnl.rebate, pnl.spread_capture,
                           pnl.adverse_selection, pnl.fees, pnl.lockup_cost,
                           pnl.dispute_haircut, pnl.net, False)
```

- [ ] **4. Run it — GREEN:**
  `./.venv/bin/pytest tests/test_maker_gate.py -o addopts="" -q` → `2 passed`.
- [ ] **5. Full suite:** `./.venv/bin/pytest -o addopts="" -q` — all prior + 2 new pass.
- [ ] **6. Commit:**
  `git add src/polybot/maker/gate.py tests/test_maker_gate.py && git commit -m "S8d D4: MakerTracker leg derivations -- cold path + full hand-computed sports report over the pinned identity"`

---

### Task D5: gate — the binary go rule (min_samples floor, strict margin, go reads .net ONLY)

- [ ] **1. Write the failing tests** — append to `tests/test_maker_gate.py`:

```python
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
```

- [ ] **2. Run it — RED for the right reason:**
  `./.venv/bin/pytest tests/test_maker_gate.py -o addopts="" -q`
  Expected: `test_go_when_sample_clears_and_net_exceeds_margin` and the `above.go is True` half
  of the margin test FAIL (`assert False is True` — go is hardcoded False from D4). The
  negative cases pass against the hardcoded False; they are pinned NOW so the go rule about to
  land cannot overreach (floor, strict margin, net-only).

- [ ] **3. Minimal implementation** — exact diff in `src/polybot/maker/gate.py`; replace the
  final `return` of `report_for`:

```python
# OLD (D4):
        return MakerReport(category, n, 0, 0, pnl.reward, pnl.rebate, pnl.spread_capture,
                           pnl.adverse_selection, pnl.fees, pnl.lockup_cost,
                           pnl.dispute_haircut, pnl.net, False)

# NEW (D5):
        # GO reads .net ONLY (never a gross leg): enough sample AND net STRICTLY over the margin.
        go = n >= c.min_samples and pnl.net > c.net_margin_min
        return MakerReport(category, n, 0, 0, pnl.reward, pnl.rebate, pnl.spread_capture,
                           pnl.adverse_selection, pnl.fees, pnl.lockup_cost,
                           pnl.dispute_haircut, pnl.net, go)
```

- [ ] **4. Run it — GREEN:**
  `./.venv/bin/pytest tests/test_maker_gate.py -o addopts="" -q` → `6 passed`.
- [ ] **5. Full suite:** `./.venv/bin/pytest -o addopts="" -q` — all prior + 4 new pass.
- [ ] **6. Commit:**
  `git add src/polybot/maker/gate.py tests/test_maker_gate.py && git commit -m "S8d D5: binary go rule -- min_samples floor + strict net>margin, go reads .net only (bleeds-invisibly pin)"`

---

### Task D6: gate — DISPUTED/VOID counted-and-excluded + unknown status fails loud

- [ ] **1. Write the failing tests** — append to `tests/test_maker_gate.py`:

```python
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
```

- [ ] **2. Run it — RED for the right reason:**
  `./.venv/bin/pytest tests/test_maker_gate.py -o addopts="" -q`
  Expected: the disputed/void tests FAIL on the counters (`assert 0 == 1` — n_disputed/n_void
  are hardcoded 0; the D4 filter already excludes the rows from the legs so `net` is right),
  and the corruption test FAILS with `DID NOT RAISE` (the 'WEIRD' row silently vanishes
  through the `in _HONEST` filter — exactly the silent-vanish bug this cycle pins away).

- [ ] **3. Minimal implementation** — `src/polybot/maker/gate.py`, full content now (the
  classification loop replaces the filter; everything else unchanged):

```python
"""Maker GO/NO-GO gate (S8 / POL-10).

Scores the shadow maker sample per category, honestly: every leg of
``net = reward + rebate + spread_capture - adverse_selection - fees - lockup_cost -
dispute_haircut`` is derived from the ledger's settled WON/LOST rows; DISPUTED/VOID are counted
separately and EXCLUDED from every leg (whale-flip immunity); GO reads ``.net`` ONLY -- never a
reward-gross leg (the master design's "bleeds invisibly" pin). Binary and data-gated: cold or
below ``min_samples`` -> no GO. ``lockup_cost`` = ``lockup_rate * total notional``; the
per-day x days-to-resolution folding is deferred deploy calibration.
"""

from dataclasses import dataclass
from decimal import Decimal

from polybot.maker.fees import rebate, taker_fee
from polybot.maker.inventory import _SGN, MakerFill, adverse_selection
from polybot.maker.netpnl import net_pnl

_HONEST = ("WON", "LOST")


@dataclass(frozen=True)
class MakerReport:
    category: str
    n_settled: int
    n_disputed: int
    n_void: int
    reward: Decimal | None
    rebate: Decimal | None
    spread_capture: Decimal | None
    adverse_selection: Decimal | None
    fees: Decimal | None
    lockup_cost: Decimal | None
    dispute_haircut: Decimal | None
    net: Decimal | None
    go: bool


class MakerTracker:
    def __init__(self, ledger, config):
        self._ledger = ledger
        self._config = config

    def report_for(self, category):
        c = self._config
        honest = []
        n_disputed = n_void = 0
        for r in self._ledger.settled(category):
            if r.status in _HONEST:
                honest.append(r)
            elif r.status == "DISPUTED":
                n_disputed += 1
            elif r.status == "VOID":
                n_void += 1
            else:
                # Exhaustive: a status outside {WON,LOST,DISPUTED,VOID} (DB corruption, or a
                # future 5th VALID_STATUSES not taught here) must fail loud, never silently
                # vanish from the accounting.
                raise ValueError(f"unhandled settlement status {r.status!r}")

        n = len(honest)
        if n == 0:  # cold -- no honest settled sample yet (shadow-only, data-gated dormant)
            return MakerReport(category, 0, n_disputed, n_void,
                               None, None, None, None, None, None, None, None, False)

        reward = sum((r.reward_accrued for r in honest), Decimal(0))
        cf_total = sum((taker_fee(r.category, r.price_exec, r.shares, schedule=c.fee_schedule)
                        for r in honest), Decimal(0))
        spread_capture = sum((_SGN[r.side] * r.shares * (r.fill_mid - r.price_exec)
                              for r in honest), Decimal(0))
        notional = sum((r.shares * r.price_exec for r in honest), Decimal(0))
        marks = {r.token_id: r.resolution_value for r in honest}
        fills = [MakerFill(token_id=r.token_id, condition_id=r.condition_id,
                           category=r.category, side=r.side, shares=r.shares,
                           price_exec=r.price_exec, fill_mid=r.fill_mid) for r in honest]
        pnl = net_pnl(reward=reward,
                      rebate=rebate(cf_total, fraction=c.rebate_fraction),
                      spread_capture=spread_capture,
                      adverse_selection=adverse_selection(fills, marks.get),
                      fees=c.forced_taker_exit_p * cf_total,
                      lockup_cost=c.lockup_rate * notional,
                      dispute_haircut=c.dispute_p * notional)
        # GO reads .net ONLY (never a gross leg): enough sample AND net STRICTLY over the margin.
        go = n >= c.min_samples and pnl.net > c.net_margin_min
        return MakerReport(category, n, n_disputed, n_void, pnl.reward, pnl.rebate,
                           pnl.spread_capture, pnl.adverse_selection, pnl.fees,
                           pnl.lockup_cost, pnl.dispute_haircut, pnl.net, go)
```

- [ ] **4. Run it — GREEN:**
  `./.venv/bin/pytest tests/test_maker_gate.py -o addopts="" -q` → `10 passed`.
- [ ] **5. Full suite:** `./.venv/bin/pytest -o addopts="" -q` — all prior + 4 new pass.
- [ ] **6. Commit:**
  `git add src/polybot/maker/gate.py tests/test_maker_gate.py && git commit -m "S8d D6: DISPUTED/VOID counted-and-excluded (whale-flip immunity by net value) + loud on unknown status"`

---

### Task D7: gate — the MakerGate facade (go_for, report_for, config-injected decide_quote)

- [ ] **1. Write the failing tests** — in `tests/test_maker_gate.py`, FIRST update the imports
  at the top of the file:

```python
# OLD:
from polybot.maker.gate import MakerTracker

# NEW:
from polybot.maker.gate import MakerGate, MakerTracker
from polybot.maker.quote_policy import PULL, QUOTE
```

  then append:

```python
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
```

- [ ] **2. Run it — RED for the right reason:**
  `./.venv/bin/pytest tests/test_maker_gate.py -o addopts="" -q`
  Expected: collection error `ImportError: cannot import name 'MakerGate' from
  'polybot.maker.gate'`.

- [ ] **3. Minimal implementation** — in `src/polybot/maker/gate.py`, add ONE import line
  (with the other `polybot.maker` imports, keeping them alphabetical):

```python
from polybot.maker import quote_policy
```

  and append the facade class at the end of the file:

```python
class MakerGate:
    """The thin facade -- the single seam S9 wires into (ANDed with the calibration ``k``).

    Mirrors ``CalibrationGate``: composes the tracker and exposes exactly what the harness
    needs -- the binary ``go_for``, the honest ``report_for`` breakdown, and ``decide_quote``
    with the gate's own config injected (a caller may NOT supply ``config``)."""

    def __init__(self, ledger, config):
        self._tracker = MakerTracker(ledger, config)
        self._config = config

    def go_for(self, category):
        """The binary GO/NO-GO for a category: True only when the honest net sample clears."""
        return self._tracker.report_for(category).go

    def report_for(self, category):
        return self._tracker.report_for(category)

    def decide_quote(self, **kw):
        """Delegates to ``quote_policy.decide_quote`` injecting this gate's config; a caller
        passing ``config`` raises TypeError (duplicate keyword) -- never a silent override."""
        return quote_policy.decide_quote(**kw, config=self._config)
```

- [ ] **4. Run it — GREEN:**
  `./.venv/bin/pytest tests/test_maker_gate.py -o addopts="" -q` → `13 passed`.
- [ ] **5. Full suite:** `./.venv/bin/pytest -o addopts="" -q` — all prior + 3 new pass.
- [ ] **6. Commit:**
  `git add src/polybot/maker/gate.py tests/test_maker_gate.py && git commit -m "S8d D7: MakerGate facade -- go_for/report_for passthrough + decide_quote with gate-injected config"`

---

### Task D8: e2e — the §7.3 lifecycle (fills → settlements → go progression + honest breakdown)

- [ ] **1. Write the test** — create `tests/test_maker_e2e.py`:

```python
"""S8 / POL-10 — whole-slice e2e (design §7.3): ledger -> tracker -> gate -> quote policy."""

from decimal import Decimal

from polybot.core.clock import MonotonicStamper
from polybot.maker.config import DEFAULT_FEE_SCHEDULE, MakerConfig
from polybot.maker.gate import MakerGate
from polybot.maker.ledger import MakerLedger
from polybot.maker.quote_policy import PULL

_VALUE = {"WON": Decimal("1"), "LOST": Decimal("0"), "DISPUTED": None, "VOID": None}


def _fill(ledger, fill_id, *, category, side, shares, price, mid, reward, status=None):
    ledger.record_fill(fill_id, token_id=f"tok-{fill_id}", condition_id="c", category=category,
                       side=side, shares=Decimal(shares), price_exec=Decimal(price),
                       fill_mid=Decimal(mid), reward_accrued=Decimal(reward))
    if status is not None:
        ledger.record_settlement(fill_id, status=status, resolution_value=_VALUE[status])


def test_lifecycle_go_progression_and_honest_breakdown(tmp_path):
    """One MakerLedger fed a sports-category shadow session: fills accrue, settlements land
    (WON/LOST + one DISPUTED), and go flips True only once the sample clears min_samples AND
    net beats the margin -- with the full honest breakdown verified leg by leg.

    Config: min_samples 4, net_margin_min 0.05, rebate_fraction 0.20 (default),
            forced_taker_exit_p 0.10, lockup_rate/dispute_p 0 (defaults).
    Fills (sports: active, fee_rate 0.03, exponent 1):
      f1 BUY  10 @ 0.48, mid 0.50, reward 0.05, WON  (mark 1)
      f2 SELL 10 @ 0.52, mid 0.50, reward 0.05, LOST (mark 0)
      f3 BUY  10 @ 0.30, mid 0.31, reward 0.05, LOST (mark 0)
      fD BUY  10 @ 0.90, mid 0.90, reward 0.01, DISPUTED  (excluded from every leg)
      f4 SELL 10 @ 0.60, mid 0.58, reward 0.05, LOST (mark 0)
    Final breakdown (hand-computed, checked twice):
      reward  = 4 * 0.05                                              = 0.20
      cf_1 = 10*0.03*0.48*0.52 = 0.07488 ; cf_2 = 10*0.03*0.52*0.48   = 0.07488
      cf_3 = 10*0.03*0.30*0.70 = 0.063   ; cf_4 = 10*0.03*0.60*0.40   = 0.072
      sum cf  = 0.07488 + 0.07488 + 0.063 + 0.072                     = 0.28476
      rebate  = 0.20 * 0.28476                                        = 0.056952
      spread  = +10*(0.50-0.48) - 10*(0.50-0.52) + 10*(0.31-0.30) - 10*(0.58-0.60)
              = 0.20 + 0.20 + 0.10 + 0.20                             = 0.70
      adverse = +10*(0.48-1) - 10*(0.52-0) + 10*(0.30-0) - 10*(0.60-0)
              = -5.2 - 5.2 + 3.0 - 6.0                                = -13.4   (favorable)
      fees    = 0.10 * 0.28476                                        = 0.028476
      net     = 0.20 + 0.056952 + 0.70 - (-13.4) - 0.028476           = 14.328476
    Interim (f1..f3 settled, n=3): reward 0.15 ; sum cf 0.21276 ; rebate 0.042552 ;
      spread 0.50 ; adverse -7.4 ; fees 0.021276 ; net = 8.071276 > margin -- but n 3 < 4.
    """
    cfg = MakerConfig(fee_schedule=DEFAULT_FEE_SCHEDULE, min_samples=4,
                      net_margin_min=Decimal("0.05"), forced_taker_exit_p=Decimal("0.10"))
    with MakerLedger(str(tmp_path / "e2e.db"), MonotonicStamper()) as l:
        g = MakerGate(l, cfg)
        assert g.go_for("sports") is False  # cold: nothing settled yet

        _fill(l, "f1", category="sports", side="BUY", shares="10", price="0.48", mid="0.50",
              reward="0.05", status="WON")
        _fill(l, "f2", category="sports", side="SELL", shares="10", price="0.52", mid="0.50",
              reward="0.05", status="LOST")
        _fill(l, "f3", category="sports", side="BUY", shares="10", price="0.30", mid="0.31",
              reward="0.05", status="LOST")
        _fill(l, "fD", category="sports", side="BUY", shares="10", price="0.90", mid="0.90",
              reward="0.01", status="DISPUTED")

        r3 = g.report_for("sports")
        assert r3.n_settled == 3 and r3.n_disputed == 1
        assert r3.net == Decimal("8.071276")
        assert r3.net > cfg.net_margin_min and r3.go is False  # healthy net, sample not cleared

        _fill(l, "f4", category="sports", side="SELL", shares="10", price="0.60", mid="0.58",
              reward="0.05", status="LOST")
        r4 = g.report_for("sports")
        assert r4.n_settled == 4 and r4.n_disputed == 1 and r4.n_void == 0
        assert r4.reward == Decimal("0.20")
        assert r4.rebate == Decimal("0.056952")
        assert r4.spread_capture == Decimal("0.70")
        assert r4.adverse_selection == Decimal("-13.4")
        assert r4.fees == Decimal("0.028476")
        assert r4.lockup_cost == Decimal("0") and r4.dispute_haircut == Decimal("0")
        assert r4.net == Decimal("14.328476")
        assert r4.go is True and g.go_for("sports") is True
```

- [ ] **2. Run it — observe:**
  `./.venv/bin/pytest tests/test_maker_e2e.py -o addopts="" -q`
  This is an INTEGRATION PIN over units already built RED→GREEN in S8a–S8d — expected: `1
  passed` on first run. If it FAILS, the fragment's hand arithmetic and the implementation
  disagree at a seam: STOP and re-derive the numbers by hand; NEVER edit an expectation to
  match observed output without the re-derivation proving the observed value right.
- [ ] **3. Implementation:** none expected (no source change for a passing integration pin).
  If step 2 exposed a real seam bug, fix it in the offending module with its own RED test
  there first, then return here.
- [ ] **4. Run it — GREEN:**
  `./.venv/bin/pytest tests/test_maker_e2e.py -o addopts="" -q` → `1 passed`.
- [ ] **5. Full suite:** `./.venv/bin/pytest -o addopts="" -q` — all prior + 1 new pass.
- [ ] **6. Commit:**
  `git add tests/test_maker_e2e.py && git commit -m "S8d D8: whole-slice e2e lifecycle -- go progression + full honest breakdown, DISPUTED excluded"`

---

### Task D9: e2e — the toxic PULL cycle + the "bleeds invisibly" category caught

- [ ] **1. Write the tests** — append to `tests/test_maker_e2e.py`:

```python
def test_toxic_pull_quotes_cycle_pulls(tmp_path):
    # the D1 toxicity seam through the facade: a toxic cycle PULLs regardless of economics.
    with MakerLedger(str(tmp_path / "e2e.db"), MonotonicStamper()) as l:
        g = MakerGate(l, MakerConfig(fee_schedule=DEFAULT_FEE_SCHEDULE))
        assert g.decide_quote(pull_quotes=True, recent_adverse=Decimal("0"),
                              break_even=Decimal("0.05"), locked_effective=Decimal("0"),
                              locked_cap=Decimal("100")) == PULL


def test_bleeds_invisibly_category_is_caught(tmp_path):
    """Design §7.3: a category whose adverse selection exceeds reward+rebate+spread reports
    net < 0 and go False -- the "safe" reward-gross illusion is caught by construction.

    Config: min_samples 4, forced_taker_exit_p 0.10 (rebate 0.20, margin 0 -- defaults).
    Fills (sports; heavy reward accrual, but the flow is toxic -- mostly LOST longs):
      b1 BUY 10 @ 0.60, mid 0.62, reward 0.30, LOST: spread 0.20, adverse +6.0
      b2 BUY 10 @ 0.55, mid 0.56, reward 0.30, LOST: spread 0.10, adverse +5.5
      b3 BUY 10 @ 0.50, mid 0.51, reward 0.30, WON : spread 0.10, adverse 10*(0.50-1) = -5.0
      b4 BUY 10 @ 0.65, mid 0.66, reward 0.30, LOST: spread 0.10, adverse +6.5
    Hand-computed (checked twice):
      reward = 1.20 ; spread = 0.50 ; adverse = 6.0 + 5.5 - 5.0 + 6.5 = 13.0
      cf = 10*0.03*(0.60*0.40 + 0.55*0.45 + 0.50*0.50 + 0.65*0.35)
         = 0.072 + 0.07425 + 0.075 + 0.06825                          = 0.2895
      rebate = 0.20*0.2895 = 0.0579 ; fees = 0.10*0.2895              = 0.02895
      gross  = 1.20 + 0.0579 + 0.50                                   = 1.7579  (looks GREAT)
      net    = 1.7579 - 13.0 - 0.02895                                = -11.27105
    """
    cfg = MakerConfig(fee_schedule=DEFAULT_FEE_SCHEDULE, min_samples=4,
                      forced_taker_exit_p=Decimal("0.10"))
    with MakerLedger(str(tmp_path / "e2e.db"), MonotonicStamper()) as l:
        _fill(l, "b1", category="sports", side="BUY", shares="10", price="0.60", mid="0.62",
              reward="0.30", status="LOST")
        _fill(l, "b2", category="sports", side="BUY", shares="10", price="0.55", mid="0.56",
              reward="0.30", status="LOST")
        _fill(l, "b3", category="sports", side="BUY", shares="10", price="0.50", mid="0.51",
              reward="0.30", status="WON")
        _fill(l, "b4", category="sports", side="BUY", shares="10", price="0.65", mid="0.66",
              reward="0.30", status="LOST")
        r = MakerGate(l, cfg).report_for("sports")
    assert r.n_settled == 4  # the sample floor IS met -- only the net stops this one
    assert r.reward + r.rebate + r.spread_capture == Decimal("1.7579")  # reward-gross positive
    assert r.net == Decimal("-11.27105") and r.net < 0
    assert r.go is False
```

- [ ] **2. Run it — observe:**
  `./.venv/bin/pytest tests/test_maker_e2e.py -o addopts="" -q`
  Integration pin, same doctrine as D8 — expected: `3 passed` on first run; any failure means
  hand arithmetic vs implementation disagree — STOP and re-derive, never adjust expectations
  to observed output.
- [ ] **3. Implementation:** none expected (see D8 step 3 doctrine).
- [ ] **4. Run it — GREEN:**
  `./.venv/bin/pytest tests/test_maker_e2e.py -o addopts="" -q` → `3 passed`.
- [ ] **5. Full suite:** `./.venv/bin/pytest -o addopts="" -q` — all prior + 2 new pass
  (= 853 + all S8a–S8c + 32 S8d tests — the assembler reconciles the exact total).
- [ ] **6. Commit:**
  `git add tests/test_maker_e2e.py && git commit -m "S8d D9: e2e toxic PULL cycle + bleeds-invisibly category caught -- gross 1.7579 vs net -11.27105, no GO"`
