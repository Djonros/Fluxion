"""A banded solver for the stiffness matrices of beams and frames.

numpy.linalg.solve goes to a multithreaded OpenBLAS, which on some machines
spends a third of a second on a 500×500 system starting its threads.  The
stiffness matrix of a beam or of a frame numbered along its length is banded
(a dozen to a few dozen columns), and the elimination inside the band is a
few hundred small vector operations: milliseconds, the same on every machine.
The matrix is symmetric positive definite once the supports are applied, so
no pivoting; a zero pivot means a mechanism.
"""
from __future__ import annotations

import numpy as np


class Singular(ValueError):
    """The structure is a mechanism: not enough supports."""


def bandwidth(a: np.ndarray) -> int:
    rows, cols = np.nonzero(a)
    return int(np.max(np.abs(rows - cols))) if len(rows) else 0


def solve(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    a = np.array(a, dtype=float)
    b = np.array(b, dtype=float)
    n = len(b)
    bw = bandwidth(a)
    scale = np.max(np.abs(np.diag(a))) if n else 1.0
    for k in range(n):
        pivot = a[k, k]
        if abs(pivot) <= 1e-12 * scale:
            raise Singular
        end = min(n, k + bw + 1)
        factors = a[k + 1:end, k] / pivot
        a[k + 1:end, k:end] -= np.outer(factors, a[k, k:end])
        b[k + 1:end] -= factors * b[k]
    x = np.zeros(n)
    for k in range(n - 1, -1, -1):
        end = min(n, k + bw + 1)
        x[k] = (b[k] - a[k, k + 1:end] @ x[k + 1:end]) / a[k, k]
    return x
