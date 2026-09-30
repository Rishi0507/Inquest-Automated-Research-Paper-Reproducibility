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

## Results on the curated corpus

Measured on a 2-core CPU with the `local` sandbox. Seed bands are 95% prediction intervals
for the mean of as many runs as the paper averages, from 20 seeds.

| Paper | Claim | Reported | Seed band | Verdict |
|---|---|---|---|---|
| VGAE | Cora AUC | 91.4 | [90.77, 92.28] | Reproduced |
| VGAE | Cora AP | 92.6 | [91.79, 93.25] | Reproduced |
| VGAE | Citeseer AUC | 90.8 | [89.27, 91.60] | Reproduced |
| VGAE | Citeseer AP | 92.0 | [90.62, 92.61] | Reproduced |
| VGAE | Pubmed AUC; GAE Cora AUC and AP | 94.4; 91.0; 92.0 | none | Not implemented: no Pubmed data; `--model` is parsed but never read |
| GCN | Cora accuracy | 81.5 | [82.79, 83.61] | Underspecified |
| GCN | Citeseer, Pubmed, Cora random splits | 70.3; 79.0; 80.1 | none | Not implemented |
| SGC | Cora, Citeseer, Pubmed accuracy | 81.0; 71.9; 78.9 | [80.99, 81.04]; [71.79, 71.89]; [78.91, 79.00] | Reproduced |
| SGC | GCN and DGI rows | 81.4; 82.5 | none | Not implemented |

Findings recorded along the way:

- The GCN reimplementation scores 83.2 on average, above the paper's 81.5. Its differences from
  the paper (row normalisation, split, weight-decay scope, initialisation, input dropout,
  missing early stopping) were each patched and measured. The paper's reported value lies inside
  the specification band once the unstated bias term is swept, so the claim is underspecified
  rather than contradicted. Its result also moves by up to one point with `PYTHONHASHSEED`
  alone, because labels are encoded through `set()` ordering.
- The VGAE reimplementation parses `--seed` but never calls a seed function, so its runs are
  non-deterministic; its run-to-run spread is about thirty times what the paper's printed
  standard errors imply. The paper leaves dropout unstated; setting it to 0.5 lowers Cora AUC
  to 85.5.
- The SGC repository's documented Citeseer command trains for 150 epochs where the paper
  states 100, and the paper does not state the propagation degree for its citation
  experiments. Both were found and priced automatically.

Controls (disclosed as controls, planted blind where marked):

| Control | Outcome |
|---|---|
| SGC, blind plant (epoch default changed) | Found and ranked first; Shapley share within 0.01 of the single-fault effect on Cora |
| GCN, blind plant (hardcoded optimizer weight decay) | Found at the optimizer, patched from the AST literal; +39.0 of the 39.5-point gap attributed |
| SGC, metric swap (weighted F1 reported as accuracy) | Attributed and explained on all three datasets |
| VGAE, metric swap (AUC reported as AP) | Attributed and explained; +0.88 points against a measured single-fault effect of +1.10 |
| VGAE, historical fault (parent of the loss-function fix `3a98122`) | No gap beyond seed noise on either claim: the historical bug did not move AUC or AP measurably |
| SGC and GCN, clean | Reproduced, nothing attributed |

Evaluation summary (`eval/results.md`): top-1 attribution accuracy on planted faults 100%
(4 of 4); clean-control false positives 0%; selection-signal lower bound held in 98 to 100% of
resampled cases; 51.5% of pairs of single runs disagree when the claim equals the code's own
mean, which is the case for bands over single-run checks; Witness re-scoring reproduced the
witnessed metric in 23 of 23 checks. E1 (extraction) and E2 (Cartographer) need a language
model and have not been measured on this machine.

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

A landing page and five sections in the chip bar at the top. Sections with more than one view
show a second row of tabs.

| Section | Views | Content |
|---|---|---|
| Overview | Overview | Corpus summary, the three capabilities with live numbers, how a claim is judged |
| Papers | Claims, Library | Claims with their pages, bands, mapping status, SCI and verdicts, with the evidence drawer (PDF location, code line and run log side by side); the paper library, registration of a new PDF and repository, and adapter review |
| Analysis | Results, Witness, Runs | Seed histogram with both bands, selection signal, attribution waterfall and screening, specification sweep; stated against observed, determinism audit, deviations and metric provenance; stage progress, counters and the live log of an analysis |
| Validation | Controls, Evaluation | Blind fault planting with a sealed manifest, clean controls, reveal and scoring; experiments E1 to E7 |
| Report | Dossier, Ledger | Export and preview of the evidence dossier as PDF; claims sharing a model, dataset, metric and protocol across papers |

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
