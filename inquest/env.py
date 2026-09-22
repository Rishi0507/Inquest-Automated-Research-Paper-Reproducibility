"""Environment reconstruction for a repository (component C1).

Dependencies are discovered from requirement files, setup.py and an import scan, then
resolved with `uv pip compile --exclude-newer <paper date>` so the dependency set matches
the paper's era. When no binary-installable set exists at the paper date, the bound is
relaxed in six-month steps and the relaxation is recorded in the environment fingerprint.
A successful resolution is written to `env.lock` and reused, which plays the role of the
pre-built pinned image for curated papers.
"""
from __future__ import annotations

import ast
import datetime as dt
import json
import platform
import re
import subprocess
import sys
from pathlib import Path

from . import config

# Import name -> distribution name, for the import scan.
IMPORT_TO_DIST = {
    "sklearn": "scikit-learn", "yaml": "pyyaml", "cv2": "opencv-python-headless", "PIL": "pillow",
    "skimage": "scikit-image", "bs4": "beautifulsoup4", "dateutil": "python-dateutil",
    "Bio": "biopython", "attr": "attrs", "tensorboardX": "tensorboardx",
}
IGNORE_IMPORTS = {"__future__", "setuptools", "distutils", "pkg_resources"}
GPU_ONLY = {"cupy", "pycuda", "apex"}
INSTRUMENTATION = ["wrapt>=1.14"]

# PyTorch wheels for Windows and CPU-only builds live in PyTorch's own archive, which carries
# no upload dates. Era bounds for these packages are taken from their PyPI release history.
TORCH_ARCHIVE = "https://download.pytorch.org/whl/cpu/torch_stable.html"
ARCHIVE_PACKAGES = ("torch", "torchvision", "torchaudio")

STDLIB = set(sys.stdlib_module_names)


def _parse_requirement_lines(text: str) -> list[str]:
    reqs = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith(("-", "git+", "http")):
            continue
        reqs.append(line)
    return reqs


def _setup_requires(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return []
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "install_requires":
            try:
                return [str(x) for x in ast.literal_eval(node.value)]
            except Exception:
                return []
    return []


def _local_modules(repo: Path) -> set[str]:
    names = set()
    for p in repo.rglob("*"):
        if ".git" in p.parts:
            continue
        if p.is_dir() and any(p.glob("*.py")):
            names.add(p.name)
        elif p.suffix == ".py":
            names.add(p.stem)
    return names


def _imports_of(py: Path) -> list[str]:
    try:
        tree = ast.parse(py.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return []
    mods = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            mods.append(node.module)
            mods += [f"{node.module}.{a.name}" for a in node.names]
    return mods


def _local_file(mod: str, search: list[Path]) -> Path | None:
    parts = mod.split(".")
    for base in search:
        cand = base.joinpath(*parts)
        if cand.with_suffix(".py").exists():
            return cand.with_suffix(".py")
        if (cand / "__init__.py").exists():
            return cand / "__init__.py"
    return None


def scan_imports(repo: Path, entries: list[Path] | None = None, search: list[Path] | None = None) -> set[str]:
    """Third-party top-level modules imported by the repository.

    With `entries`, only modules reachable from those scripts are followed, so code that
    the experiment never imports (tuning scripts, notebooks, other tasks) adds nothing.
    """
    import warnings
    warnings.filterwarnings("ignore", category=SyntaxWarning)
    local = _local_modules(repo)
    found: set[str] = set()
    if entries:
        queue = [e for e in entries if e.exists()]
        seen: set[Path] = set()
        while queue:
            py = queue.pop()
            if py in seen:
                continue
            seen.add(py)
            dirs = [py.parent, *(search or []), repo]
            for m in _imports_of(py):
                f = _local_file(m, dirs)
                if f is not None:
                    queue.append(f)
                    continue
                top = m.split(".")[0]
                if top and top not in STDLIB and top not in local and top not in IGNORE_IMPORTS:
                    found.add(top)
        return found
    for py in repo.rglob("*.py"):
        if ".git" in py.parts:
            continue
        for m in _imports_of(py):
            top = m.split(".")[0]
            if top and top not in STDLIB and top not in local and top not in IGNORE_IMPORTS:
                found.add(top)
    return found


def _name(req: str) -> str:
    return re.split(r"[<>=!~\[; ]", req, maxsplit=1)[0].strip().lower().replace("_", "-")


def discover(repo: Path, entries: list[Path] | None = None, search: list[Path] | None = None) -> dict:
    """Return stated requirements and the imports they miss.

    `unused` lists stated requirements that the entry point never imports.
    """
    stated: list[str] = []
    for f in sorted(repo.glob("requirement*.txt")):
        stated += _parse_requirement_lines(f.read_text(encoding="utf-8", errors="replace"))
    if (repo / "setup.py").exists():
        stated += _setup_requires(repo / "setup.py")
    stated = [r for r in stated if _name(r) not in GPU_ONLY]
    names = {_name(r) for r in stated}
    imported = {IMPORT_TO_DIST.get(m, m).lower() for m in scan_imports(repo, entries, search) if m not in GPU_ONLY}
    imports = sorted(d for d in imported if d not in names)
    unused = sorted(n for n in names if entries and n not in imported)
    return {"stated": stated, "imports": imports, "unused": unused}


def _uv(args: list[str], stdin: str | None = None, timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(["uv", *args], input=stdin, env=config.tool_env(), capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=timeout)


_release_cache: dict[str, dict[str, str]] = {}


def _release_dates(package: str) -> dict[str, str]:
    """version -> first upload date on PyPI, cached in the workspace for a week."""
    if package in _release_cache:
        return _release_cache[package]
    cache = config.WORKSPACE / "cache" / f"pypi_{package}.json"
    data = None
    if cache.exists() and (dt.datetime.now().timestamp() - cache.stat().st_mtime) < 7 * 86400:
        data = json.loads(cache.read_text(encoding="utf-8"))
    if data is None:
        import urllib.request
        with urllib.request.urlopen(f"https://pypi.org/pypi/{package}/json", timeout=30) as r:
            raw = json.load(r)
        data = {v: min(f["upload_time"] for f in files)[:10] for v, files in raw["releases"].items() if files}
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(data), encoding="utf-8")
    _release_cache[package] = data
    return data


def _era_constraints(reqs: list[str], bound: str) -> list[str]:
    """Upper bounds for archive packages: the newest release on or before `bound`.

    Releases older than the package's first PyPI upload exist only in the archive; for a
    bound earlier than that upload the constraint admits only those older builds.
    """
    from packaging.version import InvalidVersion, Version

    def ver(v: str):
        try:
            return Version(v)
        except InvalidVersion:
            return None

    out = []
    names = {_name(r) for r in reqs}
    for pkg in ARCHIVE_PACKAGES:
        if pkg not in names:
            continue
        dates = {v: d for v, d in _release_dates(pkg).items() if ver(v) and not ver(v).is_prerelease}
        eligible = [v for v, d in dates.items() if d <= bound]
        if eligible:
            out.append(f"{pkg}<={max(eligible, key=ver)}")
        else:
            out.append(f"{pkg}<{min(dates, key=lambda v: dates[v])}")
    return out


def _uses_archive(reqs: list[str]) -> bool:
    return any(_name(r) in ARCHIVE_PACKAGES for r in reqs)


def _compile(reqs: list[str], python: str, bound: str | None) -> tuple[bool, str]:
    args = ["pip", "compile", "-", "--python-version", python, "--only-binary", ":all:",
            "--no-header", "--no-annotate", "--quiet"]
    constraints = []
    if bound:
        args += ["--exclude-newer", bound]
        constraints = _era_constraints(reqs, bound)
    if _uses_archive(reqs):
        args += ["--find-links", TORCH_ARCHIVE]
    out = _uv(args, stdin="\n".join(reqs + constraints) + "\n")
    return out.returncode == 0, (out.stdout if out.returncode == 0 else out.stderr)


def _last_line(text: str) -> str:
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    return lines[-1][:300] if lines else ""


PYTHON_LADDER = ["3.7", "3.8", "3.10", "3.12"]


def resolve(repo: Path, paper_date: str, pythons: list[str] | None = None,
            extra: list[str] | None = None, entries: list[Path] | None = None,
            search: list[Path] | None = None) -> dict:
    """Find the earliest date-bounded, binary-installable dependency set.

    For each bound the interpreters in `pythons` are tried oldest first, so the chosen
    interpreter is also the one closest to the paper's era.
    """
    pythons = pythons or PYTHON_LADDER
    found = discover(repo, entries, search)
    extra = extra or []
    attempts = []
    stated = found["stated"] + found["imports"] + extra
    unpinned = sorted(({_name(r) for r in found["stated"]} - set(found["unused"])) | set(found["imports"])
                      | {_name(r) for r in extra})
    candidates = [("as stated", stated), ("pins relaxed", unpinned)]
    start = dt.date.fromisoformat(paper_date[:10])
    today = dt.date.today()
    for label, reqs in candidates:
        if not reqs:
            continue
        bound = start
        tries = 0
        while True:
            tries += 1
            for python in pythons:
                ok, text = _compile(reqs, python, bound.isoformat())
                attempts.append({"requirements": label, "bound": bound.isoformat(), "python": python,
                                 "ok": ok, "detail": "" if ok else _last_line(text)})
                if ok:
                    break
            if ok:
                return {"lock": text, "requirements": reqs, "requirements_label": label,
                        "paper_date": start.isoformat(), "effective_bound": bound.isoformat(),
                        "relaxed_days": (bound - start).days, "attempts": attempts, "discovered": found,
                        "python": python}
            if bound >= today or (label == "as stated" and tries >= 3):
                break  # exact pins that fail three bounds will not resolve later
            bound = min(today, bound + dt.timedelta(days=182))
    raise RuntimeError("no installable dependency set found: " + json.dumps(attempts[-3:]))


NUGET_PYTHON = {"3.7": "3.7.9", "3.6": "3.6.8"}


def python_executable(version: str) -> str:
    """Interpreter for `uv venv`. uv provides 3.8 and newer; older Windows interpreters come
    from the official python.org NuGet package, unpacked into the workspace."""
    if sys.platform != "win32" or version not in NUGET_PYTHON:
        return version
    full = NUGET_PYTHON[version]
    home = config.UV_PYTHON / f"nuget-python-{full}"
    exe = home / "tools" / "python.exe"
    if not exe.exists():
        import io
        import urllib.request
        import zipfile
        url = f"https://www.nuget.org/api/v2/package/python/{full}"
        with urllib.request.urlopen(url, timeout=120) as r:
            blob = r.read()
        home.mkdir(parents=True, exist_ok=True)
        zipfile.ZipFile(io.BytesIO(blob)).extractall(home)
    return str(exe)


def env_path(paper_id: str) -> Path:
    return config.ENVS / paper_id


def interpreter(paper_id: str) -> Path:
    base = env_path(paper_id)
    win = base / "Scripts" / "python.exe"
    return win if win.exists() else base / "bin" / "python"


def build(paper_id: str, repo: Path, paper_date: str, pythons: list[str] | None = None,
          lock_path: Path | None = None, extra: list[str] | None = None, entries: list[Path] | None = None,
          search: list[Path] | None = None, log=print) -> dict:
    """Create the environment. Uses `lock_path` when it exists, otherwise resolves and writes it."""
    config.ensure_dirs()
    if lock_path and lock_path.exists():
        record = {}
        meta_path = lock_path.with_suffix(".json")
        if meta_path.exists():
            record = json.loads(meta_path.read_text(encoding="utf-8"))
        record["lock"] = lock_path.read_text(encoding="utf-8")
        log(f"using pinned lock {lock_path.name}")
    else:
        log(f"resolving dependencies bounded by {paper_date[:10]}")
        record = resolve(repo, paper_date, pythons, extra, entries, search)
        log(f"resolved at bound {record['effective_bound']} with Python {record['python']} "
            f"({record['relaxed_days']} days after the paper)")
        if lock_path:
            lock_path.write_text(record["lock"], encoding="utf-8")
            meta = {k: v for k, v in record.items() if k != "lock"}
            lock_path.with_suffix(".json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    python = record.get("python", "3.10")
    target = env_path(paper_id)
    if not interpreter(paper_id).exists():
        log(f"creating Python {python} environment")
        out = _uv(["venv", "--quiet", "--python", python_executable(python), str(target)])
        if out.returncode != 0:
            raise RuntimeError(out.stderr)
    lockfile = config.TMP / f"{paper_id}.lock.txt"
    lockfile.write_text(record["lock"], encoding="utf-8")
    log("installing locked dependencies")
    install = ["pip", "install", "--quiet", "--python", str(interpreter(paper_id)), "-r", str(lockfile)]
    if any(line.split("==")[0].strip() in ARCHIVE_PACKAGES for line in record["lock"].splitlines()):
        install += ["--find-links", TORCH_ARCHIVE]
    out = _uv(install, timeout=3600)
    if out.returncode != 0:
        raise RuntimeError(out.stderr[-2000:])
    out = _uv(["pip", "install", "--quiet", "--python", str(interpreter(paper_id)), *INSTRUMENTATION])
    if out.returncode != 0:
        raise RuntimeError(out.stderr[-2000:])
    fp = fingerprint(paper_id, record)
    (target / "inquest_env.json").write_text(json.dumps(fp, indent=1), encoding="utf-8")
    log("environment ready")
    return fp


def fingerprint(paper_id: str, record: dict | None = None) -> dict:
    target = env_path(paper_id)
    cached = target / "inquest_env.json"
    if record is None and cached.exists():
        return json.loads(cached.read_text(encoding="utf-8"))
    py = interpreter(paper_id)
    ver = subprocess.run([str(py), "-c", "import sys;print(sys.version.split()[0])"],
                         capture_output=True, text=True).stdout.strip()
    freeze = _uv(["pip", "freeze", "--python", str(py)]).stdout.split()
    rec = record or {}
    return {
        "python": ver,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "sandbox": config.SANDBOX,
        "packages": freeze,
        "paper_date": rec.get("paper_date"),
        "effective_bound": rec.get("effective_bound"),
        "relaxed_days": rec.get("relaxed_days"),
        "requirements_label": rec.get("requirements_label"),
        "instrumentation": INSTRUMENTATION,
    }


def ready(paper_id: str) -> bool:
    return (env_path(paper_id) / "inquest_env.json").exists()
