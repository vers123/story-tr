"""`story-tr clean`：清理翻译产物。"""

import typer

from story_tr.services import clean as clean_svc


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
