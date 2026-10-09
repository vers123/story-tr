# -*- coding: utf-8 -*-
"""story-tr MCP Server。

- 传输：`stdio` —— 本地 MCP 客户端（Trae / Claude Desktop 等）直接拉起本进程。
- 工作目录：复用 `story_tr.paths.home()`——`$STORY_TR_HOME` → 向上查找 `data/character.json`
  → 当前目录。客户端配置里设置 `STORY_TR_HOME` 即可指向数据目录。
- 依赖：可选依赖 `mcp`（**需要 Python ≥3.10**）：`pip install "story-tr[mcp]"`。

只读能力（Resources + 查询 Tool）在进程内直接读文件；写/长任务（translate / fetch）
通过**子进程**调用既有 CLI，由 `jobs` 做串行排队、进度与取消。
"""
from __future__ import annotations

import functools

from .. import __version__
from ..chain import ChainError
from . import jobs, resources, tools

try:
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ResourceError, ToolError
except ImportError as exc:  # pragma: no cover - 取决于运行环境
    raise SystemExit(
        '缺少 MCP 依赖：请安装 `pip install "story-tr[mcp]"`（需要 Python ≥3.10）') from exc


INSTRUCTIONS = (
    "story-tr：原神角色故事双语（中/英）文本库 + 「来回翻译 N 次」链路工具。\n"
    "只读：列出角色与故事、读取故事的中/英原文、读取已有翻译结果。\n"
    "写/长任务：chain（单条文本翻译，同步）；translate_start / fetch_start（异步，"
    "返回 job_id，用 job_status 查询、job_cancel 取消）；clean（默认只预览）。\n"
    "数据来自本地 data/ 目录（未入库，需先由 fetch 生成、translate 产出结果）。"
)

# 需要转成「带原因的 MCP 错误」的域异常（否则客户端只看到 "Error executing tool ..."）
_DOMAIN_ERRORS = (ValueError, KeyError, LookupError, OSError, ChainError)


def _tool_errors(fn):
    """把域异常转成携带消息的 `ToolError`，让客户端/模型能据此自我纠正。"""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ToolError:
            raise
        except _DOMAIN_ERRORS as exc:
            raise ToolError(str(exc)) from exc
    return wrapper


def _resource_errors(fn):
    """资源版：把域异常转成携带消息的 `ResourceError`。"""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ResourceError:
            raise
        except _DOMAIN_ERRORS as exc:
            raise ResourceError(str(exc)) from exc
    return wrapper


def build_server() -> MCPServer:
    """构建并注册全部 Tool / Resource 的 MCP Server。"""
    server = MCPServer(name="story-tr", version=__version__, instructions=INSTRUCTIONS)

    # ---- Resources：内容 ----
    @server.resource("story://characters", mime_type="application/json",
                     description="全部角色索引（id / 中英名 / 故事数）")
    @_resource_errors
    def r_characters() -> str:
        return resources.characters_index()

    @server.resource("story://character/{char_id}", mime_type="application/json",
                     description="某角色的 meta / 资料 / 故事清单")
    @_resource_errors
    def r_character(char_id: str) -> str:
        return resources.character_profile(char_id)

    @server.resource("story://story/{char_id}/{story}/{lang}", mime_type="text/markdown",
                     description="某篇故事的原始 Markdown（lang 取 zh / en，含 frontmatter）")
    @_resource_errors
    def r_story(char_id: str, story: str, lang: str) -> str:
        return resources.story_markdown(char_id, story, lang)

    @server.resource("story://result/{char_id}/{story}", mime_type="application/json",
                     description="某篇故事的翻译结果概要（segments.json）")
    @_resource_errors
    def r_result(char_id: str, story: str) -> str:
        return resources.result_summary(char_id, story)

    # ---- Tools：只读检索 ----
    @server.tool(description="列出全部角色（id / 中英名 / 故事数）")
    @_tool_errors
    def list_characters() -> list[dict]:
        return tools.list_characters()

    @server.tool(description="列出某角色的全部故事（文件夹名 / 中英标题 / 段落数）")
    @_tool_errors
    def list_stories(char_id: str) -> list[dict]:
        return tools.list_stories(char_id)

    @server.tool(description="读取某篇故事的翻译结果概要（标题 / 全文 / 各段的源文与最终译文）")
    @_tool_errors
    def get_result_summary(char_id: str, story: str) -> dict:
        return tools.get_result_summary(char_id, story)

    @server.tool(description="读取某篇故事的某条结果明细：key 取 full / title / seg_NN")
    @_tool_errors
    def read_result_item(char_id: str, story: str, key: str) -> dict:
        return tools.read_result_item(char_id, story, key)

    # ---- Tools：同步（含联网 / 写盘） ----
    @server.tool(description="单条文本跑「来回翻译 N 次」链路（联网，同步返回逐步与最终译文）")
    @_tool_errors
    def chain(text: str, mode: str = "fixed", chain: str = "asia", languages: str = "",
              steps: int = 20, provider: str = "") -> dict:
        return tools.chain(text, mode=mode, chain=chain, languages=languages,
                           steps=steps, provider=provider)

    @server.tool(description="清理结果目录（默认只预览；dry_run=false 且 confirm=true 才真删）")
    @_tool_errors
    def clean(char_ids: str, stories: str = "", dry_run: bool = True,
              confirm: bool = False, keep_state: bool = False) -> dict:
        return tools.clean(char_ids, stories_filter=stories, dry_run=dry_run,
                           confirm=confirm, keep_state=keep_state)

    # ---- Tools：长任务（子进程 + 串行队列，start/status 轮询） ----
    @server.tool(description="启动批量翻译任务（异步）；返回 job_id，用 job_status 查进度")
    @_tool_errors
    def translate_start(char_ids: str, stories: str = "", mode: str = "fixed",
                        chain: str = "asia", workers: int = 1, provider: str = "",
                        full_only: bool = False, segments_only: bool = False) -> dict:
        ids = tools.resolve_ids(char_ids)
        if not ids:
            raise ValueError("没有匹配到任何角色 id：%s" % char_ids)
        parts = "full" if full_only else "segments" if segments_only else "both"
        total = tools.count_translate_tasks(ids, stories, parts)
        job = jobs.manager().submit(
            "translate",
            tools.translate_argv(char_ids, stories, mode, chain, workers, provider,
                                 full_only, segments_only),
            progress_fn=tools.make_translate_progress(ids, total))
        out = job.summary()
        out["characters"] = len(ids)
        out["total_tasks"] = total
        return out

    @server.tool(description="启动数据抓取任务（异步，默认 --update 增量）；返回 job_id")
    @_tool_errors
    def fetch_start(update: bool = True) -> dict:
        return jobs.manager().submit("fetch", tools.fetch_argv(update)).summary()

    @server.tool(description="查询任务状态：state / progress / returncode / 日志尾行")
    @_tool_errors
    def job_status(job_id: str) -> dict:
        return jobs.manager().status(job_id)

    @server.tool(description="取消任务（排队中或运行中；运行中会终止子进程）")
    @_tool_errors
    def job_cancel(job_id: str) -> dict:
        return jobs.manager().cancel(job_id)

    @server.tool(description="列出全部任务（新 → 旧）")
    @_tool_errors
    def job_list() -> list[dict]:
        return jobs.manager().list_jobs()

    return server


def main() -> int:
    """入口：以 stdio 运行 MCP Server（阻塞直到客户端断开）。"""
    build_server().run("stdio")
    return 0
