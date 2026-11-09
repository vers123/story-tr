"""tests 共享替身与夹具构建器。

集中放置各测试文件重复定义的 Fake* 类、Settings 构造器与临时工作区生成器，
避免同一逻辑多处漂移。
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from story_tr import transport
from story_tr.config import Settings
from story_tr.errors import ConnectionFailed, ModelNotLoaded


class FakeTranslator:
    """可配置结果 / 异常 / 可用性，并记录调用次数的翻译后端替身。"""

    def __init__(self, name="google", result="译文", error=None, available=(True, "")):
        self.name = name
        self.min_interval = 0.0
        self.result = result
        self.error = error
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


class FakeRwkv:
    """`eval` / `status` 用的本地 RWKV 替身。"""

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
    """`fetch` 用的 httpx 响应替身（`raise_for_status` 走标准异常路径）。"""

    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class FakeHttpClient:
    """`fetch` 用的 httpx.Client 替身：按 URL 回调返回 FakeResp。"""

    def __init__(self, handler):
        self._handler = handler
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        return self._handler(url)

    def close(self):
        pass


def mock_translator(monkeypatch, fake):
    """让 `transport.build_translator` 对所有 provider 返回同一个替身。"""
    monkeypatch.setattr(transport, "build_translator", lambda p, s: fake)


def settings(**overrides) -> Settings:
    """构造不联网、无密钥的 `Settings`，可按需覆盖字段。"""
    kwargs = {
        "google_api_key": "",
        "bing_api_key": "",
        "bing_region": "",
        "baidu_appid": "",
        "baidu_appkey": "",
        "rwkv_api_url": "http://127.0.0.1:8000",
        "rwkv_timeout": 300.0,
        "rwkv_chunk_chars": 1200,
        "rwkv_retries": 0,
        "rwkv_max_tokens": 1024,
        "rwkv_temperature": 1.0,
        "rwkv_top_p": 0.3,
    }
    kwargs.update(overrides)
    return Settings(**kwargs)


class FakeResponse:
    """`httpx.Response` 的最小替身（状态码 / JSON / 文本 / headers）。"""

    def __init__(self, payload=None, status=200, text=None, headers=None, content=b""):
        self.status_code = status
        self._payload = payload
        self.text = (
            text
            if text is not None
            else (json.dumps(payload, ensure_ascii=False) if payload is not None else "")
        )
        self.headers = httpx.Headers(headers or {})
        self.content = content

    def json(self):
        if self._payload is None:
            raise ValueError("不是 JSON")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=httpx.Request("GET", "http://fake"),
                response=self,
            )


class FakeClient:
    """`httpx.Client` 的最小替身：记录 get/post 调用并提供可编程响应。"""

    def __init__(self, get=None, post=None):
        self.headers = {}
        self.get_calls = []
        self.post_calls = []
        self.closed = False
        self._get_handler = get
        self._post_handler = post

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        if self._get_handler is None:
            return FakeResponse(status=404, text="")
        return self._get_handler(url, **kwargs)

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        if self._post_handler is None:
            return FakeResponse(status=404, text="")
        return self._post_handler(url, **kwargs)

    def close(self):
        self.closed = True


def install_client(monkeypatch, client):
    """把 `httpx.Client` 替换为给定替身。"""
    monkeypatch.setattr(httpx, "Client", lambda *args, **kwargs: client)
    return client


def setup_workspace(tmp_path: Path) -> Path:
    """生成含 1 个角色 / 1 个故事（zh + en）的最小工作区。"""
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
    return tmp_path


def setup_result_files(tmp_path: Path) -> Path:
    """在工作区基础上补两份已有结果文件。"""
    setup_workspace(tmp_path)
    rdir = tmp_path / "data" / "story" / "character" / "10000021" / "result" / "vision"
    rdir.mkdir(parents=True, exist_ok=True)
    (rdir / "seg_01.json").write_text('{"source": "第一段"}', encoding="utf-8")
    (rdir / "full.json").write_text('{"source": "第一段"}', encoding="utf-8")
    return rdir


def avatar(name, title, detail):
    """站点 avatar 响应替身。"""
    return {
        "name": name,
        "rank": 4,
        "element": "Pyro",
        "weaponType": "Bow",
        "region": "Mondstadt",
        "bodyType": "Girl",
        "specialProp": "prop",
        "icon": "icon.png",
        "birthday": [8, 10],
        "release": 1,
        "fetter": {
            "title": title,
            "detail": detail,
            "constellation": "const",
            "native": "native",
            "cv": {"en": "va"},
        },
        "other": {
            "costume": [{"name": "costume", "description": "costume desc"}],
            "nameCard": {"name": "card", "description": "card desc"},
            "specialFood": {"name": "food"},
        },
    }


def folders_home(tmp_path: Path, chars) -> Path:
    """生成仅含 `data/character.json` 的工作区，返回故事根目录。"""
    data = tmp_path / "data"
    data.mkdir()
    (data / "character.json").write_text(json.dumps(chars), encoding="utf-8")
    return data / "story" / "character"


def status_info(**overrides):
    """`status.render` 的最小报告字典。"""
    info = {
        "workdir": ".",
        "characters": 1,
        "characters_with_result": 0,
        "characters_without_result": 1,
        "stories": 1,
        "stories_with_result": 0,
        "stories_without_result": 1,
        "summaries": 0,
        "titles": 0,
        "fulls": 0,
        "segments": 0,
        "segment_mp3": 0,
        "data_files": 0,
        "data_bytes": 0,
        "log_files": 0,
        "log_bytes": 0,
        "state": None,
        "state_error": None,
        "languages": None,
        "rwkv": {
            "url": "u",
            "template_present": True,
            "model_path": None,
            "model_path_exists": False,
            "probe": None,
        },
    }
    info.update(overrides)
    return info


def story_rec(name_en="Amber", name_zh="安柏", story_count=1):
    """站点 en/chs 抓取记录替身。"""
    return {
        "id": "10000021",
        "en": {
            "avatar": avatar(name_en, "title_en", "detail_en"),
            "story": [
                {"i": i, "title": "Vision", "title2": "", "text": "First\\nSecond", "text2": ""}
                for i in range(1, story_count + 1)
            ],
        },
        "chs": {
            "avatar": avatar(name_zh, "title_zh", "detail_zh"),
            "story": [
                {"i": i, "title": "愿景", "title2": "", "text": "第一\\n第二", "text2": ""}
                for i in range(1, story_count + 1)
            ],
        },
    }
