# 开发指南

面向贡献者与维护者。代码分层与模块职责见 [architecture.md](architecture.md)，命令参数见 [usage.md](usage.md)。

## 开发环境

> **最低 Python 3.10**（CI 覆盖 3.10 / 3.11 / 3.12 / 3.13 / 3.14）。

```bash
git clone https://github.com/vers123/story-tr.git
cd story-tr

python -m venv .venv
.venv\Scripts\activate # Windows；macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]" # 可编辑安装 + 开发 / 测试工具
```

dev 额外依赖：`mypy`、`ruff`、`pytest`、`pytest-cov`、`build`、`twine`。**依赖以 `pyproject.toml` 为准，`requirements.lock` 记录验证版本**。

## 本地校验

与 CI 保持一致：

```bash
ruff check .
ruff format --check .
mypy src/story_tr
pytest -q --cov=story_tr --cov-report=term-missing
```

- 覆盖率门槛写在 `pyproject.toml`（`fail_under = 85`）。
- 结构守护测试（单函数 ≤ 60 行 / 分层单向依赖 / 禁止跨模块引用 `_` 私有名字）由 `tests/test_structure.py` 提供，详见 [architecture.md](architecture.md#结构守护)。
- 上面四步也可用仓库自带脚本一键执行：`scripts/verify.ps1`（Windows）/ `scripts/verify.sh`（macOS/Linux）；某一步失败不会中断后续步骤，最后统一汇总并以非零退出码结束。

## 版本与变更

本项目遵循 [Semantic Versioning 2.0.0](https://semver.org/lang/zh-CN/)，版本号形如 `MAJOR.MINOR.PATCH`；发布即打标签 `git tag -a vX.Y.Z -m "..."`。

- `MAJOR`：不兼容的 CLI / 数据契约 / 包 API 变更。
- `MINOR`：向下兼容的新功能（新命令、新参数、新后端）。
- `PATCH`：向下兼容的修复、性能与文档改进。
- 初始开发阶段（`0.y.z`）：`y` 递增可携带破坏性变更，`0.y.z` 之间不保证兼容。

完整历史见 [../CHANGELOG.md](../CHANGELOG.md)。

## Breaking changes（0.x → 1.0）

`1.0.0` 是对 `0.y.z` 的**破坏性整体重写**（分层架构 + pydantic 数据契约 + 统一后端抽象），以下内容**不兼容且不提供迁移**：

- **CLI 参数**：命令与参数已收敛（移除 `video`、`rwkv-eval` 更名为 `eval`；移除 `--proxy` / `--no-batch` / `--all` / `--tts` / 各 `--rwkv-*` 命令行开关等旧参数，代理与本地 RWKV 改走环境变量 / `.env`）。
- **结果 JSON**：`result/*.json` 改为 pydantic `TranslationResult` 契约，旧 `0.y.z` 结果视为无效，需**重新翻译**。
- **数据目录 / 档案字段**：`profile/meta.json`、`profile/text.json` 字段改为 snake_case（如 `weapon_type`、`name_card`），旧字段名不兼容；`data/` 可用 `story-tr fetch --refresh` 全量重建。
- **状态文件**：`.translate_stories_state.json` / `.translate_state.json` 改为 `BatchState` / `ChainState` 契约，旧状态不兼容（重新开始即可）。
- **包 API**：`src/story_tr/` 布局重构为 `models/core/transport/services/cli`，旧模块（`chain.py` / `stories.py` / `sync.py` / `clean.py` / `status.py` / `video.py` / `doctor.py` / `folders.py` / `report.py` / `statefile.py` / `exitcodes.py` / `rwkv.py` / `rwkv_eval.py` / `gui/` / `mcp/`）已移除，`python main.py` 与旧 `scripts/` 兼容入口亦已移除，统一入口为 `story-tr` / `python -m story_tr`。
- **功能裁剪**：GUI / MCP / video 能力整体移除。

**已移除的依赖与可选能力**（0.x → 1.0）：

- 可选能力：GUI（`PySide6`）、MCP Server（`mcp` SDK）、视频合成（`Pillow` + 系统 ffmpeg）。
- 旧依赖：`deep-translator`、`gTTS`、`tqdm`、`requests`（HTTP 已统一为 `httpx`，TTS 直连 Google TTS 端点，进度条改 `rich`）。

> 迁移方式：`data/` 用 `story-tr fetch --refresh` 重建；`result/` 用 `story-tr translate`（可选 `--overwrite`）重新翻译。

## 发布流程

1. **确认版本号**：按 SemVer 决定 `MAJOR.MINOR.PATCH`，同步修改 `src/story_tr/__init__.py` 的 `__version__`（打包版本由 `pyproject.toml` 的 `dynamic = ["version"]` 读取该字段）。
2. **更新 `CHANGELOG.md`**：遵循 Keep a Changelog 约定 —— `## 未发布` 累积变更，发布时改为 `## [X.Y.Z] - YYYY-MM-DD`；条目按类别分组（**新增** / **变更** / **改进** / **性能** / **修复** / **重构** / **工程** / **测试** / **破坏性**）。
3. **本地校验**：`ruff check .`、`ruff format --check .`、`mypy src/story_tr`、`pytest -q --cov=story_tr`（等价于 CI 的 quality + test job）；也可直接运行仓库自带的 `scripts/verify.ps1` / `scripts/verify.sh`。
4. **提交前钩子**：`pip install pre-commit` 后执行 `pre-commit install`，把校验挂到 `git commit` 前（钩子配置见 `.pre-commit-config.yaml`）。
5. **打标签并推送**：`git tag -a vX.Y.Z -m "vX.Y.Z"` → `git push origin vX.Y.Z`（标签必须以 `v` 开头，例如 `v2.0.0`）。
6. **tag 触发发布工作流**：`.github/workflows/release.yml` 监听 `v*` 标签，执行 `python -m build` + `twine check`，再以 `gh release create` 创建 GitHub Release 并附上 `dist/*`。
7. **手工构建（可选）**：`python -m build` 产出 `dist/`，`twine check dist/*` 校验后再上传。

> 版本号唯一来源是 `src/story_tr/__init__.py` 的 `__version__`；`python -m build` 与 `story-tr --version` 读同一处，避免版本漂移。
