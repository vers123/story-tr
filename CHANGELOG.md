# 变更记录

版本号遵循 [Semantic Versioning 2.0.0](https://semver.org/lang/zh-CN/)；递增规则与「初始开发阶段（`0.y.z`）」说明见 [README · 版本与变更](README.md#版本与变更)。

## 0.9.2（2026-10-09）

- **文档**：拆分文档结构（**内容不变**，仅搬家）
  - 「变更记录」移入本文件 `CHANGELOG.md`
  - 「MCP Server」章节移入 [docs/mcp.md](docs/mcp.md)，README 只留摘要与链接
  - README 保留 `## MCP Server` 与 `### 变更记录` 标题，**旧锚点（`#mcp-server` / `#版本与变更`）不失效**

## 0.9.1（2026-10-09）

- **文档**：MCP 章节补成完整版
  - 新增「客户端配置」：方式 A（可执行文件绝对路径）/ 方式 B（解释器 + `python -m story_tr.mcp`），含 `command` / `args` / `env` 字段说明
  - 说明 `npx` / `uvx` 只是**免安装启动器**（分别对应 npm / PyPI 包），本项目为本地 Python 包未发布 PyPI；附 `uvx` + git 的可选写法
  - 新增「分发到其它机器」：`python -m build` 出 wheel、`pip install "…whl[mcp]"`、git 直装、离线 `pip download` + `--no-index` 安装
  - 目录结构补充 `dist/` / `build/` 与 MCP 子进程日志路径

## 0.9.0（2026-10-09）

- **新增**：**MCP 写操作与长任务**（在 0.8.0 只读版基础上补齐）
  - 同步：`chain`（单条文本翻译链，联网）、`clean`（**默认只预览**，需 `dry_run=false` 且 `confirm=true` 才真删）
  - 长任务：`translate_start` / `fetch_start`（返回 `job_id`）+ `job_status` / `job_cancel` / `job_list`
  - 执行方式：以**子进程**调用既有 CLI（`python -m story_tr …`）；**串行队列**（同一时刻只跑一个写任务）；支持**取消**（终止子进程）
  - `translate` 进度 = 预算任务总数 + 读 `.translate_stories_state.json` 的已完成数；job 表存**内存**，子进程输出写入 `logs/mcp_<job_id>.log`
  - `translate` 只暴露安全参数，**不含** `--overwrite` / `--reset`
  - 工具/资源报错改为把**原因**透传给客户端（`ToolError` / `ResourceError`），原先只显示 "Error executing tool …"

## 0.8.0（2026-10-09）

- **新增**：**MCP Server（只读）** —— 新增子包 `story_tr/mcp/` 与命令 `story-tr-mcp`（等价 `python -m story_tr.mcp`），通过 **stdio** 把角色故事与翻译结果暴露给 MCP 客户端
  - Tools：`list_characters` / `list_stories` / `get_result_summary` / `read_result_item`
  - Resources：`story://characters`、`story://character/{id}`、`story://story/{id}/{story}/{lang}`、`story://result/{id}/{story}`
  - 只读：不联网、不写盘；`translate` / `fetch` / `clean` 等未暴露
  - 基于官方 `mcp` SDK（v2 `MCPServer`），作为**可选依赖** `.[mcp]`（需 Python ≥3.10）；未安装不影响其余功能，主包仍支持 ≥3.9

## 0.7.0（2026-10-09）

- **新增**：**标题也翻译** —— `zh.md` frontmatter 的 `title`（如「角色详细」）**始终**跑一遍完整链路，输出 `result/{story}/title.json|mp3`，并在汇总 `segments.json` 中新增 `title` 字段（含 `source` / `languages` / `final` / `json` / `mp3`）
  - 该任务**不受** `--full-only` / `--segments-only` 影响；批量开启时与全文、各分段**合并进同一请求**

## 0.6.2（2026-10-09）

- **修复**：批量请求模式下，google 免 key 通道的**连接类异常**（超时 / DNS / SSL 等）未归一化为 `ConnectionIssue`，会以原始 `requests` 异常穿透重试与后端切换逻辑，导致 CLI 打印 traceback 崩溃；现改为归一化处理，**自动切换到下一后端**（单条路径原本已正确处理，仅批量分支遗漏）

## 0.6.1（2026-10-09）

- **文档**：修正 0.6.0 关于批量的数字与表述 —— 全量批量约 **2.7 万**次调用（≈1.9h）；批量开启时 `--full-only` / `--segments-only` 只省约 **20%**（≈1.9h → ≈1.5h），主要价值是减少产物文件与磁盘占用

## 0.6.0（2026-10-09）

- **新增**：**批量请求**（默认开启）—— 同一故事的全文与各分段在每一语言步合并为**一次请求**（`clients5` 支持重复 `q`，响应与 `q` 一一对应）
  - 实测同一故事逐条 `29s` → 批量 `7s`；全量调用量 30 万 → **2.7 万**（≈11×，21h → ≈1.9h）
  - `fixed`/`custom` 下与逐条**结果完全一致**（6 组对照零差异）；形状异常/通道不支持时**自动退化为逐条**
  - `random` 模式自动关闭批量；`--no-batch` 可显式关闭
- **新增**：`--full-only` / `--segments-only` —— 只产出全文或只产出逐段
- **变更**：`chain.py` 抽出 `_step_loop` / `_translate_with_providers`，单条与批量共用同一套「限流退避 + 业务重试 + 后端切换」逻辑

## 0.5.1（2026-10-09）

- **修复**：`story-tr clean` 清理后未重置批次签名，导致紧接着 `translate` 报「参数与上次批量任务不一致」并要求 `--reset`
  - `clean` 现在移除进度记录的同时**重置签名**；`translate` 遇到无签名状态时**沿用剩余记录**并采用新签名（不再要求 `--reset`）

## 0.5.0（2026-10-09）

- **新增**：`story-tr clean <id/范围>` —— 清理 `{id}/result/` 下的翻译产物（保留 `result/` 目录本身）
  - 角色选择与 `translate` 一致（id / 范围 / `--all-characters`），`--stories` 可只清指定故事
  - 默认交互确认（列出目录 / 文件数 / 占用）；`--dry-run` 预览、`-y/--yes` 跳过
  - 默认**同步清理** `.translate_stories_state.json` 中对应 `id|story|key` 记录（`--keep-state` 保留），避免重跑被跳过

## 0.4.2（2026-10-09）

- **文档**：移除安装一节中的「什么时候需要重新安装」提示，保持安装说明简洁
- 纯文档变更：无代码行为变化（PATCH）

## 0.4.1（2026-10-09）

- **文档**：安装一节补充「[什么时候需要重新安装](README.md#安装)」—— editable 改代码免重装，改 `pyproject.toml` 的依赖/入口点/版本需重装
- **文档**：快速开始补充数据更新命令 `story-tr fetch --update`；`_fetch_all.json` 说明补充「内容指纹」
- **工程**：`.gitignore` 补充覆盖率、类型检查缓存与系统临时文件
- 纯文档与配置变更：无代码行为变化（PATCH）

## 0.4.0（2026-10-09）

- **新增**：`story-tr fetch --update` —— **更新数据**：从站点角色列表发现新角色（`{id}-{element}` 归一）、按内容指纹检测已有角色变化、**只重写变化项**并输出变更报告
- **新增**：抓取记录带**内容指纹 `hash`**（存于 `data/_fetch_all.json`），旧缓存首次运行会建立基线
- **新增**：`data/character.json` 同步更新 —— 新角色自动追加、站点新增的故事自动追加到 `stories`
- **修复**：本地无 `data/character.json` 时（全新克隆）自动按站点列表初始化，不再直接报错

## 0.3.1（2026-10-09）

- **文档**：安装改为以 **venv** 为主、**pipx** 为「只要命令」的推荐方式，补上激活与「venv 下命令不在 PATH」的提示
- 纯文档变更：无代码、数据格式或 CLI 行为变化（PATCH）

## 0.3.0（2026-10-09）

- **新增**：打包为**可安装 CLI**（`pyproject.toml` + `src/story_tr/`），提供 `story-tr fetch` / `folders` / `chain` / `translate` 四个子命令；等价入口 `python -m story_tr`、`python main.py`
- **新增**：**工作目录可配置** —— `--data-dir` → `$STORY_TR_HOME` → 向上查找 `data/character.json` → 当前目录
- **新增**：`tests/`（pytest）与 GitHub Actions CI（ruff + pytest，Python 3.9 / 3.11 / 3.13）
- **变更**：原 `scripts/*.py` 改为**兼容 shim**（旧命令仍可用），实现移入 `story_tr` 包
- **变更**：依赖以 `pyproject.toml` 为准，`requirements.txt` 保留为便捷镜像

## 0.2.0（2026-10-09）

- **新增**：`translate_stories.py` 支持按 **id / 范围** 选角色（`10000002-10000030`、`10000021,10000025-10000030`）
- **新增**：`--workers N` 并发翻译（实测同批 59.3s → 34.2s，约 1.7×）
- **新增**：`--balance` 多后端轮转分摊请求（默认关闭）
- **修复**：超长文本按 ≤1500 字自动分块 —— 此前长故事在 Google 免费端点报 400，会重试 5 次后 `Paused` **中断整轮**
- **修复**：`langdetect` 并发下抛 `Need to load profiles`（改为首次加锁预热）
- **变更**：CLI 统一（两脚本共用参数分组）；`--stories` 不指定时默认处理**全部含内容故事**（原默认 `amber_journal`）

## 0.1.1（2026-10-09）

- **文档**：重组 README —— 新增「[快速开始](README.md#快速开始)」「[目录](README.md#目录)」，调整章节顺序
- **文档**：补充「[版本与变更](README.md#版本与变更)」（SemVer 2.0.0 规则与变更记录）
- 纯文档变更：无代码、数据格式或 CLI 行为变化（PATCH）

## 0.1.0（2026-10-09）

首个版本。

- **数据**：`sync_from_site.py` 抓取/校正全部角色（Project Amber 公开 API），生成 `profile/meta.json`、`profile/text.json`、故事 `en.md`+`zh.md`、`data/character.json`
- **数据**：`sync_story_folders.py` 按 `character.json` 幂等同步故事文件夹名
- **翻译**：`translate_chain.py` 多后端（Google / Bing / Baidu）「来回翻译 N 次」链路，含限流退避、请求节流、断点续跑、可复现语言判定
- **翻译**：`translate_stories.py` 批量跑故事链路，输出分段 + 全文 + 语音（gTTS）
- **工程**：`data/` 不入库（可由脚本重建）、MIT [LICENSE](LICENSE)、依赖清单、README
