"""`story-tr folders`：同步重命名故事文件夹。"""

import typer

from story_tr.services import folders as folders_svc


def folders_cmd(ctx: typer.Context) -> None:
    """按 character.json 的 stories 同步重命名 profile/story/ 下文件夹（幂等）。"""
    rc = folders_svc.run(data_dir=ctx.obj["data_dir"])
    raise typer.Exit(int(rc))
