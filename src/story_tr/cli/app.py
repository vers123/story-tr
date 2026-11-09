"""typer + rich 命令行入口：全局参数、异常兜底与子命令注册。

各子命令的实现见 `cli.commands.*`，共用渲染能力见 `cli.ui`。
"""

from __future__ import annotations

import typer

from story_tr import __version__

from . import commands
from .ui import StoryTrTyper, setup_logging

app = StoryTrTyper(
    name="story-tr",
    help="原神角色故事双语文本抓取与「来回翻译 N 次」链路工具",
    no_args_is_help=True,
)


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
    setup_logging(verbose=verbose, quiet=quiet)


commands.register(app)

__all__ = ["app", "main"]
