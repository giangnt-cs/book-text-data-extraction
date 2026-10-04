"""Phép chiếu Poincaré và Otsu 1D — port từ knowledge-extraction/src/poincare.py."""

from __future__ import annotations

import math
from typing import Sequence


class PoincareMobiusOps:
    def __init__(self, c: float = 1.0, eps: float = 1e-5):
        if c <= 0:
            raise ValueError("Độ cong Poincaré c phải > 0")
        self.c = c
        self.eps = eps

    def _dot(self, x: Sequence[float], y: Sequence[float]) -> float:
        return sum(a * b for a, b in zip(x, y))

    def _sum_sq(self, x: Sequence[float]) -> float:
        return sum(a * a for a in x)

    def _norm(self, x: Sequence[float]) -> float:
        return math.sqrt(self._sum_sq(x))

    def mobius_add(self, x: Sequence[float], y: Sequence[float]) -> list[float]:
        if len(x) != len(y) or not x:
            raise ValueError("Hai vector phải cùng số chiều và khác rỗng")
        c = self.c
        x2 = self._sum_sq(x)
        y2 = self._sum_sq(y)
        xy = self._dot(x, y)
        num_scale_x = 1 + 2 * c * xy + c * y2
        num_scale_y = 1 - c * x2
        denom = 1 + 2 * c * xy + (c**2) * x2 * y2 + self.eps
        return [(num_scale_x * xi + num_scale_y * yi) / denom for xi, yi in zip(x, y)]

    def exp_map_zero(self, v: Sequence[float]) -> list[float]:
        if not v:
            raise ValueError("Vector chiếu không được rỗng")
        v_norm = max(self._norm(v), self.eps)
        sqrt_c = self.c**0.5
        factor = math.tanh(sqrt_c * v_norm) / (sqrt_c * v_norm)
        return [factor * vi for vi in v]

    def distance(self, x: Sequence[float], y: Sequence[float]) -> float:
        diff = self.mobius_add([-xi for xi in x], y)
        diff_norm = max(self._norm(diff), self.eps)
        sqrt_c = self.c**0.5
        arg = min(sqrt_c * diff_norm, 1.0 - 1e-5)
        return (2.0 / sqrt_c) * math.atanh(arg)

    def project_and_distance(self, vec_a: Sequence[float], vec_b: Sequence[float]) -> float:
        return self.distance(self.exp_map_zero(vec_a), self.exp_map_zero(vec_b))


def otsu_1d_threshold(data_points: Sequence[float], n_bins: int = 256) -> tuple[float, float]:
    data = list(data_points)
    if not data:
        return 0.0, 0.0
    if n_bins < 2:
        raise ValueError("n_bins phải >= 2")
    lo = min(data)
    hi = max(data)
    if hi <= lo:
        return lo, 0.0
    width = (hi - lo) / n_bins
    counts = [0] * n_bins
    n = len(data)
    for value in data:
        idx = int((value - lo) / width)
        idx = min(max(idx, 0), n_bins - 1)
        counts[idx] += 1
    p = [count / n for count in counts]
    bin_centers = [lo + (i + 0.5) * width for i in range(n_bins)]
    omega = [0.0] * n_bins
    mu = [0.0] * n_bins
    acc_p = 0.0
    acc_mu = 0.0
    for i in range(n_bins):
        acc_p += p[i]
        acc_mu += p[i] * bin_centers[i]
        omega[i] = acc_p
        mu[i] = acc_mu
    mu_total = mu[-1]
    best_idx = 0
    best_sigma = -1.0
    for i in range(n_bins):
        if omega[i] <= 0.0 or omega[i] >= 1.0:
            continue
        value = ((mu_total * omega[i] - mu[i]) ** 2) / (omega[i] * (1.0 - omega[i]))
        if value > best_sigma:
            best_sigma = value
            best_idx = i
    if best_sigma < 0.0:
        return lo, 0.0
    sigma_total = sum(p[i] * (bin_centers[i] - mu_total) ** 2 for i in range(n_bins))
    eta = best_sigma / (sigma_total + 1e-8)
    return bin_centers[best_idx], eta
