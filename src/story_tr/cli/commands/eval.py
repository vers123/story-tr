"""`story-tr eval`：本地评测与模板排名。"""

import typer

from story_tr.services import eval as eval_svc


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
