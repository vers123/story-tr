# 变更记录

版本号遵循 [Semantic Versioning 2.0.0](https://semver.org/lang/zh-CN/)；递增规则与「初始开发阶段（`0.y.z`）」说明见 [README · 版本与变更](README.md#版本与变更)。

## 0.15.0（2026-10-18）

- **改进**：视频版式
  - 去掉半透明黑色底板与背景压暗 —— 截图完整露出，不再被文字块遮挡
  - 文字改为「白色原文 / 暖黄译文 + 黑色描边」；超长仍自动缩放（54→24 px）并截断
  - logo 自动裁掉透明留白；**图标垂直中心与划线对齐**，划线从图标右缘延伸到右侧安全区，原文在线上、译文在线下
- **新增**：TTS 音轨（默认用 gTTS 朗读**译文**）—— 此前只有静音 AAC 轨，听起来「没有声音」
  - 每段时长 = `max(--duration, 语音时长 + 0.4s)`，音轨按段补齐/裁剪后拼接，音画对齐
  - 语音缓存 `result/video/audio/<story>/seg_NN.mp3`，重复生成时复用
  - 新增 `--no-tts` / `--no-audio` / `--refresh-audio` / `--proxy`（gTTS 需能访问 `translate.google.com`；单段失败退回静音并告警，不中断整条视频）
- **工程**：新增版式（无底板、背景不压暗、图标对齐划线）与音频（缓存复用、静音兜底、音轨时长）测试

## 0.14.0（2026-10-11）

- **新增**：`story-tr video` —— 把已有翻译结果渲染成「谷歌翻译 N 次」风格的视频，按 **B 站投稿规格**输出（1920×1080 / H.264 High / yuv420p / 30 fps / 10 Mbps / GOP 2s / MP4 `faststart` / 静音 AAC 320 kbps 48 kHz 立体声）
  - 每个故事一条 `<story>.mp4`（每段一屏、段间硬切），再把各故事按剧情顺序**无损拼接**为 `<char_id>.mp4`
  - 版式：截图背景（压暗 25%）+ 左下角 logo（`assets/translate-logo.webp`，120×120）+ 右下角「原文 / 译文」两个文本块（白色 / 暖黄）；自动换行 + 自动缩放（54→24 px，仍放不下则截断并加 `…`），并做简化的中文**避头尾**（标点不出现在行首）
  - **只读**翻译结果：不读写 `.translate_stories_state.json`、不改动任何 `seg_*.json` / mp3 —— 不影响仍在进行的翻译进度
  - 完整性校验（缺段 / `source` 与 `zh.md` 不一致 / 译文为空）：默认**跳过该故事并告警**；`--allow-partial`、`--strict` 可覆盖；总视频只合并已完成的故事
  - 已有 mp4 默认跳过，`--overwrite` 重新生成；支持 `--only story|total|both`、`--duration`、`--fps`、`--logo`、`--no-audio`
  - 可选依赖 `.[video]`（Pillow，HPND 宽松许可）；编码调用系统 **ffmpeg**（需在 `PATH`）
- **文档**：README 新增「`story-tr video`」一节、安装说明与依赖表；目录结构补 `video.py` / `assets/`
- **工程**：新增 `tests/test_video.py`（故事顺序、完整性校验、避头尾、自动缩放/截断、端到端编码）

## 0.13.2（2026-10-11）

- **修复**：整段只有 `……` 的**纯符号段落**会让 `langdetect` 抛 `LangDetectException: No features in text.`，**整批翻译直接中断**（GUI 表现为「退出码 -1 / 异常（见日志）」，CLI 表现为异常退出）。全库共 **83 处**这种段落（如 `10000020 / vision` 的第 9 段）
  - `chain.has_features()`：判断文本是否含「文字」（Unicode 字母 / 汉字）—— 这也是 langdetect 判语言的特征来源
  - `chain.detect_language()`：无特征文本直接返回 `en`（既然无从判定，也就不需要「先译成英文」这一步），并兜住偶发的 `LangDetectException`
  - `stories.run_chain_many()`：无文字内容的文本**不参与链路、不发任何请求**，直接**原样保留**（json 里标 `passthrough: true`、`source_detected_lang: "none"`、`steps: []`）；这类段落也**不再生成语音**（没有可朗读的内容）
- **工程**：新增回归测试（`has_features`、纯符号文本不崩、单批内「纯符号 + 正常文本」混排时顺序与内容正确）；对全库 **974** 份 `zh.md` 复扫，语言判定失败数 **0**

## 0.13.1（2026-10-11）

- **修复**：高显示缩放或小屏幕上，GUI 窗口被内容顶破 —— Windows 报 `QWindowsWindow::setGeometry: Unable to set geometry`，窗口最小高度超出屏幕可用高度，底部进度条 / 状态栏可能被挤出屏幕。翻译 / 抓取 / 清理三个页签改为**自身可滚动**（`QScrollArea`，`widgetResizable=True`），窗口最小高度由 **864 → 422 px**，内容超出时出现滚动条；结果浏览页本就是可伸缩的 `QSplitter`，无需包裹
- **工程**：新增回归测试 `test_task_tabs_are_scrollable`（断言三个页签是 `QScrollArea` 且最小高度不再随表单行数增长）

## 0.13.0（2026-10-11）

界面布局与交互重整（P0–P2），并修掉两个会导致任务直接失败的 GUI 缺陷。

- **修复**：GUI「只出全文 / 只出段落」原为 `QCheckBox`，**可同时勾选** → 生成互斥参数，真实解析器直接拒绝。改为 `QRadioButton`（三选一：都要 / 只出全文 / 只出段落），并加入参数一致性护栏
- **修复**：退出码 `3`（批量状态与上次不一致）在 GUI 里**没有任何出路** —— 核心提示「请加 `--reset`」，但界面从未暴露该开关。现在工具条会出现「**重置批次并重跑**」按钮，一键等价于 CLI 加 `--reset`
- **改进**：退出码 `3` 的报错不再直接甩原始签名 JSON，改为**指出变化的那几项**（`模式：上次 fixed → 本次 random` 等），CLI 与 GUI 同时受益（`stories.describe_signature_diff`）
- **改进**：任务结束后进度条**不再归零**，状态行追加「退出码 N（含义）」；窗口底部状态栏显示退出码与**用时**（`退出码 0（完成） · 用时 3m21s`）
- **改进**：**窗口级工具条** —— 「开始 / 停止 / 任务状态」提到窗口顶部，切换页签不再丢失状态；清理页与结果浏览页自动禁用「开始」并给出原因提示
- **改进**：翻译页参数按「**基本 / 输出内容 / 速率与后端**」分为三组；新增**模式联动置灰**（`fixed` 只启用链、`custom` 只启用自定义语言、`random` 只启用随机步数），并补上此前缺失的「随机步数」控件
- **改进**：结果浏览的**角色列表支持搜索**（id / 中文名 / 英文名）
- **改进**：日志面板支持**折叠**（收起为一行标题）、**按关键词查找**（`查找` / 回车，到底自动回卷）与**按级别着色**（ERROR / WARN / STEP / DEBUG）
- **新增**：GUI 用 `QSettings` **记住上次参数**（模式 / 链 / 后端 / 并发 / 间隔 / 各开关 / 页签 / 日志展开状态）；**不含**角色 id 与故事，避免误重跑上次目标
- **文档**：README 澄清许可表述 —— 核心依赖仍不引入 GPL / LGPL；**可选** GUI 依赖 `PySide6`（LGPL-3.0）以动态链接、可替换方式使用，不与本项目 MIT 许可冲突
- **工程**：GUI 测试补充互斥单选、模式联动、`--reset`、完成态保留、日志折叠/查找、`QSettings` 往返等用例；测试将 `QSettings` 重定向到临时 ini，避免污染真实用户配置

## 0.12.0（2026-10-09）

- **修复**：`story-tr clean` 不认 `-q` / `--verbose`，而 GUI 的「显示明细」会把它们传下去 —— 勾选后任务直接以「参数不合法」失败。现在 `clean` 也支持这两个开关（`-q` 只显示错误与最终摘要；`--verbose` 显示目录的完整相对路径）
- **修复**：GUI 日志面板只看得到 `logging`、看不到 `print` —— `fetch` 的「待抓取 / 校验报告」与 `clean` 的清理清单**完全不可见**（界面却提示「会写入下方日志面板」）。改为运行时接管 `sys.stdout` / `sys.stderr` 转发到面板，`logging` 与 `print` **两者都收**；接管期间 `report.interactive()` 为假，**进度条自动禁用**，面板不会出现 `\r` 重绘
- **修复**：GUI 后台任务抛异常（如 `mode=custom` 缺 `--languages` 触发的 `SystemExit`）时不会发出结束信号 → 界面**卡在「忙碌」**。`TaskRunner` 现在捕获 `BaseException` 并发 `error` 信号，面板显示「任务异常：…」后正常收尾
- **新增**：MCP `translate_start` 暴露 **`languages`** 参数 —— 此前 `mode="custom"` 无对应入参，跑起来必失败；缺参时现在直接给出明确错误
- **变更**：`report.setup_console` 只替换**自己管理的** handler（控制台 / 文件），不再清空调用方挂上的 handler
- **工程**：新增参数一致性护栏 `tests/test_argv_consistency.py` —— GUI 与 MCP 这两条「不经 `argparse` 生成命令行」的路径，其产出必须能被**真实解析器**接受（本次两个 argv 类 bug 都能被它拦住）

## 0.11.2（2026-10-09）

- **修复**：GUI「最小间隔」用的是**整数** `QSpinBox` 且默认 `0` —— 既填不出推荐的 `0.3`，又把 CLI 默认的 `0.3` 覆盖成「关闭节流」（易触发 429）。现改为 `QDoubleSpinBox`（步长 `0.1`、默认取 `DEFAULT_MIN_INTERVAL`），并加回归测试锁定「GUI 默认 == CLI 默认」
- **新增**：GUI 各控件补充**推荐值提示**（`setToolTip`），覆盖模式/链、后端、并发线程、最小间隔、只出全文/段落、重跑、语音、明细
- **文档**：README 的 GUI 一节新增「推荐取值」表

## 0.11.1（2026-10-09）

- **工程**：CI 新增 **`gui` job** —— 只在最新的 Python（**3.13**）上安装 `[dev,gui]`，并以 `QT_QPA_PLATFORM=offscreen` **离屏运行** GUI 测试（PySide6 约 200MB，不铺满矩阵）；顺带装上 Qt 离屏所需的系统库（`libegl1` / `libxkbcommon0` / `libdbus-1-3` / `libfontconfig1`）
- **文档**：开发与测试一节改为按 job 说明 CI 分层（`test` 跑 3.9/3.11/3.13，`gui` 跑 3.13）；目录结构里的 `ci.yml` 注释同步

## 0.11.0（2026-10-09）

- **新增**：**PySide6 桌面界面**（可选依赖 `.[gui]`，命令 `story-tr-gui` / `python -m story_tr.gui`）
  - 页签：**翻译 / 抓取 / 清理 / 结果浏览** + 实时日志面板（清空 / 保存为文件）
  - **复用包内核心**：直接调用 `stories.main` / `sync.main` / `clean.main`，参数由**真实 CLI 解析器**构造（`gui.argv.parse`），与命令行语义一致、不随 `argparse` 漂移
  - 进度来自核心 `on_progress(dict)` 回调；「停止」置位 `cancel` 后核心在**任务边界保存进度**并以退出码 `6` 返回（不硬杀线程，避免半个结果文件）
  - 日志直接挂 `translate_chain` logger，与 CLI **同源**；每次任务在面板打印**等效命令行**
  - `gui.argv` / `gui.runner` 分层：前者不依赖 PySide6，可在任意环境测试
- **新增**：核心可复用接口 —— `stories.main(args, on_progress, cancel)`、`sync.main(...)`、`sync.fetch_all(..., on_progress, cancel)`；新增 `chain.Cancelled`（退出码 `6`）
- **变更**：`report.setup_console` 只清理**自己管理的** handler（控制台 / 文件），保留外部 handler（GUI 日志面板）
- **变更**：`translate` 的收尾与异常提示由 `print` 改为日志（控制台输出不变，GUI 日志面板也能看到）
- **工程**：新增 GUI 测试（`test_gui_argv.py` 不依赖 PySide6；`test_gui_app.py` 离屏构造窗口，未装 PySide6 时自动跳过 —— CI 覆盖见 0.11.1）

## 0.10.0（2026-10-09）

- **新增**：**控制台输出层** `report.py` —— 明细「上滚」、`tqdm` 进度条**固定在底部**
  - `TqdmHandler`：logging 记录经 `tqdm.write` 输出（清条 → 写日志 → 重绘），不再被进度条冲乱；原先直接 `print` 的告警（`fetch` 抓取失败）与重命名行（`folders`）也改走同一通道
  - `report.emit()`：非 logging 的明细行统一走它；`report.bar_kwargs()` 统一进度条参数
  - `STEP`（自定义级别 15，介于 `DEBUG` 与 `INFO` 之间）：用于「每次翻译步骤」这类高频明细，文件日志全量记录、控制台默认不显示
- **新增**：`-q/--quiet` 与 `--verbose`（互斥），四个长任务命令都支持
  - 默认：分组行 + **每份结果完成一行**（`[id]  故事/键  字数  后端  耗时`）+ 告警
  - `--verbose`：再加每步明细与「跳过（已有结果）」行（`chain` 默认即显示每步明细）
  - `-q`：只显示错误与最终摘要
- **新增**：`translate` 每份结果完成一行、每个批量组 `>>> …` / `<<< …（耗时）` 两行；`fetch` 每个角色一行 `[id] 已抓取  en N 故事 / chs N 故事`
- **变更**：`translate` / `chain` 控制台不再被压到 `WARNING`（此前为躲进度条而几乎无输出）；改为按 `-q/--verbose` 分级
- **变更**：非交互终端（重定向 / 管道，如 MCP 子进程）**自动禁用进度条**并改用普通行输出，避免 `\r` 控制序列写进日志文件
- **变更**：`sync.build()` 的参数 `report=` 改名为 `info=`（原名会遮蔽同名的 `report` 模块）
- **兼容**：**不改状态文件与跳过逻辑** —— `.translate_stories_state.json` 格式、签名与「已有结果跳过」判定完全不变，现有进度与结果可无缝续跑

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
