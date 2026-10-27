"""story-tr v1.0：分层架构的「抓取 + 来回翻译 N 次」工具。

`models`（pydantic 数据契约）/ `core`（纯逻辑）/ `transport`（后端抽象）/
`services`（编排）/ `cli`（typer + rich）五层单向依赖。
"""

__version__ = "1.0.0"
