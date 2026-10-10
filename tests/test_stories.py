# -*- coding: utf-8 -*-
"""`story_tr.stories.prepare_state`：批次签名校验与进度沿用规则。"""
from story_tr import stories

SIG_A = {"characters": ["10000021"], "stories": "ALL", "mode": "fixed"}
SIG_B = {"characters": ["10000002"], "stories": "ALL", "mode": "fixed"}


def test_no_state_starts_fresh():
    state, completed, err = stories.prepare_state(None, SIG_A, False)
    assert err is None
    assert completed == set()
    assert state["signature"] == SIG_A


def test_same_signature_keeps_completed():
    old = {"signature": SIG_A, "completed": ["10000021|vision|full"],
           "started_at": "2026-01-01T00:00:00"}
    state, completed, err = stories.prepare_state(old, SIG_A, False)
    assert err is None
    assert completed == {"10000021|vision|full"}
    assert state["started_at"] == "2026-01-01T00:00:00"  # 沿用起始时间


def test_mismatched_signature_requires_reset():
    old = {"signature": SIG_B, "completed": ["10000002|vision|full"]}
    state, completed, err = stories.prepare_state(old, SIG_A, False)
    assert state is None and completed is None
    assert "--reset" in err


def test_reset_clears_completed():
    old = {"signature": SIG_A, "completed": ["10000021|vision|full"]}
    state, completed, err = stories.prepare_state(old, SIG_A, True)
    assert err is None
    assert completed == set()


def test_missing_signature_after_clean_keeps_completed():
    """clean 会重置签名；此时应直接采用新签名并沿用剩余记录，而不是要求 --reset。"""
    old = {"completed": ["10000103|vision|full"]}  # 无 signature
    state, completed, err = stories.prepare_state(old, SIG_A, False)
    assert err is None
    assert completed == {"10000103|vision|full"}
    assert state["signature"] == SIG_A


def test_mismatch_error_is_human_readable():
    """回归：报错不再直接甩原始 JSON，而是指出变化的那几项。"""
    old = {"signature": {"characters": ["10000021"], "stories": "ALL", "mode": "fixed",
                         "parts": "full"},
           "completed": []}
    cur = {"characters": ["10000021"], "stories": "ALL", "mode": "random",
           "parts": "segments"}
    _, _, err = stories.prepare_state(old, cur, False)
    assert "模式：上次 fixed → 本次 random" in err
    assert "输出内容：上次 full → 本次 segments" in err
    assert "角色范围" not in err  # 未变化的项不出现在差异里


def test_describe_signature_diff_lists_only_changes():
    prev = {"characters": ["1"], "mode": "fixed", "chain": "asia"}
    cur = {"characters": ["1"], "mode": "fixed", "chain": "eu"}
    text = stories.describe_signature_diff(prev, cur)
    assert "链：上次 asia → 本次 eu" in text
    assert "模式" not in text


def test_describe_signature_diff_empty_when_same():
    sig = {"mode": "fixed", "characters": ["1"]}
    assert "无可比较" in stories.describe_signature_diff(sig, dict(sig))
