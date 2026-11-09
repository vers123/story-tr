"""子命令注册：每个命令一个模块，统一挂到根 `app` 上。"""

from __future__ import annotations

import typer

from .chain import chain_cmd
from .clean import clean_cmd
from .doctor import doctor_cmd
from .eval import eval_cmd
from .fetch import fetch_cmd
from .folders import folders_cmd
from .status import status_cmd
from .translate import translate_cmd

COMMANDS = (
    ("fetch", fetch_cmd),
    ("folders", folders_cmd),
    ("translate", translate_cmd),
    ("chain", chain_cmd),
    ("clean", clean_cmd),
    ("status", status_cmd),
    ("doctor", doctor_cmd),
    ("eval", eval_cmd),
)


def register(app: typer.Typer) -> None:
    """把所有子命令注册到给定 Typer 应用。"""
    for name, func in COMMANDS:
        app.command(name)(func)
