"""`story-tr chain`：单文本来回翻译。"""

from typing import Literal

import typer

from story_tr.services import chain as chain_svc

from ..ui import provider_arg


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
        provider=provider_arg(provider),
        min_interval=min_interval,
        balance=balance,
        no_tts=no_tts,
        reset=reset,
        dry_run=dry_run,
        data_dir=ctx.obj["data_dir"],
        no_file_log=no_file_log,
    )
    raise typer.Exit(int(rc))
