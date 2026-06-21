"""Unit tests for the log-normal timing primitive (core/humanize.py).

All tests are deterministic: they inject either a seeded ``random.Random(...).random``
or an explicit fixed/cycling list as the rng, so no assertion depends on real
randomness or global RNG state.
"""

import itertools
import math
import random

import pytest

from core.humanize import human_delay, human_sleep


def _cycle_rng(values):
    """Return a 0-arg callable that yields ``values`` forever (a deterministic rng)."""
    it = itertools.cycle(values)
    return lambda: next(it)


# 1. Importability is proven by the module-level import above succeeding.
def test_imports_are_callable():
    assert callable(human_delay)
    assert callable(human_sleep)


# 2. Extreme injected rng values (near 0 and near 1) stay positive and within [lo, hi].
def test_clamp_near_zero_extreme():
    # u1 near 0, u2 near 0 -> cos(0)=1 -> large positive z -> huge delay -> clamped to hi.
    rng = _cycle_rng([1e-9, 1e-9])
    d = human_delay(3.0, sigma=0.5, lo=1.5, hi=5.5, rng=rng)
    assert d > 0
    assert 1.5 <= d <= 5.5
    assert d == pytest.approx(5.5)  # extreme high -> hits the upper clamp


def test_clamp_near_one_extreme():
    # u1 near 1 -> log(u1) ~ 0 -> z ~ 0 -> delay ~ mean*exp(-sigma^2/2) (small-ish),
    # but pair it so we also probe the low side firmly.
    rng = _cycle_rng([1.0 - 1e-9, 0.5])
    d = human_delay(3.0, sigma=0.5, lo=1.5, hi=5.5, rng=rng)
    assert d > 0
    assert 1.5 <= d <= 5.5


def test_clamp_drives_to_low_bound():
    # u1 near 0 (big magnitude), u2 near 0.5 -> cos(pi) = -1 -> large negative z
    # -> delay -> ~0 -> clamped up to lo.
    rng = _cycle_rng([1e-12, 0.5])
    d = human_delay(3.0, sigma=0.5, lo=1.5, hi=5.5, rng=rng)
    assert d > 0
    assert d == pytest.approx(1.5)  # extreme low -> hits the lower clamp


def test_zero_u1_does_not_blow_up():
    # rng yields exactly 0.0 for u1: must be guarded so log() is finite, not -inf/NaN.
    rng = _cycle_rng([0.0, 0.5])
    d = human_delay(3.0, sigma=0.5, lo=1.5, hi=5.5, rng=rng)
    assert math.isfinite(d)  # guarded log(u1) -> finite, not -inf/NaN
    assert d > 0
    assert 1.5 <= d <= 5.5


# 3. Distribution is right-skewed: median < mean => >55% of samples below the mean.
def test_right_skewed_not_uniform():
    mean = 3.0
    rng = random.Random(1234).random
    n = 5000
    samples = [human_delay(mean, sigma=0.5, rng=rng) for _ in range(n)]
    below = sum(1 for s in samples if s < mean)
    frac_below = below / n
    # A symmetric/uniform distribution would put ~50% below the mean; log-normal puts
    # the median below the mean, so well over half fall below it.
    assert frac_below > 0.55, f"expected >55% below mean, got {frac_below:.3f}"


# 4. Sample mean approximates the requested mean (mean-preservation).
def test_mean_preservation():
    mean = 3.0
    rng = random.Random(2026).random
    n = 20000
    samples = [human_delay(mean, sigma=0.5, rng=rng) for _ in range(n)]
    sample_mean = sum(samples) / n
    # Log-normal sample means converge slowly; 15% tolerance is robustly green here.
    assert sample_mean == pytest.approx(mean, rel=0.15), f"sample mean {sample_mean}"


# 5. Determinism: identical injected rng sequence => identical outputs.
def test_determinism_seeded_random():
    seed = 99
    rng_a = random.Random(seed).random
    rng_b = random.Random(seed).random
    out_a = [human_delay(2.5, sigma=0.4, rng=rng_a) for _ in range(50)]
    out_b = [human_delay(2.5, sigma=0.4, rng=rng_b) for _ in range(50)]
    assert out_a == out_b


def test_determinism_fixed_list_rng():
    # Two independent cycling rngs over the same fixed list must produce identical seqs.
    values = [0.1, 0.9, 0.3, 0.7, 0.05, 0.95]
    rng_a = _cycle_rng(values)
    rng_b = _cycle_rng(values)
    out_a = [human_delay(1.2, sigma=0.6, rng=rng_a) for _ in range(20)]
    out_b = [human_delay(1.2, sigma=0.6, rng=rng_b) for _ in range(20)]
    assert out_a == out_b


# 6. human_sleep calls the injected sleep with the delay and returns it.
def test_human_sleep_calls_sleep_and_returns_delay():
    recorded = []

    def spy_sleep(d):
        recorded.append(d)

    rng = _cycle_rng([0.4, 0.6])
    result = human_sleep(2.0, sigma=0.5, lo=1.0, hi=5.0, sleep=spy_sleep, rng=rng)

    assert len(recorded) == 1
    assert recorded[0] == result
    assert result > 0
    assert 1.0 <= result <= 5.0


def test_human_sleep_matches_human_delay():
    # With the same rng sequence, human_sleep's returned delay equals human_delay's.
    seed = 7
    expected = human_delay(2.0, sigma=0.5, rng=random.Random(seed).random)
    got = human_sleep(2.0, sigma=0.5, sleep=lambda _d: None, rng=random.Random(seed).random)
    assert got == expected
