"""services.fetch / fetch_pure / fetch_write：抓取、指纹与落盘。"""

import json

from story_tr.errors import ExitCode
from story_tr.services import fetch as fetch_mod
from story_tr.services import fetch_write, storage
from tests.support import FakeHttpClient, FakeResp, avatar, story_rec


def test_fetch_generates_files(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    (data / "character.json").write_text(
        json.dumps(
            [
                {
                    "id": "10000021",
                    "name_en": "Amber",
                    "name_zh": "安柏",
                    "path": "data/story/character/10000021",
                    "stories": ["vision"],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    def handler(url):
        if "/avatarFetter/" in url:
            return FakeResp(
                {
                    "data": {
                        "story": {
                            "1": {"title": "Vision", "title2": "", "text": "First\\nSecond", "text2": ""}
                        }
                    }
                }
            )
        if url.endswith("/en/avatar/10000021"):
            return FakeResp({"data": avatar("Amber", "title_en", "detail_en")})
        if url.endswith("/chs/avatar/10000021"):
            return FakeResp({"data": avatar("安柏", "title_zh", "detail_zh")})
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
    rec = story_rec()
    assert fetch_mod.fingerprint(rec) == fetch_mod.fingerprint(json.loads(json.dumps(rec)))


def test_story_diff():
    old = {
        "en": {"avatar": {"name": "A"}, "story": [{"i": 1, "text": "a", "text2": ""}]},
        "chs": {"avatar": {"name": "甲"}, "story": [{"i": 1, "text": "一", "text2": ""}]},
    }
    new = {
        "en": {"avatar": {"name": "B"}, "story": [{"i": 1, "text": "a2", "text2": ""}]},
        "chs": {
            "avatar": {"name": "甲"},
            "story": [{"i": 1, "text": "一", "text2": ""}, {"i": 2, "text": "二", "text2": ""}],
        },
    }
    diffs = fetch_mod.story_diff(old, new)
    assert any("故事数" in d for d in diffs)
    assert any("文本变化" in d for d in diffs)
    assert any("资料变化" in d for d in diffs)


def test_make_char_entry():
    rec = story_rec()
    entry = fetch_mod.make_char_entry("10000021", rec)
    assert entry["id"] == "10000021"
    assert entry["name_en"] == "Amber"
    assert entry["stories"] == ["vision"]


def test_scalar_helpers():
    assert fetch_write._s(None) == ""
    assert fetch_write._s(1) == "1"
    assert fetch_write._opt_str(None) is None
    assert fetch_write._opt_str(1) == "1"
    assert fetch_write._opt_int(None) is None
    assert fetch_write._opt_int("4") == 4
    assert fetch_write._opt_int("x") is None
    assert fetch_write._opt_int_list("nope") is None
    assert fetch_write._opt_int_list([1, "2", "x"]) == [1, 2]
    assert fetch_write._opt_int_list([]) is None


def test_ok():
    assert fetch_mod._ok(None) is False
    assert fetch_mod._ok({}) is False
    assert fetch_mod._ok(story_rec()) is True


def test_site_ids(monkeypatch):
    monkeypatch.setattr(
        fetch_mod, "_get_json", lambda client, url: {"items": {"10000021-c1": 1, "10000022-c1": 1}}
    )
    raw, bases = fetch_mod._site_ids(object())
    assert raw == ["10000021-c1", "10000022-c1"]
    assert bases == ["10000021", "10000022"]


def test_build_writes_files_and_adds_stories(tmp_path):
    home = str(tmp_path)
    chars = [
        {
            "id": "10000021",
            "name_en": "Amber",
            "name_zh": "安柏",
            "path": "data/story/character/10000021",
            "stories": [],
        }
    ]
    recs = {"10000021": story_rec()}
    info: dict = {}
    fetch_write.write_dataset(home, chars, recs, only=None, info=info)
    prof = tmp_path / "data" / "story" / "character" / "10000021" / "profile"
    assert (prof / "meta.json").exists()
    assert (prof / "text.json").exists()
    assert (prof / "story" / "vision" / "en.md").exists()
    assert chars[0]["stories"] == ["vision"]
    assert info["added_stories"]


def test_build_coerces_cv_ids_to_str(tmp_path):
    home = str(tmp_path)
    chars = [
        {
            "id": "10000021",
            "name_en": "Amber",
            "name_zh": "安柏",
            "path": "data/story/character/10000021",
            "stories": [],
        }
    ]
    rec = story_rec()
    rec["en"]["avatar"]["fetter"]["cv"] = {
        "EN": 3354392916,
        "JP": 1249977488,
        "KR": 986493223,
        "CHS": "鹿喑&多多poi",
        "XX": None,
    }
    fetch_write.write_dataset(home, chars, {"10000021": rec}, only=None, info={})
    prof = tmp_path / "data" / "story" / "character" / "10000021" / "profile"
    text = json.loads((prof / "text.json").read_text(encoding="utf-8"))
    assert text["cv"] == {"EN": "3354392916", "JP": "1249977488", "KR": "986493223", "CHS": "鹿喑&多多poi"}


def test_str_map():
    assert fetch_write._str_map(None) == {}
    assert fetch_write._str_map(["x"]) == {}
    assert fetch_write._str_map({"A": 1, "B": None, "C": "x"}) == {"A": "1", "C": "x"}


def test_build_name_diff_and_empty_story(tmp_path):
    home = str(tmp_path)
    chars = [
        {
            "id": "10000021",
            "name_en": "Amber",
            "name_zh": "安柏",
            "path": "data/story/character/10000021",
            "stories": [],
        }
    ]
    rec = story_rec(name_en="Amber2")
    rec["en"]["story"] = []
    rec["chs"]["story"] = []
    fetch_write.write_dataset(home, chars, {"10000021": rec}, only=None, info={})


def test_build_skips_unchanged_rewrite(tmp_path, monkeypatch):
    home = str(tmp_path)
    chars = [
        {
            "id": "10000021",
            "name_en": "Amber",
            "name_zh": "安柏",
            "path": "data/story/character/10000021",
            "stories": [],
        }
    ]
    recs = {"10000021": story_rec()}
    fetch_write.write_dataset(home, chars, recs, only=None, info={})

    replacements = []
    original = storage._replace_with_retry
    monkeypatch.setattr(
        storage, "_replace_with_retry", lambda tmp, path: (replacements.append(path), original(tmp, path))[1]
    )
    fetch_write.write_dataset(home, chars, recs, only=None, info={})
    assert replacements == []


def test_build_skips_not_in_only(tmp_path):
    home = str(tmp_path)
    chars = [
        {
            "id": "10000021",
            "name_en": "Amber",
            "name_zh": "安柏",
            "path": "data/story/character/10000021",
            "stories": ["vision"],
        }
    ]
    fetch_write.write_dataset(home, chars, {"10000021": story_rec()}, only=set(), info={})
    prof = tmp_path / "data" / "story" / "character" / "10000021" / "profile"
    assert not (prof / "meta.json").exists()


def test_fetch_all_refresh(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_mod.time, "sleep", lambda *a: None)

    def fake_get(loc, kind, cid, client):
        if kind == "avatar":
            return avatar("Amber", "t", "d")
        return {"story": {"1": {"title": "Vision", "title2": "", "text": "First\\nSecond", "text2": ""}}}

    monkeypatch.setattr(fetch_mod, "_get", fake_get)
    chars = [{"id": "10000021"}]
    events = []
    recs, info = fetch_mod._collect(
        str(tmp_path), chars, mode="refresh", on_progress=lambda e: events.append(e)
    )
    assert "10000021" in recs
    assert info["first"] == ["10000021"]
    assert events
    cache = json.loads((tmp_path / "data" / "_fetch_all.json").read_text(encoding="utf-8"))
    assert cache[0]["id"] == "10000021"


def test_fetch_all_concurrent(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_mod.time, "sleep", lambda *a: None)

    def fake_get(loc, kind, cid, client):
        if kind == "avatar":
            return avatar(f"Name{cid}", "t", "d")
        return {"story": {"1": {"title": "Vision", "title2": "", "text": "x", "text2": ""}}}

    monkeypatch.setattr(fetch_mod, "_get", fake_get)
    chars = [{"id": "10000021"}, {"id": "10000022"}]
    events = []
    recs, _ = fetch_mod._collect(str(tmp_path), chars, mode="refresh", on_progress=events.append, workers=2)
    assert set(recs) == {"10000021", "10000022"}
    assert len(events) == 2
    for cid in ("10000021", "10000022"):
        assert recs[cid]["en"]["avatar"]["name"] == f"Name{cid}"


def test_fetch_all_update_detects_changed_detail(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_mod.time, "sleep", lambda *a: None)
    old = story_rec(name_en="Amber")
    cache = tmp_path / "data" / "_fetch_all.json"
    cache.parent.mkdir(parents=True)
    cache.write_text(json.dumps([{**old, "hash": fetch_mod.fingerprint(old)}]), encoding="utf-8")

    def fake_get(loc, kind, cid, client):
        if kind == "avatar":
            return avatar("Amber2", "t", "d")
        return {"story": {"1": {"title": "Vision", "title2": "", "text": "x", "text2": ""}}}

    monkeypatch.setattr(fetch_mod, "_get", fake_get)
    monkeypatch.setattr(fetch_mod, "_site_ids", lambda client, loc="en": (["10000021-c1"], ["10000021"]))
    chars = [{"id": "10000021"}]
    _, info = fetch_mod._collect(str(tmp_path), chars, mode="update", on_progress=None)
    assert info["changed"] == ["10000021"]
    assert any("资料变化" in d for d in info["detail"])


def test_run_update_mode_reports(tmp_path, monkeypatch):
    # 无 character.json → 等价 update；mock 掉 _fetch_all 返回一个新增角色
    monkeypatch.setattr(
        fetch_mod,
        "_collect",
        lambda home, chars, mode, on_progress, workers=4: (
            {"10000021": story_rec()},
            {
                "mode": mode,
                "site": {"raw": 1, "bases": 1},
                "discovered": ["10000021"],
                "removed": [],
                "changed": [],
                "first": [],
                "unchanged": [],
                "detail": ["新增"],
            },
        ),
    )
    assert fetch_mod.run(data_dir=str(tmp_path), mode="update") == fetch_mod.ExitCode.OK
    char_file = tmp_path / "data" / "character.json"
    assert char_file.exists()
    chars = json.loads(char_file.read_text(encoding="utf-8"))
    assert chars[0]["id"] == "10000021"
