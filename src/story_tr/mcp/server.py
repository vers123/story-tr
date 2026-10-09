# -*- coding: utf-8 -*-
"""story-tr MCP Server（**只读**）。

- 传输：`stdio` —— 本地 MCP 客户端（Trae / Claude Desktop 等）直接拉起本进程。
- 工作目录：复用 `story_tr.paths.home()`——`$STORY_TR_HOME` → 向上查找 `data/character.json`
  → 当前目录。客户端配置里设置 `STORY_TR_HOME` 即可指向数据目录。
- 依赖：可选依赖 `mcp`（**需要 Python ≥3.10**）：`pip install "story-tr[mcp]"`。
"""
from __future__ import annotations

from .. import __version__
from . import resources, tools

try:
    from mcp.server.mcpserver import MCPServer
except ImportError as exc:  # pragma: no cover - 取决于运行环境
    raise SystemExit(
        '缺少 MCP 依赖：请安装 `pip install "story-tr[mcp]"`（需要 Python ≥3.10）') from exc


INSTRUCTIONS = (
    "story-tr：原神角色故事双语（中/英）文本库 + 「来回翻译 N 次」链路工具。\n"
    "本服务为**只读**：可列出角色与故事、读取故事的中/英原文，以及读取已有的翻译结果。\n"
    "数据来自本地 data/ 目录（未入库，需先由 `story-tr fetch` 生成、`story-tr translate` 产出结果）。"
)


def build_server() -> MCPServer:
    """构建并注册全部只读 Tool / Resource 的 MCP Server。"""
    server = MCPServer(name="story-tr", version=__version__, instructions=INSTRUCTIONS)

    # ---- Resources：内容 ----
    @server.resource("story://characters", mime_type="application/json",
                     description="全部角色索引（id / 中英名 / 故事数）")
    def r_characters() -> str:
        return resources.characters_index()

    @server.resource("story://character/{char_id}", mime_type="application/json",
                     description="某角色的 meta / 资料 / 故事清单")
    def r_character(char_id: str) -> str:
        return resources.character_profile(char_id)

    @server.resource("story://story/{char_id}/{story}/{lang}", mime_type="text/markdown",
                     description="某篇故事的原始 Markdown（lang 取 zh / en，含 frontmatter）")
    def r_story(char_id: str, story: str, lang: str) -> str:
        return resources.story_markdown(char_id, story, lang)

    @server.resource("story://result/{char_id}/{story}", mime_type="application/json",
                     description="某篇故事的翻译结果概要（segments.json）")
    def r_result(char_id: str, story: str) -> str:
        return resources.result_summary(char_id, story)

    # ---- Tools：检索 ----
    @server.tool(description="列出全部角色（id / 中英名 / 故事数）")
    def list_characters() -> list[dict]:
        return tools.list_characters()

    @server.tool(description="列出某角色的全部故事（文件夹名 / 中英标题 / 段落数）")
    def list_stories(char_id: str) -> list[dict]:
        return tools.list_stories(char_id)

    @server.tool(description="读取某篇故事的翻译结果概要（标题 / 全文 / 各段的源文与最终译文）")
    def get_result_summary(char_id: str, story: str) -> dict:
        return tools.get_result_summary(char_id, story)

    @server.tool(description="读取某篇故事的某条结果明细：key 取 full / title / seg_NN")
    def read_result_item(char_id: str, story: str, key: str) -> dict:
        return tools.read_result_item(char_id, story, key)

    return server


def main() -> int:
    """入口：以 stdio 运行 MCP Server（阻塞直到客户端断开）。"""
    build_server().run("stdio")
    return 0
