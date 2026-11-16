# 使用指南

> 面向「只想把命令跑起来」的读者。项目入口见 [README](../README.md)；后端密钥与回退见 [backends.md](backends.md)；本地 RWKV 见 [rwkv.md](rwkv.md)；数据产物字段见 [data-contract.md](data-contract.md)。

## 安装

> 建议先建虚拟环境；只想要命令的话，用 `pipx` 一步隔离装好。**最低 Python 3.10**。

**方式一：仓库内普通使用（venv）**

```bash
git clone https://github.com/vers123/story-tr.git
cd story-tr

python -m venv .venv
.venv\Scripts\activate # Windows；macOS/Linux: source .venv/bin/activate
pip install -e .

story-tr --version
```

**方式二：只要命令（pipx，自动隔离环境）**

```bash
pipx install git+https://github.com/vers123/story-tr
story-tr --version
```

安装后提供命令 **`story-tr`**；等价入口为 **`python -m story_tr`**。

> venv 下命令不在 PATH 时需先激活，或写全路径 `.venv\Scripts\story-tr.exe`（macOS/Linux 为 `.venv/bin/story-tr`）。
>
> 工作目录（`data/`、`logs/`、`out/`、`.env`、状态文件）默认按 `--data-dir` → `STORY_TR_HOME` → 向上查找 `data/character.json` → 当前目录 的顺序确定。

开发 / 测试场景（可编辑安装 + `.[dev]` 依赖）见 [development.md](development.md)。

## 快速开始

```bash
# 1) 安装（见上文；此处为 venv 方式）
git clone https://github.com/vers123/story-tr.git
cd story-tr
python -m venv .venv
.venv\Scripts\activate
pip install -e .

# 2) 获取数据（生成 data/，详见 data-contract.md）
story-tr fetch

# 3) 翻译（例：角色 10000021；也支持范围 10000002-10000030）
story-tr translate 10000021

# 4) 可选：配置翻译后端密钥与代理（复制并填写 .env）
copy .env.example .env # macOS/Linux: cp .env.example .env
```

- 只要**数据** → 做到第 2 步即可（产出 `data/`）。
- 只要**翻译结果** → 需要先有 `data/`，再执行第 3 步。
- 站点数据有更新时（新角色 / 故事改动）→ `story-tr fetch --update`。
- `.env` 不配置后端密钥时，google / bing 会走**免 key 通道**；baidu 与本地 rwkv 需要显式配置（见 [backends.md](backends.md)）。

## 命令（CLI）

所有命令都可加全局参数 `--data-dir <目录>` 指定工作目录；`-v` / `-q` 控制日志详略。业务逻辑全部在 `services` 层，`cli` 只做参数解析与 `rich` 渲染（见 [architecture.md](architecture.md)）。

### 全局参数

| 参数 | 说明 |
| --- | --- |
| `--data-dir <目录>` | 工作目录（默认 `STORY_TR_HOME` 或向上查找 `data/character.json`，最后当前目录） |
| `-v` / `--verbose` | 显示调试与步骤日志 |
| `-q` / `--quiet` | 只显示错误 |
| `--version` | 显示版本并退出 |

> `--verbose` 与 `--quiet` 互斥，只能二选一。

### `story-tr fetch`

从 Project Amber（yatta.moe）拉取各角色 en / chs 数据（`avatar` + `avatarFetter`），生成 / 校正 `data/character.json`、`profile/meta.json`、`profile/text.json`、全部故事 `en.md` / `zh.md`。

```bash
story-tr fetch # 增量：只补缺失项（默认）
story-tr fetch --update # 发现新角色 + 检测内容变化，仅重写变化项
story-tr fetch --refresh # 忽略缓存，全量重新抓取
story-tr fetch --workers 6 # 并发抓取角色数（默认 4，保留请求限速）
```

| 参数 | 说明 |
| --- | --- |
| `--update` | 发现新角色并检测内容变化，仅重写变化项（与 `--refresh` 互斥） |
| `--refresh` | 忽略缓存，全量重新抓取 |
| `--workers` | 并发抓取角色数（默认 `4`） |

- 抓取结果缓存到 `data/_fetch_all.json`（带内容指纹，可断点续抓、重复运行）。
- 增量模式只补抓缺失角色，且内容未变化的 md / json 直接跳过写入（不再整树重写）。
- `--update` 会从站点角色列表发现新角色、按指纹只重写变化项，并同步更新 `data/character.json`。
- 本地无 `data/character.json` 时自动按站点角色列表初始化（等价 `--update`）。
- 故事 md 与状态 / 档案 json 均为原子写（临时文件 + `os.replace`；json 额外保留上一版 `.bak` 备份）。

### `story-tr folders`

按 `data/character.json` 的 `stories` 同步重命名各角色 `profile/story/` 下文件夹（幂等，可反复运行）。

```bash
story-tr folders
```

### `story-tr translate`

批量翻译角色故事（「来回翻译 N 次」链路），结果写入 `data/story/character/{id}/result/{story}/`（`--result-tag <tag>` 时写入并列的 `result-<tag>/`）。

```bash
# 单个角色（默认处理其全部含内容故事）
story-tr translate 10000021

# 只处理指定故事
story-tr translate 10000021 --stories vision

# id 范围 / 组合（不在 character.json 中的 id 自动跳过并提示）
story-tr translate 10000002-10000030
story-tr translate 10000021,10000025-10000030 --stories vision

# 全部角色
story-tr translate --all-characters
```

| 参数 | 说明 | 默认 |
| --- | --- | --- |
| `char_ids`（位置参数） | 角色 id / id 范围（可多个，逗号分隔） | — |
| `--all-characters` | 处理 `data/character.json` 中的全部角色（与 `char_ids` 二选一） | — |
| `--stories` | 仅处理指定故事（逗号分隔；不指定 = 处理全部含内容故事） | 空 |
| `--mode` | 翻译路线模式：`fixed` / `random` / `custom` / `rwkv20` | `fixed` |
| `--chain` | 语言链：`asia` / `europe` / `exotic`（`fixed` 模式用） | `asia` |
| `--languages` | 自定义语言列表（逗号分隔，`custom` 模式用） | 空 |
| `--steps` | 往返翻译次数（`random` 模式抽取语言数） | `20` |
| `--provider` | 翻译后端：`auto` / `google` / `bing` / `baidu` / `rwkv`（`auto` 按序回退） | `auto` |
| `--workers` | 并发工作线程数 | `1` |
| `--min-interval` | 每个请求最小间隔（秒） | `0.3` |
| `--balance` | 在多后端间轮换起始顺序 | 关 |
| `--full-only` | 只翻译整篇 | 关 |
| `--segments-only` | 只翻译分段 | 关 |
| `--overwrite` | 覆盖已有结果 | 关 |
| `--reset` | 重置批次状态并忽略签名不一致 | 关 |
| `--revalidate` | 源文变化时重新翻译 | 关 |
| `--no-cache` | 忽略批次状态中的 `completed` 记录（仍按磁盘结果判断缓存） | 关 |
| `--result-tag` | 结果目录标签：写入 `result-<tag>/`（与默认结果隔离，签名含 tag） | 空 |
| `--batch` / `--no-batch` | 同一故事的全部任务按链路步骤批量请求（仅 Google 生效；批量失败自动回退逐条） | 开 |
| `--plan` | 只读估算任务量 / 请求数 / 签名一致性，不执行翻译 | 关 |
| `--reconcile` | 按磁盘已有结果对账批次状态（补缺失、删失效，不删除结果文件） | 关 |
| `--retry-rounds` | 失败自动重试轮数（0 = 关闭；指数退避 5s→60s） | `5` |
| `--fail-fast` | 首轮首个失败即停止剩余任务（仍会进入自动重试） | 关 |
| `--max-failures` | 首轮失败任务上限（达到后停止首轮剩余任务，仍进入重试；TTS 失败也计入） | `20` |
| `--no-tts` | 不生成语音 | 关 |
| `--tts-only` | 只补生成缺失语音（不重译文本；`--overwrite` 可重生成已有语音） | 关 |

- 分段：按 `zh.md` 正文段落；每段独立跑完整链路，产出 `seg_NN.json`。
- 全文：整篇再独立跑一遍完整链路，产出 `full.json`。
- 标题：`zh.md` frontmatter 的 `title` 始终跑一遍，产出 `title.json`。
- 纯符号段落（无文字特征）原样保留、不发请求（结果标 `passthrough: true`）。
- 续跑：`.translate_stories_state.json`（`BatchState`）记录已完成任务与参数签名；参数不一致要求 `--reset`；可用 `--reconcile` 按磁盘结果重建记录。
- 批量模式（`--batch`，默认开）把同一故事所有任务在同一步的当前文本打包进一次请求；`--workers` 在批量模式下不生效（按故事串行），可用 `--no-batch` 恢复逐条并发。批量失败会自动回退逐条翻译。
- 失败登记：翻译失败/TTS 失败都写入 `.translate_failures_state.json`，并在本次运行内按 `--retry-rounds` 自动重试（连接/限流/配额/语言/业务失败均计入；模型未加载仍立即中止）；翻译失败重译整条，TTS 失败只补语音。
- 失败清单按轮写 `logs/failures_<时间戳>_r<轮>.json` 与 `logs/tts_failures_<时间戳>_r<轮>.json`；重试耗尽仍有失败时以退出码 `7` 结束，登记文件保留供排查。
- 语音：TTS 连接失败会先按 3 次指数退避重试；仍失败时翻译结果照常落盘，并计入失败登记进入自动重试。

### `story-tr chain`

对单个文本执行「来回翻译 N 次」链路（最后译回中文）。

```bash
story-tr chain "Hello, World" # 默认 random 抽 20 个语言
story-tr chain --mode fixed --chain asia
story-tr chain "你好" --mode custom --languages ja,ko,ar
story-tr chain --mode rwkv20 # 本地一次成型
story-tr chain --dry-run # 只展示路线，不翻译
```

| 参数 | 说明 | 默认 |
| --- | --- | --- |
| `text`（位置参数） | 待翻译文本 | `Hello, World` |
| `--mode` | 路线模式：`fixed` / `random` / `custom` / `rwkv20` | `random` |
| `--chain` | 语言链：`asia` / `europe` / `exotic` | `asia` |
| `--languages` | 自定义语言列表（逗号分隔） | 空 |
| `--steps` | 往返翻译次数（`random` 模式抽取语言数） | `20` |
| `--provider` | 翻译后端：`auto` / `google` / `bing` / `baidu` / `rwkv` | `auto` |
| `--min-interval` | 每个请求最小间隔（秒） | `0.3` |
| `--balance` | 在多后端间轮换起始顺序 | 关 |
| `--no-tts` | 不生成语音 | 关 |
| `--reset` | 重置链状态并忽略签名不一致 | 关 |
| `--dry-run` | 仅展示路线，不执行翻译 | 关 |
| `--no-file-log` | 不写文件日志 | 关 |

- 结果写 `out/{时间戳}.json`（`TranslationResult`）+ 可选 mp3。
- 续跑：`.translate_state.json`（`ChainState`），参数不一致要求 `--reset`。

### `story-tr clean`

清理 `data/story/character/{id}/result/`（或 `result-<tag>/`）下的翻译产物（保留目录本身）。

```bash
story-tr clean 10000021
story-tr clean --all-characters
story-tr clean 10000021 --stories vision
story-tr clean --all-characters --dry-run # 只预览，不删除
story-tr clean 10000021 --result-tag cloud
```

| 参数 | 说明 |
| --- | --- |
| `char_ids`（位置参数） | 角色 id / id 范围（可多个，逗号分隔） |
| `--all-characters` | 处理全部角色（与 `char_ids` 二选一） |
| `--stories` | 仅清理指定故事（逗号分隔） |
| `--result-tag` | 清理指定结果标签目录（默认清理 `result/`） |
| `--dry-run` | 只预览，不删除 |
| `-y` / `--yes` | 跳过交互确认 |
| `--keep-state` | 保留状态记录 |

- 默认交互确认；`--dry-run` 预览、`-y` 跳过。
- 默认同步移除 `.translate_stories_state.json` 中对应记录并**重置批次签名**；`--keep-state` 可保留；`--result-tag` 只动标签与本次一致的记录。
- 同时清理选中角色/故事的失败登记（`.translate_failures_state.json`）。

### `story-tr status`

只读报告工作目录、数据覆盖、结果 / 语音数量、磁盘占用、批量进度与失败登记。默认不联网、不写文件。

```bash
story-tr status # 人类可读
story-tr status --json # JSON 输出
story-tr status --deep # 统计源语言分布（读取全部 seg JSON，较慢）
story-tr status --probe # 探测本地 RWKV 服务
story-tr status --manifest # 写入 data/manifest.json
story-tr status --result-tag cloud # 统计指定结果标签目录
```

- 输出会同时显示“批量状态记录数 / 磁盘结果数”，不一致时提示 `translate --reconcile`；存在未解决失败时显示“失败登记”。

### `story-tr doctor`

只读检查运行环境（Python、工作目录、角色索引、`data/` 可写、批量状态文件、`.env` 后端密钥、代理、本地 RWKV 配置）。默认不联网；数据 / 状态损坏返回退出码 `1`。

```bash
story-tr doctor
story-tr doctor --probe # 额外探测本地 RWKV 服务（默认不联网）
```

| 参数 | 说明 |
| --- | --- |
| `--probe` | 探测本地 RWKV 服务 |

### `story-tr eval`

简化版本地评测（替代旧 `rwkv-eval`）：采样 `seg_*` 段落，用本地 RWKV 按多套提示词模板生成，与已有云端结果比对并排名，报告写 `logs/rwkv_eval_<时间戳>.json|md`（不写 `result/`、不改状态文件）。参数表与评测口径见 [rwkv.md](rwkv.md#eval-评测)。

```bash
story-tr eval --limit 20 --templates builtin
```

| 参数 | 说明 | 默认 |
| --- | --- | --- |
| `char_ids`（位置参数） | 角色 id / id 范围（可多个，逗号分隔） | 全部角色 |
| `--all-characters` | 评测全部角色 | — |
| `--stories` | 仅评测指定故事（逗号分隔） | 空 |
| `--limit` | 采样段落数上限 | `20` |
| `--templates` | 评测模板（`builtin` 或 `||` 分隔的自定义模板） | 空（用 `RWKV_PROMPT_TEMPLATE`） |
| `--out` | 报告输出目录 | `logs/` |
| `--rwkv-url` | 覆盖本地 RWKV 服务地址 | — |
| `--rwkv-template` | 覆盖 RWKV 提示词模板 | — |
| `--result-tag` | 用指定结果标签目录做基线（默认 `result/`） | 空 |

### `story-tr export`

把结果目录导出为数据集（默认 JSONL）+ `manifest.json`；只读 `data/`，输出默认写 `out/export/`。parquet 需要可选依赖 `pip install "story-tr[export]"`。

```bash
story-tr export --all-characters
story-tr export 10000021 --format jsonl --with-steps
story-tr export --result-tag cloud --out out/cloud
story-tr export --format parquet
```

| 参数 | 说明 | 默认 |
| --- | --- | --- |
| `char_ids`（位置参数） | 角色 id / id 范围（可多个，逗号分隔） | 全部角色 |
| `--all-characters` | 导出全部角色（与 `char_ids` 二选一） | — |
| `--stories` | 仅导出指定故事（逗号分隔） | 空 |
| `--result-tag` | 导出指定结果标签目录 | 空（`result/`） |
| `--out` | 输出目录 | `out/export` |
| `--format` | `jsonl` / `parquet` | `jsonl` |
| `--with-steps` | 附带每步中间结果与 RWKV 元数据（parquet 中序列化为 JSON 字符串） | 关 |

- 记录为扁平结构：角色 / 故事 / 结果键 / 源文与译文 / mode / chain / providers / languages / 音频文件名 / 结果文件路径。
- `manifest.json` 记录生成时间、工具版本、结果 tag、计数（角色 / 故事 / 任务 / title / full / 段 / 语音）与抓取指纹（`_fetch_all.json`）。

## 退出码

退出码由 `story_tr.errors.ExitCode` 统一定义：

| 码 | 含义 | 说明 |
| --- | --- | --- |
| `0` | 完成 | 正常结束 |
| `1` | 参数或前置条件错误 | 参数不合法 / 缺少数据等 |
| `2` | 语言不受支持 | `chain` 等单次执行命令；`translate` 中会记为失败并重试 |
| `3` | 批量状态与上次不一致 | 需 `--reset` 或 `clean` |
| `4` | 已暂停 | 本地模型未加载（快速中止） |
| `5` | 后端连接失败 | `chain` / RWKV 预检；`translate` 中会记为失败并重试 |
| `6` | 已取消（进度已保存） | 用户主动取消 |
| `7` | 部分任务失败 | 重试轮耗尽仍有失败；失败登记与日志已保存，重跑可续 |
| `8` | 今日配额已耗尽 | `chain` 等；`translate` 中配额耗尽可能记为失败并重试（有轮数上限） |

> 未捕获的异常会以 `-1`（异常，见日志）结束。
