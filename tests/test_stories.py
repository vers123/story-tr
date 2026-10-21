# -*- coding: utf-8 -*-
"""`story_tr.stories`：批次签名、失败隔离与续跑。"""
import json
import types

from story_tr import chain as tc
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


def _main_args(**kw):
    base = dict(char_ids=["1"], all_characters=False, stories="", all=False,
                full_only=False, segments_only=False, no_file_log=True, quiet=True,
                verbose=False, mode="fixed", chain="asia", steps=20, languages="",
                provider="google", proxy="", min_interval=0.3, balance=False,
                no_tts=True, workers=1, no_batch=False, overwrite=False, reset=False,
                revalidate=False, fail_fast=False, max_failures=20, data_dir="",
                dry_run=False, no_cache=False)
    base.update(kw)
    return types.SimpleNamespace(**base)


def _make_ws(tmp_path):
    root = tmp_path / "data" / "story" / "character"
    char_dir = root / "1"
    for story in ("vision", "vision2"):
        d = char_dir / "profile" / "story" / story
        d.mkdir(parents=True)
        (d / "zh.md").write_text(
            '---\nid: "1"\nstory: %s\nlang: zh\ntitle: "标题"\n---\n\n第一段\n\n第二段\n'
            % story, encoding="utf-8")
    (tmp_path / "data" / "character.json").write_text(
        json.dumps([{"id": "1", "name_en": "A", "name_zh": "甲",
                     "path": "data/story/character/1",
                     "stories": ["vision", "vision2"]}], ensure_ascii=False),
        encoding="utf-8")
    return root, char_dir


def test_main_dry_run_prints_plan_without_writing(tmp_path, monkeypatch, capsys):
    root, char_dir = _make_ws(tmp_path)
    monkeypatch.setattr(stories, "STORY_ROOT", str(root))
    monkeypatch.setattr(stories, "CHAR_FILE", str(tmp_path / "data" / "character.json"))
    monkeypatch.setattr(stories, "STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setattr(stories, "ROOT", str(tmp_path))

    assert stories.main(_main_args(dry_run=True)) == 0
    out = capsys.readouterr().out
    assert "--dry-run" in out
    assert "结果 8 份" in out  # 每个故事 title + full + 2 段
    assert not (char_dir / "result").exists()
    assert not (tmp_path / "state.json").exists()


def test_main_uses_title_cache(tmp_path, monkeypatch):
    root, char_dir = _make_ws(tmp_path)
    monkeypatch.setattr(stories, "STORY_ROOT", str(root))
    monkeypatch.setattr(stories, "CHAR_FILE", str(tmp_path / "data" / "character.json"))
    monkeypatch.setattr(stories, "STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setattr(stories, "ROOT", str(tmp_path))
    monkeypatch.setattr(stories, "TITLE_CACHE_PATH", str(tmp_path / "title_cache.json"))
    monkeypatch.setattr(tc, "LOG_DIR", str(tmp_path / "logs"))

    args = _main_args()
    key = stories.title_cache_key("标题", args, ["google"])
    (tmp_path / "title_cache.json").write_text(json.dumps({key: {
        "source": "标题", "source_detected_lang": "zh-CN", "target_lang": "zh-CN",
        "provider": "google", "languages": ["ja"], "steps": [], "final": "缓存标题",
    }}, ensure_ascii=False), encoding="utf-8")

    seen = []

    def fake_chain(texts, languages, providers, cfg, log, extras=None, source_lang=None):
        seen.extend(texts)
        return [{"source": t, "source_detected_lang": "zh-CN",
                 "target_lang": "zh-CN", "provider": "google",
                 "languages": list(languages), "steps": [], "final": "译" + t}
                for t in texts]

    monkeypatch.setattr(stories, "run_chain_many", fake_chain)
    assert stories.main(args) == 0
    assert "标题" not in seen  # 标题直接命中缓存，不进入翻译链
    for story in ("vision", "vision2"):
        out = json.loads((char_dir / "result" / story / "title.json")
                         .read_text(encoding="utf-8"))
        assert out["final"] == "缓存标题"


def test_main_revalidate_retranslates_stale_result(tmp_path, monkeypatch):
    root, char_dir = _make_ws(tmp_path)
    monkeypatch.setattr(stories, "STORY_ROOT", str(root))
    monkeypatch.setattr(stories, "CHAR_FILE", str(tmp_path / "data" / "character.json"))
    monkeypatch.setattr(stories, "STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setattr(stories, "ROOT", str(tmp_path))
    monkeypatch.setattr(tc, "LOG_DIR", str(tmp_path / "logs"))

    stale = char_dir / "result" / "vision" / "seg_01.json"
    stale.parent.mkdir(parents=True)
    stale.write_text(json.dumps({"source": "旧文本", "final": "旧译文"}),
                     encoding="utf-8")

    def fake_chain(texts, languages, providers, cfg, log, extras=None, source_lang=None):
        return [{"source": t, "source_detected_lang": "zh-CN",
                 "target_lang": "zh-CN", "provider": "google",
                 "languages": list(languages), "steps": [], "final": "译" + t}
                for t in texts]

    monkeypatch.setattr(stories, "run_chain_many", fake_chain)
    assert stories.main(_main_args(revalidate=True)) == 0
    data = json.loads(stale.read_text(encoding="utf-8"))
    assert data["source"] == "第一段"


def test_main_continues_after_unit_failure(tmp_path, monkeypatch):
    """一个 story 的连接失败不应终止整批：成功项落盘、失败项记录、返回 7。"""
    root = tmp_path / "data" / "story" / "character"
    char_dir = root / "1"
    for story in ("vision", "vision2"):
        d = char_dir / "profile" / "story" / story
        d.mkdir(parents=True)
        (d / "zh.md").write_text(
            '---\nid: "1"\nstory: %s\nlang: zh\ntitle: "标题"\n---\n\n第一段\n\n第二段\n'
            % story, encoding="utf-8")
    (tmp_path / "data" / "character.json").write_text(
        json.dumps([{"id": "1", "name_en": "A", "name_zh": "甲",
                     "path": "data/story/character/1",
                     "stories": ["vision", "vision2"]}], ensure_ascii=False),
        encoding="utf-8")

    monkeypatch.setattr(stories, "STORY_ROOT", str(root))
    monkeypatch.setattr(stories, "CHAR_FILE", str(tmp_path / "data" / "character.json"))
    monkeypatch.setattr(stories, "STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setattr(stories, "ROOT", str(tmp_path))
    monkeypatch.setattr(tc, "LOG_DIR", str(tmp_path / "logs"))

    def fake_chain(texts, languages, providers, cfg, log, extras=None, source_lang=None):
        assert source_lang == "zh-CN"
        out = []
        for text, extra in zip(texts, extras):
            if extra["story"] == "vision2":
                raise tc.ConnectionIssue("boom")
            out.append({"source": text, "source_detected_lang": "zh-CN",
                        "target_lang": "zh-CN", "provider": "google",
                        "languages": list(languages), "steps": [], "final": "译" + text})
        return out

    monkeypatch.setattr(stories, "run_chain_many", fake_chain)

    rc = stories.main(_main_args())
    assert rc == 7
    assert (char_dir / "result" / "vision" / "seg_01.json").is_file()
    assert not (char_dir / "result" / "vision2" / "seg_01.json").exists()
    assert list((tmp_path / "logs").glob("failures_*.json"))
    summary = json.loads(next((tmp_path / "logs").glob("run_*.json")).read_text(encoding="utf-8"))
    assert summary["failed"] == 1 and summary["exit_code"] == 7
    assert (tmp_path / "state.json").is_file()  # 有失败时保留进度
