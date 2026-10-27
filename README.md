# story-tr

原神角色故事双语（中 / 英）文本抓取 + 「来回翻译 N 次」链路工具。

**版本 v1.0.0** · 遵循 [Semantic Versioning 2.0.0](https://semver.org/lang/zh-CN/) · 许可 [MIT](LICENSE) · 最低 Python 3.10

> **免责声明**：本项目仅用于学习与技术研究，不包含、不分发任何游戏资源或数据；与米哈游（miHoYo / HoYoverse）及其关联公司无任何关系。
>
> **来源与版权**：角色与文本取自 **Project Amber**（[yatta.moe](https://gi.yatta.moe/)）的公开 API，**仅供学习 / 研究用途**；角色、文本等版权归米哈游 / Project Amber 所有。

## 目录

- [安装](#安装)
- [快速开始](#快速开始)
- [命令（CLI）](#命令cli)
- [退出码](#退出码)
- [翻译后端](#翻译后端)
- [本地 RWKV（`--mode rwkv20`）](#本地-rwkv--mode-rwkv20)
- [数据目录与数据契约](#数据目录与数据契约)
- [架构](#架构)
- [依赖与许可](#依赖与许可)
- [Breaking changes（0.x → 1.0）](#breaking-changes0x--10)
- [版本与变更](#版本与变更)

## 安装

> 建议先建虚拟环境；只想要命令的话，用 `pipx` 一步隔离装好。**最低 Python 3.10**。

**方式一：仓库内开发（venv，推荐）**

```bash
git clone https://github.com/vers123/story-tr.git
cd story-tr

python -m venv .venv
.venv\Scripts\activate                 # Windows；macOS/Linux: source .venv/bin/activate
pip install -e .                       # 开发/测试再加 [dev]：pip install -e ".[dev]"

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

## 快速开始

```bash
# 1) 安装（见「安装」；此处为 venv 方式）
git clone https://github.com/vers123/story-tr.git
cd story-tr
python -m venv .venv
.venv\Scripts\activate
pip install -e .

# 2) 获取数据（生成 data/，详见「数据目录与数据契约」）
story-tr fetch

# 3) 翻译（例：角色 10000021；也支持范围 10000002-10000030）
story-tr translate 10000021

# 4) 可选：配置翻译后端密钥与代理（复制并填写 .env）
copy .env.example .env                 # macOS/Linux: cp .env.example .env
```

- 只要**数据** → 做到第 2 步即可（产出 `data/`）。
- 只要**翻译结果** → 需要先有 `data/`，再执行第 3 步。
- 站点数据有更新时（新角色 / 故事改动）→ `story-tr fetch --update`。
- `.env` 不配置后端密钥时，google / bing 会走**免 key 通道**；baidu 与本地 rwkv 需要显式配置（见「[翻译后端](#翻译后端)」）。

## 命令（CLI）

所有命令都可加全局参数 `--data-dir <目录>` 指定工作目录；`-v` / `-q` 控制日志详略。业务逻辑全部在 `services` 层，`cli` 只做参数解析与 `rich` 渲染。

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
story-tr fetch              # 增量：只补缺失项（默认）
story-tr fetch --update     # 发现新角色 + 检测内容变化，仅重写变化项
story-tr fetch --refresh    # 忽略缓存，全量重新抓取
```

| 参数 | 说明 |
| --- | --- |
| `--update` | 发现新角色并检测内容变化，仅重写变化项（与 `--refresh` 互斥） |
| `--refresh` | 忽略缓存，全量重新抓取 |

- 抓取结果缓存到 `data/_fetch_all.json`（带内容指纹，可断点续抓、重复运行）。
- `--update` 会从站点角色列表发现新角色、按指纹只重写变化项，并同步更新 `data/character.json`。
- 本地无 `data/character.json` 时自动按站点角色列表初始化（等价 `--update`）。
- 写文件均为原子写（临时文件 + 上一版 `.bak` 备份 + `os.replace`）。

### `story-tr folders`

按 `data/character.json` 的 `stories` 同步重命名各角色 `profile/story/` 下文件夹（幂等，可反复运行）。

```bash
story-tr folders
```

### `story-tr translate`

批量翻译角色故事（「来回翻译 N 次」链路），结果写入 `data/story/character/{id}/result/{story}/`。

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
| `--no-cache` | 不使用已有结果缓存 | 关 |
| `--fail-fast` | 首个失败即中止 | 关 |
| `--max-failures` | 失败任务上限 | `20` |
| `--no-tts` | 不生成语音 | 关 |

- 分段：按 `zh.md` 正文段落；每段独立跑完整链路，产出 `seg_NN.json`。
- 全文：整篇再独立跑一遍完整链路，产出 `full.json`。
- 标题：`zh.md` frontmatter 的 `title` 始终跑一遍，产出 `title.json`。
- 纯符号段落（无文字特征）原样保留、不发请求（结果标 `passthrough: true`）。
- 续跑：`.translate_stories_state.json`（`BatchState`）记录已完成任务与参数签名；参数不一致要求 `--reset`。
- 失败清单写 `logs/failures_<时间戳>.json`；失败项记录后继续，退出码 `7`。

### `story-tr chain`

对单个文本执行「来回翻译 N 次」链路（最后译回中文）。

```bash
story-tr chain "Hello, World"                          # 默认 random 抽 20 个语言
story-tr chain --mode fixed --chain asia
story-tr chain "你好" --mode custom --languages ja,ko,ar
story-tr chain --mode rwkv20                           # 本地一次成型
story-tr chain --dry-run                               # 只展示路线，不翻译
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

清理 `data/story/character/{id}/result/` 下的翻译产物（保留 `result/` 目录本身）。

```bash
story-tr clean 10000021
story-tr clean --all-characters
story-tr clean 10000021 --stories vision
story-tr clean --all-characters --dry-run   # 只预览，不删除
```

| 参数 | 说明 |
| --- | --- |
| `char_ids`（位置参数） | 角色 id / id 范围（可多个，逗号分隔） |
| `--all-characters` | 处理全部角色（与 `char_ids` 二选一） |
| `--stories` | 仅清理指定故事（逗号分隔） |
| `--dry-run` | 只预览，不删除 |
| `-y` / `--yes` | 跳过交互确认 |
| `--keep-state` | 保留状态记录 |

- 默认交互确认；`--dry-run` 预览、`-y` 跳过。
- 默认同步移除 `.translate_stories_state.json` 中对应记录并**重置批次签名**；`--keep-state` 可保留。

### `story-tr status`

只读报告工作目录、数据覆盖、结果 / 语音数量、磁盘占用与批量进度。默认不联网、不写文件。

```bash
story-tr status             # 人类可读
story-tr status --json      # JSON 输出
story-tr status --deep      # 统计源语言分布（读取全部 seg JSON，较慢）
story-tr status --probe     # 探测本地 RWKV 服务
story-tr status --manifest  # 写入 data/manifest.json
```

### `story-tr doctor`

只读检查运行环境（Python、工作目录、角色索引、`data/` 可写、批量状态文件、`.env` 后端密钥、代理、本地 RWKV 配置）。默认不联网；数据 / 状态损坏返回退出码 `1`。

```bash
story-tr doctor
story-tr doctor --probe     # 额外探测本地 RWKV 服务（默认不联网）
```

| 参数 | 说明 |
| --- | --- |
| `--probe` | 探测本地 RWKV 服务 |

### `story-tr eval`

简化版本地评测（替代旧 `rwkv-eval`）：采样 `seg_*` 段落，用本地 RWKV 按多套提示词模板生成，与已有云端结果比对并排名，报告写 `logs/rwkv_eval_<时间戳>.json|md`（不写 `result/`、不改状态文件）。

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

## 退出码

退出码由 `story_tr.errors.ExitCode` 统一定义，`label()` 提供人类可读文案：

| 码 | 含义 | 说明 |
| --- | --- | --- |
| `0` | 完成 | 正常结束 |
| `1` | 参数或前置条件错误 | 参数不合法 / 缺少数据等 |
| `2` | 语言不受支持 | 后端不支持该语言 |
| `3` | 批量状态与上次不一致 | 需 `--reset` 或 `clean` |
| `4` | 已暂停（限流或业务错误） | 业务错误重试耗尽；含本地模型未加载 |
| `5` | 后端连接失败 | 所有后端连接 / 超时 / 5xx 失败 |
| `6` | 已取消（进度已保存） | 用户主动取消 |
| `7` | 部分任务失败（失败清单已保存） | 重跑可续 |
| `8` | 今日配额已耗尽（进度已保存） | 次日续跑 |

> 未捕获的异常会以 `-1`（异常，见日志）结束。

## 翻译后端

统一 `Translator` 协议：`translate(text, src, tgt) -> str` + 可用性检查；四个实现各自负责自身的最小间隔 / 限流 / 错误分类。错误统一映射为 `TranslationError` 层级（`RateLimited` / `QuotaExceeded` / `ConnectionFailed` / `UnsupportedLanguage` / `BadOutput` / `ModelNotLoaded`）。

| 后端 | 免 key 通道 | 官方通道（`.env` 配置） | 说明 |
| --- | --- | --- | --- |
| `google` | `clients5.google.com` 的 `dict-chrome-ex` 端点 | `GOOGLE_API_KEY` → Cloud Translation v2 | 长文本按 1500 字分块 |
| `bing` | 网页免 key 通道（`cn.bing.com` / `www.bing.com` 多域名轮换，可用 `BING_WEB_BASE` 覆盖） | `BING_API_KEY`（+ `BING_REGION`）→ Azure 官方接口 | — |
| `baidu` | 无（必须官方） | `BAIDU_APPID` + `BAIDU_APPKEY` | 标准版 QPS=1，内置最小间隔 1.1s |
| `rwkv` | — | 本地 RWKV-Runner（`RWKV_*` 环境变量） | 仅与 `--mode rwkv20` 搭配 |

`--provider auto`（默认）按 **google → bing → baidu** 顺序回退；`rwkv` 不参与 auto 回退，只通过 `--mode rwkv20` 或 `--provider rwkv` 显式使用。

- 缺 key 的 google / bing 自动走免 key 通道；baidu 未配 `BAIDU_APPID` / `BAIDU_APPKEY` 时报告不可用并被跳过。
- 连接类失败（超时 / DNS / 5xx）立即切换下一后端；限流（429 等）与业务失败按各后端策略处理；全部后端失败退出码 `5`。
- 代理：经 `HTTPS_PROXY` / `HTTP_PROXY` 环境变量（或 `.env`）生效。
- TTS：用 `httpx` 直连 Google TTS 端点（`translate.google.com/translate_tts`）生成 mp3，不再依赖 gTTS；`--no-tts` 可关闭。

## 本地 RWKV（`--mode rwkv20`）

用本机离线运行的 RWKV「翻译 20 次」微调模型，**一份结果只生成 1 次**（替代云端 21~23 次请求），适合离线、无每日配额、可无人值守的批量翻译；云端链路仍保留为质量基线。

- 适用模型：`translate-20times-3B.pth`（RWKV-5 World 3B v2 的 LoRA 微调权重，约 6.1 GB）。
- 放到 **RWKV-Runner 的 `models/` 目录**，**不要放本仓库**（已由 `.gitignore` 忽略）。

**启动**

1. 用 RWKV-Runner 加载 `translate-20times-3B.pth` 并启动服务（默认 `http://127.0.0.1:8000`）。
2. 用 `story-tr doctor --probe`（或 `story-tr status --probe`）检查服务状态与已加载的模型名。

**命令**

```bash
story-tr translate 10000021 --mode rwkv20
story-tr translate --all-characters --mode rwkv20
story-tr chain "测试文本" --mode rwkv20
story-tr eval --limit 20 --templates builtin   # 校准提示词模板
```

**配置（`.env` / 环境变量）**

| 变量 | 说明 | 默认 |
| --- | --- | --- |
| `RWKV_API_URL` | 本地 RWKV-Runner 服务地址 | `http://127.0.0.1:8000` |
| `RWKV_PROMPT_TEMPLATE` | 提示词模板（占位符 `{text}` / `{src}` / `{tgt}`；`\n` 按换行解析） | `User: {text}\n\nBot:` |
| `RWKV_MAX_TOKENS` | 单次生成上限 | `1024` |
| `RWKV_TEMPERATURE` | 采样温度 | `1.0` |
| `RWKV_TOP_P` | 采样 `top_p` | `0.3` |
| `RWKV_TIMEOUT` | 单次请求超时（秒） | `300` |
| `RWKV_CHUNK_CHARS` | 长文本分块阈值 | `1200` |
| `RWKV_RETRIES` | 业务失败重试次数 | `2` |
| `RWKV_MODEL_PATH` | 模型文件路径（仅用于 `status` / `doctor` 检查文件是否存在） | 空 |

- `--mode rwkv20` 自动使用本地 `rwkv` 后端（无需 `--provider`）；`--provider rwkv` 仅能与 `--mode rwkv20` 搭配。
- 调用方式：RWKV-Runner 的 OpenAI 兼容接口（`POST /v1/completions`、`GET /status`、`GET /v1/models`）。
- 服务不可达 → 退出码 `5`；模型未加载（含 `model not loaded`）→ 退出码 `4`，均快速失败。
- 结果结构与云端一致（`languages=["rwkv20"]`、`steps` 仅 1 项），产物仍在 `result/{story}/`。
- 改变本地 URL / 模板 / 采样参数后需加 `--reset`（签名不一致退出码 `3`）。

**许可**

- RWKV-Runner：**MIT**（外部可选服务，不随本项目分发；本项目只通过其 OpenAI 兼容 HTTP 接口调用，不复制其代码）。
- 上游 RWKV-5 World 3B v2 权重：**Apache-2.0**。
- LoRA 微调权重（`translate-20times-3B.pth`）的作者与许可**未随文件提供、未知**，因此**只能本地使用，不得再分发**。

## 数据目录与数据契约

`data/` 由 `story-tr fetch` 生成，**不入库**（见 `.gitignore`）。以下为 **v1.0 数据契约**，全部结构经 pydantic v2 校验后写入：

```
<工作目录>/
├── .env                                   # 密钥（不入库，见 .env.example）
├── data/
│   ├── character.json                     # 角色索引：CharacterRef 列表
│   ├── _fetch_all.json                    # 抓取缓存（断点续抓 + 内容指纹）
│   ├── manifest.json                      # 数据清单（story-tr status --manifest 生成）
│   └── story/character/{id}/
│       ├── profile/
│       │   ├── meta.json                  # CharacterMeta
│       │   ├── text.json                  # CharacterText
│       │   └── story/{story}/
│       │       ├── en.md                  # frontmatter + 段落
│       │       └── zh.md
│       └── result/{story}/
│           ├── title.json / title.mp3     # 标题译文（TranslationResult）
│           ├── full.json / full.mp3       # 全文译文（TranslationResult）
│           ├── seg_01.json / seg_01.mp3   # 各段译文（TranslationResult）
│           ├── …                          # seg_NN 依次编号
│           └── segments.json              # 汇总（标题/全文/各段 索引）
├── out/                                   # story-tr chain 结果（{时间戳}.json / .mp3）
├── logs/                                  # 运行日志 + failures_*.json + rwkv_eval_*
├── .translate_stories_state.json          # 批量续跑状态（BatchState）
└── .translate_state.json                  # 单文本链路续跑状态（ChainState）
```

### `data/character.json`

角色**索引**，`CharacterRef` 列表：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | `str` | 角色 id（与目录名一致） |
| `name_en` / `name_zh` | `str` | 中英文名 |
| `path` | `str` | 角色目录（相对工作目录），如 `data/story/character/10000021` |
| `stories` | `list[str]` | `profile/story/` 下的故事文件夹名列表 |

### `profile/meta.json`（`CharacterMeta`）

角色**语言无关**属性：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | `str` | 角色 id |
| `rank` | `int \| null` | 稀有度（4 / 5） |
| `element` | `str \| null` | 元素 |
| `weapon_type` | `str \| null` | 武器类型 |
| `region` | `str \| null` | 所属地区 |
| `body_type` | `str \| null` | 体型 |
| `special_prop` | `str \| null` | 突破属性 |
| `icon` | `str \| null` | 图标 id |
| `birthday` | `list[int] \| null` | 生日 `[月, 日]` |
| `release` | `int \| null` | 上线时间戳 |

### `profile/text.json`（`CharacterText`）

角色**文本**（中 / 英叶子级）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `title_en` / `title_zh` | `str` | 称号 |
| `detail_en` / `detail_zh` | `str` | 角色简介 |
| `constellation_en` / `constellation_zh` | `str` | 命之座 |
| `native_en` / `native_zh` | `str` | 所属 |
| `cv` | `dict[str, str]` | 配音（统一取 en 端点，含 `EN`/`CHS`/`JP`/`KR`） |
| `costume` | `list[Costume]` | 服装数组 |
| `name_card` | `NameCard \| null` | 名片 |
| `special_food` | `SpecialFood \| null` | 特色料理 |

### 故事文件 `en.md` / `zh.md`

每个故事一个文件夹，内含 `en.md`（英文）与 `zh.md`（中文）：YAML frontmatter + 正文段落（段间空一行）。

```markdown
---
id: "10000021"
story: character_details
lang: zh
title: "角色详细"
---

安柏是西风骑士团的侦察骑士。在侦察骑士已然没落的现在，她独自坚守着这份职责。
```

### `result/{story}/{title,full,seg_NN}.json`（`TranslationResult`）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `source` | `str` | 源文本 |
| `source_lang` | `str` | 检出的源语言（`none` 表示纯符号段落） |
| `target_lang` | `str` | 目标语言（`zh-CN`） |
| `mode` | `str` | 路线模式 |
| `providers` | `list[str]` | 实际使用的后端 |
| `languages` | `list[str]` | 语言路线 |
| `steps` | `list[TranslationStep]` | 逐步中间结果（`index`/`kind`/`lang`/`provider`/`text`/`rwkv`） |
| `final` | `str` | 最终译文 |
| `chain` | `str \| null` | 路线标签 |
| `passthrough` | `bool` | 纯符号段落原样保留 |
| `rwkv` | `RwkvMeta \| null` | 本地 RWKV 元数据（url/model/template_sha1/chunks/tokens） |
| `story` / `segment_index` / `source_file` | — | 定位信息 |
| `started_at` / `finished_at` | `str \| null` | 起止时间 |

`segments.json` 为该故事的**汇总**（标题 / 全文 / 各段的源文、语言、最终结果与文件名索引）。`*.mp3` 为可选语音（`--no-tts` 时不生成）。

### 状态文件

- `.translate_stories_state.json`（`BatchState`）：`signature`（参数签名，防混跑）+ `completed`（已完成任务键 `id|story|key`）+ 起止时间。
- `.translate_state.json`（`ChainState`）：单文本链路的签名、源文、语言、当前文本与已完成步骤。

两者均为原子写（临时文件 + 上一版 `.bak` 备份 + 损坏隔离恢复）。

## 架构

代码按 **五层** 组织，依赖方向单向：**cli → services → transport + core**（`transport` / `core` 不反向依赖 `cli` / `services`；`models` 被各层引用）。

| 层 | 模块 | 职责 |
| --- | --- | --- |
| `models` | `src/story_tr/models/` | pydantic v2 数据契约：角色 / 故事 / 翻译结果 / 状态 |
| `core` | `src/story_tr/core/` | 纯逻辑：语言判定与代码映射、链路线与任务构建、分段 / 分块 / 清洗、状态签名、评测指标；**无网络 / 文件 / 框架依赖** |
| `transport` | `src/story_tr/transport/` | 统一 `Translator` 协议 + `google` / `bing` / `baidu` / `rwkv` 实现 + 可选 TTS，全部走 `httpx` |
| `services` | `src/story_tr/services/` | 编排层：`fetch` / `folders` / `translate` / `chain` / `clean` / `status` / `doctor` / `eval`，每个模块暴露 `run(...) -> ExitCode`，把 core 与 transport 组装成用例 |
| `cli` | `src/story_tr/cli/` | `typer` 参数解析 + `rich` 渲染（进度条、表格、日志），只做参数与输出，不实现业务逻辑 |

新增一个翻译后端只需实现 `transport.Translator` 协议并注册，无需改动 core / services / cli。

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

**已移除**（0.x → 1.0）：

- 可选能力：GUI（`PySide6`）、MCP Server（`mcp` SDK）、视频合成（`Pillow` + 系统 ffmpeg）。
- 旧依赖：`deep-translator`、`gTTS`、`tqdm`、`requests`（HTTP 已统一为 `httpx`，TTS 直连 Google TTS 端点，进度条改 `rich`）。

开发 / 测试依赖（`.[dev]`）：`mypy`、`ruff`、`pytest`、`pytest-cov`、`build`、`twine`。

## Breaking changes（0.x → 1.0）

`1.0.0` 是对 `0.y.z` 的**破坏性整体重写**（分层架构 + pydantic 数据契约 + 统一后端抽象），以下内容**不兼容且不提供迁移**：

- **CLI 参数**：命令与参数已收敛（移除 `video`、`rwkv-eval` 更名为 `eval`；移除 `--proxy` / `--no-batch` / `--all` / `--tts` / 各 `--rwkv-*` 命令行开关等旧参数，代理与本地 RWKV 改走环境变量 / `.env`）。
- **结果 JSON**：`result/*.json` 改为 pydantic `TranslationResult` 契约，旧 `0.y.z` 结果视为无效，需**重新翻译**。
- **数据目录 / 档案字段**：`profile/meta.json`、`profile/text.json` 字段改为 snake_case（如 `weapon_type`、`name_card`），旧字段名不兼容；`data/` 可用 `story-tr fetch --refresh` 全量重建。
- **状态文件**：`.translate_stories_state.json` / `.translate_state.json` 改为 `BatchState` / `ChainState` 契约，旧状态不兼容（重新开始即可）。
- **包 API**：`src/story_tr/` 布局重构为 `models/core/transport/services/cli`，旧模块（`chain.py` / `stories.py` / `sync.py` / `clean.py` / `status.py` / `video.py` / `doctor.py` / `folders.py` / `report.py` / `statefile.py` / `exitcodes.py` / `rwkv.py` / `rwkv_eval.py` / `gui/` / `mcp/`）已移除，`python main.py`、`scripts/` 兼容入口亦已移除，统一入口为 `story-tr` / `python -m story_tr`。
- **功能裁剪**：GUI / MCP / video 能力整体移除。

> 迁移方式：`data/` 用 `story-tr fetch --refresh` 重建；`result/` 用 `story-tr translate`（可选 `--overwrite`）重新翻译。

## 版本与变更

本项目遵循 [Semantic Versioning 2.0.0](https://semver.org/lang/zh-CN/)，版本号形如 `MAJOR.MINOR.PATCH`；发布即打标签 `git tag -a vX.Y.Z -m "..."`。

完整历史见 → [CHANGELOG.md](CHANGELOG.md)。
