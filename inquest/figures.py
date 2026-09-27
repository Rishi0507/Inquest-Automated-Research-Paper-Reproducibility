"""Static figures (component C11a): seed histogram with bands, and the attribution waterfall."""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from . import config, store  # noqa: E402

INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#8a8984"
GRID = "#e6e5e0"
SURFACE = "#fcfcfb"
SERIES = "#2a78d6"
BAND = "#cde2fb"
NEG = "#e34948"
NEUTRAL = "#b5b3ac"

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 9, "axes.edgecolor": GRID, "axes.labelcolor": INK_2,
    "xtick.color": INK_2, "ytick.color": INK_2, "axes.spines.top": False, "axes.spines.right": False,
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
})


def histogram(info: dict, title: str, path: Path) -> Path:
    vals = np.asarray(info["values"], dtype=float)
    reported = info["claim"]["value"]
    band = info["seed_band"]
    fig, ax = plt.subplots(figsize=(6.4, 3.0), dpi=160)
    spec = (info.get("spec") or {}).get("spec_band")
    lo = min(vals.min(), reported, band["lo"], spec[0] if spec else np.inf)
    hi = max(vals.max(), reported, band["hi"], spec[1] if spec else -np.inf)
    pad = (hi - lo) * 0.08 or 0.5
    if spec:
        ax.axvspan(spec[0], spec[1], color=NEUTRAL, alpha=0.18, lw=0, label="specification band")
    ax.axvspan(band["lo"], band["hi"], color=BAND, alpha=0.9, lw=0, label="seed band (95% PI)")
    bins = max(6, min(20, len(vals) // 2))
    ax.hist(vals, bins=bins, color=SERIES, rwidth=0.86, zorder=3, label=f"runs (n={len(vals)})")
    ax.axvline(reported, color=INK, lw=1.4, zorder=4)
    ymax = ax.get_ylim()[1]
    ax.text(reported, ymax * 0.97, f" reported {reported:g}", color=INK, va="top", ha="left", fontsize=8.5)
    ax.set_xlim(lo - pad, hi + pad)
    ax.set_xlabel(info.get("metric_key", "metric"))
    ax.set_ylabel("runs")
    ax.grid(axis="y", color=GRID, lw=0.6, zorder=0)
    ax.set_title(title, loc="left", fontsize=10, color=INK)
    ax.legend(frameon=False, fontsize=7.5, loc="upper left", bbox_to_anchor=(0, -0.2), ncol=3)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path


def waterfall(info: dict, title: str, path: Path) -> Path:
    att = info["attribution"]
    shares = att["shares"]
    labels = ["repository as-is"] + [s["label"] for s in shares] + ["residual", "reported"]
    fig, ax = plt.subplots(figsize=(6.8, 0.5 * len(labels) + 1.0), dpi=160)
    y = np.arange(len(labels))[::-1]
    cur = att["base"]
    ax.barh(y[0], 0.001, left=cur, color=INK)
    ax.plot([cur, cur], [y[0] - 0.3, y[0] + 0.3], color=INK, lw=2)
    ax.text(cur, y[0] + 0.38, f"{cur:.2f}", ha="center", fontsize=8, color=INK_2)
    for i, s in enumerate(shares, start=1):
        width = s["points"]
        color = NEUTRAL if s["is_noise"] else (SERIES if width * att["gap"] >= 0 else NEG)
        ax.barh(y[i], width, left=cur, height=0.56, color=color, zorder=3)
        lo, hi = s["ci"]
        ax.errorbar(cur + width, y[i], xerr=[[max(0.0, width - lo)], [max(0.0, hi - width)]], fmt="none",
                    ecolor=INK_2, elinewidth=0.9, capsize=2.5, zorder=4)
        frac = f"  {s['fraction']:.0%}" if s.get("fraction") is not None else ""
        span = max(abs(att["gap"]), max(abs(x["points"]) for x in shares), 0.1)
        ax.text(max(cur, cur + width, cur + hi) + span * 0.04, y[i], f"{width:+.2f}{frac}", va="center",
                fontsize=8, color=INK_2)
        cur += width
    res = att["residual"]
    ax.barh(y[-2], res, left=cur, height=0.56, color=NEUTRAL, zorder=3)
    ax.text(max(cur, cur + res) + abs(att["gap"]) * 0.03, y[-2],
            f"{res:+.2f}  {'inside aligned band' if att.get('residual_in_noise') else 'unexplained'}",
            va="center", fontsize=8, color=INK_2)
    rep = att["reported"]
    ax.plot([rep, rep], [y[-1] - 0.3, y[-1] + 0.3], color=INK, lw=2)
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=8.5)
    ax.grid(axis="x", color=GRID, lw=0.6, zorder=0)
    ax.set_xlabel(info.get("metric_key", "metric"))
    ax.set_title(title, loc="left", fontsize=10, color=INK)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path


def write_all(paper_id: str) -> list[Path]:
    analysis = store.latest_analysis(paper_id)
    if not analysis:
        return []
    out_dir = config.EXPORTS / paper_id
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for cid, info in analysis["claims"].items():
        if info.get("values"):
            paths.append(histogram(info, f"{cid}  {info['claim']['text']}"[:90], out_dir / f"{cid}_histogram.png"))
        if info.get("attribution") and info["attribution"].get("shares") is not None:
            paths.append(waterfall(info, f"{cid}  gap attribution", out_dir / f"{cid}_waterfall.png"))
    return paths
