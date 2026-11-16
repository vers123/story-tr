"""`story-tr export`：把翻译结果导出为 jsonl / parquet + manifest。"""

import typer

from story_tr.services import export as export_svc


def export_cmd(
    ctx: typer.Context,
    char_ids: list[str] | None = typer.Argument(None, help="角色 id / id 范围（可多个，逗号分隔）"),
    all_characters: bool = typer.Option(False, "--all-characters", help="导出全部角色"),
    stories: str = typer.Option("", "--stories", help="仅导出指定故事（逗号分隔）"),
    result_tag: str | None = typer.Option(None, "--result-tag", help="导出指定结果标签目录"),
    out: str | None = typer.Option(None, "--out", help="输出目录（默认 out/export）"),
    fmt: str = typer.Option("jsonl", "--format", help="导出格式：jsonl / parquet"),
    with_steps: bool = typer.Option(False, "--with-steps", help="附带每步中间结果与 RWKV 元数据"),
) -> None:
    """导出翻译结果数据集（JSONL / Parquet + manifest）。"""
    rc = export_svc.run(
        char_ids=char_ids,
        all_characters=all_characters,
        stories=stories,
        result_tag=result_tag,
        out=out,
        fmt=fmt,
        with_steps=with_steps,
        data_dir=ctx.obj["data_dir"],
    )
    raise typer.Exit(int(rc))
