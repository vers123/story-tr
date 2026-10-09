# -*- coding: utf-8 -*-
"""长任务编排：**串行队列 + 可取消 + 内存 job 表**（纯标准库，**不依赖 mcp**）。

MCP 的 `translate` / `fetch` 通过**子进程**调用既有 CLI（`python -m story_tr ...`），
本模块负责排队、进度、取消与退出清理。子进程 stdout/stderr 落盘到
`<log_dir>/mcp_<job_id>.log`，`status` 会返回其尾行。

设计要点（与确认结论一致）：
- 同一时刻只跑一个任务（避免多个写任务争用 `.translate_stories_state.json`）；
- job 表仅存内存，进程重启即丢（底层断点仍由状态文件保留，可续跑）；
- `shutdown()` 会终止仍在运行的子进程，避免孤儿进程占用翻译配额。
"""
from __future__ import annotations

import atexit
import itertools
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime

MAX_JOBS = 50        # 内存 job 表上限：超出后丢弃最旧的「已结束」任务
LOG_TAIL_LINES = 40  # status 返回的日志尾行数
FINAL_STATES = ("succeeded", "failed", "cancelled")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _popen_kwargs() -> dict:
    """Windows 上隐藏子进程的控制台窗口。"""
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


@dataclass
class Job:
    id: str
    kind: str
    argv: list
    log_path: str
    progress_fn: object = None
    state: str = "queued"        # queued | running | succeeded | failed | cancelled
    returncode: int | None = None
    error: str | None = None
    progress: dict | None = None  # {"done": int, "total": int}
    created_at: str = field(default_factory=_now)
    started_at: str | None = None
    finished_at: str | None = None
    proc: object = None
    cancel_requested: bool = False

    def summary(self) -> dict:
        return {"job_id": self.id, "kind": self.kind, "state": self.state,
                "returncode": self.returncode, "error": self.error,
                "progress": self.progress, "created_at": self.created_at,
                "started_at": self.started_at, "finished_at": self.finished_at}


class JobManager:
    """串行执行子进程任务的内存管理器。"""

    def __init__(self, log_dir: str):
        self._log_dir = log_dir
        self._jobs: dict = {}
        self._order: list = []
        self._queue: list = []
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._ids = itertools.count(1)
        self._worker = threading.Thread(target=self._loop, name="mcp-jobs", daemon=True)
        self._worker.start()
        atexit.register(self.shutdown)

    # ------------------------------------------------------------------ #
    # 对外 API
    # ------------------------------------------------------------------ #
    def submit(self, kind: str, argv, progress_fn=None) -> Job:
        """入队一个任务，立即返回 Job（状态 queued）。"""
        jid = "job-%d" % next(self._ids)
        job = Job(id=jid, kind=kind, argv=list(argv), progress_fn=progress_fn,
                  log_path=os.path.join(self._log_dir, "mcp_%s.log" % jid))
        with self._lock:
            self._jobs[jid] = job
            self._order.append(jid)
            self._queue.append(jid)
            self._trim_locked()
        self._wake.set()
        return job

    def status(self, job_id: str) -> dict:
        job = self._get(job_id)
        if job.state == "running" and job.progress_fn:
            job.progress = self._read_progress(job)
        out = job.summary()
        out["log_tail"] = self._tail(job)
        return out

    def cancel(self, job_id: str) -> dict:
        """取消：排队中直接置为 cancelled；运行中终止子进程。"""
        job = self._get(job_id)
        with self._lock:
            job.cancel_requested = True
            proc = job.proc
        if job.state == "queued":
            with self._lock:
                job.state = "cancelled"
                job.finished_at = _now()
        if proc is not None and proc.poll() is None:
            self._terminate(proc)
        return job.summary()

    def list_jobs(self) -> list:
        """全部任务（新 → 旧）。"""
        with self._lock:
            jobs = [self._jobs[i] for i in self._order if i in self._jobs]
        return [j.summary() for j in reversed(jobs)]

    def wait(self, job_id: str, timeout: float = 30.0) -> dict:
        """阻塞等待任务进入终态（主要用于测试/脚本）。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._get(job_id).state in FINAL_STATES:
                break
            time.sleep(0.05)
        return self.status(job_id)

    def shutdown(self) -> None:
        """终止所有仍在运行的子进程。"""
        with self._lock:
            procs = [j.proc for j in self._jobs.values() if j.proc is not None]
        for proc in procs:
            if proc.poll() is None:
                self._terminate(proc)

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #
    def _get(self, job_id: str) -> Job:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise KeyError("未知 job_id：%s（可用 job_list 查看）" % job_id)
        return job

    def _loop(self) -> None:
        while True:
            self._wake.wait()
            while True:
                with self._lock:
                    if not self._queue:
                        self._wake.clear()
                        break
                    job = self._jobs.get(self._queue.pop(0))
                if job is not None:
                    self._exec(job)

    def _exec(self, job: Job) -> None:
        with self._lock:
            if job.cancel_requested:
                job.state = "cancelled"
                job.finished_at = _now()
                return
            job.state = "running"
            job.started_at = _now()
        os.makedirs(self._log_dir, exist_ok=True)
        try:
            with open(job.log_path, "wb") as fh:
                proc = subprocess.Popen(job.argv, stdout=fh, stderr=subprocess.STDOUT,
                                        **_popen_kwargs())
                with self._lock:
                    job.proc = proc
                while proc.poll() is None:
                    job.progress = self._read_progress(job)
                    with self._lock:
                        if job.cancel_requested:
                            break
                    time.sleep(1.0)
                if job.cancel_requested:                 # 取消：确保子进程已终止
                    if proc.poll() is None:
                        self._terminate(proc)
                        proc.wait()
                    with self._lock:
                        job.state = "cancelled"
                else:
                    with self._lock:
                        job.state = "succeeded" if proc.returncode == 0 else "failed"
                job.returncode = proc.returncode
        except OSError as exc:
            job.state = "failed"
            job.error = str(exc)
        finally:
            job.progress = self._read_progress(job)
            with self._lock:
                job.proc = None
                job.finished_at = _now()

    @staticmethod
    def _read_progress(job: Job):
        if not job.progress_fn:
            return job.progress
        try:
            return job.progress_fn()
        except Exception:  # noqa: BLE001 - 进度读取失败不影响任务本身
            return job.progress

    @staticmethod
    def _terminate(proc) -> None:
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _tail(job: Job) -> list:
        if not os.path.exists(job.log_path):
            return []
        try:
            with open(job.log_path, encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
        except OSError:
            return []
        return lines[-LOG_TAIL_LINES:]

    def _trim_locked(self) -> None:
        while len(self._order) > MAX_JOBS:
            for i, jid in enumerate(self._order):
                job = self._jobs.get(jid)
                if job is not None and job.state in FINAL_STATES:
                    self._order.pop(i)
                    self._jobs.pop(jid, None)
                    break
            else:
                break


_manager = None


def manager() -> JobManager:
    """进程内单例（log_dir 取工作目录下的 `logs/`）。"""
    global _manager
    if _manager is None:
        from .. import paths
        _manager = JobManager(os.path.join(paths.home(), "logs"))
    return _manager


__all__ = ["Job", "JobManager", "manager"]
