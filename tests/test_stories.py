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
