# -*- coding: utf-8 -*-
"""MCP Server 注册层：需要可选依赖 `mcp`（**Python ≥3.10**）。

未安装 mcp（如 CI 的 3.9）时整个文件跳过，不影响其余测试。
"""
import asyncio

import pytest

pytest.importorskip("mcp")

from story_tr.mcp import server  # noqa: E402


def test_registers_tools():
    s = server.build_server()
    names = sorted(t.name for t in asyncio.run(s.list_tools()))
    assert names == ["get_result_summary", "list_characters", "list_stories", "read_result_item"]


def test_registers_resources_and_templates():
    s = server.build_server()
    assert sorted(r.uri for r in asyncio.run(s.list_resources())) == ["story://characters"]
    templates = asyncio.run(s.list_resource_templates())
    assert sorted(t.uri_template for t in templates) == [
        "story://character/{char_id}",
        "story://result/{char_id}/{story}",
        "story://story/{char_id}/{story}/{lang}",
    ]
