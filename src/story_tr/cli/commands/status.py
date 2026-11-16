"""`story-tr status`：只读报告数据覆盖与进度。"""

import typer

from story_tr.services import status as status_svc


def status_cmd(
    ctx: typer.Context,
    json_out: bool = typer.Option(False, "--json", help="以 JSON 输出"),
    deep: bool = typer.Option(False, "--deep", help="统计源语言分布"),
    probe: bool = typer.Option(False, "--probe", help="探测本地 RWKV 服务"),
    manifest: bool = typer.Option(False, "--manifest", help="写入 data/manifest.json"),
    result_tag: str | None = typer.Option(None, "--result-tag", help="统计指定结果标签目录"),
) -> None:
    """只读报告工作目录、数据覆盖与批量进度。"""
    rc = status_svc.run(
        json_out=json_out,
        deep=deep,
        probe=probe,
        manifest=manifest,
        data_dir=ctx.obj["data_dir"],
        result_tag=result_tag,
    )
    raise typer.Exit(int(rc))
