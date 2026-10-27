"""业务编排层：把 `core`（纯逻辑）与 `transport`（后端）组装成用例，供 `cli` 调用。

每个模块暴露一个 `run(...) -> ExitCode`；进度经 `on_progress` 回调上报，
日志走标准 `logging`（logger 名为 `story_tr.services` 或各模块），不依赖 typer/rich。
"""
