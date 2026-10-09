# MCP Server

> 返回 [README](../README.md) · 相关章节：[安装](../README.md#安装) · [命令（CLI）](../README.md#命令cli)

把项目接入支持 **MCP**（Model Context Protocol）的客户端（Trae / Claude Desktop / Cursor 等），让模型直接查询角色、故事与翻译结果，并可**触发**翻译/抓取等长任务。

## 安装与启动

```bash
# 安装可选依赖（官方 mcp SDK，需 Python ≥3.10）
pip install -e ".[mcp]"          # 已装好的话：pip install "mcp>=2.0,<3"

# 启动（stdio；通常由客户端的 MCP 配置自动拉起，无需手动运行）
story-tr-mcp                     # 等价：python -m story_tr.mcp
```

## 客户端配置

MCP 客户端的配置只是**声明怎么拉起 Server 进程**（不是装包）。`command` 建议写**绝对路径**；`env.STORY_TR_HOME` 指向含 `data/` 的目录。

**方式 A：指向可执行文件（最简）**

```json
{
  "mcpServers": {
    "story-tr": {
      "command": "D:/path/to/story-tr/.venv/Scripts/story-tr-mcp.exe",
      "env": { "STORY_TR_HOME": "D:/path/to/story-tr" }
    }
  }
}
```

**方式 B：用解释器 + `-m`（不依赖 PATH，最稳）**

```json
{
  "mcpServers": {
    "story-tr": {
      "command": "D:/path/to/story-tr/.venv/Scripts/python.exe",
      "args": ["-m", "story_tr.mcp"],
      "env": { "STORY_TR_HOME": "D:/path/to/story-tr" }
    }
  }
}
```

macOS / Linux 把路径换成 `.venv/bin/story-tr-mcp` 或 `.venv/bin/python` 即可。

| 字段 | 作用 |
| --- | --- |
| `command` | 启动的可执行文件（Windows 下正斜杠 `/` 可接受） |
| `args` | 传给它的参数（`story-tr-mcp` 无需参数；`python -m` 时为 `["-m", "story_tr.mcp"]`） |
| `env` | 附加环境变量；这里用 `STORY_TR_HOME` 指定**工作目录**（含 `data/` 的仓库根） |

工作目录与 CLI 一致：`$STORY_TR_HOME` → 向上查找 `data/character.json` → 当前目录。

> **关于 `npx` / `uvx`**：它们只是**免安装启动器** —— `npx` 运行 npm 包（Node），`uvx` 运行 PyPI 包（Python，需另装 `uv`）。本项目是**本地 Python 包**、未发布到 PyPI，故用上面的方式 A / B 即可。若已装 `uv`，也可直接从 git 跑（等价，二选一）：
>
> ```json
> { "mcpServers": { "story-tr": { "command": "uvx",
>   "args": ["--from", "story-tr[mcp] @ git+https://github.com/vers123/story-tr.git@v0.9.2", "story-tr-mcp"],
>   "env": { "STORY_TR_HOME": "D:/path/to/story-tr" } } } }
> ```

## 分发到其它机器

本包为纯 Python（`py3-none-any`），构建一个 wheel 即可在任意机器安装：

```bash
python -m build                                                # 产出 dist/*.whl（需 pip install -e ".[dev]"）
pip install "dist/story_tr-0.9.2-py3-none-any.whl[mcp]"         # 目标机：注意要带 [mcp]
```

也可以不传文件、直接从 git 装：

```bash
pip install "story-tr[mcp] @ git+https://github.com/vers123/story-tr.git@v0.9.2"
```

**离线（内网）机器**：联网机先连依赖一起下载，再把 `bundle/` 拷过去：

```bash
pip download "dist/story_tr-0.9.2-py3-none-any.whl[mcp]" -d bundle   # 联网机
pip install --no-index --find-links=bundle "story-tr[mcp]"           # 目标机
```

> **注意**：`mcp` 是可选依赖，wheel **不打包**它；extra 带 `python_version >= "3.10"` 标记，在 **Python 3.9** 上装 `[mcp]` 会**静默跳过**，此时 `story-tr-mcp` 会提示缺少 MCP 依赖。
> `data/` 不入库，目标机装完仍需 `story-tr fetch` 生成，或直接拷贝 `data/` 过去。

## Tools（只读检索）

| Tool | 说明 |
| --- | --- |
| `list_characters` | 全部角色：id / 中英名 / 故事数 |
| `list_stories(char_id)` | 某角色的故事：文件夹名 / 中英标题 / 段落数 |
| `get_result_summary(char_id, story)` | 翻译结果概要（标题 / 全文 / 各段的源文与最终译文） |
| `read_result_item(char_id, story, key)` | 单条明细：`key` 取 `full` / `title` / `seg_NN` |

## Tools（同步：联网 / 写盘）

| Tool | 说明 |
| --- | --- |
| `chain(text, …)` | 单条文本跑「来回翻译 N 次」链路（联网，**同步**返回逐步与最终译文） |
| `clean(char_ids, …)` | 清理 `result/`；**默认只预览**（`dry_run=true`），需 `dry_run=false` **且** `confirm=true` 才真删 |

## Tools（长任务：start / status 轮询）

| Tool | 说明 |
| --- | --- |
| `translate_start(char_ids, …)` | 启动批量翻译，**立即返回 `job_id`** |
| `fetch_start(update=true)` | 启动数据抓取（默认 `--update` 增量），返回 `job_id` |
| `job_status(job_id)` | 查询 `state`（queued/running/succeeded/failed/cancelled）/ `progress`（`done`/`total`）/ `returncode` / 日志尾行 |
| `job_cancel(job_id)` | 取消（排队中直接取消；运行中**终止子进程**） |
| `job_list` | 列出全部任务（新 → 旧） |

## Resources（内容）

| URI | 内容 |
| --- | --- |
| `story://characters` | 全部角色索引（JSON） |
| `story://character/{id}` | 角色 `meta.json` / `text.json` / 故事清单（JSON） |
| `story://story/{id}/{story}/{lang}` | 故事原文 Markdown（`lang` = `zh` / `en`，含 frontmatter） |
| `story://result/{id}/{story}` | 翻译结果概要 `segments.json`（JSON） |

> **长任务说明**：`translate` / `fetch` 由 MCP 以**子进程**调用既有 CLI（`python -m story_tr …`），
> 同一时刻**只跑一个写任务**（串行队列），避免多个任务争用 `.translate_stories_state.json`；
> 子进程输出写入 `logs/mcp_<job_id>.log`（`logs/` 已忽略）；job 表存在**内存**中，服务重启即丢列表，但底层断点仍在（重跑可续）。
> `translate` 只暴露安全参数（`char_ids` / `stories` / `mode` / `chain` / `workers` / `provider` / `full_only` / `segments_only`），
> **不含** `--overwrite` / `--reset`。
>
> 工具/资源报错会把**原因**透传给客户端（如「未知 job_id：…」「尚无翻译结果：…」），便于模型自我纠正。

## 代码位置

| 文件 | 作用 |
| --- | --- |
| `src/story_tr/mcp/server.py` | Tool / Resource 注册 + stdio 运行 |
| `src/story_tr/mcp/tools.py` | Tool 函数（不依赖 `mcp`） |
| `src/story_tr/mcp/resources.py` | Resource 函数（不依赖 `mcp`） |
| `src/story_tr/mcp/jobs.py` | 长任务：串行队列 / 进度 / 取消（不依赖 `mcp`） |
