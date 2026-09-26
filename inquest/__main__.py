"""Command line: python -m inquest <command> ..."""
from __future__ import annotations

import argparse
import json
import sys


def _print_verdicts(result: dict) -> None:
    for v in result.get("verdicts", []):
        info = result["claims"][v["claim_id"]]
        band = v.get("seed_band")
        b = f"band [{band['lo']:.2f}, {band['hi']:.2f}]" if band else "no band"
        print(f"{v['claim_id']:6} {v['verdict']:36} reported {v['reported']:<6} {b:26} {' '.join(v['flags'])}")
        for r in v.get("reasons", []):
            print(f"         {r}")
        att = info.get("attribution")
        if att:
            print(f"         gap {att['gap']:+.2f}  tau {att['tau']:.2f}  residual {att['residual']:+.2f}")
            for s in att["shares"]:
                frac = f"{s['fraction']:.0%}" if s["fraction"] is not None else "n/a"
                print(f"           {s['label']:32} {s['points']:+.2f} pts  {frac:>5}  CI [{s['ci'][0]:+.2f}, {s['ci'][1]:+.2f}]"
                      f"{'  (noise)' if s['is_noise'] else ''}")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except AttributeError:
            pass
    ap = argparse.ArgumentParser(prog="inquest", description="Forensic reproducibility for ML papers")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("prepare", help="clone the repository and reconstruct its environment")
    p.add_argument("paper")
    p.add_argument("--rebuild", action="store_true")

    p = sub.add_parser("analyze", help="run the full analysis and print verdicts")
    p.add_argument("paper")
    p.add_argument("--seeds", type=int, default=None, help="seeds in the baseline swarm (default 20)")
    p.add_argument("--deviations", choices=["hand", "finder"], default=None)
    p.add_argument("--figures", action="store_true", help="write the histogram and waterfall PNGs")

    p = sub.add_parser("figures", help="write figures for the latest analysis")
    p.add_argument("paper")

    p = sub.add_parser("serve", help="start the API and web interface")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)

    p = sub.add_parser("faults", help="plant faults, check out historical faults, or create clean controls")
    p.add_argument("action", choices=["plant", "history", "clean", "reveal", "list"])
    p.add_argument("--paper")
    p.add_argument("--fault")
    p.add_argument("--fix-commit")
    p.add_argument("--out")
    p.add_argument("--seed", type=int, default=None)

    p = sub.add_parser("eval", help="run evaluation experiments E1 to E7")
    p.add_argument("which", nargs="?", default="all")

    p = sub.add_parser("image", help="build the pinned container image for the docker sandbox")
    p.add_argument("paper")

    p = sub.add_parser("dossier", help="export the evidence dossier as PDF")
    p.add_argument("paper")

    args = ap.parse_args(argv)
    if args.cmd == "prepare":
        from .prepare import prepare
        fp = prepare(args.paper, rebuild=args.rebuild)
        print(json.dumps({k: v for k, v in fp.items() if k != "packages"}, indent=1))
    elif args.cmd == "analyze":
        from . import pipeline
        job = pipeline.Job(args.paper)
        job.log = lambda msg, _orig=job.log: (print(msg, flush=True), _orig(msg))  # type: ignore[method-assign]
        result = pipeline.analyze(args.paper, job, deviation_source=args.deviations, n_seeds=args.seeds)
        _print_verdicts(result)
        c = result.get("cost", {})
        print(f"\nruns requested {c.get('runs_requested')}  executed {c.get('runs_executed')}  cache hits "
              f"{c.get('cache_hits')}  eval re-scores {c.get('rescores')}  wall {c.get('wall_seconds')} s")
        if result.get("errors"):
            for e in result["errors"]:
                print(f"error in {e['stage']}: {e['error']}", file=sys.stderr)
        if args.figures:
            from . import figures
            for path in figures.write_all(args.paper):
                print(path)
        return 0 if result.get("status") == "done" else 1
    elif args.cmd == "figures":
        from . import figures
        for path in figures.write_all(args.paper):
            print(path)
    elif args.cmd == "serve":
        import uvicorn
        uvicorn.run("inquest.api:app", host=args.host, port=args.port, log_level="info")
    elif args.cmd == "faults":
        from . import faults
        return faults.cli(args)
    elif args.cmd == "eval":
        from . import evaluate
        return evaluate.cli(args.which)
    elif args.cmd == "image":
        from . import corpus, sandbox, store
        paper = corpus.get(args.paper)
        env_dir = paper.dir if paper.source == "corpus" else paper.dir
        digest = sandbox.build_image(paper.env_id, env_dir)
        adapter = paper.adapter()
        store.put_adapter(paper.paper_id, adapter.model_copy(update={"image": digest}).model_dump(),
                          paper.adapter_origin() or "hand")
        print(digest)
    elif args.cmd == "dossier":
        from . import dossier
        print(dossier.export(args.paper))
    return 0


if __name__ == "__main__":
    sys.exit(main())
