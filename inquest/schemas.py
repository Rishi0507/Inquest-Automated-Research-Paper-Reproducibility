"""Frozen contracts shared by every component.

These models follow section 2 of the prototype PRD. Extensions beyond the PRD are
marked "extension" and are additive: no PRD field changes meaning.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

Scalar = str | int | float | bool | None


class PDFSpan(BaseModel):
    page: int                                  # 1-based page number
    bbox: tuple[float, float, float, float]    # x0, y0, x1, y1 in PDF points
    table: Optional[str] = None                # "Table 3"
    section: Optional[str] = None              # "4.1"
    text: Optional[str] = None                 # extension: the verified words at the span


class StatedParam(BaseModel):
    value: Scalar
    span: PDFSpan


class Claim(BaseModel):
    claim_id: str
    text: str
    metric: str
    value: float
    value_text: str
    decimals: int
    dataset: str
    model: Optional[str] = None
    n_test: Optional[int] = None
    n_seeds_reported: Optional[int] = None
    dispersion: Optional[float] = None                            # extension: the printed "±" value
    dispersion_kind: Optional[Literal["std", "stderr"]] = None    # extension
    stated_config: dict[str, StatedParam] = {}
    procedures: list[str] = []
    required_params: list[str] = []
    source: PDFSpan
    entrypoint: Optional[str] = None
    origin: Literal["hand", "extracted"] = "hand"                 # extension
    protocol: Optional[str] = None                                # extension: evaluation protocol, for the ledger


class MetricSpec(BaseModel):
    witness_fn: Optional[str] = None     # "roc_auc_score" or "package.module:function" for repo-defined metrics
    stdout_regex: Optional[str] = None
    scale: float = 100.0
    rescore_fn: Optional[str] = None     # extension: sklearn function equivalent to a repo-defined metric


class ClaimMapping(BaseModel):
    """Extension: how one claim is produced by the repository."""
    status: Literal["mapped", "not_implemented", "unmappable"] = "mapped"
    config: dict = {}
    metric: Optional[str] = None         # key into AdapterSpec.metrics
    reason: Optional[str] = None
    code_ref: Optional[str] = None


class AdapterSpec(BaseModel):
    paper_id: str
    image: str = ""                      # container image digest (docker backend)
    workdir: str = "."                   # repo-relative working directory
    setup: list[str] = []
    command: str                         # "python train.py"; {placeholders} are filled from config
    flags: dict[str, str] = {}           # extension: config key -> CLI flag, appended when the key is set
    config_types: dict[str, str] = {}    # "int" | "float" | "str" | "bool" | "flag"
    seed_flag: Optional[str] = None
    seed_env: Optional[str] = None
    metrics: dict[str, MetricSpec] = {}  # extension of `metric`: several metrics may come from one run
    pythonpath: list[str] = []           # extension: repo-relative entries prepended to PYTHONPATH
    smoke_overrides: dict = {}
    claim_map: dict[str, ClaimMapping] = {}
    python: str = "3.10"                 # extension: interpreter for the reconstructed environment
    validated: bool = False
    validation_log: list[str] = []       # extension


WitnessKind = Literal["ARGS", "SEED_CALL", "DETERMINISM_FLAG", "METRIC_CALL",
                      "SPLIT_CALL", "OPTIMIZER", "SCHEDULER", "DATALOADER", "PATCH_HIT"]


class WitnessEvent(BaseModel):
    idx: int
    kind: WitnessKind
    name: str
    kwargs: dict
    value: Optional[float] = None
    caller: str
    payload_path: Optional[str] = None


DeviationType = Literal["VALUE_MISMATCH", "SEMANTIC_MISMATCH", "UNSPECIFIED_IN_PAPER",
                        "UNIMPLEMENTED_IN_CODE", "AMBIGUOUS"]


class Deviation(BaseModel):
    dev_id: str
    type: DeviationType
    phase: Literal["train", "eval"]
    param: str
    paper_value: Scalar
    repo_value: Scalar
    paper_span: Optional[PDFSpan] = None
    witness_idx: Optional[int] = None
    code_ref: Optional[str] = None
    patch: dict = {}
    patch_verified: bool = False
    togglable: bool = True
    label: str = ""                                   # extension: short human label
    source: Literal["hand", "witness", "ast", "llm"] = "hand"   # extension
    note: Optional[str] = None                        # extension
    alternatives: list[Scalar] = []                   # extension: sweep values for C14


class RunRequest(BaseModel):
    paper_id: str
    config: dict = {}
    seed: int
    force: bool = False
    hash_seed: Optional[str] = None      # extension: PYTHONHASHSEED override (defaults to the seed)


class RunResult(BaseModel):
    run_id: str
    cache_key: str
    seed: int
    metric_name: str
    metric_value: float
    metric_scale: float
    predictions_path: Optional[str] = None
    witness_path: str
    wall_seconds: float
    stdout_path: str
    env_fingerprint: dict = {}
    cached: bool = False
    ok: bool = True                                   # extension
    error: Optional[str] = None                       # extension
    metrics: dict[str, float] = {}                    # extension: every bound metric, paper scale
    predictions: dict[str, str] = {}                  # extension: metric -> npz path
    metric_sources: dict[str, str] = {}               # extension: metric -> "witness:eval.py:88" | "stdout"
    config: dict = {}                                 # extension


Verdict = Literal[
    "NUMERICALLY_IMPOSSIBLE", "NOT_IMPLEMENTED", "UNVERIFIABLE", "NOT_EXECUTABLE",
    "REPRODUCED", "NOT_REPRODUCED_EXPLAINED", "UNDERSPECIFIED",
    "NOT_REPRODUCED_PARTIALLY_EXPLAINED", "NOT_REPRODUCED_UNEXPLAINED",
]


class Band(BaseModel):
    mean: float
    std: float
    n: int
    m: int
    lo: float
    hi: float


class SelectionSignal(BaseModel):
    n: int
    exceed: int
    p_hi: float
    k_lower: float
    distribution: Literal["baseline", "aligned"]


class Share(BaseModel):
    dev_id: str
    label: str
    points: float
    fraction: Optional[float]
    ci: tuple[float, float]
    is_noise: bool


class ClaimVerdict(BaseModel):
    claim_id: str
    verdict: Verdict
    reported: float
    seed_band: Optional[Band] = None
    aligned_band: Optional[Band] = None
    spec_band: Optional[tuple[float, float]] = None
    selection: Optional[SelectionSignal] = None
    attribution: Optional[list[Share]] = None
    residual: Optional[float] = None
    sci: float = 0.0
    flags: list[str] = []
    reasons: list[str] = Field(default_factory=list)   # extension: the rule path that produced the verdict
