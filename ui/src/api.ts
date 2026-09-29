// Thin client for the Inquest API. Types mirror inquest/schemas.py where the UI depends on them.

export type Span = { page: number; bbox: [number, number, number, number]; table?: string | null; section?: string | null; text?: string | null };
export type StatedParam = { value: unknown; span: Span };
export type Claim = {
  claim_id: string; text: string; metric: string; value: number; value_text: string; decimals: number;
  dataset: string; model?: string | null; n_test?: number | null; n_seeds_reported?: number | null;
  dispersion?: number | null; dispersion_kind?: string | null;
  stated_config: Record<string, StatedParam>; procedures: string[]; required_params: string[];
  source: Span; origin: string;
};
export type Band = { mean: number; std: number; n: number; m: number; lo: number; hi: number };
export type Selection = { n: number; exceed: number; p_hi: number; k_lower: number; distribution: string };
export type Share = { dev_id: string; label: string; points: number; fraction: number | null; ci: [number, number]; is_noise: boolean };
export type Verdict = {
  claim_id: string; verdict: string; reported: number; seed_band?: Band | null; aligned_band?: Band | null;
  spec_band?: [number, number] | null; selection?: Selection | null; attribution?: Share[] | null;
  residual?: number | null; sci: number; flags: string[]; reasons: string[];
};
export type Attribution = {
  metric: string; reported: number; base: number; full: number; aligned: number; gap: number; tau: number;
  seed_std: number; k: number; paired: boolean; effects: Record<string, { first_order: number; total: number; effect: number; floor?: number; survives: boolean }>;
  survivors: string[]; pruned: string[]; residual: number; residual_in_noise: boolean; pruned_joint_effect: boolean;
  shares_valid: boolean; shares: Share[]; coalitions: Record<string, number[]>; trainings: number; rescores: number;
  excluded: { dev_id: string; label: string; reason: string }[];
};
export type ClaimInfo = {
  claim: Claim; sci?: { plain: number; weighted: number | null; stated: string[]; missing: string[] };
  preflight?: { test: string; status: string; reason?: string; n?: number; m?: number; achievable?: number[] };
  mapping?: { status: string; config?: Record<string, unknown>; metric?: string; reason?: string; code_ref?: string };
  metric_key?: string; values?: number[]; seed_band?: Band; selection?: Selection; selection_aligned?: Selection;
  selection_resolution?: number; config?: Record<string, unknown>; baseline_run_ids?: string[];
  dispersion_check?: { status: string; reported: number; kind: string; implied_std: number; measured_std: number; ratio: number } | null;
  attribution?: Attribution | null; attribution_note?: string; aligned_band?: Band; aligned_values?: number[];
  spec?: { spec_band: [number, number]; costs: Record<string, number>; configs: { label: string; param: string | null; band?: Band; mean?: number; values?: number[]; skipped?: string }[]; swept: string[] };
  verdict?: Verdict;
};
export type Deviation = {
  dev_id: string; type: string; phase: string; param: string; paper_value: unknown; repo_value: unknown;
  paper_span?: Span | null; witness_idx?: number | null; code_ref?: string | null; patch: Record<string, unknown>;
  patch_verified: boolean; togglable: boolean; label: string; source: string; note?: string | null;
};
export type WitnessSummary = {
  args: { idx: number; caller: string; namespace: Record<string, unknown>; actions: { dest: string; flags: string[]; default: unknown }[]; explicit: string[] } | null;
  seed_calls: { idx: number; name: string; seed: unknown; caller: string }[];
  determinism: Record<string, unknown>[];
  metric_sites: { idx: number; name: string; kwargs: Record<string, unknown>; value: number; caller: string; count: number; payload?: string | null }[];
  optimizers: { idx: number; name: string; lr: unknown; weight_decay: unknown; caller: string; param_groups: Record<string, unknown>[] }[];
  schedulers: Record<string, unknown>[]; splits: Record<string, unknown>[]; dataloaders: Record<string, unknown>[];
  patch_hits: { tag: string; caller: string }[]; exit_state: Record<string, unknown> | null; n_metric_calls: number;
};
export type WitnessBlock = {
  config: Record<string, unknown>; run_id: string; summary: WitnessSummary;
  self_check: Record<string, { fn: string; rescored?: number; witnessed?: number; abs_diff?: number; ok: boolean; error?: string }>;
  metric_sources: Record<string, string>;
};
export type Determinism = {
  deterministic: boolean; delta: number; hash_sensitive: boolean; hash_delta: number; metric: string;
  values: Record<string, number>; seed_calls_observed: { name: string; seed: unknown; caller: string }[];
  seed_arg_parsed: boolean; candidates: string[]; run_ids: string[];
};
export type Analysis = {
  analysis_id: string; paper_id: string; created: number; status: string; standing_note: string;
  claims: Record<string, ClaimInfo>; verdicts: Verdict[]; determinism: Determinism | null; deviations: Deviation[];
  witness: WitnessBlock[]; environment: Record<string, unknown>; cost: Record<string, number>;
  stages: Record<string, string>; errors: { stage: string; error: string }[]; deviation_source?: string;
  adapter?: Record<string, unknown> | null; adapter_origin?: string;
};
export type PaperSummary = {
  paper_id: string; title: string; authors?: string; venue?: string; arxiv?: string; paper_date?: string;
  repo_url?: string; repo_sha?: string; provenance?: string; provenance_note?: string; source: string;
  parent?: string | null; role?: string; variant_kind?: string | null; claims_source: string;
  has_hand_claims: boolean; n_claims: number; adapter_origin?: string | null;
  analysis?: { analysis_id: string; created: number; status: string; verdicts: Record<string, string> } | null;
  active_job?: Job | null;
};
export type Job = {
  job_id: string; kind: string; paper_id?: string; status: string; stage: string | null; stages: Record<string, string>;
  progress: number; runs_done: number; runs_executed: number; cache_hits: number; rescores: number;
  log: { t: number; msg: string }[]; started: number; finished: number | null; error: string | null;
  result?: Record<string, unknown>;
};
export type Status = { llm: { anthropic: boolean; model: string; local: boolean; local_model: string | null }; sandbox: string; workers: number; threads_per_run: number; counters: Record<string, number>; standing_note: string };

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, init);
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`;
    try { const j = await r.json(); if (j.detail) msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch { /* keep status text */ }
    throw new Error(msg);
  }
  return r.json() as Promise<T>;
}
const json = (method: string, body?: unknown): RequestInit => ({
  method, headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body),
});

export const api = {
  status: () => req<Status>("/api/status"),
  papers: () => req<PaperSummary[]>("/api/papers"),
  claims: (id: string, source?: string) => req<{ source: string; claims: Claim[]; extraction_report: Record<string, unknown> | null }>(`/api/papers/${enc(id)}/claims${source ? `?source=${source}` : ""}`),
  setClaimsSource: (id: string, source: string) => req(`/api/papers/${enc(id)}/claims_source`, json("PUT", { source })),
  extract: (id: string) => req<{ job_id: string }>(`/api/papers/${enc(id)}/extract`, json("POST")),
  adapter: (id: string) => req<{ adapter: Record<string, unknown> | null; origin: string | null; report: Record<string, unknown> | null }>(`/api/papers/${enc(id)}/adapter`),
  putAdapter: (id: string, adapter: unknown) => req<{ job_id: string }>(`/api/papers/${enc(id)}/adapter`, json("PUT", adapter)),
  cartographer: (id: string) => req<{ job_id: string }>(`/api/papers/${enc(id)}/cartographer`, json("POST")),
  analyze: (id: string, body: { deviations?: string; seeds?: number } = {}) => req<{ job_id: string }>(`/api/papers/${enc(id)}/analyze`, json("POST", body)),
  analysis: (id: string) => req<Analysis>(`/api/papers/${enc(id)}/analysis`),
  job: (jobId: string) => req<Job>(`/api/jobs/${jobId}`),
  jobs: (id: string) => req<Job[]>(`/api/papers/${enc(id)}/jobs`),
  evidence: (id: string, cid: string) => req<{ claim: Claim; info: ClaimInfo | null; code_refs: { ref: string; why: string }[]; runs: string[] }>(`/api/claims/${enc(id)}/${enc(cid)}/evidence`),
  code: (id: string, path: string, line: number) => req<{ path: string; line: number; start: number; lines: string[]; total: number; sha: string }>(`/api/code/${enc(id)}?path=${encodeURIComponent(path)}&line=${line}`),
  run: (runId: string) => req<{ result: Record<string, unknown>; command: { argv: string[]; cwd: string }; stdout_tail: string; stdout_lines: number }>(`/api/runs/${runId}`),
  consensus: () => req<LedgerGroup[]>("/api/consensus"),
  faults: () => req<{ variants: Variant[]; catalogue: Record<string, string[]>; not_applicable: Record<string, string> }>("/api/faults"),
  plant: (paper_id: string, fault: string) => req<{ job_id: string }>("/api/faults/plant", json("POST", { paper_id, fault })),
  clean: (paper_id: string) => req<{ job_id: string }>(`/api/faults/clean/${enc(paper_id)}`, json("POST")),
  reveal: (variant: string) => req<Record<string, unknown>>(`/api/faults/reveal/${enc(variant)}`, json("POST")),
  evalResults: () => req<Record<string, any>>("/api/eval"),
  evalRun: () => req<{ job_id: string }>("/api/eval/run", json("POST")),
  dossier: (id: string) => req<{ path: string; url: string }>(`/api/papers/${enc(id)}/dossier`, json("POST")),
  register: (form: FormData) => req<{ paper_id: string; job_id: string }>("/api/papers", { method: "POST", body: form }),
  remove: (id: string) => req(`/api/papers/${enc(id)}`, { method: "DELETE" }),
};

export type LedgerEntry = { paper_id: string; paper_title: string; claim_id: string; model: string; dataset: string; metric: string; reported: number; value_text: string; n_runs: number | null; executed_mean: number | null; band: [number, number] | null; verdict: string | null };
export type LedgerGroup = { model: string; dataset: string; metric: string; protocol: string | null; papers: string[]; entries: LedgerEntry[]; spread: number; band_width: number | null; ratio: number | null; flag: boolean };
export type Variant = { paper_id: string; parent: string; kind: string; sealed: string | null; revealed: Record<string, any> | null };

export const enc = (s: string) => encodeURIComponent(s);
export const pdfPage = (id: string, span: Span, zoom = 2) =>
  `/api/pdf/${enc(id)}/page/${span.page}.png?x0=${span.bbox[0]}&y0=${span.bbox[1]}&x1=${span.bbox[2]}&y1=${span.bbox[3]}&zoom=${zoom}`;
