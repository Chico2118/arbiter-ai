"""Small, dependency-free statistics used by the gap report.

- Wilson score interval for a binomial proportion (good coverage at small n).
- Exact two-sided binomial test, used for McNemar's test on paired outcomes
  (discordant pairs only) and for the sign test on blind votes (ties dropped).
- Cohen's kappa for judge-vs-human agreement.
"""

from __future__ import annotations

import math
from statistics import median
from typing import Iterable, Sequence

Z95 = 1.959963984540054


def wilson(successes: int, n: int, z: float = Z95) -> tuple[float, float]:
    """Wilson score interval (low, high) for successes/n. (0, 1) when n == 0."""
    if n <= 0:
        return 0.0, 1.0
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def binom_two_sided(k: int, n: int) -> float:
    """Exact two-sided p-value for k successes in n trials with p = 0.5.

    Computed as min(1, 2 * P(X <= min(k, n - k))), the standard form for the
    symmetric p = 0.5 case.
    """
    if n <= 0:
        return 1.0
    lo = min(k, n - k)
    tail = sum(math.comb(n, i) for i in range(lo + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def mcnemar_exact(only_a: int, only_b: int) -> float:
    """Exact McNemar test p-value from the two discordant counts."""
    return binom_two_sided(only_a, only_a + only_b)


def sign_test(wins: int, losses: int) -> float:
    """Two-sided sign test; ties must be excluded by the caller."""
    return binom_two_sided(wins, wins + losses)


def cohen_kappa(a: Sequence[bool], b: Sequence[bool]) -> float | None:
    """Cohen's kappa for two binary raters. None when undefined (n == 0).

    When expected agreement is 1 (both raters constant and identical) kappa is
    undefined; we return 1.0 if observed agreement is also perfect.
    """
    if len(a) != len(b):
        raise ValueError("rater sequences must have equal length")
    n = len(a)
    if n == 0:
        return None
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    pa, pb = sum(a) / n, sum(b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    if pe >= 1.0:
        return 1.0 if po == 1.0 else 0.0
    return (po - pe) / (1 - pe)


def mean(values: Iterable[float]) -> float | None:
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def med(values: Iterable[float]) -> float | None:
    vals = [v for v in values if v is not None]
    return float(median(vals)) if vals else None
