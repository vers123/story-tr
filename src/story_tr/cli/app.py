"""typer + rich 命令行：参数解析与渲染，业务逻辑全部委托 `services.*.run`。

本模块只负责三件事：
- 用 typer 解析参数（与 `services` 各模块 `run(...)` 的形参一一对应）
- 把 `--provider auto` 归一为 `None`（services 期望 `str | None`）
- 用 rich 渲染进度条与日志，并把 `ExitCode` 转成进程退出码
"""
from __future__ import annotations

import contextlib
import logging
import signal
import threading
from collections.abc import Callable
from typing import Any, Literal

import typer
from rich.logging import RichHandler
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)

from story_tr import __version__
from story_tr.services import chain as chain_svc
from story_tr.services import clean as clean_svc
from story_tr.services import doctor as doctor_svc
from story_tr.services import eval as eval_svc
from story_tr.services import fetch as fetch_svc
from story_tr.services import folders as folders_svc
from story_tr.services import status as status_svc
from story_tr.services import translate as translate_svc

app = typer.Typer(
    name="story-tr",
    help="原神角色故事双语文本抓取与「来回翻译 N 次」链路工具",
    no_args_is_help=True,
)

_PROGRESS_COLUMNS = (
    SpinnerColumn(),
    TextColumn("[progress.description]{task.description}"),
    BarColumn(),
    TaskProgressColumn(),
    TimeElapsedColumn(),
)


def _setup_logging(verbose: bool, quiet: bool) -> None:
    """把 `story_tr.*` 的日志统一路由到 rich 控制台。"""
    level = logging.DEBUG if verbose else (logging.ERROR if quiet else logging.INFO)
    handler = RichHandler(show_path=False)
    handler.setLevel(level)
    logger = logging.getLogger("story_tr")
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"story-tr {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    data_dir: str | None = typer.Option(
        None, "--data-dir", help="工作目录（默认 STORY_TR_HOME 或向上查找 data/character.json）"
    ),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="显示调试与步骤日志"),
    quiet: bool = typer.Option(False, "-q", "--quiet", help="只显示错误"),
    version: bool | None = typer.Option(
        None, "--version", callback=_version_callback, is_eager=True, help="显示版本并退出"
    ),
) -> None:
    if verbose and quiet:
        raise typer.BadParameter("--verbose 与 --quiet 互斥，只能二选一。")
    ctx.obj = {"data_dir": data_dir, "verbose": verbose, "quiet": quiet}
    _setup_logging(verbose=verbose, quiet=quiet)


def _provider_arg(provider: str) -> str | None:
    """`auto` 表示按序回退，services 层约定为 `None`。"""
    return None if provider == "auto" else provider


def _fetch_progress_callback(progress: Progress) -> Callable[[dict[str, Any]], None]:
    """按角色数量的单进度条（fetch）。"""
    task_id: TaskID | None = None

    def _cb(evt: dict[str, Any]) -> None:
        nonlocal task_id
        if evt.get("event") != "progress":
            return
        done = int(evt.get("done") or 0)
        total = int(evt.get("total") or 0)
        char_id = str(evt.get("char_id") or "")
        if task_id is None:
            task_id = progress.add_task("[cyan]抓取角色", total=total or None)
        progress.update(task_id, total=total or None, completed=done,
                        description=f"[cyan]抓取 [{char_id}]")

    return _cb


def _translate_progress_callback(progress: Progress) -> Callable[[dict[str, Any]], None]:
    """双进度条：外层按角色，内层按份（translate）。"""
    char_task = progress.add_task("[cyan]角色", total=None)
    seg_task = progress.add_task("[green]份", total=None)

    def _cb(evt: dict[str, Any]) -> None:
        event = evt.get("event")
        chars = int(evt.get("chars") or 0)
        tasks = int(evt.get("tasks") or 0)
        if event == "char_start":
            char_index = int(evt.get("char_index") or 0)
            char_id = str(evt.get("char_id") or "")
            progress.update(char_task, total=chars or None, completed=char_index - 1,
                            description=f"[cyan]角色 {char_index}/{chars}" if chars else "[cyan]角色")
            progress.update(seg_task, total=tasks or None, completed=0,
                            description=f"[green]份 [{char_id}]")
        elif event == "progress":
            progress.update(seg_task, total=tasks or None, completed=int(evt.get("done") or 0))
        elif event == "char_done":
            progress.update(char_task, total=chars or None, completed=int(evt.get("char_index") or 0))

    return _cb


@app.command("fetch")
def fetch_cmd(
    ctx: typer.Context,
    update: bool = typer.Option(False, "--update", help="发现新角色并检测内容变化，仅重写变化项"),
    refresh: bool = typer.Option(False, "--refresh", help="忽略缓存，全量重新抓取"),
) -> None:
    """抓取角色数据与故事，生成 / 校正 data/ 文件。"""
    if update and refresh:
        raise typer.BadParameter("--update 与 --refresh 互斥，只能二选一。")
    mode = "refresh" if refresh else ("update" if update else "incremental")
    with Progress(*_PROGRESS_COLUMNS) as progress:
        rc = fetch_svc.run(
            data_dir=ctx.obj["data_dir"],
            mode=mode,
            on_progress=_fetch_progress_callback(progress),
        )
    raise typer.Exit(int(rc))


@app.command("folders")
def folders_cmd(ctx: typer.Context) -> None:
    """按 character.json 的 stories 同步重命名 profile/story/ 下文件夹（幂等）。"""
    rc = folders_svc.run(data_dir=ctx.obj["data_dir"])
    raise typer.Exit(int(rc))


@app.command("translate")
def translate_cmd(
    ctx: typer.Context,
    char_ids: list[str] | None = typer.Argument(None, help="角色 id / id 范围（可多个，逗号分隔）"),
    all_characters: bool = typer.Option(False, "--all-characters", help="处理全部角色"),
    stories: str = typer.Option("", "--stories", help="仅处理指定故事（逗号分隔）"),
    mode: Literal["fixed", "random", "custom", "rwkv20"] = typer.Option(
        "fixed", "--mode", help="翻译路线模式"
    ),
    chain: str = typer.Option("asia", "--chain", help="语言链（asia/europe/exotic）"),
    languages: str = typer.Option("", "--languages", help="自定义语言列表（逗号分隔）"),
    steps: int = typer.Option(20, "--steps", help="往返翻译次数"),
    provider: Literal["auto", "google", "bing", "baidu", "rwkv"] = typer.Option(
        "auto", "--provider", help="翻译后端（auto 表示按序回退）"
    ),
    workers: int = typer.Option(1, "--workers", help="并发工作线程数"),
    min_interval: float = typer.Option(0.3, "--min-interval", help="每个请求最小间隔（秒）"),
    balance: bool = typer.Option(False, "--balance", help="在多后端间轮换起始顺序"),
    full_only: bool = typer.Option(False, "--full-only", help="只翻译整篇"),
    segments_only: bool = typer.Option(False, "--segments-only", help="只翻译分段"),
    overwrite: bool = typer.Option(False, "--overwrite", help="覆盖已有结果"),
    reset: bool = typer.Option(False, "--reset", help="重置批次状态并忽略签名不一致"),
    revalidate: bool = typer.Option(False, "--revalidate", help="源文变化时重新翻译"),
    no_cache: bool = typer.Option(False, "--no-cache", help="不使用已有结果缓存"),
    fail_fast: bool = typer.Option(False, "--fail-fast", help="首个失败即中止"),
    max_failures: int = typer.Option(20, "--max-failures", help="失败任务上限"),
    no_tts: bool = typer.Option(False, "--no-tts", help="不生成语音"),
) -> None:
    """批量翻译角色故事（「来回翻译 N 次」链路）。"""
    cancel_event = threading.Event()
    previous_sigint = signal.getsignal(signal.SIGINT)

    def _on_sigint(signum: int, frame: Any) -> None:
        cancel_event.set()

    with contextlib.suppress(ValueError, OSError):
        signal.signal(signal.SIGINT, _on_sigint)
    try:
        with Progress(*_PROGRESS_COLUMNS) as progress:
            rc = translate_svc.run(
                char_ids=list(char_ids or []),
                all_characters=all_characters,
                stories=stories,
                mode=mode,
                chain=chain,
                languages=languages,
                steps=steps,
                provider=_provider_arg(provider),
                workers=workers,
                min_interval=min_interval,
                balance=balance,
                full_only=full_only,
                segments_only=segments_only,
                overwrite=overwrite,
                reset=reset,
                revalidate=revalidate,
                no_cache=no_cache,
                fail_fast=fail_fast,
                max_failures=max_failures,
                no_tts=no_tts,
                data_dir=ctx.obj["data_dir"],
                on_progress=_translate_progress_callback(progress),
                cancel=cancel_event,
            )
    finally:
        with contextlib.suppress(ValueError, OSError):
            signal.signal(signal.SIGINT, previous_sigint)
    raise typer.Exit(int(rc))


@app.command("chain")
def chain_cmd(
    ctx: typer.Context,
    text: str = typer.Argument("Hello, World", help="待翻译的原文"),
    mode: Literal["fixed", "random", "custom", "rwkv20"] = typer.Option(
        "random", "--mode", help="翻译路线模式"
    ),
    chain: str = typer.Option("asia", "--chain", help="语言链（asia/europe/exotic）"),
    languages: str = typer.Option("", "--languages", help="自定义语言列表（逗号分隔）"),
    steps: int = typer.Option(20, "--steps", help="往返翻译次数"),
    provider: Literal["auto", "google", "bing", "baidu", "rwkv"] = typer.Option(
        "auto", "--provider", help="翻译后端（auto 表示按序回退）"
    ),
    min_interval: float = typer.Option(0.3, "--min-interval", help="每个请求最小间隔（秒）"),
    balance: bool = typer.Option(False, "--balance", help="在多后端间轮换起始顺序"),
    no_tts: bool = typer.Option(False, "--no-tts", help="不生成语音"),
    reset: bool = typer.Option(False, "--reset", help="重置链状态并忽略签名不一致"),
    dry_run: bool = typer.Option(False, "--dry-run", help="仅展示路线，不执行翻译"),
    no_file_log: bool = typer.Option(False, "--no-file-log", help="不写日志文件"),
) -> None:
    """对单个文本执行「来回翻译 N 次」链路。"""
    rc = chain_svc.run(
        text=text,
        mode=mode,
        chain=chain,
        languages=languages,
        steps=steps,
        provider=_provider_arg(provider),
        min_interval=min_interval,
        balance=balance,
        no_tts=no_tts,
        reset=reset,
        dry_run=dry_run,
        data_dir=ctx.obj["data_dir"],
        no_file_log=no_file_log,
    )
    raise typer.Exit(int(rc))


@app.command("clean")
def clean_cmd(
    ctx: typer.Context,
    char_ids: list[str] | None = typer.Argument(None, help="角色 id / id 范围（可多个，逗号分隔）"),
    all_characters: bool = typer.Option(False, "--all-characters", help="处理全部角色"),
    stories: str = typer.Option("", "--stories", help="仅清理指定故事（逗号分隔）"),
    dry_run: bool = typer.Option(False, "--dry-run", help="只预览，不删除"),
    yes: bool = typer.Option(False, "-y", "--yes", help="跳过交互确认"),
    keep_state: bool = typer.Option(False, "--keep-state", help="保留状态记录"),
) -> None:
    """清理 result/ 下的翻译产物（保留 result/ 目录本身）。"""
    rc = clean_svc.run(
        char_ids=list(char_ids or []),
        all_characters=all_characters,
        stories=stories,
        dry_run=dry_run,
        yes=yes,
        keep_state=keep_state,
        data_dir=ctx.obj["data_dir"],
    )
    raise typer.Exit(int(rc))


@app.command("status")
def status_cmd(
    ctx: typer.Context,
    json_out: bool = typer.Option(False, "--json", help="以 JSON 输出"),
    deep: bool = typer.Option(False, "--deep", help="统计源语言分布"),
    probe: bool = typer.Option(False, "--probe", help="探测本地 RWKV 服务"),
    manifest: bool = typer.Option(False, "--manifest", help="写入 data/manifest.json"),
) -> None:
    """只读报告工作目录、数据覆盖与批量进度。"""
    rc = status_svc.run(
        json_out=json_out,
        deep=deep,
        probe=probe,
        manifest=manifest,
        data_dir=ctx.obj["data_dir"],
    )
    raise typer.Exit(int(rc))


@app.command("doctor")
def doctor_cmd(
    ctx: typer.Context,
    probe: bool = typer.Option(False, "--probe", help="探测本地 RWKV 服务"),
) -> None:
    """检查运行环境（数据、状态文件、密钥、代理、本地 RWKV）。"""
    rc = doctor_svc.run(probe=probe, data_dir=ctx.obj["data_dir"])
    raise typer.Exit(int(rc))


@app.command("eval")
def eval_cmd(
    ctx: typer.Context,
    char_ids: list[str] | None = typer.Argument(None, help="角色 id / id 范围（可多个，逗号分隔）"),
    all_characters: bool = typer.Option(False, "--all-characters", help="评测全部角色"),
    stories: str = typer.Option("", "--stories", help="仅评测指定故事（逗号分隔）"),
    limit: int = typer.Option(20, "--limit", help="采样段落数上限"),
    templates: str = typer.Option("", "--templates", help="评测模板（builtin 或 || 分隔）"),
    out: str | None = typer.Option(None, "--out", help="报告输出目录"),
    rwkv_url: str | None = typer.Option(None, "--rwkv-url", help="覆盖本地 RWKV 服务地址"),
    rwkv_template: str | None = typer.Option(None, "--rwkv-template", help="覆盖 RWKV 提示词模板"),
) -> None:
    """简化版本地评测：用多套模板采样生成并排名。"""
    rc = eval_svc.run(
        char_ids=char_ids,
        all_characters=all_characters,
        stories=stories,
        limit=limit,
        templates=templates,
        out=out,
        rwkv_url=rwkv_url,
        rwkv_template=rwkv_template,
        data_dir=ctx.obj["data_dir"],
    )
    raise typer.Exit(int(rc))
