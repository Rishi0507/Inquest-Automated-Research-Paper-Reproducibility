"""Paper registry: curated corpus papers, registered (unseen) papers and fault variants."""
from __future__ import annotations

import json
from functools import cached_property
from pathlib import Path

import yaml

from . import config, repos, store
from .schemas import AdapterSpec, Claim, Deviation


class PaperNotFound(KeyError):
    pass


class Paper:
    def __init__(self, paper_id: str, meta: dict, directory: Path, source: str):
        self.paper_id = paper_id
        self.meta = meta
        self.dir = directory
        self.source = source            # "corpus" | "registered" | "variant"

    # ------------------------------------------------------------- identity
    @property
    def parent_id(self) -> str | None:
        return self.meta.get("parent")

    @property
    def env_id(self) -> str:
        """Variants share their parent's reconstructed environment."""
        return self.parent_id or self.paper_id

    @property
    def title(self) -> str:
        return self.meta.get("title", self.paper_id)

    @property
    def pdf_path(self) -> Path:
        if self.parent_id:
            return get(self.parent_id).pdf_path
        return self.dir / "paper.pdf"

    @property
    def repo_path(self) -> Path:
        if self.meta.get("repo_path"):
            return Path(self.meta["repo_path"])
        return config.REPOS / self.paper_id

    @property
    def repo_url(self) -> str | None:
        return self.meta.get("repo_url")

    @cached_property
    def repo_sha(self) -> str:
        """The commit the code under test comes from. A control's copy has no history of its own:
        it carries its parent's pinned commit, or the checked-out commit of a historical fault."""
        if self.meta.get("repo_sha_effective"):
            return self.meta["repo_sha_effective"]
        if self.parent_id or not (self.repo_path / ".git").exists():
            return self.meta.get("repo_sha", "")
        try:
            return repos.head_sha(self.repo_path)
        except Exception:
            return self.meta.get("repo_sha", "")

    def ensure_pdf(self) -> Path:
        """Fetch the pinned PDF version when the corpus ships only its URL."""
        path = self.pdf_path
        if not path.exists():
            url = self.meta.get("pdf_url") or (get(self.parent_id).meta.get("pdf_url") if self.parent_id else None)
            if not url:
                raise FileNotFoundError(f"{path} is missing and no pdf_url is recorded")
            import urllib.request
            req = urllib.request.Request(url, headers={"User-Agent": "inquest/0.1 (reproducibility research)"})
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
            if not data.startswith(b"%PDF"):
                raise RuntimeError(f"{url} did not return a PDF")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return path

    def ensure_repo(self) -> Path:
        if self.source == "variant":
            return self.repo_path
        path, _ = repos.clone(self.paper_id, self.meta["repo_url"], self.meta.get("repo_sha"))
        return path

    # ------------------------------------------------------------- claims
    def hand_claims(self) -> list[Claim]:
        if self.parent_id and not (self.dir / "claims.json").exists():
            return get(self.parent_id).hand_claims()
        f = self.dir / "claims.json"
        if not f.exists():
            return []
        return [Claim.model_validate(c) for c in json.loads(f.read_text(encoding="utf-8"))]

    def extracted_claims(self) -> list[Claim]:
        return [Claim.model_validate(c) for c in store.get_claims(self.paper_id, "extracted")]

    @property
    def claims_source(self) -> str:
        chosen = store.kv_get(f"claims_source:{self.paper_id}")
        if chosen in ("hand", "extracted"):
            return chosen
        return "hand" if self.hand_claims() else "extracted"

    def claims(self) -> list[Claim]:
        return self.hand_claims() if self.claims_source == "hand" else self.extracted_claims()

    def claim(self, claim_id: str) -> Claim:
        for c in self.claims():
            if c.claim_id == claim_id:
                return c
        raise KeyError(claim_id)

    # ------------------------------------------------------------- adapter
    def adapter(self) -> AdapterSpec | None:
        stored = store.get_adapter(self.paper_id)
        if stored:
            return AdapterSpec.model_validate(stored[0])
        f = self.dir / "adapter.yaml"
        if f.exists():
            return AdapterSpec.model_validate(yaml.safe_load(f.read_text(encoding="utf-8")))
        if self.parent_id:
            parent = get(self.parent_id).adapter()
            return parent.model_copy(update={"paper_id": self.paper_id}) if parent else None
        return None

    def adapter_origin(self) -> str | None:
        stored = store.get_adapter(self.paper_id)
        if stored:
            return stored[1]
        if (self.dir / "adapter.yaml").exists():
            return "hand"
        if self.parent_id:
            return get(self.parent_id).adapter_origin()
        return None

    # ------------------------------------------------------------- deviations
    def hand_deviations(self) -> list[Deviation]:
        f = self.dir / "deviations.json"
        if not f.exists():
            if self.parent_id:
                return get(self.parent_id).hand_deviations()
            return []
        return [Deviation.model_validate(d) for d in json.loads(f.read_text(encoding="utf-8"))]

    def found_deviations(self) -> list[Deviation]:
        return [Deviation.model_validate(d) for d in store.get_deviations(self.paper_id, "finder")]

    def summary(self) -> dict:
        return {
            "paper_id": self.paper_id, "title": self.title, "authors": self.meta.get("authors"),
            "venue": self.meta.get("venue"), "arxiv": self.meta.get("arxiv"), "paper_date": self.meta.get("paper_date"),
            "repo_url": self.repo_url, "repo_sha": self.meta.get("repo_sha_effective") or self.meta.get("repo_sha"),
            "provenance": self.meta.get("provenance"),
            "provenance_note": self.meta.get("provenance_note"), "source": self.source, "parent": self.parent_id,
            "role": self.meta.get("role"), "variant_kind": self.meta.get("variant_kind"),
            "claims_source": self.claims_source, "has_hand_claims": bool(self.hand_claims()),
            "n_claims": len(self.claims()), "adapter_origin": self.adapter_origin(),
        }


VARIANTS = config.WORKSPACE / "variants"


def _corpus_papers() -> dict[str, Paper]:
    """Curated papers from the corpus, and controls generated into the workspace."""
    out = {}
    for root in (config.CORPUS, VARIANTS):
        if not root.exists():
            continue
        for d in sorted(root.iterdir()):
            f = d / "meta.json"
            if f.exists():
                meta = json.loads(f.read_text(encoding="utf-8"))
                source = "variant" if meta.get("parent") else "corpus"
                if source == "variant":
                    meta.setdefault("repo_path", str(VARIANTS / d.name / "repo"))
                out[meta["paper_id"]] = Paper(meta["paper_id"], meta, d, source)
    return out


def all_papers() -> dict[str, Paper]:
    papers = _corpus_papers()
    for meta in store.registered_papers():
        pid = meta["paper_id"]
        if pid not in papers:
            papers[pid] = Paper(pid, meta, config.UPLOADS / pid, meta.get("source", "registered"))
    return papers


def get(paper_id: str) -> Paper:
    papers = all_papers()
    if paper_id not in papers:
        raise PaperNotFound(paper_id)
    return papers[paper_id]


def write_yaml(path: Path, data: dict) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
