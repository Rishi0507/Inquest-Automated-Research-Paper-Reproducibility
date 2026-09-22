"""Inquest Runtime Witness (component C4).

Loaded at interpreter start through PYTHONPATH inside the run sandbox. Every hook logs and
then calls through, so the observed program behaves exactly as it would without the shim.
Records are JSON lines written to $WITNESS_OUT; each carries the repository `file:line`
that made the call.

Supported surface: argparse, random, NumPy, PyTorch and scikit-learn. This file must stay
compatible with Python 3.8 because it runs inside reconstructed, era-appropriate
environments.
"""
import atexit
import json
import os
import sys
import threading
import time

_OUT = os.environ.get("WITNESS_OUT")
_ROOT = os.path.normcase(os.path.abspath(os.environ.get("WITNESS_REPO_ROOT", os.getcwd())))
_PAYLOAD_DIR = os.environ.get("WITNESS_PAYLOAD_DIR")
_PAYLOAD_CAP = int(os.environ.get("WITNESS_PAYLOAD_CAP_MB", "200")) * 1024 * 1024
_METRIC_FNS = [x for x in os.environ.get("WITNESS_METRIC_FNS", "").split(",") if x]

_lock = threading.Lock()
_local = threading.local()
_state = {"idx": 0, "payload_bytes": 0, "hits": {}, "seed_calls": 0}
_log = None

SKLEARN_METRICS = (
    "accuracy_score", "balanced_accuracy_score", "f1_score", "fbeta_score", "precision_score",
    "recall_score", "roc_auc_score", "average_precision_score", "matthews_corrcoef",
    "jaccard_score", "top_k_accuracy_score", "log_loss", "mean_squared_error",
    "mean_absolute_error", "r2_score",
)
ARRAY_KWARGS = ("y_true", "y_pred", "y_score", "y_prob", "probas_pred")


def _busy():
    return getattr(_local, "busy", False)


class _Guard(object):
    def __enter__(self):
        _local.busy = True

    def __exit__(self, *exc):
        _local.busy = False


def _caller():
    f = sys._getframe(2)
    while f is not None:
        fn = os.path.normcase(os.path.abspath(f.f_code.co_filename))
        if fn.startswith(_ROOT + os.sep) and "sitecustomize" not in fn:
            rel = os.path.relpath(fn, _ROOT).replace(os.sep, "/")
            return "%s:%d" % (rel, f.f_lineno)
        f = f.f_back
    return "unknown"


def _safe(v, depth=0):
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return v if v == v and v not in (float("inf"), float("-inf")) else repr(v)
    if depth < 2 and isinstance(v, (list, tuple)) and len(v) <= 16:
        return [_safe(x, depth + 1) for x in v]
    if depth < 2 and isinstance(v, dict) and len(v) <= 64:
        return {str(k): _safe(x, depth + 1) for k, x in v.items()}
    try:
        item = getattr(v, "item", None)
        size = getattr(v, "size", None)
        if callable(size):
            size = size()
        if item is not None and (size == 1 or size == ()):
            return _safe(item(), depth)
    except Exception:
        pass
    r = repr(v)
    return r if len(r) <= 200 else r[:197] + "..."


def emit(kind, name, kwargs, value=None, payload=None, caller=None):
    if _log is None:
        return None
    with _lock:
        idx = _state["idx"]
        _state["idx"] += 1
        rec = {"idx": idx, "kind": kind, "name": name, "kwargs": kwargs, "value": value,
               "caller": caller or _caller(), "payload_path": payload, "t": time.time()}
        try:
            _log.write(json.dumps(rec) + "\n")
        except Exception:
            rec["kwargs"] = {"unserialisable": repr(kwargs)[:500]}
            _log.write(json.dumps(rec) + "\n")
    return idx


def _to_numpy(x):
    try:
        import numpy as np
    except Exception:
        return None
    try:
        if hasattr(x, "detach"):
            x = x.detach()
        if hasattr(x, "cpu"):
            x = x.cpu()
        if hasattr(x, "numpy"):
            x = x.numpy()
        arr = np.asarray(x)
        if arr.dtype == object:
            return None
        return arr
    except Exception:
        return None


_pending = {}
_KEEP_PER_SITE = 2


def _save_arrays(name, arrays, site=None):
    """Retain the arrays of a metric call in memory; the last calls per call site are written at exit.

    Per-epoch validation calls would otherwise cost one file write each. The returned
    token is resolved to a file path through payloads/index.json.
    """
    if not _PAYLOAD_DIR:
        return None
    clean = {}
    total = 0
    for k, v in arrays.items():
        a = _to_numpy(v)
        if a is None:
            continue
        clean[k] = a.copy()
        total += a.nbytes
    if not clean or total > _PAYLOAD_CAP:
        return None
    idx = _state["idx"]
    key = (name, site)
    kept = _pending.setdefault(key, [])
    kept.append((idx, name, clean))
    if len(kept) > _KEEP_PER_SITE:
        kept.pop(0)
    return "pending:%d" % idx


def _flush_payloads():
    if not _PAYLOAD_DIR or not _pending:
        return
    import numpy as np
    os.makedirs(_PAYLOAD_DIR, exist_ok=True)
    index = {}
    for kept in _pending.values():
        for idx, name, arrays in kept:
            path = os.path.join(_PAYLOAD_DIR, "%s_%d.npz" % (name.replace(":", "_").replace(".", "_"), idx))
            np.savez_compressed(path, **arrays)
            index[str(idx)] = path
    with open(os.path.join(_PAYLOAD_DIR, "index.json"), "w") as f:
        json.dump(index, f)


def _float(v):
    try:
        if hasattr(v, "item"):
            v = v.item()
        return float(v)
    except Exception:
        return None


def witness_hit(tag):
    """Called by probe lines that Inquest inserts into patched code."""
    first = tag not in _state["hits"]
    _state["hits"][tag] = _state["hits"].get(tag, 0) + 1
    if first:
        emit("PATCH_HIT", str(tag), {}, caller=_caller())


# ----------------------------------------------------------------------------- hooks


def _install_argparse():
    import argparse

    original = argparse.ArgumentParser.parse_known_args

    def parse_known_args(self, args=None, namespace=None):
        depth = getattr(_local, "argdepth", 0)
        _local.argdepth = depth + 1
        try:
            result = original(self, args, namespace)
        finally:
            _local.argdepth = depth
        if depth == 0 and not _busy():
            with _Guard():
                ns = vars(result[0])
                actions = []
                explicit = []
                for a in self._actions:
                    if a.dest in ("help",) or a.dest is argparse.SUPPRESS:
                        continue
                    actions.append({
                        "dest": a.dest, "flags": list(a.option_strings), "default": _safe(a.default),
                        "type": getattr(a.type, "__name__", None) if a.type else None,
                        "choices": _safe(list(a.choices)) if a.choices else None,
                        "help": (a.help or "")[:200], "store_true": a.__class__.__name__ in ("_StoreTrueAction", "_StoreFalseAction"),
                    })
                    if a.dest in ns and ns[a.dest] != a.default:
                        explicit.append(a.dest)
                emit("ARGS", "argparse", {"namespace": {k: _safe(v) for k, v in ns.items()},
                                          "actions": actions, "explicit": explicit,
                                          "argv": list(sys.argv[1:])})
        return result

    argparse.ArgumentParser.parse_known_args = parse_known_args


def _seed_wrapper(label):
    def wrapper(wrapped, instance, args, kwargs):
        if not _busy():
            with _Guard():
                seed = args[0] if args else kwargs.get("seed", kwargs.get("a"))
                _state["seed_calls"] += 1
                emit("SEED_CALL", label, {"seed": _safe(seed)})
        return wrapped(*args, **kwargs)
    return wrapper


def _install_random(wrapt):
    import random
    wrapt.wrap_function_wrapper(random, "seed", _seed_wrapper("random.seed"))


def _hook_numpy(wrapt):
    def hook(npr):
        wrapt.wrap_function_wrapper(npr, "seed", _seed_wrapper("numpy.random.seed"))
        if hasattr(npr, "default_rng"):
            wrapt.wrap_function_wrapper(npr, "default_rng", _seed_wrapper("numpy.random.default_rng"))
    wrapt.register_post_import_hook(hook, "numpy.random")


def _init_wrapper(kind, describe):
    """Wrap a constructor; nested constructors (super().__init__) record only the outermost call."""
    def outer(wrapped, instance, args, kwargs):
        depth = getattr(_local, "init_depth", 0)
        _local.init_depth = depth + 1
        try:
            result = wrapped(*args, **kwargs)
        finally:
            _local.init_depth = depth
        if depth == 0 and not _busy() and instance is not None:
            with _Guard():
                try:
                    emit(kind, type(instance).__name__, describe(instance, args, kwargs))
                except Exception as exc:
                    emit(kind, type(instance).__name__, {"error": repr(exc)[:200]})
        return result
    return outer


def _describe_optimizer(opt, args, kwargs):
    groups = []
    for g in opt.param_groups:
        groups.append({k: _safe(v) for k, v in g.items() if k != "params"})
        groups[-1]["n_params"] = sum(int(p.numel()) for p in g["params"])
    return {"defaults": {k: _safe(v) for k, v in opt.defaults.items()}, "param_groups": groups,
            "lr": _safe(opt.defaults.get("lr")), "weight_decay": _safe(opt.defaults.get("weight_decay"))}


def _describe_scheduler(s, args, kwargs):
    out = {k: _safe(v) for k, v in kwargs.items()}
    for attr in ("step_size", "gamma", "milestones", "T_max", "warmup_steps", "total_iters", "start_factor",
                 "end_factor", "factor", "patience", "mode"):
        if hasattr(s, attr) and attr not in out:
            out[attr] = _safe(getattr(s, attr))
    return out


def _describe_loader(dl, args, kwargs):
    try:
        n = len(dl.dataset)
    except Exception:
        n = None
    sampler = type(getattr(dl, "sampler", None)).__name__
    return {"dataset_len": n, "batch_size": _safe(dl.batch_size), "shuffle": sampler == "RandomSampler",
            "sampler": sampler, "drop_last": _safe(dl.drop_last), "num_workers": _safe(dl.num_workers)}


def _hook_torch(wrapt):
    threads = os.environ.get("INQUEST_THREADS")

    def on_torch(torch):
        wrapt.wrap_function_wrapper(torch, "manual_seed", _seed_wrapper("torch.manual_seed"))
        if hasattr(torch, "use_deterministic_algorithms"):
            def det(wrapped, instance, args, kwargs):
                if not _busy():
                    with _Guard():
                        emit("DETERMINISM_FLAG", "torch.use_deterministic_algorithms",
                             {"mode": _safe(args[0] if args else kwargs.get("mode")), "source": "repository"})
                return wrapped(*args, **kwargs)
            wrapt.wrap_function_wrapper(torch, "use_deterministic_algorithms", det)
        if threads:
            try:
                torch.set_num_threads(int(threads))
            except Exception:
                pass
        if os.environ.get("WITNESS_DETERMINISTIC") == "1" and hasattr(torch, "use_deterministic_algorithms"):
            try:
                torch.use_deterministic_algorithms.__wrapped__(True, warn_only=True)
                emit("DETERMINISM_FLAG", "torch.use_deterministic_algorithms",
                     {"mode": True, "warn_only": True, "source": "injected"}, caller="inquest")
            except TypeError:
                emit("DETERMINISM_FLAG", "torch.use_deterministic_algorithms",
                     {"mode": None, "source": "injection_unsupported"}, caller="inquest")

    def on_cuda(cuda):
        for name in ("manual_seed", "manual_seed_all"):
            if hasattr(cuda, name):
                wrapt.wrap_function_wrapper(cuda, name, _seed_wrapper("torch.cuda." + name))

    def on_optim(optim):
        base = optim.Optimizer
        for name in dir(optim):
            cls = getattr(optim, name)
            if isinstance(cls, type) and issubclass(cls, base) and cls is not base and "__init__" in cls.__dict__:
                wrapt.wrap_function_wrapper(cls, "__init__", _init_wrapper("OPTIMIZER", _describe_optimizer))

    def on_sched(sched):
        base = getattr(sched, "LRScheduler", None) or getattr(sched, "_LRScheduler", None)
        for name in dir(sched):
            cls = getattr(sched, name)
            if isinstance(cls, type) and "__init__" in cls.__dict__ and name[0] != "_" and (
                    (base is not None and issubclass(cls, base) and cls is not base) or name == "ReduceLROnPlateau"):
                wrapt.wrap_function_wrapper(cls, "__init__", _init_wrapper("SCHEDULER", _describe_scheduler))

    def on_data(data):
        wrapt.wrap_function_wrapper(data.DataLoader, "__init__", _init_wrapper("DATALOADER", _describe_loader))

    wrapt.register_post_import_hook(on_torch, "torch")
    wrapt.register_post_import_hook(on_cuda, "torch.cuda")
    wrapt.register_post_import_hook(on_optim, "torch.optim")
    wrapt.register_post_import_hook(on_sched, "torch.optim.lr_scheduler")
    wrapt.register_post_import_hook(on_data, "torch.utils.data")


def _metric_wrapper(label):
    def wrapper(wrapped, instance, args, kwargs):
        out = wrapped(*args, **kwargs)
        if not _busy():
            with _Guard():
                try:
                    arrays = {}
                    if len(args) >= 1:
                        arrays["y_true"] = args[0]
                    if len(args) >= 2:
                        arrays["y_pred"] = args[1]
                    for k in ARRAY_KWARGS:
                        if k in kwargs:
                            arrays["y_true" if k == "y_true" else "y_pred"] = kwargs[k]
                    site = _caller()
                    payload = _save_arrays(label, arrays, site)
                    kw = {k: _safe(v) for k, v in kwargs.items() if k not in ARRAY_KWARGS}
                    emit("METRIC_CALL", label, kw, _float(out), payload, caller=site)
                except Exception as exc:
                    emit("METRIC_CALL", label, {"error": repr(exc)[:200]}, _float(out))
        return out
    return wrapper


def _repo_metric_wrapper(label):
    def wrapper(wrapped, instance, args, kwargs):
        out = wrapped(*args, **kwargs)
        if not _busy():
            with _Guard():
                arrays = {}
                if len(args) >= 1:
                    arrays["y_pred"] = args[0]
                if len(args) >= 2:
                    arrays["y_true"] = args[1]
                site = _caller()
                payload = _save_arrays(label, arrays, site)
                emit("METRIC_CALL", label, {"repository_function": True}, _float(out), payload, caller=site)
        return out
    return wrapper


def _hook_sklearn(wrapt):
    def on_metrics(mod):
        for fn in SKLEARN_METRICS:
            if hasattr(mod, fn):
                wrapt.wrap_function_wrapper(mod, fn, _metric_wrapper(fn))

    def on_selection(mod):
        def split(wrapped, instance, args, kwargs):
            out = wrapped(*args, **kwargs)
            if not _busy():
                with _Guard():
                    sizes = []
                    for part in out:
                        try:
                            sizes.append(len(part))
                        except Exception:
                            sizes.append(None)
                    emit("SPLIT_CALL", "train_test_split", {
                        "test_size": _safe(kwargs.get("test_size")), "train_size": _safe(kwargs.get("train_size")),
                        "stratify": kwargs.get("stratify") is not None, "shuffle": _safe(kwargs.get("shuffle", True)),
                        "random_state": _safe(kwargs.get("random_state")), "sizes": sizes})
            return out
        if hasattr(mod, "train_test_split"):
            wrapt.wrap_function_wrapper(mod, "train_test_split", split)

        def describe(obj, args, kwargs):
            return {"n_splits": _safe(getattr(obj, "n_splits", None)), "shuffle": _safe(getattr(obj, "shuffle", None)),
                    "random_state": _safe(getattr(obj, "random_state", None)),
                    "stratified": "Stratified" in type(obj).__name__}
        for name in ("KFold", "StratifiedKFold", "ShuffleSplit", "StratifiedShuffleSplit", "RepeatedKFold"):
            if hasattr(mod, name):
                wrapt.wrap_function_wrapper(getattr(mod, name), "__init__", _init_wrapper("SPLIT_CALL", describe))

    wrapt.register_post_import_hook(on_metrics, "sklearn.metrics")
    wrapt.register_post_import_hook(on_selection, "sklearn.model_selection")


def _hook_repo_metrics(wrapt):
    for spec in _METRIC_FNS:
        if ":" not in spec:
            continue
        module, fn = spec.split(":", 1)

        def hook(mod, fn=fn, spec=spec):
            if hasattr(mod, fn):
                wrapt.wrap_function_wrapper(mod, fn, _repo_metric_wrapper(spec))
        wrapt.register_post_import_hook(hook, module)


def _install_network_guard():
    import socket

    def blocked(*args, **kwargs):
        raise OSError("network access is disabled in the Inquest run phase")

    socket.socket.connect = blocked
    socket.socket.connect_ex = blocked
    socket.create_connection = blocked
    socket.getaddrinfo = blocked


def _at_exit():
    try:
        state = {"seed_calls_observed": _state["seed_calls"], "patch_hits": _state["hits"],
                 "pythonhashseed": os.environ.get("PYTHONHASHSEED")}
        torch = sys.modules.get("torch")
        if torch is not None:
            try:
                state["cudnn_deterministic"] = bool(torch.backends.cudnn.deterministic)
                state["cudnn_benchmark"] = bool(torch.backends.cudnn.benchmark)
                if hasattr(torch, "are_deterministic_algorithms_enabled"):
                    state["deterministic_algorithms"] = bool(torch.are_deterministic_algorithms_enabled())
            except Exception:
                pass
        emit("DETERMINISM_FLAG", "state_at_exit", state, caller="inquest")
        _flush_payloads()
        if _log is not None:
            _log.flush()
    except Exception:
        pass


def _activate():
    global _log
    _log = open(_OUT, "a", buffering=1, encoding="utf-8")
    if os.environ.get("INQUEST_NO_NETWORK") == "1":
        _install_network_guard()
    _install_argparse()
    try:
        import wrapt
    except ImportError:
        emit("DETERMINISM_FLAG", "witness_partial", {"reason": "wrapt is not installed"}, caller="inquest")
        atexit.register(_at_exit)
        return
    _install_random(wrapt)
    _hook_numpy(wrapt)
    _hook_torch(wrapt)
    _hook_sklearn(wrapt)
    _hook_repo_metrics(wrapt)
    atexit.register(_at_exit)


if _OUT:
    try:
        _activate()
    except Exception as _exc:  # the Witness must never break the program it observes
        sys.stderr.write("[inquest witness] disabled: %r\n" % (_exc,))
