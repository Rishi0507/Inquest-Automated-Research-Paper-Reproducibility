"""Specification sweep and the Specification Completeness Index (component C14).

For parameters the paper leaves unstated or ambiguous, plausible alternatives are run and
the claim metric is measured. The specification band is the union of the seed bands of
every swept configuration: what an honest reader of the text could have obtained.

Alternative configurations run three seeds each. Their bands use the run-to-run standard
deviation measured by the baseline swarm (n = 20), which assumes the alternatives change
the mean rather than the spread; three-seed standard deviations alone would make the
prediction interval uninformative.
"""
from __future__ import annotations

import math
from typing import Callable, Sequence

import numpy as np
from scipy import stats

from . import patches, runner
from .schemas import Band, Claim, Deviation, RunRequest

SWEEP_SEEDS = (0, 1, 2)
MAX_PARAMS = 3
MAX_ALTERNATIVES = 2

# Common defaults for frequently omitted parameters, keyed by name.
CATALOGUE = {
    "weight_decay": [0.0, 1e-4, 5e-4],
    "dropout": [0.0, 0.5],
    "warmup_steps": [0, 500],
    "batch_size": ["x0.5", "x2"],
    "epochs": ["x0.5", "x2"],
    "lr": ["x0.1", "x10"],
}


def alternative_patches(dev: Deviation, current=None) -> list[tuple[str, dict]]:
    """(label, patch) pairs for the alternatives of one unspecified parameter."""
    cfg = patches.config_part(dev.patch)
    out: list[tuple[str, dict]] = []
    if cfg:
        key = next(iter(cfg))
        values = list(dev.alternatives) or CATALOGUE.get(key, [])
        if not values and isinstance(current, (int, float)) and not isinstance(current, bool) and current:
            values = ["x0.5", "x2"]  # generic plausible range: half and double the code's value
        for v in values:
            if isinstance(v, str) and v.startswith("x") and current is not None:
                v = type(current)(current * float(v[1:]))
            if v == current:
                continue
            out.append((f"{key}={v}", {key: v}))
    elif dev.patch.get("code"):
        label = f"{dev.param}={dev.alternatives[0]}" if dev.alternatives else f"{dev.param} (patched)"
        out.append((label, dev.patch))
    return out[:MAX_ALTERNATIVES]


def band_from(values: Sequence[float], pooled_std: float, n_pool: int, m: int) -> Band:
    k = len(values)
    mean = float(np.mean(values))
    t = float(stats.t.ppf(0.975, max(1, n_pool - 1)))
    half = t * pooled_std * math.sqrt(1.0 / m + 1.0 / k)
    return Band(mean=mean, std=pooled_std, n=k, m=m, lo=mean - half, hi=mean + half)


def sweep(paper_id: str, base_cfg: dict, claim: Claim, metric: str, unspecified: list[Deviation],
          baseline: Band, progress: Callable[[str], None] | None = None) -> dict:
    m = claim.n_seeds_reported or 1
    configs = [{"label": "repository as-is", "param": None, "band": baseline.model_dump(), "mean": baseline.mean}]
    costs: dict[str, float] = {}
    swept: list[str] = []
    for dev in unspecified[:MAX_PARAMS]:
        current = dev.repo_value
        means = [baseline.mean]
        for label, patch in alternative_patches(dev, current):
            probe = dev.model_copy(update={"patch": patch, "phase": "train"})
            checked = patches.verify(paper_id, base_cfg, probe)
            if not checked.patch_verified:
                configs.append({"label": label, "param": dev.param, "skipped": checked.note})
                continue
            cfg = patches.apply_patches(base_cfg, [patch])
            runs = runner.run_many(RunRequest(paper_id=paper_id, config=cfg, seed=s) for s in SWEEP_SEEDS)
            vals = [r.metrics[metric] for r in runs if r.ok and metric in r.metrics]
            if not vals:
                configs.append({"label": label, "param": dev.param, "skipped": "runs failed"})
                continue
            b = band_from(vals, baseline.std, baseline.n, m)
            configs.append({"label": label, "param": dev.param, "band": b.model_dump(), "mean": b.mean,
                            "values": vals})
            means.append(b.mean)
            if progress:
                progress(f"sweep {label}: {b.mean:.2f}")
        if len(means) > 1:
            costs[dev.param] = float(max(means) - min(means))
            swept.append(dev.param)
    bands = [c["band"] for c in configs if "band" in c]
    spec_band = (min(b["lo"] for b in bands), max(b["hi"] for b in bands))
    return {"spec_band": spec_band, "costs": costs, "configs": configs, "swept": swept}


def sci(claim: Claim, costs: dict[str, float] | None = None, seed_std: float | None = None,
        unspecified: list[str] | None = None) -> dict:
    """Plain and cost-weighted Specification Completeness Index.

    Plain: the fraction of required parameters the paper states.
    Cost-weighted: stated parameters and unswept omissions weigh one seed standard deviation
    each; a swept omission weighs its measured cost in seed standard deviations.
    """
    required = list(dict.fromkeys(claim.required_params + (unspecified or [])))
    if not required:
        return {"plain": 1.0, "weighted": None, "stated": [], "missing": []}
    stated = [p for p in required if p in claim.stated_config]
    missing = [p for p in required if p not in claim.stated_config]
    plain = len(stated) / len(required)
    weighted = None
    if costs and seed_std and seed_std > 0:
        w_missing = sum(costs[p] / seed_std if p in costs else 1.0 for p in missing)
        weighted = len(stated) / (len(stated) + w_missing) if (len(stated) + w_missing) > 0 else 1.0
    return {"plain": plain, "weighted": weighted, "stated": stated, "missing": missing}
