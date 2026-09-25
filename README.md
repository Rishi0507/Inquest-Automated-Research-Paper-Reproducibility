# Inquest

Forensic reproducibility for machine learning papers.

Inquest takes a paper and the repository that implements it, runs the experiments in a
sandbox, and judges every numerical claim against measured error bars. It does not ask a
language model why a result failed to reproduce. It observes what the code does as it runs,
removes the differences between code and paper one at a time by re-executing the code, and
measures what each difference is worth.

Three capabilities carry the analysis:

- **Measured gap attribution.** Two-sided screening against the measured noise floor, exact
  Shapley values over the surviving deviations, a joint check for pruned deviations, and
  bootstrap intervals over paired seeds. Shares sum to the measured gap; the remainder is
  labelled as noise or as unexplained.
- **Runtime Witness.** Hooks inside the sandbox record the resolved arguments, seed calls,
  optimizers, schedulers, splits, loaders and metric calls, each with the file and line that
  made the call, and keep the predictions so that a metric-definition difference can be
  re-scored without training.
- **Two-band verdicts.** A seed band for what the code can produce and a specification band
  for what the paper's text permits, the second measured by sweeping the parameters the paper
  leaves unstated.

Supporting checks: GRIM for count-based metrics, dispersion consistency, the selection signal
(how many undisclosed attempts a reported value implies), a determinism audit, and a claim
ledger across papers.

The discipline behind all of it: language models read and propose; execution and statistics
judge. No verdict is produced by a model.

## Curated corpus

| Paper | Repository | Provenance |
|---|---|---|
| Kipf and Welling, *Variational Graph Auto-Encoders*, NIPS 2016 workshop, arXiv:1611.07308v1 | [zfjsail/gae-pytorch](https://github.com/zfjsail/gae-pytorch) at `c0b95ca` | Third-party PyTorch reimplementation |
| Kipf and Welling, *Semi-Supervised Classification with Graph Convolutional Networks*, ICLR 2017, arXiv:1609.02907v4 | [tkipf/pygcn](https://github.com/tkipf/pygcn) at `1600b5b` | Author's PyTorch reimplementation |
| Wu et al., *Simplifying Graph Convolutional Networks*, ICML 2019, arXiv:1902.07153v2 | [Tiiiger/SGC](https://github.com/Tiiiger/SGC) at `2c7a272` | Official implementation |

Each corpus directory holds `meta.json` (paper, repository, pinned commit, pinned PDF URL),
`claims.json` (hand-authored ground truth with verified spans), `adapter.yaml` (how to run the
experiments and which claim each configuration produces), and the reconstructed environment
lock `env.lock` with its resolution record `env.json`. The GCN corpus entry also carries a
hand-authored deviation set, `deviations.json`, used as the reference for the automatic
deviation finder. PDFs are downloaded from arXiv on first use and are not stored in the
repository.

The papers were chosen because they train on a CPU in seconds to minutes and together cover
scikit-learn metrics (VGAE), repository-defined metrics (GCN, SGC), a deterministic and a
non-deterministic repository, and a claim tuple shared across papers (GCN on Cora).

## Requirements

- Python 3.12 for the platform, with [uv](https://docs.astral.sh/uv/) on `PATH`
- Node.js 20 or newer to build the web interface
- Git
- Optional: Docker, for the network-isolated container sandbox
- Optional: an Anthropic API key, for claim extraction, the Cartographer and patch proposals
  on papers outside the curated corpus

Environments for the papers under test are built automatically. Interpreters, caches,
environments, runs and the database are kept under `.inquest/` in the project directory.

## Setup

```bash
git clone https://github.com/Rishi0507/Inquest-Automated-Research-Paper-Reproducibility.git inquest
cd inquest
uv venv --python 3.12 .venv
uv pip install --python .venv -r requirements.txt
cp .env.example .env            # add ANTHROPIC_API_KEY to enable extraction and mapping
cd ui && npm install && npm run build && cd ..
```

On Windows, run the same commands from Git Bash, or use `.venv\Scripts\python` in place of
`.venv/bin/python` below.

## Running

Start the API and web interface, then open <http://127.0.0.1:8000>:

```bash
.venv/bin/python -m inquest serve
```

Command line:

```bash
.venv/bin/python -m inquest prepare kipf2017-gcn      # clone at the pinned commit, rebuild the environment
.venv/bin/python -m inquest analyze kipf2017-gcn --figures
.venv/bin/python -m inquest faults plant --paper wu2019-sgc --fault random
.venv/bin/python -m inquest faults clean --paper kipf2017-gcn
.venv/bin/python -m inquest faults reveal --out <variant id>
.venv/bin/python -m inquest eval all
.venv/bin/python -m inquest dossier kipf2017-gcn
.venv/bin/python -m pytest
```

For UI development, `npm run dev` inside `ui/` serves the interface on port 3000 and proxies
`/api` to port 8000.

## Web interface

A landing page and ten sections reached from the chip bar at the top:

| Section | Content |
|---|---|
| Overview | Corpus summary, the three capabilities with live numbers, how a claim is judged |
| Papers | Corpus, controls and registered papers; registration of a new PDF and repository; adapter review and edit |
| Claims | Claims with their pages, bands, mapping status, SCI and verdicts; the evidence drawer shows the PDF location, the code line and the run log side by side |
| Runs | Stage progress, run, cache and re-score counters, and the live log of an analysis |
| Analysis | Seed histogram with both bands, selection signal, attribution waterfall with intervals, screening table, specification sweep |
| Witness | Stated against observed, determinism audit, seed calls, metric provenance and the re-scoring self-check |
| Ledger | Claims sharing a model, dataset and metric across papers |
| Controls | Blind fault planting with a sealed manifest, clean controls, reveal and scoring |
| Evaluation | Experiments E1 to E7 |
| Report | Export and preview of the evidence dossier as PDF |

## Adding a paper

Register a PDF (upload or URL) and a repository URL in the Papers section, or `POST
/api/papers`. The platform clones the repository, extracts claims and stated parameters with
span verification, drafts an adapter with the Cartographer, rebuilds the environment at the
paper's date, and validates the adapter by execution: `--help` must list every mapped flag,
a smoke run must exit cleanly with a bindable metric, and a run at each claim's configuration
must show the mapped values in the Witness. A failed validation is returned to the model once;
after that the adapter waits for a human edit in the review view. The analysis then uses the
automatic deviation finder.

This path needs a language model. Set `ANTHROPIC_API_KEY` in `.env`, or point
`INQUEST_LOCAL_LLM_URL` and `INQUEST_LOCAL_LLM_MODEL` at an Ollama server.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | unset | Enables extraction, mapping and patch proposals |
| `INQUEST_LLM_MODEL` | `claude-opus-5` | Model for those calls |
| `INQUEST_LOCAL_LLM_URL`, `INQUEST_LOCAL_LLM_MODEL` | unset | Local Ollama fallback |
| `INQUEST_SANDBOX` | `local` | `local` or `docker` |
| `INQUEST_WORKERS` | half the logical cores | Parallel runs |
| `INQUEST_THREADS_PER_RUN` | `1` | Thread cap inside each run |
| `INQUEST_RUN_TIMEOUT` | `420` | Wall-clock cap per run, in seconds |
| `INQUEST_RUN_MEMORY_MB` | `4096` | Memory cap per run |
| `INQUEST_WORKSPACE` | `./.inquest` | Runs, environments, caches and database |

## Documentation

- [Architecture](docs/architecture.md): components, sandbox, Witness, attribution, verdict rules, statistics
- [API](docs/api.md): HTTP endpoints
- [Evaluation](docs/evaluation.md): experiments E1 to E7 and how controls are built

## Limitations

- Attribution explains only deviations the platform can identify and toggle, and is
  conditional on that set. Unidentified causes appear as residual.
- The specification band reflects the alternatives that were swept, not every possible one.
- The selection signal assumes the authors' seed distribution matches the reproduction and
  reports a lower bound with a stated resolution limit.
- GRIM applies only to count-based metrics with a known test-set size.
- The Witness supports argparse, random, NumPy, PyTorch and scikit-learn. Other frameworks
  fall back to parsing standard output and are flagged.
- The `local` sandbox isolates resources and blocks network access at the Python level; it
  does not isolate the file system. Use the `docker` backend for untrusted code.
- Execution runs on CPU; the authors' hardware is unknown, and every dossier says so.
- The ledger is demonstrated at corpus scale and is not a literature-scale consensus.
