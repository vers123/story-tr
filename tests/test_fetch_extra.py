"""`story_tr.services.fetch` 补充单测：纯函数、_build 与 _fetch_all 的离线路径。"""
import json

from story_tr.services import fetch as fetch_mod


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


def _story_rec(name_en="Amber", name_zh="安柏", story_count=1):
    return {
        "id": "10000021",
        "en": {
            "avatar": _avatar(name_en, "title_en", "detail_en"),
            "story": [{"title": "Vision", "title2": "", "text": "First\\nSecond", "text2": ""}
                      for _ in range(story_count)],
        },
        "chs": {
            "avatar": _avatar(name_zh, "title_zh", "detail_zh"),
            "story": [{"title": "愿景", "title2": "", "text": "第一\\n第二", "text2": ""}
                      for _ in range(story_count)],
        },
    }


# --------------------------------------------------------------------------- #
# 纯函数
# --------------------------------------------------------------------------- #
def test_slug():
    assert fetch_mod.slug("Amber's Story") == "amber_story"
    assert fetch_mod.slug("Hello, World!") == "hello_world"
    assert fetch_mod.slug("") == ""
    assert fetch_mod.slug(None) == ""


def test_pick_title():
    assert fetch_mod.pick_title("Vision", "") == "Vision"
    assert fetch_mod.pick_title("???", "Backup") == "Backup"
    assert fetch_mod.pick_title("？？？", "备胎") == "备胎"
    assert fetch_mod.pick_title("", "") == ""


def test_paragraphs():
    assert fetch_mod.paragraphs("a\\nb\\nc") == ["a", "b", "c"]
    assert fetch_mod.paragraphs("") == []
    assert fetch_mod.paragraphs(None) == []


def test_fingerprint_stable():
    rec = _story_rec()
    assert fetch_mod.fingerprint(rec) == fetch_mod.fingerprint(json.loads(json.dumps(rec)))


def test_story_diff():
    old = {"en": {"avatar": {"name": "A"}, "story": [{"i": 1, "text": "a", "text2": ""}]},
           "chs": {"avatar": {"name": "甲"}, "story": [{"i": 1, "text": "一", "text2": ""}]}}
    new = {"en": {"avatar": {"name": "B"}, "story": [{"i": 1, "text": "a2", "text2": ""}]},
           "chs": {"avatar": {"name": "甲"}, "story": [{"i": 1, "text": "一", "text2": ""},
                                                       {"i": 2, "text": "二", "text2": ""}]}}
    diffs = fetch_mod.story_diff(old, new)
    assert any("故事数" in d for d in diffs)
    assert any("文本变化" in d for d in diffs)
    assert any("资料变化" in d for d in diffs)


def test_make_char_entry():
    rec = _story_rec()
    entry = fetch_mod.make_char_entry("10000021", rec)
    assert entry["id"] == "10000021"
    assert entry["name_en"] == "Amber"
    assert entry["stories"] == ["vision"]


def test_scalar_helpers():
    assert fetch_mod._s(None) == ""
    assert fetch_mod._s(1) == "1"
    assert fetch_mod._opt_str(None) is None
    assert fetch_mod._opt_str(1) == "1"
    assert fetch_mod._opt_int(None) is None
    assert fetch_mod._opt_int("4") == 4
    assert fetch_mod._opt_int("x") is None
    assert fetch_mod._opt_int_list("nope") is None
    assert fetch_mod._opt_int_list([1, "2", "x"]) == [1, 2]
    assert fetch_mod._opt_int_list([]) is None


def test_ok():
    assert fetch_mod._ok(None) is False
    assert fetch_mod._ok({}) is False
    assert fetch_mod._ok(_story_rec()) is True


def test_site_ids(monkeypatch):
    monkeypatch.setattr(fetch_mod, "_get_json",
                        lambda client, url: {"items": {"10000021-c1": 1, "10000022-c1": 1}})
    raw, bases = fetch_mod._site_ids(object())
    assert raw == ["10000021-c1", "10000022-c1"]
    assert bases == ["10000021", "10000022"]


# --------------------------------------------------------------------------- #
# _build
# --------------------------------------------------------------------------- #
def test_build_writes_files_and_adds_stories(tmp_path):
    home = str(tmp_path)
    chars = [{"id": "10000021", "name_en": "Amber", "name_zh": "安柏",
              "path": "data/story/character/10000021", "stories": []}]
    recs = {"10000021": _story_rec()}
    info: dict = {}
    fetch_mod._build(home, chars, recs, only=None, info=info)
    prof = tmp_path / "data" / "story" / "character" / "10000021" / "profile"
    assert (prof / "meta.json").exists()
    assert (prof / "text.json").exists()
    assert (prof / "story" / "vision" / "en.md").exists()
    assert chars[0]["stories"] == ["vision"]
    assert info["added_stories"]


def test_build_name_diff_and_empty_story(tmp_path):
    home = str(tmp_path)
    chars = [{"id": "10000021", "name_en": "Amber", "name_zh": "安柏",
              "path": "data/story/character/10000021", "stories": []}]
    rec = _story_rec(name_en="Amber2")
    rec["en"]["story"] = []
    rec["chs"]["story"] = []
    fetch_mod._build(home, chars, {"10000021": rec}, only=None, info={})


def test_build_skips_not_in_only(tmp_path):
    home = str(tmp_path)
    chars = [{"id": "10000021", "name_en": "Amber", "name_zh": "安柏",
              "path": "data/story/character/10000021", "stories": ["vision"]}]
    fetch_mod._build(home, chars, {"10000021": _story_rec()}, only=set(), info={})
    prof = tmp_path / "data" / "story" / "character" / "10000021" / "profile"
    assert not (prof / "meta.json").exists()


# --------------------------------------------------------------------------- #
# _fetch_all
# --------------------------------------------------------------------------- #
def test_fetch_all_refresh(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_mod.time, "sleep", lambda *a: None)

    def fake_get(loc, kind, cid, client):
        if kind == "avatar":
            return _avatar("Amber", "t", "d")
        return {"story": {"1": {"title": "Vision", "title2": "",
                                "text": "First\\nSecond", "text2": ""}}}

    monkeypatch.setattr(fetch_mod, "_get", fake_get)
    chars = [{"id": "10000021"}]
    events = []
    recs, info = fetch_mod._fetch_all(str(tmp_path), chars, mode="refresh",
                                      on_progress=lambda e: events.append(e))
    assert "10000021" in recs
    assert info["first"] == ["10000021"]
    assert events
    cache = json.loads((tmp_path / "data" / "_fetch_all.json").read_text(encoding="utf-8"))
    assert cache[0]["id"] == "10000021"


def test_run_update_mode_reports(tmp_path, monkeypatch):
    # 无 character.json → 等价 update；mock 掉 _fetch_all 返回一个新增角色
    monkeypatch.setattr(fetch_mod, "_fetch_all", lambda home, chars, mode, on_progress: (
        {"10000021": _story_rec()},
        {"mode": mode, "site": {"raw": 1, "bases": 1}, "discovered": ["10000021"],
         "removed": [], "changed": [], "first": [], "unchanged": [], "detail": ["新增"]},
    ))
    assert fetch_mod.run(data_dir=str(tmp_path), mode="update") == fetch_mod.ExitCode.OK
    char_file = tmp_path / "data" / "character.json"
    assert char_file.exists()
    chars = json.loads(char_file.read_text(encoding="utf-8"))
    assert chars[0]["id"] == "10000021"
