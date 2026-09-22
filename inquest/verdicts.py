"""Rule-based verdict engine (component C10). No language model participates in a verdict.

Rules are evaluated in the order of strategy section 8.1; the first that applies wins.
Every verdict carries the rule path that produced it.
"""
from __future__ import annotations

from typing import Optional

from .schemas import Band, ClaimVerdict, SelectionSignal, Share

SCI_FLOOR = 0.4
PARTIAL_FRACTION = 0.30
SELECTION_K = 10.0


def _inside(x: float, band: Optional[Band]) -> bool:
    return band is not None and band.lo <= x <= band.hi


def decide(claim_id: str, reported: float, *, preflight: Optional[dict] = None, mapping_status: str = "mapped",
           mapping_reason: Optional[str] = None, runnable: bool = True, baseline_failed: bool = False,
           baseline_error: Optional[str] = None, seed_band: Optional[Band] = None, aligned_band: Optional[Band] = None,
           spec_band: Optional[tuple[float, float]] = None, attribution: Optional[dict] = None,
           selection: Optional[SelectionSignal] = None, sci: float = 0.0, flags: Optional[list[str]] = None
           ) -> ClaimVerdict:
    flags = list(flags or [])
    reasons: list[str] = []
    shares = [Share.model_validate(s) for s in (attribution or {}).get("shares", [])] or None
    residual = (attribution or {}).get("residual")

    def out(verdict: str) -> ClaimVerdict:
        if selection and selection.k_lower >= SELECTION_K and "SELECTION_SIGNAL" not in flags:
            flags.append("SELECTION_SIGNAL")
        if attribution and attribution.get("pruned_joint_effect") and "PRUNED_JOINT_EFFECT" not in flags:
            flags.append("PRUNED_JOINT_EFFECT")
        return ClaimVerdict(claim_id=claim_id, verdict=verdict, reported=reported, seed_band=seed_band,
                            aligned_band=aligned_band, spec_band=spec_band, selection=selection, attribution=shares,
                            residual=residual, sci=sci, flags=flags, reasons=reasons)

    if preflight and preflight.get("status") == "impossible":
        reasons.append(f"GRIM: {preflight.get('reason')}")
        return out("NUMERICALLY_IMPOSSIBLE")
    if mapping_status == "not_implemented":
        reasons.append(f"No code path produces this claim: {mapping_reason or 'not found in the repository'}")
        return out("NOT_IMPLEMENTED")
    if mapping_status == "unmappable" or (sci < SCI_FLOOR and not runnable):
        reasons.append(mapping_reason or f"No runnable experiment mapped (SCI {sci:.2f})")
        return out("UNVERIFIABLE")
    if baseline_failed:
        reasons.append(f"Baseline run failed: {baseline_error or 'unknown error'}")
        return out("NOT_EXECUTABLE")
    if _inside(reported, seed_band):
        reasons.append(f"{reported} lies inside the baseline seed band [{seed_band.lo:.2f}, {seed_band.hi:.2f}]")
        return out("REPRODUCED")
    if seed_band:
        reasons.append(f"{reported} lies outside the baseline seed band [{seed_band.lo:.2f}, {seed_band.hi:.2f}]")
    if attribution and _inside(reported, aligned_band):
        reasons.append(f"After aligning the attributed deviations, {reported} lies inside the aligned band "
                       f"[{aligned_band.lo:.2f}, {aligned_band.hi:.2f}]")
        return out("NOT_REPRODUCED_EXPLAINED")
    if spec_band and spec_band[0] <= reported <= spec_band[1]:
        reasons.append(f"{reported} lies inside the specification band [{spec_band[0]:.2f}, {spec_band[1]:.2f}]: "
                       "the paper's text permits it")
        return out("UNDERSPECIFIED")
    if attribution and attribution.get("gap"):
        gap = attribution["gap"]
        explained = sum(s.points for s in (shares or []) if not s.is_noise)
        frac = explained / gap if gap else 0.0
        if frac >= PARTIAL_FRACTION:
            reasons.append(f"Attribution explains {frac:.0%} of the {gap:+.2f}-point gap; the residual lies outside "
                           "all bands")
            return out("NOT_REPRODUCED_PARTIALLY_EXPLAINED")
        reasons.append(f"Attribution explains {frac:.0%} of the {gap:+.2f}-point gap")
    return out("NOT_REPRODUCED_UNEXPLAINED")
