"""`story-tr fetch`：抓取角色数据与故事。"""

import typer
from rich.progress import Progress

from story_tr.services import fetch as fetch_svc

from ..ui import PROGRESS_COLUMNS, fetch_progress


def fetch_cmd(
    ctx: typer.Context,
    update: bool = typer.Option(False, "--update", help="发现新角色并检测内容变化，仅重写变化项"),
    refresh: bool = typer.Option(False, "--refresh", help="忽略缓存，全量重新抓取"),
    workers: int = typer.Option(4, "--workers", help="并发抓取角色数"),
) -> None:
    """抓取角色数据与故事，生成 / 校正 data/ 文件。"""
    if update and refresh:
        raise typer.BadParameter("--update 与 --refresh 互斥，只能二选一。")
    mode = "refresh" if refresh else ("update" if update else "incremental")
    with Progress(*PROGRESS_COLUMNS) as progress:
        rc = fetch_svc.run(
            data_dir=ctx.obj["data_dir"],
            mode=mode,
            on_progress=fetch_progress(progress),
            workers=workers,
        )
    raise typer.Exit(int(rc))
