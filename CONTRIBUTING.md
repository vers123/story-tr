# 贡献指南

感谢你关注 story-tr。本文说明如何搭建开发环境、运行本地质量检查、遵守代码结构约定，以及如何提交改动与文档。

建议先阅读：

- `README.md`：项目定位、安装与 CLI 用法
- `docs/usage.md`、`docs/architecture.md`、`docs/development.md`：使用、架构与开发细节
- `CHANGELOG.md`：按 Keep a Changelog 组织的行为变更记录
- `SECURITY.md`：漏洞私密上报流程

## 开发环境搭建

### 前置要求

- Python ≥ 3.10（CI 覆盖 3.10–3.14）
- Windows 或 POSIX（Linux / macOS）
- Git

### 创建虚拟环境并安装依赖

Windows（PowerShell）：

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -e .[dev]
```

POSIX（bash / zsh）：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
```

安装完成后命令入口为 `story-tr`，等价入口为 `python -m story_tr`；开发依赖定义在 `pyproject.toml` 的 `[project.optional-dependencies].dev`。

## 本地质量检查

提交前请保证以下四条命令全部通过：

```bash
ruff check .
ruff format --check .
mypy src/story_tr
pytest -q --cov=story_tr --cov-report=term-missing
```

覆盖率门槛为 85%（当前约 95%），低于门槛会失败。

也可以直接运行一键脚本：

- Windows：`scripts/verify.ps1`
- POSIX：`scripts/verify.sh`

建议安装预提交钩子，让 `ruff` 与 `ruff format` 在提交前自动执行（钩子定义见 `.pre-commit-config.yaml`，包含 ruff v0.17.4、ruff-format 与基础检查）：

```bash
pre-commit install
```

## 代码组织与新增功能指引

生产代码位于 `src/story_tr/`，分为五层，只允许从上往下单向依赖：`models → core → transport → services → cli`。

| 层 | 职责 |
| --- | --- |
| `models` | pydantic 数据模型：角色、文本、翻译结果、状态文件等纯数据结构 |
| `core` | 不依赖外界的纯逻辑：语言判定、分段、指标、路由、状态机 |
| `transport` | 外部 I/O 适配：HTTP 客户端、翻译后端（google / bing / baidu / rwkv）、语音合成 |
| `services` | 业务流程编排：抓取、翻译链路、清理、状态扫描、评测 |
| `cli` | typer 命令与终端输出，负责参数解析、调用服务与渲染结果 |

### 新增翻译后端

1. 在 `src/story_tr/transport/` 下新增模块，实现 `Translator` 协议（可继承 `BaseTranslator` 复用重试与错误处理）。
2. 在 `src/story_tr/transport/__init__.py` 中导出新类，把后端名加入 `PROVIDERS`；如需参与默认回退顺序，再加入 `DEFAULT_ORDER`。
3. 在 `build_translator()` 中按后端名构造新实例，未知后端保持抛 `ValueError`。
4. 在 `tests/test_transport.py` 补充离线用例，HTTP 请求一律使用替身。

### 新增 CLI 子命令

1. 在 `src/story_tr/cli/commands/<name>.py` 中定义 typer 子命令，参考现有的 `fetch` / `translate` / `chain` 模块。
2. 在 `src/story_tr/cli/commands/__init__.py` 的 `COMMANDS` 元组中，按 `(fetch, fetch_cmd)` 的样式追加 `(name, name_cmd)`。
3. 业务逻辑写在 `services` 层，命令层只负责参数解析、调用与输出。

## 结构守护

`tests/test_structure.py` 通过 AST 静态检查三条约束，防止重构后结构腐化：

1. 生产代码单个函数不超过 60 行（`MAX_FUNCTION_LINES`）。
2. 分层依赖保持单向：`models → core → transport → services → cli`，禁止反向导入。
3. 生产模块不得跨模块引用以 `_` 开头的私有名字。

任一条不满足都会让该测试失败；请拆分函数或调整依赖后重新运行测试。

## 测试约定

- 测试全部离线执行，不访问网络；HTTP 交互统一使用替身。
- 测试文件与被测模块一对一命名：`tests/test_<module>.py`。
- 多个测试共用的替身、样例数据与工厂放在 `tests/support.py`。
- 新增行为请同时覆盖失败路径与边界情况，保持覆盖率不低于门槛。

## 提交规范

提交信息遵循 Conventional Commits：

```text
<type>(<scope>): <subject>
```

- `type` 必填，`scope` 可选（如 `cli`、`transport`、`docs`）。
- `subject` 使用祈使句，简洁描述改动，句末不加句号。
- 破坏性变更在正文中追加 `BREAKING CHANGE:` 段落说明。

| type | 适用范围 |
| --- | --- |
| `feat` | 新增功能或 CLI 能力 |
| `fix` | 缺陷修复 |
| `perf` | 性能优化，不改变外部行为 |
| `refactor` | 重构，不新增功能也不修复缺陷 |
| `test` | 测试的新增或调整 |
| `docs` | 文档变更（README、`docs/`、CHANGELOG 等） |
| `ci` | CI、工作流与发布脚本变更 |
| `chore` | 杂项维护，如依赖版本微调、缓存清理 |
| `build` | 构建与打包配置，如 `pyproject.toml`、依赖清单 |

示例：

```text
feat(transport): 新增翻译后端并加入 PROVIDERS
fix(cli): translate --tts-only 在空批次时报错
docs: 补充 CONTRIBUTING 与 SECURITY
ci: 在发布工作流中校验 tag 版本
```

## 文档与变更记录

- 修改 CLI 参数、输出格式或数据文件结构时，必须同步更新 `docs/` 下对应文档（入口见 `docs/usage.md`、`docs/architecture.md`、`docs/data-contract.md`）。
- 任何用户可见变更都要写入 `CHANGELOG.md` 的未发布区段（Keep a Changelog 的 `[Unreleased]`），按 Added / Changed / Fixed 等分类追加。
- 文档中的命令与路径需与仓库实际情况保持一致。

## PR 检查清单

- [ ] 四条质量命令全部通过
- [ ] 新增或修改的代码有对应测试，且测试离线可运行
- [ ] 未引入反向分层依赖或跨模块私有引用
- [ ] 单个函数不超过 60 行
- [ ] 用户可见变更已同步 `docs/` 与 `CHANGELOG.md`
- [ ] 提交信息符合 Conventional Commits
- [ ] 未提交 `.env`、密钥或抓取数据
