# 架构

代码按 **五层** 组织，依赖方向单向：**cli → services → transport + core**（`transport` / `core` 不反向依赖 `cli` / `services`；`models` 被各层引用）。

## 分层

| 层 | 模块 | 职责 |
| --- | --- | --- |
| `models` | `src/story_tr/models/` | pydantic v2 数据契约：角色 / 故事 / 翻译结果 / 状态 |
| `core` | `src/story_tr/core/` | 纯逻辑：语言判定与代码映射、链路线与任务构建、分段 / 分块 / 清洗、状态签名、评测指标；**无网络 / 文件 / 框架依赖** |
| `transport` | `src/story_tr/transport/` | 统一 `Translator` 协议 + `google` / `bing` / `baidu` / `rwkv` 实现 + 可选 TTS，全部走 `httpx` |
| `services` | `src/story_tr/services/` | 编排层：`fetch` / `folders` / `translate` / `chain` / `clean` / `status` / `doctor` / `eval`，每个模块暴露 `run(...) -> ExitCode`，把 core 与 transport 组装成用例 |
| `cli` | `src/story_tr/cli/` | `typer` 参数解析 + `rich` 渲染（进度条、表格、日志），只做参数与输出，不实现业务逻辑 |

## services 内部拆分

`services` 内部再按职责拆分，避免单文件膨胀：

| 模块 | 职责 |
| --- | --- |
| `storage.py` | 工作目录 / 路径助手、原子写（含 `.bak` 与跳过未变化）、后端解析 |
| `story_inputs.py` | 角色 id / 范围解析、故事 Markdown 读取与发现 |
| `chain_core.py` | 翻译链共享语义：签名、后端轮换、单步翻译、结果合并 |
| `fetch_pure.py` / `fetch_write.py` | 抓取纯函数（slug / 指纹 / 差异）与落盘（故事 md、档案 JSON、索引） |
| `status_scan.py` | `result/` 单次遍历统计（文件计数、覆盖、语言分布） |
| `translate/` | 批量翻译包：`tasks`（任务）、`chain_runner`（单份执行）、`runner`（批次调度）、`tts` / `tts_only`（语音）、`summary`（汇总）、`errors`（控制流异常） |

## cli 内部拆分

- `app.py`：`typer` 应用本体，只保留全局参数（`--data-dir` / `-v` / `-q` / `--version`）、异常兜底与子命令注册。
- `ui.py`：共用渲染与日志（`rich` 控制台、`StoryTrTyper`、`setup_logging`）。
- `commands/<命令>.py`：每个子命令一个模块（`fetch` / `folders` / `translate` / `chain` / `clean` / `status` / `doctor` / `eval`）。

新增一个翻译后端只需实现 `transport.Translator` 协议并注册，无需改动 core / services / cli。

## 结构守护

结构约束由 [../tests/test_structure.py](../tests/test_structure.py) 自动守护，重构后结构不会悄悄腐化：

- **单函数 ≤ 60 行**（`MAX_FUNCTION_LINES = 60`），超长函数会列出文件与行号。
- **分层单向依赖**：`models → core → transport → services → cli`，禁止反向 import。
- **禁止跨模块引用 `_` 私有名字**：生产代码不得 `from story_tr.x import _y`。

## 目录结构

```
story-tr/
├── src/story_tr/ # 生产代码（五层）
│ ├── __init__.py # __version__
│ ├── __main__.py # python -m story_tr 入口
│ ├── config.py # pydantic-settings 配置（.env / 环境变量）
│ ├── errors.py # StoryTrError 层级 + ExitCode
│ ├── models/ # 数据契约
│ │ ├── character.py
│ │ ├── state.py
│ │ └── translation.py
│ ├── core/ # 纯逻辑
│ │ ├── lang.py
│ │ ├── metrics.py
│ │ ├── routes.py
│ │ ├── segment.py
│ │ └── state.py
│ ├── transport/ # 后端抽象与实现
│ │ ├── base.py
│ │ ├── google.py
│ │ ├── bing.py
│ │ ├── baidu.py
│ │ ├── rwkv.py
│ │ └── tts.py
│ ├── services/ # 用例编排
│ │ ├── storage.py
│ │ ├── story_inputs.py
│ │ ├── chain_core.py
│ │ ├── fetch_pure.py
│ │ ├── fetch_write.py
│ │ ├── status_scan.py
│ │ ├── fetch.py
│ │ ├── folders.py
│ │ ├── chain.py
│ │ ├── clean.py
│ │ ├── status.py
│ │ ├── doctor.py
│ │ ├── eval.py
│ │ └── translate/ # 批量翻译包
│ │ ├── tasks.py
│ │ ├── chain_runner.py
│ │ ├── runner.py
│ │ ├── tts.py
│ │ ├── tts_only.py
│ │ ├── summary.py
│ │ └── errors.py
│ └── cli/
│ ├── app.py
│ ├── ui.py
│ └── commands/
│ ├── fetch.py
│ ├── folders.py
│ ├── translate.py
│ ├── chain.py
│ ├── clean.py
│ ├── status.py
│ ├── doctor.py
│ └── eval.py
├── tests/ # pytest：与被测模块一对一
├── docs/ # 文档（本目录）
├── .github/workflows/ # ci.yml（质量 / 测试 / 构建）与 release.yml（tag 发布）
├── scripts/ # 一键校验（verify.ps1 / verify.sh）
├── pyproject.toml # 打包、依赖、ruff / mypy / pytest 配置
├── CHANGELOG.md
├── LICENSE
└── README.md
```

> 各包内另有 `__init__.py`（已略）；`data/`、`logs/`、`out/` 为运行时目录，不入库（契约见 [data-contract.md](data-contract.md)）。
