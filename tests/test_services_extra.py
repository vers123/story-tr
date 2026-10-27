"""services / config / _io 补充单测：错误分支与编排细节（离线，mock transport/httpx）。"""
import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from story_tr import config as config_mod
from story_tr import models
from story_tr import transport as transport_mod
from story_tr.config import Settings
from story_tr.errors import (
    BadOutput,
    ConnectionFailed,
    ExitCode,
    ModelNotLoaded,
    QuotaExceeded,
    RateLimited,
    UnsupportedLanguage,
)
from story_tr.services import _io
from story_tr.services import chain as chain_mod
from story_tr.services import clean as clean_mod
from story_tr.services import doctor as doctor_mod
from story_tr.services import eval as eval_mod
from story_tr.services import folders as folders_mod
from story_tr.services import status as status_mod
from story_tr.services import translate as translate_mod


# --------------------------------------------------------------------------- #
# 测试替身
# --------------------------------------------------------------------------- #
class FakeTranslator:
    def __init__(self, name="google", result="译文", error=None, available=(True, "")):
        self.name = name
        self.min_interval = 0.0
        self.result = result
        self.error = error  # 异常实例；不为 None 时 translate 抛它
        self._available = available
        self.calls = 0
        self.last_chunks = 0
        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0

    def available(self):
        return self._available

    def translate(self, text, src, tgt):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result


def _mock_translator(monkeypatch, fake):
    monkeypatch.setattr(transport_mod, "build_translator", lambda p, s: fake)


def _setup_workspace(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    story_dir = data / "story" / "character" / "10000021" / "profile" / "story" / "vision"
    story_dir.mkdir(parents=True)
    (story_dir / "zh.md").write_text(
        '---\nid: "10000021"\nstory: vision\nlang: zh\ntitle: "角色详细"\n---\n\n第一段\n\n第二段\n',
        encoding="utf-8",
    )
    (data / "character.json").write_text(
        json.dumps([{"id": "10000021", "name_en": "Amber", "name_zh": "安柏",
                     "path": "data/story/character/10000021", "stories": ["vision"]}],
                   ensure_ascii=False),
        encoding="utf-8",
    )
    return tmp_path


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #
def test_unescape_controls():
    assert config_mod._unescape("") == ""
    assert config_mod._unescape(r"a\nb\tc") == "a\nb\tc"


def test_resolve_data_dir_env(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "load_settings", lambda: SimpleNamespace(data_dir=str(tmp_path)))
    assert config_mod.resolve_data_dir(None) == os.path.abspath(str(tmp_path))


def test_resolve_data_dir_walk_finds_marker(tmp_path, monkeypatch):
    marker_dir = tmp_path / "marker"
    (marker_dir / "data").mkdir(parents=True)
    (marker_dir / "data" / "character.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(config_mod, "load_settings", lambda: SimpleNamespace(data_dir=""))
    monkeypatch.setattr(os, "getcwd", lambda: str(marker_dir))
    assert config_mod.resolve_data_dir(None) == str(marker_dir)


def test_resolve_data_dir_walk_to_cwd(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "load_settings", lambda: SimpleNamespace(data_dir=""))
    monkeypatch.setattr(config_mod, "_MARKER", "data/_nonexistent_marker_xyz.json")
    monkeypatch.setattr(os, "getcwd", lambda: str(tmp_path))
    assert config_mod.resolve_data_dir(None) == str(tmp_path)


# --------------------------------------------------------------------------- #
# _io
# --------------------------------------------------------------------------- #
def test_atomic_write_json_with_backup(tmp_path):
    p = str(tmp_path / "x.json")
    _io.atomic_write_json(p, {"a": 1})
    _io.atomic_write_json(p, {"a": 2})
    assert json.loads(Path(p).read_text(encoding="utf-8")) == {"a": 2}
    assert json.loads(Path(p + ".bak").read_text(encoding="utf-8")) == {"a": 1}


def test_atomic_write_json_pydantic(tmp_path):
    p = str(tmp_path / "m.json")
    _io.atomic_write_json(p, models.BatchState(signature={}, started_at="2026-01-01T00:00:00"))
    data = json.loads(Path(p).read_text(encoding="utf-8"))
    assert data["started_at"] == "2026-01-01T00:00:00"


def test_load_json_with_backup_restores(tmp_path):
    p = str(tmp_path / "s.json")
    Path(p).write_text("{bad json", encoding="utf-8")
    Path(p + ".bak").write_text('{"ok": true}', encoding="utf-8")
    assert _io.load_json_with_backup(p) == {"ok": True}


def test_load_json_with_backup_corrupt_no_backup(tmp_path):
    p = str(tmp_path / "s.json")
    Path(p).write_text("{bad json", encoding="utf-8")
    assert _io.load_json_with_backup(p) is None
    assert list(Path(tmp_path).glob("s.json.corrupt-*"))


def test_load_json_with_backup_both_corrupt(tmp_path):
    p = str(tmp_path / "s.json")
    Path(p).write_text("{bad", encoding="utf-8")
    Path(p + ".bak").write_text("{also bad", encoding="utf-8")
    assert _io.load_json_with_backup(p) is None


def test_remove_with_backup(tmp_path):
    p = tmp_path / "s.json"
    p.write_text("x", encoding="utf-8")
    _io.remove_with_backup(str(p))
    assert not p.exists()
    assert Path(str(p) + ".bak").exists()


def test_resolve_providers():
    assert _io.resolve_providers("auto", "fixed") == (["google", "bing", "baidu"], None)
    assert _io.resolve_providers(None, "fixed") == (["google", "bing", "baidu"], None)
    assert _io.resolve_providers("google", "fixed") == (["google"], None)
    assert _io.resolve_providers("rwkv", "rwkv20") == (["rwkv"], None)
    assert _io.resolve_providers("google", "rwkv20")[1] is not None
    assert _io.resolve_providers("rwkv", "fixed")[1] is not None
    assert _io.resolve_providers("yandex", "fixed")[1] is not None


# --------------------------------------------------------------------------- #
# chain / translate 共有的 _translate_step 分支
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("mod", [chain_mod, translate_mod])
class TestTranslateStep:
    def _step(self, mod, translators, providers, balance=False):
        return mod._translate_step("你好", "zh-CN", "en", translators, providers, balance, Settings())

    def test_unavailable(self, mod):
        t = FakeTranslator(available=(False, "nokey"))
        with pytest.raises(ConnectionFailed):
            self._step(mod, [t], ["google"])

    def test_unsupported_language(self, mod):
        t = FakeTranslator(error=UnsupportedLanguage("x"))
        with pytest.raises(UnsupportedLanguage):
            self._step(mod, [t], ["google"])

    def test_quota(self, mod):
        t = FakeTranslator(error=QuotaExceeded("quota"))
        with pytest.raises(QuotaExceeded):
            self._step(mod, [t], ["google"])

    def test_model_not_loaded(self, mod):
        t = FakeTranslator(error=ModelNotLoaded("m"))
        with pytest.raises(ModelNotLoaded):
            self._step(mod, [t], ["google"])

    def test_rate_limited_single(self, mod):
        t = FakeTranslator(error=RateLimited("limited"))
        with pytest.raises(ConnectionFailed):
            self._step(mod, [t], ["google"])

    def test_bad_output_single(self, mod):
        t = FakeTranslator(error=BadOutput("bad"))
        with pytest.raises(BadOutput):
            self._step(mod, [t], ["google"])

    def test_empty_result_single(self, mod):
        t = FakeTranslator(result="")
        with pytest.raises(BadOutput):
            self._step(mod, [t], ["google"])

    def test_fallback_after_connection(self, mod):
        t1 = FakeTranslator(error=ConnectionFailed("c"))
        t2 = FakeTranslator(result="译文")
        name, out, meta = self._step(mod, [t1, t2], ["google", "bing"])
        assert name == "bing"
        assert out == "译文"
        assert meta is None

    def test_fallback_after_bad_output(self, mod):
        t1 = FakeTranslator(error=BadOutput("bad"))
        t2 = FakeTranslator(result="译文")
        name, out, _ = self._step(mod, [t1, t2], ["google", "bing"])
        assert name == "bing"
        assert out == "译文"

    def test_order_balance(self, mod):
        t1 = FakeTranslator(name="google")
        t2 = FakeTranslator(name="bing")
        pairs = mod._order([t1, t2], ["google", "bing"], balance=True)
        assert len(pairs) == 2

    def test_rwkv_meta(self, mod):
        t = FakeTranslator(name="rwkv")
        t.last_chunks = 2
        meta = mod._rwkv_meta(t, Settings())
        assert meta is not None and meta.chunks == 2
        assert mod._rwkv_meta(FakeTranslator(name="google"), Settings()) is None

    def test_merge_rwkv(self, mod):
        meta = models.RwkvMeta(url="u", model="m", template_sha1="s", chunks=1)
        step = models.TranslationStep(index=0, kind="rwkv20", lang="zh-CN",
                                      provider="rwkv", text="x", rwkv=meta)
        merged = mod._merge_rwkv([step])
        assert merged is not None and merged.chunks == 1
        assert mod._merge_rwkv([]) is None


# --------------------------------------------------------------------------- #
# chain.run 分支
# --------------------------------------------------------------------------- #
def test_chain_dry_run(tmp_path):
    assert chain_mod.run(text="Hello", mode="rwkv20", dry_run=True,
                         data_dir=str(tmp_path)) == ExitCode.OK


def test_chain_signature_mismatch(tmp_path):
    state = models.ChainState(
        signature={"mode": "random"}, source="你好", detected="zh-CN",
        languages=["ja"], text="你好", next_index=0, steps=[], started_at="2026-01-01T00:00:00")
    _io.atomic_write_json(os.path.join(str(tmp_path), chain_mod.STATE_FILENAME), state)
    assert chain_mod.run(text="你好", mode="fixed", chain="asia", no_tts=True,
                         data_dir=str(tmp_path)) == ExitCode.SIGNATURE


@pytest.mark.parametrize("error,code", [
    (QuotaExceeded("q"), ExitCode.QUOTA),
    (UnsupportedLanguage("u"), ExitCode.UNSUPPORTED_LANGUAGE),
    (ModelNotLoaded("m"), ExitCode.PAUSED),
    (ConnectionFailed("c"), ExitCode.CONNECTION),
    (BadOutput("b"), ExitCode.PAUSED),
])
def test_chain_run_error_branches(tmp_path, monkeypatch, error, code):
    _mock_translator(monkeypatch, FakeTranslator(name="rwkv", error=error))
    assert chain_mod.run(text="Hello", mode="rwkv20", no_tts=True,
                         data_dir=str(tmp_path)) == code


def test_chain_tts_success(tmp_path, monkeypatch):
    _mock_translator(monkeypatch, FakeTranslator(name="google", result="译文"))
    monkeypatch.setattr(chain_mod, "tts_bytes", lambda *a, **k: b"mp3")
    assert chain_mod.run(text="你好", mode="custom", languages="ja", no_file_log=True,
                         data_dir=str(tmp_path)) == ExitCode.OK
    assert list((tmp_path / "out").glob("*.mp3"))


def test_chain_tts_failure_still_ok(tmp_path, monkeypatch):
    _mock_translator(monkeypatch, FakeTranslator(name="google", result="译文"))

    def boom(*a, **k):
        raise ConnectionFailed("tts down")

    monkeypatch.setattr(chain_mod, "tts_bytes", boom)
    assert chain_mod.run(text="你好", mode="custom", languages="ja", no_file_log=True,
                         data_dir=str(tmp_path)) == ExitCode.OK


def test_chain_file_log(tmp_path):
    handler = chain_mod._setup_file_log(str(tmp_path), no_file_log=False)
    assert handler is not None
    handler.close()
    assert chain_mod._setup_file_log(str(tmp_path), no_file_log=True) is None


# --------------------------------------------------------------------------- #
# translate 纯函数与 run 分支
# --------------------------------------------------------------------------- #
def test_translate_parse_char_ids_range_and_missing():
    assert translate_mod._parse_char_ids(["1-3"], ["1", "2", "3"]) == ["1", "2", "3"]
    assert translate_mod._parse_char_ids(["3-1"], ["1", "2", "3"]) == ["1", "2", "3"]
    with pytest.raises(ValueError):
        translate_mod._parse_char_ids(["a-b"], ["1"])
    assert translate_mod._parse_char_ids(["999"], ["1"]) == []


def test_translate_resolve_stories(tmp_path):
    _setup_workspace(tmp_path)
    assert translate_mod._resolve_stories(str(tmp_path), "10000021", ["vision"]) == ["vision"]
    assert translate_mod._resolve_stories(str(tmp_path), "10000021", []) == ["vision"]
    assert translate_mod._resolve_stories(str(tmp_path), "404", []) == []


def test_translate_build_tasks(tmp_path):
    _setup_workspace(tmp_path)
    tasks = translate_mod._build_tasks(str(tmp_path), "10000021", ["vision"], "both")
    keys = [t["key"] for t in tasks]
    assert "title" in keys and "full" in keys and "seg_01" in keys and "seg_02" in keys
    seg_only = translate_mod._build_tasks(str(tmp_path), "10000021", ["vision"], "segments")
    assert [t["key"] for t in seg_only] == ["title", "seg_01", "seg_02"]
    full_only = translate_mod._build_tasks(str(tmp_path), "10000021", ["vision"], "full")
    assert [t["key"] for t in full_only] == ["title", "full"]


def test_translate_result_is_stale(tmp_path):
    p = tmp_path / "r.json"
    p.write_text('{"source": "原文"}', encoding="utf-8")
    assert translate_mod._result_is_stale(str(p), "原文") is False
    assert translate_mod._result_is_stale(str(p), "变化") is True
    assert translate_mod._result_is_stale(str(tmp_path / "none.json"), "原文") is True
    p.write_text("{bad", encoding="utf-8")
    assert translate_mod._result_is_stale(str(p), "原文") is True


def test_translate_run_chain_passthrough():
    out = translate_mod._run_chain("……", ["ja"], [], ["google"], False, Settings())
    assert out["passthrough"] is True
    assert out["final"] == "……"


def test_translate_run_chain_detect_english():
    fake = FakeTranslator(name="google", result="你好")
    out = translate_mod._run_chain("hello world", ["ja"], [fake], ["google"], False, Settings())
    assert out["source_lang"] == "en"
    assert out["final"] == "你好"


def test_translate_usage_both_and_neither(tmp_path):
    assert translate_mod.run([], data_dir=str(tmp_path)) == ExitCode.USAGE
    assert translate_mod.run(["1"], all_characters=True, data_dir=str(tmp_path)) == ExitCode.USAGE


def test_translate_bad_provider_and_mode(tmp_path):
    _setup_workspace(tmp_path)
    assert translate_mod.run(["10000021"], mode="rwkv20", provider="google",
                             data_dir=str(tmp_path)) == ExitCode.USAGE
    assert translate_mod.run(["10000021"], mode="nope",
                             data_dir=str(tmp_path)) == ExitCode.USAGE


def test_translate_missing_char_file(tmp_path):
    assert translate_mod.run(["1"], data_dir=str(tmp_path)) == ExitCode.USAGE


def test_translate_no_match(tmp_path):
    _setup_workspace(tmp_path)
    assert translate_mod.run(["999"], data_dir=str(tmp_path)) == ExitCode.USAGE


def test_translate_partial_failures(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    _mock_translator(monkeypatch, FakeTranslator(name="google", error=BadOutput("bad")))
    rc = translate_mod.run(["10000021"], stories="vision", mode="fixed", chain="asia",
                           no_tts=True, data_dir=str(tmp_path))
    assert rc == ExitCode.PARTIAL
    assert list((tmp_path / "logs").glob("failures_*.json"))


def test_translate_fail_fast(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    _mock_translator(monkeypatch, FakeTranslator(name="google", error=BadOutput("bad")))
    rc = translate_mod.run(["10000021"], stories="vision", mode="fixed", chain="asia",
                           no_tts=True, fail_fast=True, data_dir=str(tmp_path))
    assert rc == ExitCode.PARTIAL


def test_translate_max_failures(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    _mock_translator(monkeypatch, FakeTranslator(name="google", error=BadOutput("bad")))
    rc = translate_mod.run(["10000021"], stories="vision", mode="fixed", chain="asia",
                           no_tts=True, max_failures=1, data_dir=str(tmp_path))
    assert rc == ExitCode.PARTIAL


def test_translate_workers_concurrent(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    _mock_translator(monkeypatch, FakeTranslator(name="google", result="译文"))
    rc = translate_mod.run(["10000021"], stories="vision", mode="fixed", chain="asia",
                           workers=2, no_tts=True, data_dir=str(tmp_path))
    assert rc == ExitCode.OK


def test_translate_cancel(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    _mock_translator(monkeypatch, FakeTranslator(name="google", result="译文"))
    cancel = threading.Event()
    cancel.set()
    rc = translate_mod.run(["10000021"], stories="vision", mode="fixed", chain="asia",
                           no_tts=True, data_dir=str(tmp_path), cancel=cancel)
    assert rc == ExitCode.CANCELLED


def test_translate_tts_success_and_failure(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    _mock_translator(monkeypatch, FakeTranslator(name="google", result="译文"))
    monkeypatch.setattr(translate_mod, "tts_bytes", lambda *a, **k: b"mp3")
    rc = translate_mod.run(["10000021"], stories="vision", mode="fixed", chain="asia",
                           data_dir=str(tmp_path))
    assert rc == ExitCode.OK
    rdir = tmp_path / "data" / "story" / "character" / "10000021" / "result" / "vision"
    assert (rdir / "seg_01.mp3").exists()


def test_translate_overwrite_and_revalidate(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    fake = FakeTranslator(name="google", result="译文")
    _mock_translator(monkeypatch, fake)
    args = dict(stories="vision", mode="fixed", chain="asia", no_tts=True,
                data_dir=str(tmp_path))
    assert translate_mod.run(["10000021"], **args) == ExitCode.OK
    first = fake.calls
    # 默认跳过已有结果
    assert translate_mod.run(["10000021"], **args) == ExitCode.OK
    assert fake.calls == first
    # overwrite 强制重跑
    assert translate_mod.run(["10000021"], overwrite=True, **args) == ExitCode.OK
    assert fake.calls > first


# --------------------------------------------------------------------------- #
# clean
# --------------------------------------------------------------------------- #
def test_clean_parse_char_ids():
    assert clean_mod._parse_char_ids(["1-3"], ["1", "2", "3"]) == ["1", "2", "3"]
    with pytest.raises(ValueError):
        clean_mod._parse_char_ids(["a-b"], ["1"])


def test_clean_state_key_match():
    assert clean_mod._state_key_match("1|vision|seg_01", {"1"}, None) is True
    assert clean_mod._state_key_match("1|vision|seg_01", {"1"}, ["vision"]) is True
    assert clean_mod._state_key_match("1|vision|seg_01", {"1"}, ["other"]) is False
    assert clean_mod._state_key_match("1|vision|seg_01", {"2"}, None) is False
    assert clean_mod._state_key_match("bad", {"1"}, None) is False


def test_clean_state_removes_hits(tmp_path):
    home = str(tmp_path)
    state = models.BatchState(signature={"mode": "fixed"},
                              completed=["10000021|vision|seg_01"], started_at="2026-01-01T00:00:00")
    _io.atomic_write_json(os.path.join(home, clean_mod.STATE_FILENAME), state)
    hits = clean_mod._clean_state(home, ["10000021"], None, dry_run=False)
    assert hits == 1
    raw = _io.load_json_with_backup(os.path.join(home, clean_mod.STATE_FILENAME))
    assert raw["completed"] == []


def test_clean_usage_errors(tmp_path):
    assert clean_mod.run([], data_dir=str(tmp_path)) == ExitCode.USAGE
    assert clean_mod.run(["1"], all_characters=True, data_dir=str(tmp_path)) == ExitCode.USAGE


def test_clean_no_match(tmp_path):
    _setup_workspace(tmp_path)
    assert clean_mod.run(["999"], data_dir=str(tmp_path)) == ExitCode.USAGE


def test_clean_confirm_cancel(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    rdir = tmp_path / "data" / "story" / "character" / "10000021" / "result" / "vision"
    rdir.mkdir(parents=True)
    (rdir / "seg_01.json").write_text('{"source": "第一段"}', encoding="utf-8")
    monkeypatch.setattr("builtins.input", lambda *a: "n")
    assert clean_mod.run(["10000021"], data_dir=str(tmp_path)) == ExitCode.OK
    assert (rdir / "seg_01.json").exists()


def test_clean_targets_protect_video(tmp_path):
    _setup_workspace(tmp_path)
    rdir = tmp_path / "data" / "story" / "character" / "10000021" / "result"
    (rdir / "video").mkdir(parents=True)
    (rdir / "vision").mkdir()
    targets = clean_mod._targets(str(tmp_path), "10000021", None)
    assert [os.path.basename(t) for t in targets] == ["vision"]


# --------------------------------------------------------------------------- #
# status
# --------------------------------------------------------------------------- #
def test_status_read_state_missing(tmp_path):
    assert status_mod._read_state(str(tmp_path / "none.json")) == (None, None)


def test_status_collect_deep_and_state_error(tmp_path):
    _setup_workspace(tmp_path)
    rdir = tmp_path / "data" / "story" / "character" / "10000021" / "result" / "vision"
    rdir.mkdir(parents=True)
    (rdir / "seg_01.json").write_text(
        json.dumps({"source": "x", "source_lang": "ja"}), encoding="utf-8")
    info = status_mod.collect(str(tmp_path), deep=True)
    assert info["languages"] == {"ja": 1}
    # 损坏状态文件 → state_error
    (tmp_path / status_mod.STATE_FILENAME).write_text("{bad", encoding="utf-8")
    info = status_mod.collect(str(tmp_path))
    assert info["state_error"] is not None


def test_status_render_rwkv_and_state(tmp_path):
    _setup_workspace(tmp_path)
    info = {
        "workdir": str(tmp_path), "characters": 1,
        "characters_with_result": 0, "characters_without_result": 1,
        "stories": 1, "stories_with_result": 0, "stories_without_result": 1,
        "summaries": 0, "titles": 0, "fulls": 0, "segments": 0, "segment_mp3": 0,
        "data_files": 0, "data_bytes": 0, "log_files": 0, "log_bytes": 0,
        "state": {"completed": 1, "updated_at": "2026-01-01T00:00:00",
                  "signature": {"mode": "fixed"}},
        "state_error": None, "languages": None,
        "rwkv": {"url": "http://127.0.0.1:8000", "template_present": True,
                 "model_path": "/nope/model.pth", "model_path_exists": False,
                 "probe": None},
    }
    text = status_mod.render(info)
    assert "模型文件不存在" in text
    assert "批量状态" in text


def test_status_run_json(tmp_path):
    _setup_workspace(tmp_path)
    assert status_mod.run(json_out=True, data_dir=str(tmp_path)) == ExitCode.OK


def test_status_write_manifest_with_cache(tmp_path):
    _setup_workspace(tmp_path)
    cache = tmp_path / "data" / "_fetch_all.json"
    cache.write_text(json.dumps([{"id": "10000021", "hash": "abc"}]), encoding="utf-8")
    path = status_mod.write_manifest(str(tmp_path), status_mod.collect(str(tmp_path)))
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    assert data["hashes"]["10000021"] == "abc"


# --------------------------------------------------------------------------- #
# doctor
# --------------------------------------------------------------------------- #
def test_doctor_collect_with_env_and_corrupt_state(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    (tmp_path / ".env").write_text("GOOGLE_API_KEY=k\n", encoding="utf-8")
    (tmp_path / doctor_mod.STATE_FILENAME).write_text("{bad", encoding="utf-8")
    rows = doctor_mod.collect(str(tmp_path))
    names = [name for _, name, _ in rows]
    assert "批量状态" in names
    assert any(status == "FAIL" for status, _, _ in rows)


def test_doctor_fail_returns_usage(tmp_path):
    assert doctor_mod.run(data_dir=str(tmp_path)) == ExitCode.USAGE


def test_doctor_rwkv_config_row():
    settings = SimpleNamespace(
        rwkv_api_url="http://127.0.0.1:8000", rwkv_prompt_template="t",
        rwkv_model_path="/nope/model.pth")
    status_, name, detail = doctor_mod._rwkv_config_row(settings)
    assert status_ == "WARN"  # 配置了不存在的模型路径


# --------------------------------------------------------------------------- #
# folders
# --------------------------------------------------------------------------- #
def test_folders_missing_char_file(tmp_path):
    assert folders_mod.run(data_dir=str(tmp_path)) == ExitCode.USAGE


# --------------------------------------------------------------------------- #
# eval
# --------------------------------------------------------------------------- #
def test_eval_parse_templates():
    assert eval_mod._parse_templates("") == []
    assert len(eval_mod._parse_templates("builtin")) == len(eval_mod.BUILTIN_TEMPLATES)
    assert eval_mod._parse_templates("a||b||") == ["a", "b"]


def test_eval_usage_errors(tmp_path):
    assert eval_mod.run(limit=0, data_dir=str(tmp_path)) == ExitCode.USAGE
    assert eval_mod.run(limit=2, data_dir=str(tmp_path)) == ExitCode.USAGE  # 无角色索引


def test_eval_no_samples(tmp_path):
    _setup_workspace(tmp_path)
    assert eval_mod.run(char_ids=["404"], limit=2, data_dir=str(tmp_path)) == ExitCode.USAGE


def test_eval_bad_output_fallback(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)

    class BadRwkv:
        def __init__(self, settings):
            self.settings = settings

        def translate(self, text, src, tgt):
            raise BadOutput("bad")

    monkeypatch.setattr(eval_mod, "RwkvTranslator", BadRwkv)
    rc = eval_mod.run(char_ids=["10000021"], limit=2, templates="builtin",
                      data_dir=str(tmp_path))
    assert rc == ExitCode.OK


def test_eval_url_override(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)

    class OkRwkv:
        def __init__(self, settings):
            self.settings = settings

        def translate(self, text, src, tgt):
            return "这是译文"

    monkeypatch.setattr(eval_mod, "RwkvTranslator", OkRwkv)
    rc = eval_mod.run(char_ids=["10000021"], limit=1, templates="{text}",
                      rwkv_url="http://localhost:9999", data_dir=str(tmp_path))
    assert rc == ExitCode.OK
