"""Pre-flight forensics (component C13). Zero compute, run before any training.

GRIM for ML: accuracy on N samples is k/N, so a reported value must be a multiple of
100/(m·N) after rounding to the printed decimals. The test is silent whenever every
printed value is achievable, which is the common case on large test sets.

Cross-consistency: reported dispersion against the number of runs, and relative claims
recomputed from table values.
"""
from __future__ import annotations

import math
from typing import Optional

from .schemas import Claim

COUNT_METRICS = {"accuracy", "error", "error_rate", "precision_at_1", "hit_rate", "recall_at_1"}


def grim(value: float, decimals: int, n: int, m: int = 1) -> Optional[bool]:
    """True if `value` is achievable as 100·k/(m·n) at `decimals` places. None means the test is silent."""
    step = 100.0 / (m * n)
    if step <= 10 ** -decimals:
        return None
    k0 = round(value / step)
    return any(abs(round(k * step, decimals) - round(value, decimals)) < 1e-9 for k in (k0 - 1, k0, k0 + 1))


def nearest_achievable(value: float, decimals: int, n: int, m: int = 1) -> list[float]:
    step = 100.0 / (m * n)
    k0 = math.floor(value / step)
    return sorted({round(k * step, decimals) for k in (k0 - 1, k0, k0 + 1, k0 + 2)})


def check_claim(claim: Claim, observed_n: Optional[int] = None) -> dict:
    """Return {"status": "impossible" | "pass" | "silent" | "not_applicable", ...}."""
    if claim.metric not in COUNT_METRICS:
        return {"test": "GRIM", "status": "not_applicable", "reason": f"{claim.metric} is not a count-based metric"}
    n = observed_n or claim.n_test
    if not n:
        return {"test": "GRIM", "status": "not_applicable", "reason": "test-set size unknown"}
    m = claim.n_seeds_reported or 1
    res = grim(claim.value, claim.decimals, n, m)
    src = "observed at runtime" if observed_n else "stated in the paper"
    if res is None:
        return {"test": "GRIM", "status": "silent", "n": n, "m": m, "n_source": src,
                "reason": f"with N={n} and m={m} every value at {claim.decimals} decimals is achievable"}
    if res:
        return {"test": "GRIM", "status": "pass", "n": n, "m": m, "n_source": src}
    return {"test": "GRIM", "status": "impossible", "n": n, "m": m, "n_source": src,
            "achievable": nearest_achievable(claim.value, claim.decimals, n, m),
            "reason": f"{claim.value_text} is not a multiple of 100/({m}·{n}) at {claim.decimals} decimals"}


def dispersion_check(claim: Claim, measured_std: Optional[float]) -> Optional[dict]:
    """Compare a reported ± with the spread the code actually produces.

    A reported standard error s_e over m runs implies a run-to-run deviation of s_e·√m.
    A ratio beyond 3 in either direction is reported as an inconsistency signal.
    """
    if claim.dispersion is None or measured_std is None or measured_std <= 0:
        return None
    m = claim.n_seeds_reported or 1
    implied = claim.dispersion * math.sqrt(m) if claim.dispersion_kind == "stderr" else claim.dispersion
    ratio = measured_std / implied if implied > 0 else math.inf
    status = "inconsistent" if (ratio > 3 or ratio < 1 / 3) else "consistent"
    return {"test": "dispersion", "status": status, "reported": claim.dispersion,
            "kind": claim.dispersion_kind or "std", "implied_std": implied, "measured_std": measured_std,
            "ratio": ratio}


def relative_claims(claims: list[Claim], relations: list[dict]) -> list[dict]:
    """Recompute stated differences such as "+2.1 pp over baseline" from claim values."""
    by_id = {c.claim_id: c for c in claims}
    out = []
    for r in relations:
        a, b = by_id.get(r["claim"]), by_id.get(r["baseline"])
        if not a or not b:
            continue
        diff = round(a.value - b.value, max(a.decimals, b.decimals))
        ok = abs(diff - r["stated_delta"]) <= 0.5 * 10 ** -max(a.decimals, b.decimals) + 1e-9
        out.append({"test": "relative", "status": "consistent" if ok else "inconsistent", "claim": a.claim_id,
                    "baseline": b.claim_id, "stated": r["stated_delta"], "recomputed": diff})
    return out
