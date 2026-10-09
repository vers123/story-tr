# -*- coding: utf-8 -*-
"""长任务编排（`story_tr.mcp.jobs`）：串行队列 / 状态 / 进度 / 取消。

纯标准库实现，不依赖 mcp，任何受支持的 Python 版本都可跑。
"""
import sys
import time

from story_tr.mcp import jobs


def _sleep_job(jm, seconds):
    return jm.submit("t", [sys.executable, "-c", "import time; time.sleep(%s)" % seconds])


def _wait_running(jm, job_id, timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if jm.status(job_id)["state"] == "running":
            return True
        time.sleep(0.05)
    return False


def test_success_and_log_tail(tmp_path):
    jm = jobs.JobManager(str(tmp_path))
    job = jm.submit("t", [sys.executable, "-c", "print('ok')"])
    out = jm.wait(job.id, timeout=20)
    assert out["state"] == "succeeded"
    assert out["returncode"] == 0
    assert any("ok" in line for line in out["log_tail"])


def test_failure_keeps_returncode(tmp_path):
    jm = jobs.JobManager(str(tmp_path))
    job = jm.submit("t", [sys.executable, "-c", "import sys; sys.exit(3)"])
    out = jm.wait(job.id, timeout=20)
    assert out["state"] == "failed"
    assert out["returncode"] == 3


def test_cancel_running_process(tmp_path):
    jm = jobs.JobManager(str(tmp_path))
    job = _sleep_job(jm, 30)
    assert _wait_running(jm, job.id)
    jm.cancel(job.id)
    assert jm.wait(job.id, timeout=20)["state"] == "cancelled"


def test_cancel_queued_job(tmp_path):
    jm = jobs.JobManager(str(tmp_path))
    first = _sleep_job(jm, 2)
    second = _sleep_job(jm, 2)
    assert _wait_running(jm, first.id)
    jm.cancel(second.id)                      # 还在排队
    assert jm.status(second.id)["state"] == "cancelled"
    jm.cancel(first.id)
    jm.wait(first.id, timeout=20)


def test_jobs_run_serially(tmp_path):
    jm = jobs.JobManager(str(tmp_path))
    a = _sleep_job(jm, 0.4)
    b = _sleep_job(jm, 0.1)
    oa = jm.wait(a.id, timeout=20)
    ob = jm.wait(b.id, timeout=20)
    assert oa["state"] == "succeeded" and ob["state"] == "succeeded"
    assert ob["started_at"] >= oa["finished_at"]   # b 在 a 结束后才开始


def test_progress_fn_and_list_jobs(tmp_path):
    jm = jobs.JobManager(str(tmp_path))
    job = jm.submit("t", [sys.executable, "-c", "import time; time.sleep(30)"],
                    progress_fn=lambda: {"done": 1, "total": 2})
    assert _wait_running(jm, job.id)
    assert jm.status(job.id)["progress"] == {"done": 1, "total": 2}
    assert [j["job_id"] for j in jm.list_jobs()] == [job.id]
    jm.cancel(job.id)
    jm.wait(job.id, timeout=20)


def test_unknown_job_id_raises(tmp_path):
    jm = jobs.JobManager(str(tmp_path))
    try:
        jm.status("nope")
    except KeyError as exc:
        assert "nope" in str(exc)
    else:
        raise AssertionError("应抛出 KeyError")


def test_trim_drops_oldest_finished(monkeypatch, tmp_path):
    monkeypatch.setattr(jobs, "MAX_JOBS", 2)
    jm = jobs.JobManager(str(tmp_path))
    first = jm.submit("t", [sys.executable, "-c", "pass"])
    jm.wait(first.id, timeout=20)
    jm.submit("t", [sys.executable, "-c", "pass"])
    jm.submit("t", [sys.executable, "-c", "pass"])
    assert len(jm.list_jobs()) == 2
    assert first.id not in [j["job_id"] for j in jm.list_jobs()]
