"""services 编排层单测：离线，mock transport / httpx。"""
import json
from pathlib import Path

from story_tr import transport as transport_mod
from story_tr.errors import ConnectionFailed, ExitCode, ModelNotLoaded
from story_tr.services import (
    chain,
    clean,
    doctor,
    folders,
    status,
    translate,
)
from story_tr.services import (
    eval as eval_mod,
)
from story_tr.services import (
    fetch as fetch_mod,
)


# --------------------------------------------------------------------------- #
# 测试替身
# --------------------------------------------------------------------------- #
class FakeTranslator:
    def __init__(self, name="google", result="译文", error=None):
        self.name = name
        self.min_interval = 0.0
        self.result = result
        self.error = error
        self.calls = 0

    def available(self):
        return True, ""

    def translate(self, text, src, tgt):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result


class FakeRwkv:
    def __init__(self, settings, fail=None):
        self.settings = settings
        self.fail = fail

    def translate(self, text, src, tgt):
        if self.fail == "connection":
            raise ConnectionFailed("connection refused")
        if self.fail == "model":
            raise ModelNotLoaded("model not loaded")
        return "这是译文"


class FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class FakeHttpClient:
    def __init__(self, handler):
        self._handler = handler
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        return self._handler(url)

    def close(self):
        pass


# --------------------------------------------------------------------------- #
# 工作区
# --------------------------------------------------------------------------- #
def _setup_workspace(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    story_dir = data / "story" / "character" / "10000021" / "profile" / "story" / "vision"
    story_dir.mkdir(parents=True)
    (story_dir / "zh.md").write_text(
        '---\nid: "10000021"\nstory: vision\nlang: zh\ntitle: "角色详细"\n---\n\n第一段\n\n第二段\n',
        encoding="utf-8",
    )
    (story_dir / "en.md").write_text(
        '---\nid: "10000021"\nstory: vision\nlang: en\n'
        'title: "Character Detail"\n---\n\nFirst paragraph\n\nSecond paragraph\n',
        encoding="utf-8",
    )
    (data / "character.json").write_text(
        json.dumps([{"id": "10000021", "name_en": "Amber", "name_zh": "安柏",
                     "path": "data/story/character/10000021", "stories": ["vision"]}],
                   ensure_ascii=False),
        encoding="utf-8",
    )
    return tmp_path


def _setup_result_files(tmp_path: Path) -> Path:
    _setup_workspace(tmp_path)
    rdir = tmp_path / "data" / "story" / "character" / "10000021" / "result" / "vision"
    rdir.mkdir(parents=True, exist_ok=True)
    (rdir / "seg_01.json").write_text('{"source": "第一段"}', encoding="utf-8")
    (rdir / "full.json").write_text('{"source": "第一段"}', encoding="utf-8")
    return rdir


# --------------------------------------------------------------------------- #
# fetch
# --------------------------------------------------------------------------- #
def _avatar(name, title, detail):
    return {
        "name": name, "rank": 4, "element": "Pyro", "weaponType": "Bow",
        "region": "Mondstadt", "bodyType": "Girl", "specialProp": "prop",
        "icon": "icon.png", "birthday": [8, 10], "release": 1,
        "fetter": {"title": title, "detail": detail, "constellation": "const",
                   "native": "native", "cv": {"en": "va"}},
        "other": {"costume": [{"name": "costume", "description": "costume desc"}],
                  "nameCard": {"name": "card", "description": "card desc"},
                  "specialFood": {"name": "food"}},
    }


def test_fetch_generates_files(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    (data / "character.json").write_text(
        json.dumps([{"id": "10000021", "name_en": "Amber", "name_zh": "安柏",
                     "path": "data/story/character/10000021", "stories": ["vision"]}],
                   ensure_ascii=False),
        encoding="utf-8",
    )

    def handler(url):
        if "/avatarFetter/" in url:
            return FakeResp({"data": {"story": {
                "1": {"title": "Vision", "title2": "",
                      "text": "First\\nSecond", "text2": ""}}}})
        if url.endswith("/en/avatar/10000021"):
            return FakeResp({"data": _avatar("Amber", "title_en", "detail_en")})
        if url.endswith("/chs/avatar/10000021"):
            return FakeResp({"data": _avatar("安柏", "title_zh", "detail_zh")})
        return FakeResp({"data": None}, status=404)

    client = FakeHttpClient(handler)
    monkeypatch.setattr(fetch_mod.httpx, "Client", lambda *a, **k: client)
    rc = fetch_mod.run(data_dir=str(tmp_path), mode="refresh")
    assert rc == ExitCode.OK

    base = tmp_path / "data" / "story" / "character" / "10000021" / "profile"
    assert (data / "character.json").exists()
    assert (base / "meta.json").exists()
    assert (base / "text.json").exists()
    assert (base / "story" / "vision" / "en.md").exists()
    assert (base / "story" / "vision" / "zh.md").exists()


# --------------------------------------------------------------------------- #
# translate
# --------------------------------------------------------------------------- #
def _mock_translator(monkeypatch, fake):
    monkeypatch.setattr(transport_mod, "build_translator", lambda p, s: fake)


def test_translate_writes_result(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    fake = FakeTranslator(name="google", result="译文")
    _mock_translator(monkeypatch, fake)
    rc = translate.run(["10000021"], stories="vision", mode="fixed", chain="asia",
                       no_tts=True, data_dir=str(tmp_path))
    assert rc == ExitCode.OK

    rdir = tmp_path / "data" / "story" / "character" / "10000021" / "result" / "vision"
    data = json.loads((rdir / "seg_01.json").read_text(encoding="utf-8"))
    assert data["source"] == "第一段"
    assert data["mode"] == "fixed"
    assert data["providers"] == ["google"]
    assert data["final"] == "译文"
    assert (rdir / "segments.json").exists()


def test_translate_resume_skips_existing(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    fake = FakeTranslator(name="google", result="译文")
    _mock_translator(monkeypatch, fake)
    args = dict(stories="vision", mode="fixed", chain="asia", no_tts=True,
                data_dir=str(tmp_path))
    assert translate.run(["10000021"], **args) == ExitCode.OK
    first_calls = fake.calls
    assert translate.run(["10000021"], **args) == ExitCode.OK
    assert fake.calls == first_calls  # 已有结果全部跳过


def test_translate_signature_mismatch(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    fake = FakeTranslator(name="google", result="译文")
    _mock_translator(monkeypatch, fake)
    (tmp_path / ".translate_stories_state.json").write_text(
        json.dumps({"signature": {"mode": "fixed", "chain": "europe", "steps": None,
                                  "languages": None, "providers": ["google", "bing", "baidu"]},
                    "completed": [], "started_at": "2026-01-01T00:00:00"},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    rc = translate.run(["10000021"], stories="vision", mode="fixed", chain="asia",
                       no_tts=True, data_dir=str(tmp_path))
    assert rc == ExitCode.SIGNATURE


def test_translate_local_connection_failure(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    fake = FakeTranslator(name="rwkv", error=ConnectionFailed("connection refused"))
    _mock_translator(monkeypatch, fake)
    rc = translate.run(["10000021"], stories="vision", mode="rwkv20",
                       no_tts=True, data_dir=str(tmp_path))
    assert rc == ExitCode.CONNECTION


def test_translate_model_not_loaded(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    fake = FakeTranslator(name="rwkv", error=ModelNotLoaded("model not loaded"))
    _mock_translator(monkeypatch, fake)
    rc = translate.run(["10000021"], stories="vision", mode="rwkv20",
                       no_tts=True, data_dir=str(tmp_path))
    assert rc == ExitCode.PAUSED


def test_translate_rwkv20_reads_english_source(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    fake = FakeTranslator(name="rwkv", result="这是译文")
    _mock_translator(monkeypatch, fake)
    rc = translate.run(["10000021"], stories="vision", mode="rwkv20",
                       no_tts=True, data_dir=str(tmp_path))
    assert rc == ExitCode.OK

    rdir = tmp_path / "data" / "story" / "character" / "10000021" / "result" / "vision"
    data = json.loads((rdir / "seg_01.json").read_text(encoding="utf-8"))
    assert data["source"] == "First paragraph"
    assert data["source_file"].endswith("en.md")
    assert [s["kind"] for s in data["steps"]] == ["rwkv20"]
    assert data["steps"][0]["text"] == "这是译文"


# --------------------------------------------------------------------------- #
# chain
# --------------------------------------------------------------------------- #
def test_chain_writes_out_json(tmp_path, monkeypatch):
    fake = FakeTranslator(name="google", result="译文")
    _mock_translator(monkeypatch, fake)
    rc = chain.run(text="你好世界", mode="fixed", chain="asia", no_tts=True,
                   no_file_log=True, data_dir=str(tmp_path))
    assert rc == ExitCode.OK
    out_files = list((tmp_path / "out").glob("*.json"))
    assert len(out_files) == 1
    data = json.loads(out_files[0].read_text(encoding="utf-8"))
    assert data["final"] == "译文"
    assert data["source_lang"] == "zh-CN"


def test_chain_rwkv20_requires_english(tmp_path):
    rc = chain.run(text="你好世界", mode="rwkv20", no_tts=True,
                   no_file_log=True, data_dir=str(tmp_path))
    assert rc == ExitCode.USAGE


def test_chain_rwkv20_uses_english_text(tmp_path, monkeypatch):
    fake = FakeTranslator(name="rwkv", result="这是译文")
    _mock_translator(monkeypatch, fake)
    rc = chain.run(text="Hello, world", mode="rwkv20", no_tts=True,
                   no_file_log=True, data_dir=str(tmp_path))
    assert rc == ExitCode.OK
    data = json.loads(next((tmp_path / "out").glob("*.json")).read_text(encoding="utf-8"))
    assert data["providers"] == ["rwkv"]
    assert [s["kind"] for s in data["steps"]] == ["rwkv20"]


# --------------------------------------------------------------------------- #
# clean
# --------------------------------------------------------------------------- #
def test_clean_dry_run_keeps_files(tmp_path):
    rdir = _setup_result_files(tmp_path)
    rc = clean.run(["10000021"], dry_run=True, data_dir=str(tmp_path))
    assert rc == ExitCode.OK
    assert (rdir / "seg_01.json").exists()


def test_clean_executes_and_keeps_result_dir(tmp_path):
    rdir = _setup_result_files(tmp_path)
    rc = clean.run(["10000021"], yes=True, data_dir=str(tmp_path))
    assert rc == ExitCode.OK
    result_root = tmp_path / "data" / "story" / "character" / "10000021" / "result"
    assert result_root.exists()
    assert not (rdir / "seg_01.json").exists()


# --------------------------------------------------------------------------- #
# status / doctor / folders
# --------------------------------------------------------------------------- #
def test_status_collect_counts(tmp_path):
    rdir = _setup_result_files(tmp_path)
    (rdir / "seg_01.mp3").write_bytes(b"x")
    (rdir / "segments.json").write_text("{}", encoding="utf-8")
    (rdir / "title.json").write_text("{}", encoding="utf-8")
    info = status.collect(str(tmp_path))
    assert info["characters"] == 1
    assert info["stories"] == 1
    assert info["stories_with_result"] == 1
    assert info["segments"] == 1
    assert info["segment_mp3"] == 1
    assert info["summaries"] == 1
    assert info["titles"] == 1


def test_status_manifest(tmp_path):
    _setup_workspace(tmp_path)
    rc = status.run(manifest=True, data_dir=str(tmp_path))
    assert rc == ExitCode.OK
    assert (tmp_path / "data" / "manifest.json").exists()


def test_doctor_ok(tmp_path):
    _setup_workspace(tmp_path)
    assert doctor.run(data_dir=str(tmp_path)) == ExitCode.OK


def test_folders_rename_idempotent(tmp_path):
    data = tmp_path / "data"
    char_dir = data / "story" / "character" / "10000021"
    story_dir = char_dir / "profile" / "story" / "old_name"
    story_dir.mkdir(parents=True)
    (story_dir / "zh.md").write_text("第一段\n", encoding="utf-8")
    (data / "character.json").write_text(
        json.dumps([{"id": "10000021", "name_en": "A", "name_zh": "甲",
                     "path": "p", "stories": ["new_name"]}], ensure_ascii=False),
        encoding="utf-8",
    )
    assert folders.run(data_dir=str(tmp_path)) == ExitCode.OK
    assert (char_dir / "profile" / "story" / "new_name").exists()
    assert not (char_dir / "profile" / "story" / "old_name").exists()
    assert folders.run(data_dir=str(tmp_path)) == ExitCode.OK


# --------------------------------------------------------------------------- #
# eval
# --------------------------------------------------------------------------- #
def test_eval_writes_report(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    monkeypatch.setattr(eval_mod, "RwkvTranslator", lambda settings: FakeRwkv(settings))
    rc = eval_mod.run(char_ids=["10000021"], limit=2, templates="builtin",
                      data_dir=str(tmp_path))
    assert rc == ExitCode.OK
    logs = tmp_path / "logs"
    assert len(list(logs.glob("rwkv_eval_*.json"))) == 1
    assert len(list(logs.glob("rwkv_eval_*.md"))) == 1
    payload = json.loads(next(logs.glob("rwkv_eval_*.json")).read_text(encoding="utf-8"))
    assert payload["samples"] == 2
    assert payload["results"][0]["metrics"]["ok"] == 2


def test_eval_all_connection_failure(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    monkeypatch.setattr(eval_mod, "RwkvTranslator",
                        lambda settings: FakeRwkv(settings, fail="connection"))
    rc = eval_mod.run(char_ids=["10000021"], limit=2, templates="builtin",
                      data_dir=str(tmp_path))
    assert rc == ExitCode.CONNECTION


def test_eval_all_model_not_loaded(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    monkeypatch.setattr(eval_mod, "RwkvTranslator",
                        lambda settings: FakeRwkv(settings, fail="model"))
    rc = eval_mod.run(char_ids=["10000021"], limit=2, templates="builtin",
                      data_dir=str(tmp_path))
    assert rc == ExitCode.PAUSED
