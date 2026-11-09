"""services.eval：本地评测与模板排名。"""

import json

from story_tr.errors import BadOutput, ExitCode
from story_tr.services import eval as eval_mod
from tests.support import FakeRwkv
from tests.support import setup_workspace as _setup_workspace


def test_eval_writes_report(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    monkeypatch.setattr(eval_mod, "RwkvTranslator", lambda settings: FakeRwkv(settings))
    rc = eval_mod.run(char_ids=["10000021"], limit=2, templates="builtin", data_dir=str(tmp_path))
    assert rc == ExitCode.OK
    logs = tmp_path / "logs"
    assert len(list(logs.glob("rwkv_eval_*.json"))) == 1
    assert len(list(logs.glob("rwkv_eval_*.md"))) == 1
    payload = json.loads(next(logs.glob("rwkv_eval_*.json")).read_text(encoding="utf-8"))
    assert payload["samples"] == 2
    assert payload["results"][0]["metrics"]["ok"] == 2


def test_eval_all_connection_failure(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    monkeypatch.setattr(eval_mod, "RwkvTranslator", lambda settings: FakeRwkv(settings, fail="connection"))
    rc = eval_mod.run(char_ids=["10000021"], limit=2, templates="builtin", data_dir=str(tmp_path))
    assert rc == ExitCode.CONNECTION


def test_eval_all_model_not_loaded(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    monkeypatch.setattr(eval_mod, "RwkvTranslator", lambda settings: FakeRwkv(settings, fail="model"))
    rc = eval_mod.run(char_ids=["10000021"], limit=2, templates="builtin", data_dir=str(tmp_path))
    assert rc == ExitCode.PAUSED


def test_eval_parse_templates():
    assert eval_mod._parse_templates("") == []
    assert len(eval_mod._parse_templates("builtin")) == len(eval_mod.BUILTIN_TEMPLATES)
    assert eval_mod._parse_templates("a||b||") == ["a", "b"]


def test_eval_usage_errors(tmp_path):
    assert eval_mod.run(limit=0, data_dir=str(tmp_path)) == ExitCode.USAGE
    assert eval_mod.run(limit=2, data_dir=str(tmp_path)) == ExitCode.USAGE


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
    rc = eval_mod.run(char_ids=["10000021"], limit=2, templates="builtin", data_dir=str(tmp_path))
    assert rc == ExitCode.OK


def test_eval_url_override(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)

    class OkRwkv:
        def __init__(self, settings):
            self.settings = settings

        def translate(self, text, src, tgt):
            return "这是译文"

    monkeypatch.setattr(eval_mod, "RwkvTranslator", OkRwkv)
    rc = eval_mod.run(
        char_ids=["10000021"],
        limit=1,
        templates="{text}",
        rwkv_url="http://localhost:9999",
        data_dir=str(tmp_path),
    )
    assert rc == ExitCode.OK
