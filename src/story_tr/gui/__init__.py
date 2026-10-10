# -*- coding: utf-8 -*-
"""story-tr 的 PySide6 桌面界面（可选依赖 `.[gui]`）。

- `argv`：把表单值翻译成 CLI 参数（纯 Python，不依赖 PySide6）
- `runner`：把包内核心（`stories` / `sync` / `clean` 的 `main`）放到 `QThread` 里执行
- `app`：主窗口（翻译 / 抓取 / 清理 / 结果浏览 四个页签 + 实时日志）

GUI **复用包内核心**：直接调用各模块的 `main(args, on_progress=…, cancel=…)`，
而不是解析子进程输出，因此进度与取消都精确。
"""
