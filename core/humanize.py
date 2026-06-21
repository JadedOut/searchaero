"""Log-normal timing primitive for human-shaped delays (the keystone helper).

Naive anti-bot "jitter" with ``random.uniform()`` produces a *flat* distribution
that is itself a detectable fingerprint ("the jitter trap"). Real human inter-event
timing is right-skewed: most events cluster a little below the mean, with a long tail
of occasional longer pauses. This module samples **mean-preserving log-normal** delays
so the average matches a requested ``mean`` while the *shape* looks human.

Mean-preserving property
-------------------------
A log-normal variable ``exp(mu + sigma*z)`` (z ~ standard normal) has expected value
``exp(mu + sigma**2 / 2)``. To make that expectation equal the requested ``mean`` we
pick ``mu = ln(mean) - sigma**2 / 2``; then ``E[delay] == mean`` regardless of sigma.
The distribution is right-skewed, so the *median* sits below the mean — more than half
of samples fall under ``mean`` — which is exactly the not-uniform signal we want.

Determinism / testability
--------------------------
The ONLY source of randomness is the injectable ``rng`` callable (a 0..1 uniform
source, default :func:`random.random`). Tests inject a deterministic ``rng`` (a seeded
``random.Random(...).random`` or a fixed cycling list) to get reproducible output.
:func:`human_sleep` additionally takes an injectable ``sleep`` (default
:func:`time.sleep`) so tests never actually block.
"""

import math
import random
import time

# Smallest u1 we allow into log(u1) so the Box-Muller transform stays finite when an
# injected rng yields exactly 0.0.
_EPSILON = 1e-12


def human_delay(mean, *, sigma=0.5, lo=None, hi=None, rng=random.random) -> float:
    """Sample one mean-preserving log-normal delay (seconds), clamped to ``[lo, hi]``.

    The expected value of the (unclamped) sample equals ``mean`` because
    ``mu = ln(mean) - sigma**2 / 2`` cancels the log-normal's ``exp(sigma**2 / 2)``
    inflation. A standard-normal ``z`` is produced by Box-Muller from two ``rng()``
    draws, so the result is right-skewed (human-shaped), never uniform.

    Args:
        mean: Target mean delay in seconds (must be > 0).
        sigma: Log-space standard deviation; larger => more spread / longer tail.
        lo: Optional lower clamp (seconds). If given, result is at least ``lo``.
        hi: Optional upper clamp (seconds). If given, result is at most ``hi``.
        rng: Injectable 0..1 uniform source (the ONLY randomness). Default
            :func:`random.random`. Tests pass a deterministic callable.

    Returns:
        A positive float delay in seconds, clamped into ``[lo, hi]`` when provided.
    """
    mu = math.log(mean) - sigma ** 2 / 2.0

    # Box-Muller: two uniform draws -> one standard-normal z.
    u1 = rng()
    u2 = rng()
    if u1 < _EPSILON:
        u1 = _EPSILON
    z = math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)

    delay = math.exp(mu + sigma * z)

    if lo is not None:
        delay = max(delay, lo)
    if hi is not None:
        delay = min(delay, hi)
    return delay


def human_sleep(mean, *, sigma=0.5, lo=None, hi=None, sleep=time.sleep, rng=random.random):
    """Sleep for a :func:`human_delay` sample, then return the duration slept.

    Convenience wrapper around :func:`human_delay`. Both the randomness source
    (``rng``) and the blocking call (``sleep``) are injectable so tests can run it
    deterministically and without actually pausing.

    Args:
        mean: Target mean delay in seconds.
        sigma: Log-space standard deviation (see :func:`human_delay`).
        lo: Optional lower clamp (seconds).
        hi: Optional upper clamp (seconds).
        sleep: Injectable blocking call, default :func:`time.sleep`.
        rng: Injectable 0..1 uniform source, default :func:`random.random`.

    Returns:
        The delay (seconds) that was passed to ``sleep``.
    """
    d = human_delay(mean, sigma=sigma, lo=lo, hi=hi, rng=rng)
    sleep(d)
    return d
