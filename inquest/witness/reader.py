"""Host-side reading of Witness logs: event parsing, metric binding and summaries."""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..schemas import MetricSpec, WitnessEvent


def load(path: str | Path) -> list[WitnessEvent]:
    p = Path(path)
    if not p.exists():
        return []
    index_path = p.parent / "payloads" / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
    events = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        raw.pop("t", None)
        pp = raw.get("payload_path")
        if isinstance(pp, str) and pp.startswith("pending:"):
            raw["payload_path"] = index.get(pp.split(":", 1)[1])
        try:
            events.append(WitnessEvent.model_validate(raw))
        except Exception:
            continue
    return events


def _decimals(text: str) -> int:
    return len(text.split(".", 1)[1]) if "." in text else 0


def parse_stdout(stdout: str, regex: str) -> tuple[float, str] | None:
    matches = list(re.finditer(regex, stdout))
    if not matches:
        return None
    text = matches[-1].group(1)
    try:
        return float(text), text
    except ValueError:
        return None


def bind_metric(spec: MetricSpec, events: list[WitnessEvent], stdout: str) -> dict:
    """Return {value_raw, source, event, printed} for one metric of one run.

    The claim's metric is bound to the witnessed call whose return value matches the
    printed final metric. Ties resolve to the last call. The printed value may be rounded,
    so the tolerance is half a unit in its last printed digit (at least 1e-6).
    """
    printed = parse_stdout(stdout, spec.stdout_regex) if spec.stdout_regex else None
    calls = [e for e in events if e.kind == "METRIC_CALL" and spec.witness_fn and e.name == spec.witness_fn
             and e.value is not None]
    if printed is not None and calls:
        value, text = printed
        tol = max(1e-6, 0.5 * 10 ** (-_decimals(text)))
        matching = [e for e in calls if abs(e.value - value) <= tol]
        if matching:
            ev = matching[-1]
            return {"value_raw": ev.value, "source": f"witness:{ev.caller}", "event": ev, "printed": text,
                    "binding": "value_match"}
        return {"value_raw": value, "source": "stdout", "event": None, "printed": text,
                "binding": "no_witnessed_call_matches_printed_value"}
    if calls:
        ev = calls[-1]
        return {"value_raw": ev.value, "source": f"witness:{ev.caller}", "event": ev, "printed": None,
                "binding": "last_call"}
    if printed is not None:
        return {"value_raw": printed[0], "source": "stdout", "event": None, "printed": printed[1],
                "binding": "stdout_only"}
    return {"value_raw": None, "source": None, "event": None, "printed": None, "binding": "not_found"}


def summarize(events: list[WitnessEvent]) -> dict:
    """Condensed observation used by the deviation finder and the Witness panel."""
    out: dict = {"args": None, "seed_calls": [], "determinism": [], "metric_calls": [], "splits": [],
                 "optimizers": [], "schedulers": [], "dataloaders": [], "patch_hits": [], "exit_state": None}
    for e in events:
        if e.kind == "ARGS" and out["args"] is None:
            out["args"] = {"idx": e.idx, "caller": e.caller, **e.kwargs}
        elif e.kind == "SEED_CALL":
            out["seed_calls"].append({"idx": e.idx, "name": e.name, "seed": e.kwargs.get("seed"), "caller": e.caller})
        elif e.kind == "DETERMINISM_FLAG":
            if e.name == "state_at_exit":
                out["exit_state"] = e.kwargs
            else:
                out["determinism"].append({"idx": e.idx, "name": e.name, **e.kwargs, "caller": e.caller})
        elif e.kind == "METRIC_CALL":
            out["metric_calls"].append({"idx": e.idx, "name": e.name, "kwargs": e.kwargs, "value": e.value,
                                        "caller": e.caller, "payload": e.payload_path})
        elif e.kind == "SPLIT_CALL":
            out["splits"].append({"idx": e.idx, "name": e.name, **e.kwargs, "caller": e.caller})
        elif e.kind == "OPTIMIZER":
            out["optimizers"].append({"idx": e.idx, "name": e.name, **e.kwargs, "caller": e.caller})
        elif e.kind == "SCHEDULER":
            out["schedulers"].append({"idx": e.idx, "name": e.name, **e.kwargs, "caller": e.caller})
        elif e.kind == "DATALOADER":
            out["dataloaders"].append({"idx": e.idx, "name": e.name, **e.kwargs, "caller": e.caller})
        elif e.kind == "PATCH_HIT":
            out["patch_hits"].append({"idx": e.idx, "tag": e.name, "caller": e.caller})
    # Collapse repeated metric calls (per-epoch validation) into counts plus the last call per site.
    by_site: dict = {}
    for m in out["metric_calls"]:
        key = (m["name"], m["caller"], json.dumps(m["kwargs"], sort_keys=True))
        entry = by_site.setdefault(key, {**m, "count": 0})
        entry["count"] += 1
        entry.update({"idx": m["idx"], "value": m["value"], "payload": m["payload"]})
    out["metric_sites"] = list(by_site.values())
    out["n_metric_calls"] = len(out["metric_calls"])
    out["metric_calls"] = out["metric_calls"][-50:]
    return out
