"""services.doctor：环境检查。"""

from types import SimpleNamespace

from story_tr.errors import ExitCode
from story_tr.services import doctor as doctor_mod
from tests.support import setup_workspace as _setup_workspace


def test_doctor_ok(tmp_path):
    _setup_workspace(tmp_path)
    assert doctor_mod.run(data_dir=str(tmp_path)) == ExitCode.OK


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


def test_doctor_rwkv_config_row(tmp_path):
    settings = SimpleNamespace(
        rwkv_api_url="http://127.0.0.1:8000", rwkv_prompt_template="t", rwkv_model_path="/nope/model.pth"
    )
    status_, name, detail = doctor_mod._rwkv_config_row(settings)
    assert status_ == "WARN"  # 配置了不存在的模型路径

    model = tmp_path / "m.pth"
    model.write_text("x", encoding="utf-8")
    ok, _, _ = doctor_mod._rwkv_config_row(
        SimpleNamespace(rwkv_api_url="u", rwkv_prompt_template="t", rwkv_model_path=str(model))
    )
    assert ok == doctor_mod.OK
    ok, _, _ = doctor_mod._rwkv_config_row(
        SimpleNamespace(rwkv_api_url="u", rwkv_prompt_template="t", rwkv_model_path="")
    )
    assert ok == doctor_mod.OK
    warn, _, _ = doctor_mod._rwkv_config_row(
        SimpleNamespace(rwkv_api_url="u", rwkv_prompt_template="", rwkv_model_path="")
    )
    assert warn == doctor_mod.WARN


def test_doctor_probe_rows(monkeypatch):
    class Boom:
        def __init__(self, settings):
            pass

        def probe(self):
            raise RuntimeError("boom")

    monkeypatch.setattr(doctor_mod, "RwkvTranslator", Boom)
    assert doctor_mod._rwkv_probe_row(SimpleNamespace())[0] == doctor_mod.WARN

    cases = [
        ({"reachable": False, "error": "no"}, doctor_mod.WARN),
        ({"reachable": True, "status": "Loading"}, doctor_mod.WARN),
        ({"reachable": True, "status": "Working", "model": "m"}, doctor_mod.OK),
    ]
    for payload, expected in cases:
        monkeypatch.setattr(
            doctor_mod,
            "RwkvTranslator",
            lambda settings, _payload=payload: SimpleNamespace(probe=lambda: _payload),
        )
        assert doctor_mod._rwkv_probe_row(SimpleNamespace())[0] == expected


def test_doctor_collect_env_state_proxy_probe(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    (tmp_path / ".env").write_text("GOOGLE_API_KEY=k\n", encoding="utf-8")
    (tmp_path / doctor_mod.STATE_FILENAME).write_text('{"completed": ["a"]}', encoding="utf-8")
    settings = SimpleNamespace(
        google_api_key="k",
        bing_api_key="",
        baidu_appid="",
        baidu_appkey="",
        proxy="http://p",
        http_proxy="",
        rwkv_prompt_template="t",
        rwkv_model_path="",
        rwkv_api_url="u",
    )
    monkeypatch.setattr(doctor_mod, "load_settings", lambda: settings)

    class Fake:
        def __init__(self, settings):
            pass

        def probe(self):
            return {"reachable": True, "status": "Working", "model": "m"}

    monkeypatch.setattr(doctor_mod, "RwkvTranslator", Fake)
    rows = doctor_mod.collect(str(tmp_path), probe=True)
    details = {name: detail for _, name, detail in rows}
    assert "GOOGLE_API_KEY" in details[".env"]
    assert "1 条完成" in details["批量状态"]
    assert details["代理"] == "http://p"
    assert any(status == doctor_mod.OK and name == "本地 RWKV 服务" for status, name, _ in rows)
    assert doctor_mod.render([]) == ""


def test_doctor_run_success(tmp_path, monkeypatch):
    _setup_workspace(tmp_path)
    monkeypatch.setattr(
        doctor_mod,
        "load_settings",
        lambda: SimpleNamespace(
            google_api_key="",
            bing_api_key="",
            baidu_appid="",
            baidu_appkey="",
            proxy="",
            http_proxy="",
            rwkv_prompt_template="t",
            rwkv_model_path="",
            rwkv_api_url="u",
        ),
    )
    assert doctor_mod.run(data_dir=str(tmp_path)) == ExitCode.OK
