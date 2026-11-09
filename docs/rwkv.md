# 本地 RWKV（`--mode rwkv20`）

用本机离线运行的 RWKV「翻译 20 次」微调模型，**一份结果只生成 1 次**（替代云端 21~23 次请求），适合离线、无每日配额、可无人值守的批量翻译；云端链路仍保留为质量基线。

- 适用模型：`translate-20times-3B.pth`（RWKV-5 World 3B v2 的 LoRA 微调权重，约 6.1 GB）。
- 放到 **RWKV-Runner 的 `models/` 目录**，**不要放本仓库**（已由 `.gitignore` 忽略）。

## 启动

1. 用 RWKV-Runner 加载 `translate-20times-3B.pth` 并启动服务（默认 `http://127.0.0.1:8000`）。
2. 用 `story-tr doctor --probe`（或 `story-tr status --probe`）检查服务状态与已加载的模型名。

## 命令

```bash
story-tr translate 10000021 --mode rwkv20
story-tr translate --all-characters --mode rwkv20
story-tr chain "测试文本" --mode rwkv20
story-tr eval --limit 20 --templates builtin # 校准提示词模板
```

## 配置（`.env` / 环境变量）

| 变量 | 说明 | 默认 |
| --- | --- | --- |
| `RWKV_API_URL` | 本地 RWKV-Runner 服务地址 | `http://127.0.0.1:8000` |
| `RWKV_PROMPT_TEMPLATE` | 提示词模板（占位符 `{text}` / `{src}` / `{tgt}`；`\n` 按换行解析） | `Translate this into Chinese.\n\nEnglish: {text}\nChinese:` |
| `RWKV_MAX_TOKENS` | 单次生成上限 | `1024` |
| `RWKV_TEMPERATURE` | 采样温度 | `1.0` |
| `RWKV_TOP_P` | 采样 `top_p` | `0.3` |
| `RWKV_TIMEOUT` | 单次请求超时（秒） | `300` |
| `RWKV_CHUNK_CHARS` | 长文本分块阈值 | `600` |
| `RWKV_RETRIES` | 业务失败重试次数 | `2` |
| `RWKV_MODEL_PATH` | 模型文件路径（仅用于 `status` / `doctor` 检查文件是否存在） | 空 |

## 行为说明

- `--mode rwkv20` 自动使用本地 `rwkv` 后端（无需 `--provider`）；`--provider rwkv` 仅能与 `--mode rwkv20` 搭配。
- `rwkv20` 的输入必须是英文：批量翻译直接读取故事目录下的 `en.md`（官方英文原文），`chain` 则要求传入英文文本；缺失 `en.md` 的故事会被跳过并记录日志。
- 调用方式：RWKV-Runner 的 OpenAI 兼容接口（`POST /v1/completions`、`GET /status`、`GET /v1/models`）。
- `translate-20times-3B` 必须使用上面的 `English/Chinese` 指令模板；RWKV-Runner 上下文建议 ≥2048，避免长段落被截断。
- 服务不可达 → 退出码 `5`；模型未加载（含 `model not loaded`）→ 退出码 `4`，均快速失败。
- 结果结构与云端一致（`languages=["rwkv20"]`、`steps` 仅 1 项），产物仍在 `result/{story}/`。
- 改变本地 URL / 模板 / 采样参数后需加 `--reset`（签名不一致退出码 `3`）。

## eval 评测

`story-tr eval` 用来校准提示词模板、对比本地与云端质量：采样 `seg_*` 段落，用本地 RWKV 按多套模板生成，再与已有云端结果比对排名；报告写 `logs/rwkv_eval_<时间戳>.json` 与 `.md`，**不写** `result/`、**不改**状态文件。

```bash
story-tr eval --limit 20 --templates builtin
story-tr eval 10000021 --limit 50 --out logs
```

| 参数 | 说明 | 默认 |
| --- | --- | --- |
| `char_ids`（位置参数） | 采样范围：角色 id / id 范围（逗号分隔） | 全部角色 |
| `--all-characters` | 评测全部角色 | — |
| `--stories` | 仅评测指定故事（逗号分隔） | 空 |
| `--limit` | 采样段落数上限 | `20` |
| `--templates` | `builtin` 或 `||` 分隔的自定义模板 | 空（用 `RWKV_PROMPT_TEMPLATE`） |
| `--out` | 报告输出目录 | `logs/` |
| `--rwkv-url` / `--rwkv-template` | 覆盖服务地址 / 提示词模板 | — |

命令层面的参数速查见 [usage.md](usage.md#story-tr-eval)。

## 许可

- RWKV-Runner：**MIT**（外部可选服务，不随本项目分发；本项目只通过其 OpenAI 兼容 HTTP 接口调用，不复制其代码）。
- 上游 RWKV-5 World 3B v2 权重：**Apache-2.0**。
- LoRA 微调权重（`translate-20times-3B.pth`）的作者与许可**未随文件提供、未知**，因此**只能本地使用，不得再分发**。
