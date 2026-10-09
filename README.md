# story-tr

原神角色故事文本仓库（中文 / 英文对照），用于翻译。

**版本 `v0.1.0`** · 遵循 [Semantic Versioning 2.0.0](https://semver.org/lang/zh-CN/) · 许可 [MIT](LICENSE)

> **数据未入库**：`data/`（角色故事、`character.json` 等）**不在仓库中** —— 它体积大，且可由脚本完整重建。克隆后请先按「[快速开始](#快速开始)」生成。
>
> **模板角色：`10000021`（安柏 / Amber）** —— 该角色的 `profile`（`meta.json`、`text.json`、全部故事 `en.md` + `zh.md`）与 `result/` 下的翻译结果均已完整填写，作为**其它角色的填写模板**与各工具产出的样例。
>
> **来源与版权**：角色与文本取自 Project Amber 的公开 API，**仅供学习/研究用途**；版权归米哈游 / Project Amber 所有。

## 目录

- [快速开始](#快速开始)
- [数据来源](#数据来源)
- [目录结构](#目录结构)
- [数据约定](#数据约定)
- [数据文件说明](#数据文件说明)
- [工具脚本](#工具脚本)
- [依赖与许可](#依赖与许可)
- [版本与变更](#版本与变更)

## 快速开始

```bash
# 1) 克隆
git clone https://github.com/vers123/story-tr.git
cd story-tr

# 2) 建虚拟环境并安装依赖
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt

# 3) 获取数据（生成 data/，详见「数据来源 → 获取方式」）
.venv\Scripts\python scripts\sync_from_site.py

# 4) 翻译（例：模板角色 10000021 的全部故事）
.venv\Scripts\python scripts\translate_stories.py 10000021 --all

# 5) 可选：配置翻译后端密钥与代理
copy .env.example .env
```

- 只要**数据** → 做到第 3 步即可（产出 `data/`）。
- 只要**翻译结果** → 需要先有 `data/`，再执行第 4 步。

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

用 `scripts/sync_from_site.py` 直接请求接口，**批量拉取全部角色**并生成/校正仓库文件（见「[工具脚本](#工具脚本)」）。

```bash
# 增量抓取：只补缺失项（默认）
.venv\Scripts\python scripts\sync_from_site.py

# 重新抓取：忽略缓存，强制从网站重新拉取全部角色
.venv\Scripts\python scripts\sync_from_site.py --refresh
```

特点：

- 一次跑完全部角色，自动生成 `meta.json` / `text.json` / 各故事 `en.md`+`zh.md` / `character.json`
- 结果缓存到 `data/_fetch_all.json`，可**重复运行**（只补缺失项，支持断点续抓）
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
├── data/                               # ★ 不入库：由 sync_from_site.py 生成（见「数据来源」）
│   ├── character.json                  # 角色索引（id/名称 + path/stories）
│   ├── _fetch_all.json                 # 原始抓取缓存（断点续抓用）
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
│       └── result/{story}/               # 由 translate_stories.py 生成
│           ├── full.json / full.mp3      # 全文（源文/语言/逐步/最终译文 + 语音）
│           ├── seg_01.json / seg_01.mp3  # 各段同结构
│           └── segments.json             # 汇总
├── scripts/                            # 工具脚本（见「工具脚本」）
│   ├── sync_from_site.py               # 从 Project Amber 拉取并生成/校正全部角色数据
│   ├── sync_story_folders.py           # 按 character.json 同步重命名故事文件夹
│   ├── translate_chain.py              # 多后端「来回翻译 N 次」链路工具
│   └── translate_stories.py            # 对故事跑链路并把结果落入 result/
├── logs/                               # 运行日志（不入库）
├── out/                                # translate_chain 的结果 json/mp3（不入库）
├── .venv/                              # 虚拟环境（不入库）
├── .env                                # 密钥配置（不入库，见 .env.example）
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

> `path` / `stories` 是**文件夹重命名**的依据：以 `character.json` 为准同步重命名对应文件夹（见 `sync_story_folders.py`）。

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

## 工具脚本

环境准备见「[快速开始](#快速开始)」（`python -m venv .venv` + `pip install -r requirements.txt`）。

各脚本的**长任务统一用 `tqdm` 显示进度条**。

### `scripts/sync_from_site.py`

从 Project Amber（yatta.moe）拉取**全部角色**的 en / chs 数据（`avatar` + `avatarFetter`），生成/校正各角色的 `meta.json`、`text.json`、全部故事 `en.md`/`zh.md`，并重写 `data/character.json`（即「[数据来源](#数据来源) · 获取方式 · 方式二」）。

```bash
# 增量：只抓缺失项（默认，走缓存 data/_fetch_all.json）
.venv\Scripts\python scripts\sync_from_site.py

# 重新抓取：忽略缓存，强制从网站重新拉取全部角色
.venv\Scripts\python scripts\sync_from_site.py --refresh
```

| 参数 | 说明 |
| --- | --- |
| （无） | 增量抓取：读缓存，只补缺失的 avatar / story |
| `--refresh` | 忽略缓存，**强制重新抓取全部角色**并覆盖缓存 |

行为说明：

- 抓取结果缓存到 `data/_fetch_all.json`，可**重复运行**（只补缺失项，支持断点续抓）
- 想从零重抓，也可直接**删除 `data/_fetch_all.json`** 后运行（效果等同 `--refresh`）
- 进度：`抓取`（本次待抓角色数）与 `生成`（写入文件的角色数）两条 `tqdm` 进度条，均为按角色
- 按「[数据约定](#数据约定)」生成 slug、段落、`cv` 等
- 运行结尾打印**校验信息**：`slug` 与 `character.json` 的 `stories` 是否一致、无故事角色、名字差异等

### `scripts/sync_story_folders.py`

按 `data/character.json` 的 `stories` 同步重命名各角色 `profile/story/` 下的文件夹（幂等，可反复运行）。

```bash
.venv\Scripts\python scripts\sync_story_folders.py
```

行为说明：

- 进度：`同步文件夹`（按角色）`tqdm` 进度条
- 重命名行经 `tqdm.write` 输出，不与进度条混排
- 已一致的角色跳过（幂等）；`stories` 为空（站点无故事，如 `10000117` / `10000118`）的角色不告警

### `scripts/translate_chain.py`

把一段文本按语言路线来回翻译 N 次，最后译回中文（"翻译接龙"）。

```bash
# 默认：random 抽 20 个语言，默认文本 Hello, World
.venv\Scripts\python scripts\translate_chain.py "Hello, World"

# 固定链（asia / europe / exotic，各 20 个语言）
.venv\Scripts\python scripts\translate_chain.py --mode fixed --chain asia

# 自定义语言序列（不限 20 个）
.venv\Scripts\python scripts\translate_chain.py "你好" --mode custom --languages ja,ko,ar

# 指定后端（默认 google → bing → baidu 自动回退）
.venv\Scripts\python scripts\translate_chain.py --provider baidu
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
| `--reset` | 忽略旧进度，从头开始 |
| `--dry-run` | 只显示路线，不执行翻译 |
| `--no-file-log` | 不写文件日志 |
| `--no-tts` | 不生成最终译文的语音 |

行为说明：

- 流程：`langdetect` 判语言（固定 seed，可复现）→ 非英文先译成英文（不计入 N）→ 按路线翻译 N 次 → 译回中文
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

### `scripts/translate_stories.py`

对某角色的故事（`zh.md`）跑翻译链，结果写入 `{id}/result/{story}/`（分段 + 全文，含语音）。

```bash
# 默认：10000021 的 amber_journal，fixed/asia（20 次）
.venv\Scripts\python scripts\translate_stories.py 10000021

# 指定多个故事 / 全部含内容的故事
.venv\Scripts\python scripts\translate_stories.py 10000021 --stories amber_journal,vision
.venv\Scripts\python scripts\translate_stories.py 10000021 --all

# 全部角色（读 data/character.json 遍历所有 id）的所有含内容故事
.venv\Scripts\python scripts\translate_stories.py --all-characters --all

# 覆盖已有结果、关闭语音
.venv\Scripts\python scripts\translate_stories.py 10000021 --overwrite --no-tts
```

| 参数 | 说明 |
| --- | --- |
| `char_id`（位置参数） | 角色 id；配合 `--all-characters` 时可省略 |
| `--all-characters` | 处理 `data/character.json` 中的**所有角色** |
| `--stories` | 故事文件夹名，逗号分隔（默认 `amber_journal`） |
| `--all` | 处理该角色所有含 zh 内容的故事 |
| `--mode` / `--chain` / `--languages` / `--steps` | 同 `translate_chain.py`（默认 `fixed` + `asia`） |
| `--provider` / `--proxy` | 同 `translate_chain.py` |
| `--min-interval` | 同 `translate_chain.py`（Google 请求最小间隔，默认 `0.3`） |
| `--overwrite` | 已有结果也重跑（默认跳过） |
| `--reset` | 忽略旧批量状态 |
| `--no-tts` / `--no-file-log` | 同上 |

行为说明：

- 分段：按 `zh.md` 正文段落（去 frontmatter）；每段独立跑完整链路
- 全文：整篇再独立跑一遍完整链路
- **全部角色**（`--all-characters`）：按 `data/character.json` 顺序逐个角色处理，进度用「角色」总进度条；配合 `--all` 即**翻译全部角色的全部故事**
- 输出：`result/{story}/full.json|mp3`、`seg_NN.json|mp3`、`segments.json`（汇总：段号/源文/语言/最终结果/文件名）
- 每份 json = 链路结果（源文/检出语言/语言路线/逐步/最终译文）+ `story` / `segment_index` / `source_file`
- 续跑：`.translate_stories_state.json`（批量级进度，参数一致才续；键为 `id|story|key`）；已有结果默认跳过
- `random` 模式下每份独立随机；进度用 `tqdm`（批量时「角色」总进度条 + 每个角色的「翻译链」进度条）

## 依赖与许可

本项目以 **MIT** 许可发布，见 [LICENSE](LICENSE)（Copyright © 2026 vers123）。

第三方依赖（见 `requirements.txt`）：

| 依赖 | 用途 | 许可 |
| --- | --- | --- |
| [deep-translator](https://github.com/nidhaloff/deep-translator) | Bing / 百度 / Google 翻译后端 | MIT |
| [gTTS](https://github.com/pndurette/gTTS) | Google 翻译 TTS（`out/*.mp3`） | MIT |
| [langdetect](https://github.com/Mimino666/langdetect) | 源语言判定 | Apache-2.0 |
| [requests](https://github.com/psf/requests) | HTTP 请求 | Apache-2.0 |
| [python-dotenv](https://github.com/theskumar/python-dotenv) | 读取 `.env` | BSD-3-Clause |
| [tqdm](https://github.com/tqdm/tqdm) | 进度条 | MPL-2.0 / MIT |
| [tenacity](https://github.com/jd/tenacity) | 重试 / 退避（翻译后端、数据抓取） | Apache-2.0 |

> 说明：`beautifulsoup4` 为 `deep-translator` 的**传递依赖**（MIT），由 pip 自动安装，无需单独声明。

> 选型原则：优先 **MIT / BSD / Apache-2.0** 等宽松许可。**未采用 GPL / LGPL 的库**（例如 `edge-tts` 为 GPLv3 / LGPLv3，与 MIT 项目不兼容），以免引入传染性义务。

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

#### 0.1.0（2026-10-09）

首个版本。

- **数据**：`sync_from_site.py` 抓取/校正全部角色（Project Amber 公开 API），生成 `profile/meta.json`、`profile/text.json`、故事 `en.md`+`zh.md`、`data/character.json`
- **数据**：`sync_story_folders.py` 按 `character.json` 幂等同步故事文件夹名
- **翻译**：`translate_chain.py` 多后端（Google / Bing / Baidu）「来回翻译 N 次」链路，含限流退避、请求节流、断点续跑、可复现语言判定
- **翻译**：`translate_stories.py` 批量跑故事链路，输出分段 + 全文 + 语音（gTTS）
- **工程**：`data/` 不入库（可由脚本重建）、MIT [LICENSE](LICENSE)、依赖清单、README
