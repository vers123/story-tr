# -*- coding: utf-8 -*-
"""`story_tr.doctor`：环境检查不因可选依赖缺失而 FAIL，但缺数据要 FAIL。"""
import json
import types

from story_tr import doctor, stories


def _setup(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    (data / "character.json").write_text(json.dumps([]), encoding="utf-8")
    monkeypatch.setenv("STORY_TR_HOME", str(tmp_path))
    monkeypatch.setattr(stories, "CHAR_FILE", str(data / "character.json"))
    monkeypatch.setattr(stories, "STORY_ROOT", str(data / "story" / "character"))
    monkeypatch.setattr(stories, "STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    monkeypatch.delenv("RWKV_API_URL", raising=False)
    monkeypatch.delenv("RWKV_MODEL_PATH", raising=False)


def _rows(rows, keyword):
    return [row for row in rows if keyword in row[1]]


def test_collect_has_no_fail_when_data_exists(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    rows = doctor.collect()
    assert not [r for r in rows if r[0] == doctor.FAIL]
    assert any("ffmpeg" in name for _, name, _ in rows)


def test_main_returns_0_with_data(tmp_path, monkeypatch, capsys):
    _setup(tmp_path, monkeypatch)
    assert doctor.main(types.SimpleNamespace()) == 0
    out = capsys.readouterr().out
    assert "[ OK ]" in out and "检查通过" in out


def test_main_returns_1_without_character_index(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("STORY_TR_HOME", str(tmp_path))
    monkeypatch.setattr(stories, "CHAR_FILE", str(tmp_path / "missing.json"))
    assert doctor.main(types.SimpleNamespace()) == 1
    assert "检查未通过" in capsys.readouterr().out


def test_collect_loads_dotenv(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(doctor.rwkv, "load_env", lambda home=None: calls.append(home))
    doctor.collect()
    assert calls == [str(tmp_path)]


def test_local_rwkv_offline_no_network(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)

    def _no_probe(*args, **kwargs):
        raise AssertionError("should not probe")

    monkeypatch.setattr(doctor.rwkv, "probe", _no_probe)
    rows = doctor.collect()
    assert [r for r in rows if r[1] == "本地 RWKV"]
    assert not _rows(rows, "本地 RWKV 服务")


def test_local_rwkv_probe_working(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(doctor.rwkv, "probe", lambda cfg=None, **kw: {
        "reachable": True, "status": "Working",
        "model": "translate-20times-3B", "error": None})
    rows = doctor.collect(probe=True)
    service = _rows(rows, "本地 RWKV 服务")
    assert len(service) == 1
    assert service[0][0] == doctor.OK
    assert "Working" in service[0][2] and "translate-20times-3B" in service[0][2]


def test_local_rwkv_probe_unreachable_is_warn(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(doctor.rwkv, "probe", lambda cfg=None, **kw: {
        "reachable": False, "status": None, "model": None,
        "error": "Connection refused"})
    rows = doctor.collect(probe=True)
    service = _rows(rows, "本地 RWKV 服务")
    assert len(service) == 1
    assert service[0][0] == doctor.WARN
    assert "不可达" in service[0][2]
    assert not [r for r in rows if r[0] == doctor.FAIL]
