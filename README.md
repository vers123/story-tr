# story-tr

原神角色故事文本仓库（中文 / 英文对照），用于翻译。

**版本 `v0.8.0`** · 遵循 [Semantic Versioning 2.0.0](https://semver.org/lang/zh-CN/) · 许可 [MIT](LICENSE)

> **数据未入库**：`data/`（角色故事、`character.json` 等）**不在仓库中** —— 它体积大，且可由脚本完整重建。克隆后请先按「[快速开始](#快速开始)」生成。
>
> **模板角色：`10000021`（安柏 / Amber）** —— 该角色的 `profile`（`meta.json`、`text.json`、全部故事 `en.md` + `zh.md`）与 `result/` 下的翻译结果均已完整填写，作为**其它角色的填写模板**与各工具产出的样例。
>
> **来源与版权**：角色与文本取自 Project Amber 的公开 API，**仅供学习/研究用途**；版权归米哈游 / Project Amber 所有。

## 目录

- [安装](#安装)
- [快速开始](#快速开始)
- [数据来源](#数据来源)
- [目录结构](#目录结构)
- [数据约定](#数据约定)
- [数据文件说明](#数据文件说明)
- [命令（CLI）](#命令cli)
- [MCP Server（只读）](#mcp-server只读)
- [依赖与许可](#依赖与许可)
- [版本与变更](#版本与变更)

## 安装

> **建议先建虚拟环境**，避免与系统 Python 的包相互污染。只想要命令的话，用 `pipx` 一步隔离装好。

**方式一：仓库内开发（venv，推荐）**

```bash
git clone https://github.com/vers123/story-tr.git
cd story-tr

python -m venv .venv
.venv\Scripts\activate                 # Windows；macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"                # 只运行用 pip install -e .

story-tr --version
```

**方式二：只要命令（pipx，自动隔离环境）**

```bash
pipx install git+https://github.com/vers123/story-tr
story-tr --version
```

**方式三：不安装，直接用仓库内入口**

```bash
python main.py --help                  # 也可 python -m story_tr / python scripts/xxx.py
```

安装后提供命令 **`story-tr`**；等价入口还有 `python -m story_tr` 与 `python main.py`。

> **MCP Server（可选）**：如需把项目接入 MCP 客户端，额外安装可选依赖：`pip install -e ".[mcp]"`（官方 `mcp` SDK **需 Python ≥3.10**）。安装后提供命令 **`story-tr-mcp`**（等价 `python -m story_tr.mcp`），详见「[MCP Server（只读）](#mcp-server只读)」。

> **venv 下命令不在 PATH**：需先激活，或写全路径 `.venv\Scripts\story-tr.exe`（macOS/Linux 为 `.venv/bin/story-tr`）。

> 工作目录（`data/`、`logs/`、`out/`、`.env`）默认按 `--data-dir` → `$STORY_TR_HOME` → 向上查找 `data/character.json` → 当前目录 的顺序确定。

## 快速开始

```bash
# 1) 安装（见「安装」；此处为 venv 方式）
git clone https://github.com/vers123/story-tr.git
cd story-tr
python -m venv .venv
.venv\Scripts\activate
pip install -e .

# 2) 获取数据（生成 data/，详见「数据来源 → 获取方式」）
story-tr fetch

# 3) 翻译（例：模板角色 10000021 的全部含内容故事；也支持范围 10000002-10000030）
story-tr translate 10000021

# 4) 可选：配置翻译后端密钥与代理
copy .env.example .env                 # macOS/Linux: cp .env.example .env
```

- 只要**数据** → 做到第 2 步即可（产出 `data/`）。
- 只要**翻译结果** → 需要先有 `data/`，再执行第 3 步。
- 站点数据有更新时（新角色 / 故事改动）→ `story-tr fetch --update`（发现新角色 + 只重写变化项）。
- 未安装也能跑：把 `story-tr xxx` 换成 `python main.py xxx` 或 `python scripts/xxx.py`（兼容入口）。

## 数据来源

角色文本取自 **Project Amber**（[yatta.moe](https://gi.yatta.moe/)）的**公开 API**，仅供学习/研究用途；角色、文本等版权归米哈游 / Project Amber 所有。

`data/` 由本仓库脚本抓取生成，**不随仓库分发**（原因与获取步骤见下）。

接口路径中的语言参数：

- `chs` —— **中文**
- `en` —— **英文**

> 例：`https://gi.yatta.moe/api/v2/chs/avatar/10000021` 为中文；`https://gi.yatta.moe/api/v2/en/avatar/10000021` 为英文。

### 接口

| 接口 | 说明 |
| --- | --- |
| `https://gi.yatta.moe/api/v2/{lang}/avatar/{id}` | 角色基础信息：称号 `fetter.title`、简介 `fetter.detail`、命之座 `fetter.constellation`、所属 `fetter.native`、CV `fetter.cv`，以及服装 `other.costume`、名片 `other.nameCard`、特色料理 `other.specialFood` |
| `https://gi.yatta.moe/api/v2/{lang}/avatarFetter/{id}` | 角色故事 `data.story` 与角色语音 `data.quotes` |

示例：

- 中文基础信息：`https://gi.yatta.moe/api/v2/chs/avatar/10000021`
- 英文基础信息：`https://gi.yatta.moe/api/v2/en/avatar/10000021`
- 英文故事 / 语音：`https://gi.yatta.moe/api/v2/en/avatarFetter/10000021`

### 获取方式

`data/` **未随仓库分发**，需在本地获取/生成；以下两种方式产出格式完全一致，可按需选用。

#### 方式一：浏览器访问接口（单个 / 手动核对）

浏览器地址栏直接打开接口 URL，页面即返回 **JSON 原文**，无需登录或额外工具。适合**单个角色**地查看、核对，或手动补填某个字段。

1. 在浏览器中打开某个接口，例如角色故事：
   `https://gi.yatta.moe/api/v2/en/avatarFetter/10000021`
2. 页面显示 JSON，从顶层 `data` 中读取所需内容：
   - `data.fetter.*` / `data.other.*` → 写入 `profile/text.json`、`profile/meta.json`
   - `data.story.*` → 写入 `profile/story/{story}/{en,zh}.md`
   - `data.quotes.*` → 角色语音（当前未落地为文件）
3. 中文与英文各打开一次（把 URL 中的 `en` 换成 `chs`）对照填写。

> 优点：所见即所得，便于**人工核对**；缺点：**不能批量**，逐条复制较繁琐。

#### 方式二：Python 脚本（推荐，批量）

用 `story-tr fetch` 直接请求接口，**批量拉取全部角色**并生成/校正仓库文件（见「[命令（CLI）](#命令cli)」）。

```bash
# 增量抓取：只补缺失项（默认）
story-tr fetch

# 更新数据：发现新角色 + 检测内容变化，只重写变化项
story-tr fetch --update

# 重新抓取：忽略缓存，强制从网站重新拉取并重写全部角色
story-tr fetch --refresh
```

特点：

- 一次跑完全部角色，自动生成 `meta.json` / `text.json` / 各故事 `en.md`+`zh.md` / `character.json`
- 结果缓存到 `data/_fetch_all.json`，可**重复运行**（支持断点续抓）；每条带内容指纹，供 `--update` 检测变化
- **更新数据**：`--update` 从站点列表发现新角色、按指纹只重写变化项，并同步更新 `character.json`
- **重新抓取**：加 `--refresh` 忽略缓存强制重抓（或先删除 `data/_fetch_all.json` 再运行）
- 抓取与生成过程各有一条 `tqdm` 进度条（`抓取` / `生成`，按角色）
- 请求自带重试，结尾输出**校验信息**（slug 与 `stories` 是否一致、无故事角色、名字差异等）

### `story` 结构

`data.story` 为对象，键为序号，值为 `{ title, title2, text, text2, tips }`。

- 序号 `0`：角色详细 → `character_details`
- 序号 `1`~`5`：角色故事 1~5 → `character_story_1`~`character_story_5`
- 倒数第 `2` 项：角色专属剧情 → `name_1`
- 倒数第 `1` 项：神之眼 / 邪眼 等 → `name_2`

注意：正文可能位于 `text` 或 `text2`（如 Furina 的 `name_2`，`title` 为 `???` 占位、真实名称在 `title2`，正文在 `text2`）。

### `quotes` 结构

`data.quotes` 为对象，值为 `{ title, audio, text, tips, tasks }`（角色语音）。

## 目录结构

```
.
├── data/                               # ★ 不入库：由 `story-tr fetch` 生成（见「数据来源」）
│   ├── character.json                  # 角色索引（id/名称 + path/stories）
│   ├── _fetch_all.json                 # 原始抓取缓存（断点续抓 + 内容指纹 hash，供 --update 检测变化）
│   └── story/character/{id}/
│       ├── profile/
│       │   ├── meta.json                 # 语言无关属性（rank/element/birthday/…）
│       │   ├── text.json                 # 角色文本（称号/简介/命之座/所属/cv/服装/名片/料理）
│       │   └── story/
│       │       ├── character_details/{en,zh}.md
│       │       ├── character_story_1/{en,zh}.md
│       │       ├── character_story_2/{en,zh}.md
│       │       ├── character_story_3/{en,zh}.md
│       │       ├── character_story_4/{en,zh}.md
│       │       ├── character_story_5/{en,zh}.md
│       │       ├── {name_1}/{en,zh}.md      # 角色专属剧情（文件夹名为标题 slug）
│       │       └── {name_2}/{en,zh}.md      # 神之眼 / 邪眼 等
│       └── result/{story}/               # 由 `story-tr translate` 生成
│           ├── title.json / title.mp3    # 标题（frontmatter 的 title，如「角色详细」）
│           ├── full.json / full.mp3      # 全文（源文/语言/逐步/最终译文 + 语音）
│           ├── seg_01.json / seg_01.mp3  # 各段同结构
│           └── segments.json             # 汇总
├── src/story_tr/                       # 包源码（CLI 实现）
│   ├── __init__.py                     # __version__
│   ├── __main__.py                     # python -m story_tr
│   ├── cli.py                          # 顶层 CLI（子命令注册）
│   ├── paths.py                        # 工作目录解析
│   ├── sync.py                         # story-tr fetch
│   ├── folders.py                      # story-tr folders
│   ├── chain.py                        # story-tr chain（翻译链核心）
│   ├── stories.py                      # story-tr translate（批量翻译）
│   ├── clean.py                        # story-tr clean（清理 result/）
│   └── mcp/                            # story-tr-mcp（MCP Server，只读；需可选依赖）
│       ├── server.py                   # Tool / Resource 注册 + stdio 运行
│       ├── tools.py                    # 只读工具函数（不依赖 mcp）
│       └── resources.py                # 只读资源函数（不依赖 mcp）
├── scripts/                            # 兼容入口（转发到包内实现）
│   ├── sync_from_site.py               # = story-tr fetch
│   ├── sync_story_folders.py           # = story-tr folders
│   ├── translate_chain.py              # = story-tr chain
│   └── translate_stories.py            # = story-tr translate
├── tests/                              # pytest 单元测试
├── .github/workflows/ci.yml            # CI：ruff + pytest（3.9 / 3.11 / 3.13）
├── logs/                               # 运行日志（不入库）
├── out/                                # translate_chain 的结果 json/mp3（不入库）
├── .venv/                              # 虚拟环境（不入库）
├── .env                                # 密钥配置（不入库，见 .env.example）
├── pyproject.toml                      # 打包元数据 + 入口点 + ruff/pytest 配置
├── main.py                             # 兼容入口：python main.py <子命令>
├── requirements.txt
├── LICENSE                             # MIT
└── README.md
```

## 数据约定

- **语言**：接口 `chs` = 中文、`en` = 英文；文本按**叶子级分语言**（如 `name_en` / `name_zh`、`title_en` / `title_zh`）。
- **段落**：站点正文用**字面量 `\n`**（反斜杠 + n）换行，**单换行即一段**；写入 md 时段间空一行。
- **故事文件夹名（slug）**：由**英文标题**生成 —— 小写、去 `'s`、非 `[a-z0-9]` 字符转下划线（如 `A Legend of Sword` → `a_legend_of_sword`）。
- **占位标题**：仅当主标题是占位（`???` / `？？？`）时才改用 `title2`（如钟离 `vision` → `gnosis`、Furina `???` → `Vision`）。
- **`cv`**：统一取 **en 端点**（原始配音名），含 `EN` / `CHS` / `JP` / `KR` 四种语言。
- **未收录**（数值 / 枚举 id / 重复 / 空）：`route`、`upgrade`、`ascension`、`items`、`talent`、`constellation`、`dictionary`。

## 数据文件说明

### `data/character.json`

角色**索引**，每条一个角色，含基本信息与所属文件夹结构：

| 字段 | 说明 |
| --- | --- |
| `id` | 角色 id（与目录名一致） |
| `name_en` / `name_zh` | 中英文名 |
| `path` | 角色目录（相对项目根），如 `data/story/character/10000021` |
| `stories` | `profile/story/` 下的故事文件夹名列表 |

> `path` / `stories` 是**文件夹重命名**的依据：以 `character.json` 为准同步重命名对应文件夹（见 `story-tr folders`）。

### `profile/meta.json`

该角色**语言无关**的属性：

| 字段 | 说明 |
| --- | --- |
| `id` | 角色 id |
| `rank` | 稀有度（4 / 5） |
| `element` | 元素（Fire / Ice / Wind …；旅行者与 Manekin/Manekina 为 `null`） |
| `weaponType` | 武器类型枚举 |
| `region` | 所属地区枚举 |
| `bodyType` | 体型枚举 |
| `specialProp` | 突破属性枚举 |
| `icon` | 图标 id |
| `birthday` | 生日 `[月, 日]` |
| `release` | 上线时间戳 |

### `profile/text.json`

该角色的**文本**（与 `meta.json` 互补：一个装文本，一个装语言无关属性）：

| 字段 | 来源 | 说明 |
| --- | --- | --- |
| `title_en` / `title_zh` | `fetter.title` | 称号 |
| `detail_en` / `detail_zh` | `fetter.detail` | 角色简介 |
| `constellation_en` / `constellation_zh` | `fetter.constellation` | 命之座 |
| `native_en` / `native_zh` | `fetter.native` | 所属 |
| `cv` | `fetter.cv`（en 端点） | 配音，统一用 en 格式，含 `EN`/`CHS`/`JP`/`KR` |
| `costume` | `other.costume` | 服装，数组；每项 `{ name_en, name_zh, description_en, description_zh }` |
| `nameCard` | `other.nameCard` | 名片：`{ name_en, name_zh, description_en, description_zh }`（无则 `null`） |
| `specialFood` | `other.specialFood` | 特色料理：`{ name_en, name_zh }`（无则 `null`） |

### 故事文件 `en.md` / `zh.md`

每个故事一个文件夹，内含 `en.md`（英文）与 `zh.md`（中文）。内容为 YAML frontmatter + 正文段落（段间空一行）。下例取自模板角色 `10000021`：

```markdown
---
id: "10000021"
story: character_details
lang: zh
title: "角色详细"
---

安柏是西风骑士团的侦察骑士。在侦察骑士已然没落的现在，她独自坚守着这份职责。

初来乍到的旅客用不着三天，便能与这位热情似火的少女打成一片。
```

## 命令（CLI）

安装见「[安装](#安装)」；未安装时把 `story-tr` 换成 `python main.py`（或用兼容 shim `python scripts/xxx.py`）。

所有命令的**长任务统一用 `tqdm` 显示进度条**；都可加全局参数 `--data-dir <目录>` 指定工作目录。

| 子命令 | 作用 |
| --- | --- |
| `story-tr fetch [--update\|--refresh]` | 拉取并生成/校正角色数据；`--update` 发现新角色并只重写变化项 |
| `story-tr folders` | 按 `character.json` 的 `stories` 同步重命名故事文件夹 |
| `story-tr chain [text]` | 单文本「来回翻译 N 次」链路（最后译回中文） |
| `story-tr translate <id/范围>` | 批量翻译角色故事，结果落入 `{id}/result/`（默认合并请求；`--full-only` 只出全文） |
| `story-tr clean <id/范围>` | 清理 `{id}/result/` 下的翻译产物（保留 `result/` 目录本身） |

### `story-tr fetch`

从 Project Amber（yatta.moe）拉取各角色的 en / chs 数据（`avatar` + `avatarFetter`），生成/校正 `meta.json`、`text.json`、全部故事 `en.md`/`zh.md`，并重写 `data/character.json`（即「[数据来源](#数据来源) · 获取方式 · 方式二」）。

```bash
# 增量：只补缺失项（默认，走缓存 data/_fetch_all.json）
story-tr fetch

# 更新数据：发现新角色 + 检测已有角色内容变化，只重写变化项（建议定期跑）
story-tr fetch --update

# 保险丝：忽略缓存，强制重抓并重写全部角色
story-tr fetch --refresh
```

| 模式 | 联网拉站点列表 | 重抓范围 | 重写范围 |
| --- | --- | --- | --- |
| `fetch`（默认） | ✗ | 仅缺失 | 全部（幂等） |
| `fetch --update` | ✓ | 全部（为检测变化） | **仅变化项** |
| `fetch --refresh` | ✗ | 全部 | 全部 |

行为说明：

- 抓取结果缓存到 `data/_fetch_all.json`，可**重复运行**（支持断点续抓）；每条记录带**内容指纹 `hash`**
- **`--update`（更新数据）**：
  - 从站点列表接口 `/api/v2/{lang}/avatar` 取**当前全部角色 id**，按 `{id}-{element}` → `{id}` 归一（旅行者的分元素条目归到同一角色），据此**发现新角色**；
  - 全量重抓后比对内容指纹，**只有内容变化的角色才重写文件**，并输出**变更报告**（新增角色 / 站点已移除 / 变化明细 / 未变化数）；
  - **`data/character.json` 同步更新**：新角色自动追加（`name_en`/`name_zh`/`path`/`stories` 自动派生），站点新增的故事自动追加到该角色 `stories`；
  - 旧缓存记录没有 `hash` 时，本次会全部重抓以**首次建立指纹基线**（之后才是增量）；
  - 站点已移除的 id **本地保留**（仅在报告中提示），不会自动删除。
- 首次使用（本地无 `data/character.json`）会自动按站点列表初始化
- 想从零重抓，也可直接**删除 `data/_fetch_all.json`** 后运行（效果等同 `--refresh`）
- 进度：`抓取`（本次待抓角色数）与 `生成`（写入文件的角色数）两条 `tqdm` 进度条，均为按角色
- 按「[数据约定](#数据约定)」生成 slug、段落、`cv` 等
- 运行结尾打印**校验信息**：`slug` 与 `character.json` 的 `stories` 是否一致、无故事角色、名字差异等

### `story-tr folders`

按 `data/character.json` 的 `stories` 同步重命名各角色 `profile/story/` 下的文件夹（幂等，可反复运行）。

```bash
story-tr folders
```

行为说明：

- 进度：`同步文件夹`（按角色）`tqdm` 进度条
- 重命名行经 `tqdm.write` 输出，不与进度条混排
- 已一致的角色跳过（幂等）；`stories` 为空（站点无故事，如 `10000117` / `10000118`）的角色不告警

### `story-tr chain`

把一段文本按语言路线来回翻译 N 次，最后译回中文（"翻译接龙"）。

```bash
# 默认：random 抽 20 个语言，默认文本 Hello, World
story-tr chain "Hello, World"

# 固定链（asia / europe / exotic，各 20 个语言）
story-tr chain --mode fixed --chain asia

# 自定义语言序列（不限 20 个）
story-tr chain "你好" --mode custom --languages ja,ko,ar

# 指定后端（默认 google → bing → baidu 自动回退）
story-tr chain --provider baidu
```

| 参数 | 说明 |
| --- | --- |
| `text`（位置参数） | 待翻译文本，默认 `Hello, World` |
| `--mode` | `fixed` / `random`(默认) / `custom` |
| `--chain` | `fixed` 模式用哪套链：`asia`(默认) / `europe` / `exotic` |
| `--languages` | `custom` 模式的语言序列，逗号分隔 |
| `--provider` | `google`(默认) / `bing` / `baidu`；不指定则 `google → bing → baidu` 自动回退 |
| `--steps` | `random` 模式抽取语言数，默认 `20` |
| `--proxy` | 代理地址（覆盖 `.env` 的 `HTTPS_PROXY`/`HTTP_PROXY`） |
| `--min-interval` | Google 请求最小间隔秒数，避免触发限流（默认 `0.3`，`0` 关闭） |
| `--balance` | 多后端轮转分摊请求（默认关闭，见「通道分流」） |
| `--reset` | 忽略旧进度，从头开始 |
| `--dry-run` | 只显示路线，不执行翻译 |
| `--no-file-log` | 不写文件日志 |
| `--no-tts` | 不生成最终译文的语音 |

行为说明：

- 流程：`langdetect` 判语言（固定 seed，可复现）→ 非英文先译成英文（不计入 N）→ 按路线翻译 N 次 → 译回中文
- **长文本自动分块**：单次请求上限 `1500` 字（`MAX_CHARS_PER_REQUEST`），超长按句末标点切成多块翻译后拼接 —— 避免 Google 免费端点对超长文本返回 400
- **通道分流**（`--balance`）：多后端轮转分摊请求，可把 Google 的每日配额分摊到 Bing；默认关闭（稳定优先，备用通道更慢/更易限流）
- 后端与密钥（写进 `.env`，见 `.env.example`）：三个后端都是「**优先官方 API，无 key 回退免费通道**」
  - `google`：配 `GOOGLE_API_KEY` → 走 Cloud Translation v2（稳定、有配额、支持长文本）；未配则走 `clients5.google.com` 的 `dict-chrome-ex` 免 key 端点，失败再回退 deep-translator 的 `GoogleTranslator`
  - `bing`：配 `BING_API_KEY`（+ `BING_REGION`）→ 走 Azure 官方接口；未配则走 Bing 网页免 key 通道（`cn.bing.com` / `www.bing.com` 多域名轮换，可用 `BING_WEB_BASE` 指定）
  - `baidu`：配 `BAIDU_APPID` + `BAIDU_APPKEY` → 走官方翻译开放平台
- 后端选择：默认 `google → bing → baidu` 依次回退（`--provider` 可固定单个）
- 错误处理（`tenacity`）：**被限流**（429 / `TooManyRequests` / 百度 `Invalid Access Limit`/`54003`）→ **退避等待后重试同一后端**（5s 起、指数增长、上限 120s，共 10 次），不会因为备用后端不可用而整轮中止；**连接类**错误（超时/DNS/SSL/拒连）→ 立即切换后端（`google → bing → baidu`，都不行则出报告）；**业务类**错误（内容/参数/空结果等）→ 重试 5 次后暂停并出报告
- **稳定性优先**：每个后端都按最小间隔节流 —— 实际间隔取 `max(--min-interval, 该后端下限)`；百度官方标准版 **QPS=1**，固定下限 `1.1s`，Google/Bing 下限 `0`（由全局 `--min-interval` 控制，默认 `0.3s`）。宁可慢也不中断
- 配额提示：Google 免费端点约 **5 请求/秒、20 万请求/天**；若触发的是**日配额**，需次日再跑（进度可续跑）
- 语音：用 **gTTS**（MIT，Google 翻译 TTS 的封装）把**最终中文译文**读成 `out/<时间戳>.mp3`；长文本由 gTTS 自动分词分请求；`--no-tts` 可关闭。仅完整跑完（有最终译文）时才生成
- 续跑：进度存根目录 `.translate_state.json`；重跑（参数一致）自动从断点继续，参数不一致需 `--reset`
- 进度：单条 `tqdm` 进度条（`翻译链`，按步）；`set_description` 实时显示当前 `src→tgt`，断点续跑时从已完成步数继续
- 日志：明细（含每步中间结果）写入 `logs/YYYYMMDD_HHMMSS.log`；控制台仅输出告警与关键结果，避免与 tqdm 进度条混排
- 结果：`out/<时间戳>.json`（含元数据与逐步中间结果）

**各后端推荐参数**（实测）：

| 后端 | 瓶颈 | 建议 `--min-interval` | 说明 |
| --- | --- | --- | --- |
| `google`（免费 clients5） | 网络延迟 ~0.5s | `0.3` | 比 `1.0` 快约 2–3×；再降（`0.1`/`0`）无收益，`0.3` 仍远低于 5 请求/秒 |
| `bing`（免费网页） | 网络延迟 ~1.2s | `0.3`～`0.5` | 间隔不敏感，延迟主导 |
| `baidu`（官方） | **QPS=1** | `1.1`（内置下限，调不低） | 官方标准版限速，代码已内置 |

> 实测同批 `10000021/amber_journal`（5 份）：`--min-interval 0.3` ≈ **59s**，`1.0` ≈ **110s**，两者均**零限流**。
> 并发实测：同批 `--workers 4 --min-interval 0.25` ≈ **34s**（≈1.7×，总速率约 **4 请求/秒**）。
> 全量估算（现有 `data/`：13,646 份结果 / 974 个故事）：**逐条**约 30 万次调用 → 单线程 ≈45 h、`--workers 4 --min-interval 0.25` ≈21 h；**批量（默认）**约 2.7 万次调用 → **≈2 h**。

### `story-tr translate`

对某角色的故事（`zh.md`）跑翻译链，结果写入 `{id}/result/{story}/`（分段 + 全文，含语音）。

```bash
# 单个角色（默认处理其全部含内容故事）
story-tr translate 10000021

# 只处理指定故事
story-tr translate 10000021 --stories amber_journal,vision

# 按 id 范围（含两端；不在 character.json 中的 id 自动跳过并提示）
story-tr translate 10000002-10000030

# 组合：多个 id / 多段范围
story-tr translate 10000021,10000025-10000030 --stories vision

# 全部角色
story-tr translate --all-characters

# 并发提速（约 4 请求/秒；配合 --min-interval 控制速率）
story-tr translate --all-characters --workers 4 --min-interval 0.25

# 覆盖已有结果、关闭语音
story-tr translate 10000021 --overwrite --no-tts

# 只要最终译文（跳过逐段任务）
story-tr translate --all-characters --full-only

# 只要逐段译文（跳过全文）
story-tr translate 10000021 --segments-only

# 关闭批量（同一故事的逐段在每步各自发请求）
story-tr translate 10000021 --no-batch
```

| 参数 | 说明 |
| --- | --- |
| `char_ids`（位置参数） | 角色 id / 范围：`10000021`、`10000002-10000030`、`10000021,10000025-10000030`（空格或逗号分隔） |
| `--all-characters` | 处理 `data/character.json` 中的**全部角色**（与 `char_ids` 互斥） |
| `--stories` | 只处理指定故事文件夹，逗号分隔（**不指定 = 处理全部含内容故事**） |
| `--all` | 显式处理全部含内容故事（即默认行为） |
| `--full-only` | 只跑**全文**（跳过逐段任务；**标题始终翻译**） |
| `--segments-only` | 只跑**逐段**任务（跳过全文；**标题始终翻译**） |
| `--mode` / `--chain` / `--languages` / `--steps` | 同 `translate_chain.py`（默认 `fixed` + `asia`） |
| `--provider` / `--proxy` | 同 `translate_chain.py` |
| `--min-interval` | 同 `translate_chain.py`（Google 请求最小间隔，默认 `0.3`） |
| `--workers` | 并发线程数（默认 `1`；建议 `3~4`，配合 `--min-interval` 控制总速率） |
| `--no-batch` | 关闭批量：同一故事的标题与逐段在每步**各自发请求**（默认合并为一次） |
| `--balance` | 同 `translate_chain.py`（多后端轮转分摊，默认关闭） |
| `--overwrite` | 已有结果也重跑（默认跳过） |
| `--reset` | 忽略旧批量状态 |
| `--no-tts` / `--no-file-log` | 同上 |

行为说明：

- **角色选择**：位置参数支持单个 id / `起始-结束` 范围 / 逗号组合；按 `character.json` 顺序处理，范围内不存在的 id 自动跳过并提示
- **故事选择**：不指定 `--stories` 时，默认处理该角色的**全部含内容故事**
- 分段：按 `zh.md` 正文段落（去 frontmatter）；每段独立跑完整链路
- 全文：整篇再独立跑一遍完整链路
- 标题：`zh.md` frontmatter 的 `title`（如「角色详细」）**始终**跑一遍完整链路，写入 `title.json`
- **全部角色**（`--all-characters`）：处理 `data/character.json` 中的全部角色，进度用「角色」总进度条
- 输出：`result/{story}/title.json|mp3`、`full.json|mp3`、`seg_NN.json|mp3`、`segments.json`（汇总：标题/段号/源文/语言/最终结果/文件名）
- 每份 json = 链路结果（源文/检出语言/语言路线/逐步/最终译文）+ `story` / `segment_index` / `source_file`
- 续跑：`.translate_stories_state.json`（批量级进度，参数一致才续；键为 `id|story|key`）；已有结果默认跳过
  - 参数与上次不一致时会要求加 `--reset`；`story-tr clean` 会**重置批次签名**，之后可用新参数直接运行
- `random` 模式下每份独立随机；进度用 `tqdm`（批量时「角色」总进度条 + 每个角色的「翻译链」进度条）

**批量请求**（默认开启）：同一故事的**标题 + 全文 + 各分段**在每一语言步合并为**一次请求** —— 免费通道 `clients5` 支持同一请求多个 `q`，响应与 `q` 一一对应。
- 实测：同一故事逐条 `29s` → 批量 `7s`（≈4×）；全量调用量 30 万 → **2.7 万**（≈11×，即 21h → ≈1.9h）
- 等价性：`fixed`/`custom` 下与逐条**结果完全一致**（连续 6 组对照零差异）
- 每步请求仍按「组内累计 ≤ `1500` 字符」分块（沿用长文本分块阈值），通道不支持批量或返回形状异常时**自动退化为逐条**
- `random` 模式（每份独立随机）下**自动关闭**批量，保持每份随机链独立；`--no-batch` 可显式关闭
- 若某次请求的形状不符（如端点变更），会退化为逐条，不影响结果正确性
- **注意**：批量开启（默认）时，标题、逐段与全文共用同一请求（平均 907 字符/组，上限 1500），因此 `--full-only` / `--segments-only` 只省约 **20%**（≈1.9h → ≈1.5h）；它们的主要价值是减少产物文件与磁盘占用

### `story-tr clean`

清理 `{id}/result/` 下的翻译产物（`title.*` / `full.*` / `seg_NN.*` / `segments.json` 等），用于重跑、换链或释放空间。

```bash
# 清理单个角色的 result/ 内容
story-tr clean 10000021

# 范围 / 全部角色
story-tr clean 10000002-10000030
story-tr clean --all-characters

# 只清指定故事
story-tr clean 10000021 --stories vision

# 只预览，不删除
story-tr clean --all-characters --dry-run
```

| 参数 | 说明 |
| --- | --- |
| `char_ids` | 角色 id / 范围（空格或逗号分隔）：`10000021` / `10000002-10000030` / `10000021,10000025-10000030` |
| `--all-characters` | 处理 `character.json` 中的全部角色（与 `char_ids` 互斥） |
| `--stories` | 只清理指定故事，逗号分隔（不指定 = 清理 `result/` 下的全部内容） |
| `--dry-run` | 只预览将删除的内容（目录、文件数、占用大小），不实际删除 |
| `-y` / `--yes` | 跳过交互确认 |
| `--keep-state` | 保留 `.translate_stories_state.json` 记录（默认同步清理） |

行为说明：

- 只删 `result/` 里的内容，**保留 `{id}/result/` 目录本身**（维持目录结构约定）
- 默认**交互确认**（先列出目录、文件数与占用大小）；`--dry-run` 预览、`-y` 跳过确认
- **默认同步清理**状态记录：移除 `.translate_stories_state.json` 中对应的 `id|story|key` 条目，并**重置批次签名** —— 否则重跑 `translate` 会把这些任务当成「已完成」而跳过，或因签名不一致要求 `--reset`；`--keep-state` 可保留
- 角色选择语义与 `story-tr translate` 完全一致（含范围内不存在 id 的跳过提示）

## MCP Server（只读）

把项目接入支持 **MCP**（Model Context Protocol）的客户端（Trae / Claude Desktop / Cursor 等），让模型直接查询角色、故事与翻译结果。

```bash
# 安装可选依赖（官方 mcp SDK，需 Python ≥3.10）
pip install -e ".[mcp]"          # 已装好的话：pip install "mcp>=2.0,<3"

# 启动（stdio；通常由客户端的 MCP 配置自动拉起，无需手动运行）
story-tr-mcp                     # 等价：python -m story_tr.mcp
```

**客户端配置示例**（用 `STORY_TR_HOME` 指向仓库根目录）：

```json
{
  "mcpServers": {
    "story-tr": {
      "command": "story-tr-mcp",
      "env": { "STORY_TR_HOME": "D:/path/to/story-tr" }
    }
  }
}
```

工作目录与 CLI 一致：`$STORY_TR_HOME` → 向上查找 `data/character.json` → 当前目录。

### Tools（检索）

| Tool | 说明 |
| --- | --- |
| `list_characters` | 全部角色：id / 中英名 / 故事数 |
| `list_stories(char_id)` | 某角色的故事：文件夹名 / 中英标题 / 段落数 |
| `get_result_summary(char_id, story)` | 翻译结果概要（标题 / 全文 / 各段的源文与最终译文） |
| `read_result_item(char_id, story, key)` | 单条明细：`key` 取 `full` / `title` / `seg_NN` |

### Resources（内容）

| URI | 内容 |
| --- | --- |
| `story://characters` | 全部角色索引（JSON） |
| `story://character/{id}` | 角色 `meta.json` / `text.json` / 故事清单（JSON） |
| `story://story/{id}/{story}/{lang}` | 故事原文 Markdown（`lang` = `zh` / `en`，含 frontmatter） |
| `story://result/{id}/{story}` | 翻译结果概要 `segments.json`（JSON） |

> 本版为 **P1：只读** —— 不联网、不写盘；`translate` / `fetch` / `clean` 等写操作未暴露（后续版本再评估）。

## 依赖与许可

本项目以 **MIT** 许可发布，见 [LICENSE](LICENSE)（Copyright © 2026 vers123）。

第三方依赖（以 `pyproject.toml` 的 `dependencies` 为准；`requirements.txt` 为便捷镜像）：

| 依赖 | 用途 | 许可 |
| --- | --- | --- |
| [deep-translator](https://github.com/nidhaloff/deep-translator) | Bing / 百度 / Google 翻译后端 | MIT |
| [gTTS](https://github.com/pndurette/gTTS) | Google 翻译 TTS（`out/*.mp3`） | MIT |
| [langdetect](https://github.com/Mimino666/langdetect) | 源语言判定 | Apache-2.0 |
| [requests](https://github.com/psf/requests) | HTTP 请求 | Apache-2.0 |
| [python-dotenv](https://github.com/theskumar/python-dotenv) | 读取 `.env` | BSD-3-Clause |
| [tqdm](https://github.com/tqdm/tqdm) | 进度条 | MPL-2.0 / MIT |
| [tenacity](https://github.com/jd/tenacity) | 重试 / 退避（翻译后端、数据抓取） | Apache-2.0 |

可选依赖（**MCP Server**，`pip install -e ".[mcp]"`，需 Python ≥3.10）：

| 依赖 | 用途 | 许可 |
| --- | --- | --- |
| [mcp](https://github.com/modelcontextprotocol/python-sdk) | MCP Server（官方 Python SDK，`MCPServer`） | MIT |

> 说明：`beautifulsoup4` 为 `deep-translator` 的**传递依赖**（MIT），由 pip 自动安装，无需单独声明。

> 选型原则：优先 **MIT / BSD / Apache-2.0** 等宽松许可。**未采用 GPL / LGPL 的库**（例如 `edge-tts` 为 GPLv3 / LGPLv3，与 MIT 项目不兼容），以免引入传染性义务。

> 开发与测试：`pip install -e ".[dev,mcp]"` → `ruff check .` + `pytest`（MCP 测试在未装 mcp 的版本自动跳过；CI 见 `.github/workflows/ci.yml`）。

## 版本与变更

本项目遵循 [Semantic Versioning 2.0.0](https://semver.org/lang/zh-CN/)，版本号形如 `MAJOR.MINOR.PATCH`：

| 位 | 何时递增 | 对本项目而言 |
| --- | --- | --- |
| `MAJOR` | 不兼容的变化 | 破坏性调整：数据目录结构、`character.json` / `*.json` 字段、CLI 参数语义 |
| `MINOR` | 向后兼容地新增功能 | 新增脚本、CLI 参数、翻译后端、输出文件 |
| `PATCH` | 向后兼容的缺陷修复 | 修 bug、调参、性能与稳定性改进 |

- **`0.y.z` 为初始开发阶段**：公共 API（数据目录结构、`*.json` 字段、CLI 行为）**可能随时变化**，暂不保证稳定；达到 `1.0.0` 后再严格执行兼容性。
- 预发布用 `-` 后缀（如 `0.2.0-beta.1`），构建元数据用 `+`（如 `0.1.0+build.5`）。
- 发布即打标签：`git tag -a vX.Y.Z -m "..."`（`v` 仅为标签约定，版本号本身遵循 SemVer）。

### 变更记录

#### 0.8.0（2026-10-09）

- **新增**：**MCP Server（只读）** —— 新增子包 `story_tr/mcp/` 与命令 `story-tr-mcp`（等价 `python -m story_tr.mcp`），通过 **stdio** 把角色故事与翻译结果暴露给 MCP 客户端
  - Tools：`list_characters` / `list_stories` / `get_result_summary` / `read_result_item`
  - Resources：`story://characters`、`story://character/{id}`、`story://story/{id}/{story}/{lang}`、`story://result/{id}/{story}`
  - 只读：不联网、不写盘；`translate` / `fetch` / `clean` 等未暴露
  - 基于官方 `mcp` SDK（v2 `MCPServer`），作为**可选依赖** `.[mcp]`（需 Python ≥3.10）；未安装不影响其余功能，主包仍支持 ≥3.9

#### 0.7.0（2026-10-09）

- **新增**：**标题也翻译** —— `zh.md` frontmatter 的 `title`（如「角色详细」）**始终**跑一遍完整链路，输出 `result/{story}/title.json|mp3`，并在汇总 `segments.json` 中新增 `title` 字段（含 `source` / `languages` / `final` / `json` / `mp3`）
  - 该任务**不受** `--full-only` / `--segments-only` 影响；批量开启时与全文、各分段**合并进同一请求**

#### 0.6.2（2026-10-09）

- **修复**：批量请求模式下，google 免 key 通道的**连接类异常**（超时 / DNS / SSL 等）未归一化为 `ConnectionIssue`，会以原始 `requests` 异常穿透重试与后端切换逻辑，导致 CLI 打印 traceback 崩溃；现改为归一化处理，**自动切换到下一后端**（单条路径原本已正确处理，仅批量分支遗漏）

#### 0.6.1（2026-10-09）

- **文档**：修正 0.6.0 关于批量的数字与表述 —— 全量批量约 **2.7 万**次调用（≈1.9h）；批量开启时 `--full-only` / `--segments-only` 只省约 **20%**（≈1.9h → ≈1.5h），主要价值是减少产物文件与磁盘占用

#### 0.6.0（2026-10-09）

- **新增**：**批量请求**（默认开启）—— 同一故事的全文与各分段在每一语言步合并为**一次请求**（`clients5` 支持重复 `q`，响应与 `q` 一一对应）
  - 实测同一故事逐条 `29s` → 批量 `7s`；全量调用量 30 万 → **2.7 万**（≈11×，21h → ≈1.9h）
  - `fixed`/`custom` 下与逐条**结果完全一致**（6 组对照零差异）；形状异常/通道不支持时**自动退化为逐条**
  - `random` 模式自动关闭批量；`--no-batch` 可显式关闭
- **新增**：`--full-only` / `--segments-only` —— 只产出全文或只产出逐段
- **变更**：`chain.py` 抽出 `_step_loop` / `_translate_with_providers`，单条与批量共用同一套「限流退避 + 业务重试 + 后端切换」逻辑

#### 0.5.1（2026-10-09）

- **修复**：`story-tr clean` 清理后未重置批次签名，导致紧接着 `translate` 报「参数与上次批量任务不一致」并要求 `--reset`
  - `clean` 现在移除进度记录的同时**重置签名**；`translate` 遇到无签名状态时**沿用剩余记录**并采用新签名（不再要求 `--reset`）

#### 0.5.0（2026-10-09）

- **新增**：`story-tr clean <id/范围>` —— 清理 `{id}/result/` 下的翻译产物（保留 `result/` 目录本身）
  - 角色选择与 `translate` 一致（id / 范围 / `--all-characters`），`--stories` 可只清指定故事
  - 默认交互确认（列出目录 / 文件数 / 占用）；`--dry-run` 预览、`-y/--yes` 跳过
  - 默认**同步清理** `.translate_stories_state.json` 中对应 `id|story|key` 记录（`--keep-state` 保留），避免重跑被跳过

#### 0.4.2（2026-10-09）

- **文档**：移除安装一节中的「什么时候需要重新安装」提示，保持安装说明简洁
- 纯文档变更：无代码行为变化（PATCH）

#### 0.4.1（2026-10-09）

- **文档**：安装一节补充「[什么时候需要重新安装](#安装)」—— editable 改代码免重装，改 `pyproject.toml` 的依赖/入口点/版本需重装
- **文档**：快速开始补充数据更新命令 `story-tr fetch --update`；`_fetch_all.json` 说明补充「内容指纹」
- **工程**：`.gitignore` 补充覆盖率、类型检查缓存与系统临时文件
- 纯文档与配置变更：无代码行为变化（PATCH）

#### 0.4.0（2026-10-09）

- **新增**：`story-tr fetch --update` —— **更新数据**：从站点角色列表发现新角色（`{id}-{element}` 归一）、按内容指纹检测已有角色变化、**只重写变化项**并输出变更报告
- **新增**：抓取记录带**内容指纹 `hash`**（存于 `data/_fetch_all.json`），旧缓存首次运行会建立基线
- **新增**：`data/character.json` 同步更新 —— 新角色自动追加、站点新增的故事自动追加到 `stories`
- **修复**：本地无 `data/character.json` 时（全新克隆）自动按站点列表初始化，不再直接报错

#### 0.3.1（2026-10-09）

- **文档**：安装改为以 **venv** 为主、**pipx** 为「只要命令」的推荐方式，补上激活与「venv 下命令不在 PATH」的提示
- 纯文档变更：无代码、数据格式或 CLI 行为变化（PATCH）

#### 0.3.0（2026-10-09）

- **新增**：打包为**可安装 CLI**（`pyproject.toml` + `src/story_tr/`），提供 `story-tr fetch` / `folders` / `chain` / `translate` 四个子命令；等价入口 `python -m story_tr`、`python main.py`
- **新增**：**工作目录可配置** —— `--data-dir` → `$STORY_TR_HOME` → 向上查找 `data/character.json` → 当前目录
- **新增**：`tests/`（pytest）与 GitHub Actions CI（ruff + pytest，Python 3.9 / 3.11 / 3.13）
- **变更**：原 `scripts/*.py` 改为**兼容 shim**（旧命令仍可用），实现移入 `story_tr` 包
- **变更**：依赖以 `pyproject.toml` 为准，`requirements.txt` 保留为便捷镜像

#### 0.2.0（2026-10-09）

- **新增**：`translate_stories.py` 支持按 **id / 范围** 选角色（`10000002-10000030`、`10000021,10000025-10000030`）
- **新增**：`--workers N` 并发翻译（实测同批 59.3s → 34.2s，约 1.7×）
- **新增**：`--balance` 多后端轮转分摊请求（默认关闭）
- **修复**：超长文本按 ≤1500 字自动分块 —— 此前长故事在 Google 免费端点报 400，会重试 5 次后 `Paused` **中断整轮**
- **修复**：`langdetect` 并发下抛 `Need to load profiles`（改为首次加锁预热）
- **变更**：CLI 统一（两脚本共用参数分组）；`--stories` 不指定时默认处理**全部含内容故事**（原默认 `amber_journal`）

#### 0.1.1（2026-10-09）

- **文档**：重组 README —— 新增「[快速开始](#快速开始)」「[目录](#目录)」，调整章节顺序
- **文档**：补充「[版本与变更](#版本与变更)」（SemVer 2.0.0 规则与变更记录）
- 纯文档变更：无代码、数据格式或 CLI 行为变化（PATCH）

#### 0.1.0（2026-10-09）

首个版本。

- **数据**：`sync_from_site.py` 抓取/校正全部角色（Project Amber 公开 API），生成 `profile/meta.json`、`profile/text.json`、故事 `en.md`+`zh.md`、`data/character.json`
- **数据**：`sync_story_folders.py` 按 `character.json` 幂等同步故事文件夹名
- **翻译**：`translate_chain.py` 多后端（Google / Bing / Baidu）「来回翻译 N 次」链路，含限流退避、请求节流、断点续跑、可复现语言判定
- **翻译**：`translate_stories.py` 批量跑故事链路，输出分段 + 全文 + 语音（gTTS）
- **工程**：`data/` 不入库（可由脚本重建）、MIT [LICENSE](LICENSE)、依赖清单、README
