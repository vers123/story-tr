# -*- coding: utf-8 -*-
"""story-tr 的 MCP Server 子包（**只读**）。

- `tools` / `resources`：纯 Python 的数据访问函数，**不依赖 mcp**，可在任意受支持的
  Python 版本下导入与测试；
- `server`：把上述函数注册成 MCP Tool / Resource，并通过 stdio 运行（需要可选依赖 mcp）。

注意：本 `__init__` 刻意**不导入** `server`，以便未安装 mcp 的环境仍能导入 `tools` / `resources`。
"""
