"""CLI 共用设施：异常兜底、日志、进度条与参数归一。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import typer
from rich.logging import RichHandler
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)

from story_tr.errors import ExitCode, StoryTrError

PROGRESS_COLUMNS = (
    SpinnerColumn(),
    TextColumn("[progress.description]{task.description}"),
    BarColumn(),
    TaskProgressColumn(),
    TimeElapsedColumn(),
)


class StoryTrTyper(typer.Typer):
    """统一兜底：把未捕获异常转成友好提示，避免向用户抛出 traceback。"""

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        try:
            return super().__call__(*args, **kwargs)
        except SystemExit:
            raise
        except (typer.Exit, typer.Abort) as exc:
            raise SystemExit(getattr(exc, "exit_code", 1)) from None
        except KeyboardInterrupt:
            raise SystemExit(int(ExitCode.CANCELLED)) from None
        except StoryTrError as exc:
            typer.secho(f"错误：{exc}", err=True, fg="red")
            raise SystemExit(int(ExitCode.USAGE)) from None
        except Exception as exc:  # noqa: BLE001 - 兜底所有未预期异常
            typer.secho(f"意外错误（{type(exc).__name__}）：{exc}", err=True, fg="red")
            typer.secho("可加 -v/--verbose 重新运行以查看详细日志。", err=True)
            raise SystemExit(1) from None


def setup_logging(verbose: bool, quiet: bool) -> None:
    """把 `story_tr.*` 的日志统一路由到 rich 控制台。"""
    level = logging.DEBUG if verbose else (logging.ERROR if quiet else logging.INFO)
    handler = RichHandler(show_path=False)
    handler.setLevel(level)
    logger = logging.getLogger("story_tr")
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False


def provider_arg(provider: str) -> str | None:
    """`auto` 表示按序回退，services 层约定为 `None`。"""
    return None if provider == "auto" else provider


def fetch_progress(progress: Progress) -> Callable[[dict[str, Any]], None]:
    """按角色数量的单进度条（fetch）。"""
    task_id: TaskID | None = None

    def _cb(evt: dict[str, Any]) -> None:
        nonlocal task_id
        if evt.get("event") != "progress":
            return
        done = int(evt.get("done") or 0)
        total = int(evt.get("total") or 0)
        char_id = str(evt.get("char_id") or "")
        if task_id is None:
            task_id = progress.add_task("[cyan]抓取角色", total=total or None)
        progress.update(task_id, total=total or None, completed=done, description=f"[cyan]抓取 [{char_id}]")

    return _cb


def translate_progress(progress: Progress) -> Callable[[dict[str, Any]], None]:
    """双进度条：外层按角色，内层按份（translate）。"""
    char_task = progress.add_task("[cyan]角色", total=None)
    seg_task = progress.add_task("[green]份", total=None)

    def _cb(evt: dict[str, Any]) -> None:
        event = evt.get("event")
        chars = int(evt.get("chars") or 0)
        tasks = int(evt.get("tasks") or 0)
        if event == "char_start":
            char_index = int(evt.get("char_index") or 0)
            char_id = str(evt.get("char_id") or "")
            progress.update(
                char_task,
                total=chars or None,
                completed=char_index - 1,
                description=f"[cyan]角色 {char_index}/{chars}" if chars else "[cyan]角色",
            )
            progress.update(seg_task, total=tasks or None, completed=0, description=f"[green]份 [{char_id}]")
        elif event == "progress":
            progress.update(seg_task, total=tasks or None, completed=int(evt.get("done") or 0))
        elif event == "char_done":
            progress.update(char_task, total=chars or None, completed=int(evt.get("char_index") or 0))

    return _cb
