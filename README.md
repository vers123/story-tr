# story-tr

原神角色故事双语（中 / 英）文本抓取 + 「来回翻译 N 次」链路工具。

![version](https://img.shields.io/badge/version-2.0.0-blue)
![python](https://img.shields.io/badge/python-3.10%20%7C%203.14-blue)
![license](https://img.shields.io/badge/license-MIT-green)
![CI](https://img.shields.io/github/actions/workflow/status/vers123/story-tr/ci.yml?branch=main&label=CI)

> **免责声明**：本项目仅用于学习与技术研究，不包含、不分发任何游戏资源或数据；与米哈游（miHoYo / HoYoverse）及其关联公司无任何关系。
>
> **来源与版权**：角色与文本取自 **Project Amber**（[yatta.moe](https://gi.yatta.moe/)）的公开 API，**仅供学习 / 研究用途**；角色、文本等版权归米哈游 / Project Amber 所有。

## 5 分钟上手

```bash
git clone https://github.com/vers123/story-tr.git
cd story-tr

python -m venv .venv
.venv\Scripts\activate # macOS/Linux: source .venv/bin/activate
pip install -e . # 最低 Python 3.10

story-tr fetch # 1) 抓取角色数据 → data/
story-tr translate 10000021 # 2) 来回翻译 N 次 → result/
```

- 只要**数据**：做到 `story-tr fetch` 即可。
- 只要**命令**、不想管仓库：`pipx install git+https://github.com/vers123/story-tr`。
- 想换后端 / 配密钥：`copy .env.example .env`（macOS/Linux: `cp`），详见 [docs/backends.md](docs/backends.md)。
- 想离线跑：见 [docs/rwkv.md](docs/rwkv.md)。

## 命令速查

| 命令 | 用途 | 文档 |
| --- | --- | --- |
| `story-tr fetch` | 抓取 / 更新角色数据与故事 Markdown（`data/`） | [docs/usage.md](docs/usage.md#story-tr-fetch) |
| `story-tr folders` | 按 `character.json` 同步重命名故事文件夹（幂等） | [docs/usage.md](docs/usage.md#story-tr-folders) |
| `story-tr translate` | 批量翻译角色故事（来回翻译 N 次） | [docs/usage.md](docs/usage.md#story-tr-translate) |
| `story-tr chain` | 对单条文本执行链路翻译 | [docs/usage.md](docs/usage.md#story-tr-chain) |
| `story-tr clean` | 清理 `result/` 下的翻译产物 | [docs/usage.md](docs/usage.md#story-tr-clean) |
| `story-tr status` | 只读报告数据覆盖 / 结果数量 / 进度 | [docs/usage.md](docs/usage.md#story-tr-status) |
| `story-tr doctor` | 只读检查运行环境与配置 | [docs/usage.md](docs/usage.md#story-tr-doctor) |
| `story-tr eval` | 采样段落评测本地 RWKV 模板质量 | [docs/rwkv.md](docs/rwkv.md#eval-评测) |

> 所有命令都支持全局参数 `--data-dir <目录>`；`-v` / `-q` 控制日志详略。退出码含义见 [docs/usage.md](docs/usage.md#退出码)。

## 文档

| 文档 | 内容 |
| --- | --- |
| [docs/README.md](docs/README.md) | 文档索引与推荐阅读顺序 |
| [docs/usage.md](docs/usage.md) | 安装、快速开始、8 个子命令与参数表、退出码 |
| [docs/architecture.md](docs/architecture.md) | 五层架构、模块拆分、结构守护、目录树 |
| [docs/data-contract.md](docs/data-contract.md) | `data/` 结构与各 JSON / Markdown 字段契约 |
| [docs/backends.md](docs/backends.md) | 翻译后端、回退策略、代理与 TTS |
| [docs/rwkv.md](docs/rwkv.md) | 本地 RWKV（`--mode rwkv20`）与 `eval` 评测 |
| [docs/development.md](docs/development.md) | 开发环境、本地校验、Breaking changes、发布流程 |

## 依赖与许可

本项目以 **MIT** 许可发布，见 [LICENSE](LICENSE)。核心依赖（以 `pyproject.toml` 的 `dependencies` 为准）：

| 依赖 | 用途 | 许可 |
| --- | --- | --- |
| [typer](https://github.com/fastapi/typer) | CLI | MIT |
| [httpx](https://github.com/encode/httpx) | HTTP 请求 | BSD-3-Clause |
| [pydantic](https://github.com/pydantic/pydantic)（v2） | 数据模型 | MIT |
| [pydantic-settings](https://github.com/pydantic/pydantic-settings) | 配置（`.env` / 环境变量） | MIT |
| [rich](https://github.com/Textualize/rich) | 进度条 / 表格 / 日志 | MIT |
| [tenacity](https://github.com/jd/tenacity) | 重试 / 退避 | Apache-2.0 |
| [langdetect](https://github.com/Mimino666/langdetect) | 源语言判定 | Apache-2.0 |

- 安装方式（venv / pipx / 可选后端密钥）见 [docs/usage.md](docs/usage.md#安装)；开发与测试依赖（`.[dev]`）见 [docs/development.md](docs/development.md#开发环境)。
- 版本遵循 [Semantic Versioning 2.0.0](https://semver.org/lang/zh-CN/)，变更见 [CHANGELOG.md](CHANGELOG.md)。
