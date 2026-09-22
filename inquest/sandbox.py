"""Execution sandboxes for untrusted research code.

`docker` is the reference backend: CPU-only container, `--network=none`, CPU and memory
caps, repository mounted read-write into a throwaway container.

`local` is the backend for hosts without a container runtime. It applies the same caps
with operating-system primitives: a Windows Job Object (memory limit, kill on close) or
POSIX rlimits, a wall-clock timeout, thread caps, and a Python-level network guard
installed by the Witness shim. It isolates resources, not the filesystem, and every run
records which backend executed it.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from . import config


@dataclass
class Execution:
    returncode: int
    wall_seconds: float
    timed_out: bool
    backend: str


def _windows_job(memory_mb: int):
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class EXTENDED(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    JOB_OBJECT_LIMIT_JOB_MEMORY = 0x200
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    job = k32.CreateJobObjectW(None, None)
    info = EXTENDED()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_JOB_MEMORY | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    info.JobMemoryLimit = memory_mb * 1024 * 1024
    k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
    return k32, job


def _run_local(cmd: list[str], cwd: Path, env: dict, stdout_path: Path, timeout: int, memory_mb: int) -> Execution:
    start = time.perf_counter()
    with open(stdout_path, "wb") as out:
        if sys.platform == "win32":
            k32, job = _windows_job(memory_mb)
            proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=out, stderr=subprocess.STDOUT,
                                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
            PROCESS_ALL_ACCESS = 0x1F0FFF
            handle = k32.OpenProcess(PROCESS_ALL_ACCESS, False, proc.pid)
            k32.AssignProcessToJobObject(job, handle)
            k32.CloseHandle(handle)
            try:
                rc = proc.wait(timeout=timeout)
                timed_out = False
            except subprocess.TimeoutExpired:
                k32.TerminateJobObject(job, 1)
                rc = proc.wait()
                timed_out = True
            finally:
                k32.CloseHandle(job)
        else:
            import resource

            def limits():
                cap = memory_mb * 1024 * 1024
                resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
                os.setsid()

            proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=out, stderr=subprocess.STDOUT, preexec_fn=limits)
            try:
                rc = proc.wait(timeout=timeout)
                timed_out = False
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, 9)
                rc = proc.wait()
                timed_out = True
    return Execution(rc, time.perf_counter() - start, timed_out, "local")


def _run_docker(cmd: list[str], cwd: Path, repo_root: Path, env: dict, stdout_path: Path, timeout: int,
                memory_mb: int, image: str, out_dir: Path) -> Execution:
    rel = cwd.relative_to(repo_root).as_posix()
    passthrough = {k: v for k, v in env.items() if k.startswith(("WITNESS_", "INQUEST_", "OMP_", "MKL_",
                                                                 "OPENBLAS_", "PYTHONHASHSEED", "CUBLAS_"))}
    passthrough.update({"WITNESS_OUT": "/out/witness.jsonl", "WITNESS_PAYLOAD_DIR": "/out/payloads",
                        "WITNESS_REPO_ROOT": "/work/repo", "PYTHONPATH": env.get("INQUEST_CONTAINER_PYTHONPATH", "/witness")})
    args = ["docker", "run", "--rm", "--network=none", f"--cpus={config.THREADS_PER_RUN}",
            f"--memory={memory_mb}m", "-v", f"{repo_root}:/work/repo", "-v", f"{config.WITNESS_DIR}:/witness:ro",
            "-v", f"{out_dir}:/out", "-w", f"/work/repo/{rel}"]
    for k, v in passthrough.items():
        args += ["-e", f"{k}={v}"]
    args += [image, "python", *cmd[1:]]
    start = time.perf_counter()
    with open(stdout_path, "wb") as out:
        try:
            rc = subprocess.run(args, stdout=out, stderr=subprocess.STDOUT, timeout=timeout).returncode
            timed_out = False
        except subprocess.TimeoutExpired:
            rc, timed_out = -9, True
    return Execution(rc, time.perf_counter() - start, timed_out, "docker")


def docker_available() -> bool:
    return shutil.which("docker") is not None


def execute(cmd: list[str], cwd: Path, repo_root: Path, env: dict, stdout_path: Path, out_dir: Path,
            image: str = "", timeout: int | None = None, memory_mb: int | None = None) -> Execution:
    timeout = timeout or config.RUN_TIMEOUT_S
    memory_mb = memory_mb or config.RUN_MEMORY_MB
    if config.SANDBOX == "docker":
        if not docker_available():
            raise RuntimeError("INQUEST_SANDBOX=docker but no docker executable is on PATH")
        if not image:
            raise RuntimeError("the docker backend needs an image; run `python -m inquest image <paper>` first")
        return _run_docker(cmd, cwd, repo_root, env, stdout_path, timeout, memory_mb, image, out_dir)
    return _run_local(cmd, cwd, env, stdout_path, timeout, memory_mb)


DOCKERFILE = """FROM python:{python}-slim
ENV PIP_NO_CACHE_DIR=1 PYTHONDONTWRITEBYTECODE=1
COPY env.lock /tmp/env.lock
RUN pip install -r /tmp/env.lock "wrapt>=1.14"
"""


def build_image(paper_id: str, python: str, lock_path: Path) -> str:
    """Build the pinned image for a paper from its lock file. Returns the image digest."""
    ctx = config.TMP / f"image_{paper_id}"
    ctx.mkdir(parents=True, exist_ok=True)
    (ctx / "Dockerfile").write_text(DOCKERFILE.format(python=python), encoding="utf-8")
    shutil.copy(lock_path, ctx / "env.lock")
    tag = f"inquest/{paper_id}:latest"
    subprocess.run(["docker", "build", "-t", tag, str(ctx)], check=True)
    digest = subprocess.run(["docker", "image", "inspect", "--format", "{{.Id}}", tag],
                            capture_output=True, text=True, check=True).stdout.strip()
    return digest or tag
