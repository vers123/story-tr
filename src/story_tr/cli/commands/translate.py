"""`story-tr translate`：批量翻译角色故事。"""

import contextlib
import signal
import threading
from typing import Any, Literal

import typer
from rich.progress import Progress

from story_tr.errors import ExitCode
from story_tr.services import translate as translate_svc

from ..ui import PROGRESS_COLUMNS, provider_arg, translate_progress


def _run_with_cancel(ctx: typer.Context, cancel_event: threading.Event, **options: Any) -> ExitCode:
    """安装 Ctrl+C → 取消事件的信号处理，执行翻译并还原信号处理。"""
    previous_sigint = signal.getsignal(signal.SIGINT)

    def _on_sigint(signum: int, frame: Any) -> None:
        cancel_event.set()

    with contextlib.suppress(ValueError, OSError):
        signal.signal(signal.SIGINT, _on_sigint)
    try:
        with Progress(*PROGRESS_COLUMNS) as progress:
            return translate_svc.run(
                data_dir=ctx.obj["data_dir"],
                on_progress=translate_progress(progress),
                cancel=cancel_event,
                **options,
            )
    finally:
        with contextlib.suppress(ValueError, OSError):
            signal.signal(signal.SIGINT, previous_sigint)


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
    tts_only: bool = typer.Option(
        False, "--tts-only", help="只补生成缺失语音（不重译文本；可配 --stories / --overwrite）"
    ),
) -> None:
    """批量翻译角色故事（「来回翻译 N 次」链路）。"""
    if tts_only and no_tts:
        raise typer.BadParameter("--tts-only 与 --no-tts 互斥，只能二选一。")
    rc = _run_with_cancel(
        ctx,
        threading.Event(),
        char_ids=list(char_ids or []),
        all_characters=all_characters,
        stories=stories,
        mode=mode,
        chain=chain,
        languages=languages,
        steps=steps,
        provider=provider_arg(provider),
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
        tts_only=tts_only,
    )
    raise typer.Exit(int(rc))
