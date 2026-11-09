"""`story-tr doctor`：检查运行环境。"""

import typer

from story_tr.services import doctor as doctor_svc


def doctor_cmd(
    ctx: typer.Context,
    probe: bool = typer.Option(False, "--probe", help="探测本地 RWKV 服务"),
) -> None:
    """检查运行环境（数据、状态文件、密钥、代理、本地 RWKV）。"""
    rc = doctor_svc.run(probe=probe, data_dir=ctx.obj["data_dir"])
    raise typer.Exit(int(rc))
