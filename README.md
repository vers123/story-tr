# story-tr

原神角色故事文本仓库（中文 / 英文对照），用于翻译。

**版本 `v0.16.0`** · 遵循 [Semantic Versioning 2.0.0](https://semver.org/lang/zh-CN/) · 许可 [MIT](LICENSE)

> **数据未入库**：`data/`（角色故事、`character.json` 等）**不在仓库中** —— 它体积大，且可由脚本完整重建。克隆后请先按「[快速开始](#快速开始)」生成。
>
> **模板角色：`10000021`（安柏 / Amber）** —— 该角色的 `profile`（`meta.json`、`text.json`、全部故事 `en.md` + `zh.md`）与 `result/` 下的翻译结果均已完整填写，作为**其它角色的填写模板**与各工具产出的样例。
>
> **来源与版权**：角色与文本取自 Project Amber 的公开 API，**仅供学习/研究用途**；版权归米哈游 / Project Amber 所有。

> **免责声明**：本项目仅用于学习与技术研究，不包含、不分发任何游戏资源或数据；与米哈游（miHoYo / HoYoverse）及其关联公司无任何关系。

## 目录

- [安装](#安装)
- [快速开始](#快速开始)
- [数据来源](#数据来源)
- [目录结构](#目录结构)
- [数据约定](#数据约定)
- [数据文件说明](#数据文件说明)
- [命令（CLI）](#命令cli)
- [MCP Server](#mcp-server)
- [桌面界面（GUI）](#桌面界面gui)
- [依赖与许可](#依赖与许可)
- [版本与变更](#版本与变更)
- [变更记录](CHANGELOG.md)

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

> **MCP Server（可选）**：如需把项目接入 MCP 客户端，额外安装可选依赖：`pip install -e ".[mcp]"`（官方 `mcp` SDK **需 Python ≥3.10**）。安装后提供命令 **`story-tr-mcp`**（等价 `python -m story_tr.mcp`），详见 **[docs/mcp.md](docs/mcp.md)**。
>
> **桌面界面（可选）**：`pip install -e ".[gui]"`（PySide6）后提供命令 **`story-tr-gui`**（等价 `python -m story_tr.gui`），详见「[桌面界面（GUI）](#桌面界面gui)」。
>
> **视频合成（可选）**：`pip install -e ".[video]"`（Pillow）后可用 `story-tr video`；另需系统 **ffmpeg** 在 `PATH` 中，详见「[`story-tr video`](#story-tr-video)」。

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
│       └── result/                        # 由 `story-tr translate` / `video` 生成
│           ├── {story}/                   # 每篇故事
│           │   ├── title.json / title.mp3 # 标题（frontmatter 的 title，如「角色详细」）
│           │   ├── full.json / full.mp3   # 全文（源文/语言/逐步/最终译文 + 语音）
│           │   ├── seg_01.json / seg_01.mp3  # 各段同结构
│           │   └── segments.json          # 汇总
│           └── video/                     # `story-tr video` 产物（mp4 + TTS 缓存）
├── src/story_tr/                       # 包源码（CLI 实现）
│   ├── __init__.py                     # __version__
│   ├── __main__.py                     # python -m story_tr
│   ├── cli.py                          # 顶层 CLI（子命令注册）
│   ├── paths.py                        # 工作目录解析
│   ├── report.py                       # 控制台输出层（日志上滚、进度条固定底部、-q/--verbose）
│   ├── statefile.py                    # 状态文件原子写 / 备份 / 损坏恢复
│   ├── exitcodes.py                    # 统一退出码与文案
│   ├── sync.py                         # story-tr fetch
│   ├── folders.py                      # story-tr folders
│   ├── chain.py                        # story-tr chain（翻译链核心）
│   ├── stories.py                      # story-tr translate（批量翻译）
│   ├── clean.py                        # story-tr clean（清理 result/）
│   ├── status.py                       # story-tr status（只读状态报告）
│   ├── video.py                        # story-tr video（「谷歌翻译 N 次」视频，需可选依赖 + ffmpeg）
│   ├── mcp/                            # story-tr-mcp（MCP Server；需可选依赖）
│       ├── server.py                   # Tool / Resource 注册 + stdio 运行
│       ├── tools.py                    # Tool 函数（不依赖 mcp）
│       ├── resources.py                # Resource 函数（不依赖 mcp）
│       └── jobs.py                     # 长任务：串行队列 / 进度 / 取消（不依赖 mcp）
│   └── gui/                            # story-tr-gui（PySide6 桌面界面；需可选依赖）
│       ├── app.py                      # 主窗口：窗口级工具条 + 四个页签 + 可折叠日志面板
│       ├── runner.py                   # QThread 执行核心 + 日志转发到界面
│       └── argv.py                     # 表单 → CLI 参数（不依赖 PySide6）
├── assets/                             # 固定素材（translate-logo.webp，随仓库提交）
├── scripts/                            # 兼容入口（转发到包内实现）
│   ├── sync_from_site.py               # = story-tr fetch
│   ├── sync_story_folders.py           # = story-tr folders
│   ├── translate_chain.py              # = story-tr chain
│   └── translate_stories.py            # = story-tr translate
├── docs/mcp.md                         # MCP 详细文档（客户端配置 / 分发 / Tools·Resources）
├── tests/                              # pytest 单元测试
├── .github/workflows/ci.yml            # CI：test（3.9/3.11/3.13/3.14）+ gui/video（3.13）+ build
├── logs/                               # 运行日志 + MCP 子进程日志 logs/mcp_*.log（不入库）
├── out/                                # translate_chain 的结果 json/mp3（不入库）
├── dist/ / build/                      # python -m build 的产物 *.whl / *.tar.gz（不入库）
├── .venv/                              # 虚拟环境（不入库）
├── .env                                # 密钥配置（不入库，见 .env.example）
├── pyproject.toml                      # 打包元数据 + 入口点 + ruff/pytest 配置
├── main.py                             # 兼容入口：python main.py <子命令>
├── requirements.txt
├── LICENSE                             # MIT
├── CHANGELOG.md                        # 变更记录（各版本历史）
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

### 控制台输出

**明细「上滚」，进度条固定在底部**：日志与明细都经 `tqdm.write` 输出（先清条 → 写日志 → 重绘条），不会被进度条冲乱；原先直接 `print` 的告警、重命名行也改走同一通道。

| 参数 | 控制台显示 |
| --- | --- |
| 默认 | 分组行 + **每份结果完成一行**（`[id]  故事/键  字数  后端  耗时`）+ 告警 |
| `--verbose` | 再加「每次翻译步骤」明细（`[google] 中文 → 英文：…`）与「跳过（已有结果）」行 |
| `-q` / `--quiet` | 只显示错误与最终摘要（进度条仍在） |

- 文件日志（`logs/*.log`）始终记录**全量明细**，不受以上开关影响
- `story-tr chain` 默认就显示每次步骤明细（单条文本看每步才是重点）
- 非交互终端（重定向 / 管道，例如 MCP 子进程）**自动禁用进度条**并改用普通行输出，避免把 `\r` 控制序列写进日志文件

| 子命令 | 作用 |
| --- | --- |
| `story-tr fetch [--update\|--refresh]` | 拉取并生成/校正角色数据；`--update` 发现新角色并只重写变化项 |
| `story-tr folders` | 按 `character.json` 的 `stories` 同步重命名故事文件夹 |
| `story-tr chain [text]` | 单文本「来回翻译 N 次」链路（最后译回中文） |
| `story-tr translate <id/范围>` | 批量翻译角色故事，结果落入 `{id}/result/`（默认合并请求；`--full-only` 只出全文） |
| `story-tr clean <id/范围>` | 清理 `{id}/result/` 下的翻译产物（保留 `result/` 目录本身；默认不删 `result/video/`） |
| `story-tr video <id/范围>` | 把翻译结果渲染成「谷歌翻译 N 次」视频（B 站 1080P） |
| `story-tr status [--json] [--deep]` | 只读报告角色/故事覆盖、结果与语音数量、磁盘占用、批量进度 |

**退出码**（CLI 与 GUI 共用）：

| 码 | 含义 |
| --- | --- |
| `0` | 完成 |
| `1` | 参数或前置条件错误 |
| `2` | 语言不受支持 |
| `3` | 批量状态与上次不一致（需 `--reset` 或 `clean`） |
| `4` | 已暂停（业务错误重试耗尽） |
| `5` | 所有后端连接失败 |
| `6` | 已取消（进度已保存） |
| `7` | 部分任务失败（失败清单已保存，重跑可续） |
| `8` | 当日配额耗尽（进度已保存，次日续跑） |

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
- 进度：`抓取`（本次待抓角色数）与 `生成`（写入文件的角色数）两条 `tqdm` 进度条，均为按角色；**进度条固定在底部**，每个角色抓完输出一行 `[id] 已抓取  en N 故事 / chs N 故事`
- 控制台详略：`-q` 只看错误与报告、`--verbose` 显示更多（见「[控制台输出](#控制台输出)」）
- 按「[数据约定](#数据约定)」生成 slug、段落、`cv` 等
- 运行结尾打印**校验信息**：`slug` 与 `character.json` 的 `stories` 是否一致、无故事角色、名字差异等

### `story-tr folders`

按 `data/character.json` 的 `stories` 同步重命名各角色 `profile/story/` 下的文件夹（幂等，可反复运行）。

```bash
story-tr folders
```

行为说明：

- 进度：`同步文件夹`（按角色）`tqdm` 进度条
- 重命名行经 `tqdm.write` 输出，不与进度条混排；`-q` 只看汇总
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
| `-q` / `--verbose` | 控制台详略（见「[控制台输出](#控制台输出)」） |

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
- 日志：明细（含每步中间结果）写入 `logs/YYYYMMDD_HHMMSS.log`；控制台**默认显示每次步骤明细**（日志上滚、进度条固定在底部），`-q` 可只看错误
- 结果：`out/<时间戳>.json`（含元数据与逐步中间结果）

**各后端推荐参数**（实测）：

| 后端 | 瓶颈 | 建议 `--min-interval` | 说明 |
| --- | --- | --- | --- |
| `google`（免费 clients5） | 网络延迟 ~0.5s | `0.3` | 比 `1.0` 快约 2–3×；再降（`0.1`/`0`）无收益，`0.3` 仍远低于 5 请求/秒 |
| `bing`（免费网页） | 网络延迟 ~1.2s | `0.3`～`0.5` | 间隔不敏感，延迟主导 |
| `baidu`（官方） | **QPS=1** | `1.1`（内置下限，调不低） | 官方标准版限速，代码已内置 |

> 实测同批 `10000021/amber_journal`（5 份）：`--min-interval 0.3` ≈ **59s**，`1.0` ≈ **110s**，两者均**零限流**。
> 并发实测：同批 `--workers 4 --min-interval 0.25` ≈ **34s**（≈1.7×，总速率约 **4 请求/秒**）。
> 全量估算（当前 `data/` 约 1.1 万份结果 / 974 个故事，实际以 `story-tr status` 为准）：**逐条**约 24 万次调用 → 单线程 ≈35 h；**批量（默认）**约 2–3 万次调用 → **≈2 h**。

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
| `-q` / `--verbose` | 控制台详略（见「[控制台输出](#控制台输出)」） |

行为说明：

- **角色选择**：位置参数支持单个 id / `起始-结束` 范围 / 逗号组合；按 `character.json` 顺序处理，范围内不存在的 id 自动跳过并提示
- **故事选择**：不指定 `--stories` 时，默认处理该角色的**全部含内容故事**
- 分段：按 `zh.md` 正文段落（去 frontmatter）；每段独立跑完整链路
- 全文：整篇再独立跑一遍完整链路
- 标题：`zh.md` frontmatter 的 `title`（如「角色详细」）**始终**跑一遍完整链路，写入 `title.json`
- **全部角色**（`--all-characters`）：处理 `data/character.json` 中的全部角色，进度用「角色」总进度条
- 输出：`result/{story}/title.json|mp3`、`full.json|mp3`、`seg_NN.json|mp3`、`segments.json`（汇总：标题/段号/源文/语言/最终结果/文件名）
- 每份 json = 链路结果（源文/检出语言/语言路线/逐步/最终译文）+ `story` / `segment_index` / `source_file`
- **纯符号段落**（如整段只有 `……`，全库共 83 处）：没有语言特征，判定不了也翻译不了 —— 直接**原样保留**、不发任何请求（json 里 `passthrough: true`、`source_detected_lang: "none"`、`steps: []`），也不生成语音
- 续跑：`.translate_stories_state.json`（批量级进度，参数一致才续；键为 `id|story|key`）；已有结果默认跳过
  - 参数与上次不一致时会要求加 `--reset`；`story-tr clean` 会**重置批次签名**，之后可用新参数直接运行
- 进度与输出：`tqdm` 双条（「角色」总进度条 + 每个角色的「翻译链」进度条），**进度条固定在底部**，明细逐行上滚 —— 每个故事组一行 `>>> …` / `<<< …（耗时）`，每份结果一行 `[id]  故事/键  字数  后端`；`--verbose` 再加每步明细与「跳过（已有结果）」行
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
| `-q` / `--verbose` | 控制台详略：`-q` 只显示错误与最终摘要；`--verbose` 列出目录的**完整相对路径**（默认只显示目录名） |

行为说明：

- 只删 `result/` 里的内容，**保留 `{id}/result/` 目录本身**（维持目录结构约定）
- **默认保留 `result/video/`**（视频与 TTS 缓存体积大）：未指定 `--stories` 时跳过，需要清理时显式 `story-tr clean <id> --stories video`
- 默认**交互确认**（先列出目录、文件数与占用大小）；`--dry-run` 预览、`-y` 跳过确认
- **默认同步清理**状态记录：移除 `.translate_stories_state.json` 中对应的 `id|story|key` 条目，并**重置批次签名** —— 否则重跑 `translate` 会把这些任务当成「已完成」而跳过，或因签名不一致要求 `--reset`；`--keep-state` 可保留
- 角色选择语义与 `story-tr translate` 完全一致（含范围内不存在 id 的跳过提示）

### `story-tr status`

只读报告，不联网、不写文件；用于快速确认“还差多少、磁盘多大、批量进度是什么”：

```bash
story-tr status            # 人类可读
story-tr status --json     # 机器可读
story-tr status --deep     # 额外统计段落源语言分布（需读取全部 seg JSON，较慢）
```

输出包括：角色/故事覆盖（有结果 / 无结果）、段落 JSON 与 mp3 数量、视频数量与体积、`data/` 与 `logs/` 占用、`.translate_stories_state.json` 的完成数与签名。`--deep` 可用来检查语言判定质量（例如是否还有中文被误判为韩/日）。

### `story-tr video`

把已有翻译结果渲染成「谷歌翻译 N 次」风格的视频，按 **B 站投稿规格**输出。

> 前置条件：每篇故事需要 `profile/story/<story>/<story>.png` 背景图；缺图会跳过该故事（`--allow-partial` 不会补图）。

```bash
pip install -e ".[video]"      # Pillow（另需系统 ffmpeg 在 PATH 中）
story-tr video 10000021        # 8 条故事视频 + 1 条合并视频
```

输出（`data/story/character/<id>/result/video/`）：

```text
result/video/
├─ character_details.mp4     # 每个故事一条（段内按顺序，每段一屏）
├─ character_story_1.mp4
├─ …
├─ 10000021.mp4              # 各故事按剧情顺序无损拼接
└─ audio/<story>/seg_NN.mp3  # TTS 缓存（重复生成时复用）
```

| 参数 | 说明 |
| --- | --- |
| `char_ids` / `--all-characters` | 同 `translate` |
| `--stories` | 只处理指定故事，逗号分隔 |
| `--only story\|total\|both` | 只出分故事 / 只出总视频 / 两者（默认 `both`） |
| `--duration` | 每段**最短**停留秒数（默认 `3.0`；有语音时以语音时长为准） |
| `--fps` | 帧率（默认 `30`） |
| `--crf` | 用 CRF 质量控制（0–51，越小越清晰；常见 20–23）；指定后忽略 `--bitrate` |
| `--bitrate` | 固定码率（默认 `10M`，B 站规格） |
| `--logo` | logo 路径（默认 `assets/translate-logo.webp`） |
| `--overwrite` | 已有 mp4 也重新生成（默认跳过） |
| `--no-tts` | 不生成语音（保留静音音轨） |
| `--no-audio` | 完全不添加音轨 |
| `--refresh-audio` | 重新生成 TTS（默认复用 `result/video/audio/` 缓存） |
| `--proxy` | 代理地址（覆盖 `.env` 的 `HTTPS_PROXY` / `HTTP_PROXY`） |
| `--allow-partial` | 缺少部分段落时用已有段落生成（默认跳过该故事） |
| `--strict` | 任一故事不完整就整体失败，不生成任何文件 |
| `-q` / `--verbose` | 控制台详略 |

版式（1920×1080）：背景为 `profile/story/<story>/<story>.png`（**不压暗、不加底板**，完整露出）；文字用「白色原文 / 暖黄译文 + 黑色描边」，自动换行 + 自动缩放（54→24 px，仍放不下则截断并加 `…`），并做简化的中文**避头尾**。左下角 logo（120×120，自动裁掉透明留白），**图标垂直中心与划线对齐**；划线从图标右缘一直延伸到右侧安全区，原文在线上、译文在线下。

音频：默认用 **gTTS 朗读译文**，每段时长 = `max(--duration, 语音时长 + 0.4s)`。语音按以下顺序查找：`result/<story>/seg_NN.mp3`（**翻译阶段已生成，直接复用，零请求**）→ `result/video/audio/<story>/seg_NN.mp3`（视频缓存）→ 现场用 gTTS 生成并写入视频缓存。gTTS 需要能访问 `translate.google.com` —— 需要代理时加 `--proxy http://127.0.0.1:10808` 或在 `.env` 填 `HTTPS_PROXY` / `HTTP_PROXY`；单段失败会优先退回已有语音，其次静音并告警，不中断整条视频。

B 站规格：H.264 High / yuv420p / 30 fps / 10 Mbps / GOP 2s / MP4 `faststart` / AAC 320 kbps 48 kHz 立体声（`--no-audio` 时无音轨）。

行为说明：

- **只读翻译结果**：只读 `result/<story>/seg_*.json`，**不读写** `.translate_stories_state.json`，也不改动任何翻译产物 —— 不会影响仍在进行的翻译进度
- **完整性校验**：逐段检查 `seg_NN.json` 是否存在、`source` 是否与当前 `zh.md` 一致、`final` 是否为空；不完整的故事**跳过并告警**，总视频只合并已完成的故事
- **总视频**：直接拼接已生成的单故事 mp4（编码参数一致，`-c copy`，不二次损失）；若某条缺失会先补生成
- **音画同步**：每段音轨都会补齐/裁剪到该段画面时长，再按顺序拼接
- 之后若重跑了翻译，重跑一次 `story-tr video` 即可更新视频（默认跳过已有文件，需更新时加 `--overwrite`）

## MCP Server

把项目接入支持 **MCP**（Model Context Protocol）的客户端（Trae / Claude Desktop / Cursor 等），让模型直接查询角色、故事与翻译结果，并可**触发**翻译/抓取等长任务。

- 命令：`story-tr-mcp`（等价 `python -m story_tr.mcp`），**stdio** 传输
- 依赖：可选依赖 `pip install -e ".[mcp]"`（官方 `mcp` SDK，**需 Python ≥3.10**）
- 能力：**11 个 Tool**（4 只读 + 2 同步 + 5 长任务 job）+ **4 个 Resource**（角色 / 故事原文 / 翻译结果）
- 工作目录同 CLI：`$STORY_TR_HOME` → 向上查找 `data/character.json` → 当前目录

**完整说明见 → [docs/mcp.md](docs/mcp.md)**（客户端配置、分发安装、Tool / Resource 清单）

## 桌面界面（GUI）

可选的 **PySide6** 桌面应用：把「抓取 / 翻译 / 清理 / 结果浏览」与实时日志放进一个窗口，适合不想记命令行参数的场景。

```bash
pip install -e ".[gui]"      # PySide6（约 200MB）
story-tr-gui                 # 等价：python -m story_tr.gui
```

| 页签 | 内容 |
| --- | --- |
| **翻译** | 参数按「基本 / 输出内容 / 速率与后端」**三组**排布；角色 id / 范围（或全部）、故事、模式与链、后端、并发、最小间隔；输出内容三选一（都要 / 只出全文 / 只出段落）、重跑 / 不生成语音 / 详细日志 |
| **抓取** | 增量 / 更新（`--update`）/ 强制重抓（`--refresh`） |
| **清理** | 角色与故事范围；**预览**（`--dry-run`）与**执行**（确认对话框 + `--yes`）；`--keep-state` |
| **结果浏览** | 角色（**带搜索**）→ 故事 → `segments.json`（标题 / 全文 / 各段），只读 |
| **窗口级工具条** | 「开始 / 停止 / 重置批次并重跑」+ 当前任务状态，**常驻窗口顶部**，切换页签不丢状态 |
| **状态栏** | 任务结束后保留最终结果：退出码、含义、用时（如 `退出码 0（完成） · 用时 3m21s`） |
| **日志面板** | 实时日志（与 CLI 同一套输出）；**可折叠**（默认收起为一行标题）、可**按关键词查找**、按级别**着色**（ERROR / WARN / STEP / DEBUG），可清空 / 保存为文件 |

**推荐取值**（GUI 默认即为此，鼠标悬停各控件也有相应提示）：

| 参数 | 推荐值 | 说明 |
| --- | --- | --- |
| 模式 / 链 | `fixed` + `asia` | 同一批结果可复现、可断点续跑；`random` 每份独立随机且**自动关批量** |
| 后端 | 自动（`google → bing → baidu`） | 依次回退最稳；本机长期连不上 google 时固定 `bing` / `baidu`，省掉每次超时等待 |
| 并发线程 | `1`（想快用 `3~4`） | 并发越高越易触发限流，**退避等待后不一定更快** |
| 最小间隔 | `0.3` 秒 | 与 CLI 默认一致；`0` = 关闭节流（易被限流）。百度官方通道另有 `1.1` 秒下限，调不低 |
| 输出内容 | 两者都要 | 三选一的**互斥单选**；选「只出全文 / 只出段落」在批量开启时只省约 **20%** 请求，主要价值是减少产物文件与磁盘占用 |
| 模式相关参数 | 跟随模式置灰 | 选 `fixed` 只启用「链」、`custom` 只启用「自定义语言」、`random` 只启用「随机步数」，其余置灰，避免填了却不生效 |
| 已有结果也重跑 | 不勾 | 默认跳过已有结果 → 可断点续跑；仅在需要重跑时勾选 |
| 不生成语音 | **首次跑全量建议勾** | 省掉每份结果的 gTTS 请求与 mp3 体积，之后可单独补跑 |
| 显示每步明细 | 不勾 | 勾选后追加每次翻译步骤的中间结果（全量约 30 万行） |
| 抓取 | 增量；全新克隆先用「更新」 | 无 `data/character.json` 时它会自动按站点列表初始化 |
| 清理 | 先「预览」再「执行」 | 预览 = `--dry-run`；执行前弹确认，默认**同步清理进度记录并重置批次签名** |

行为说明：

- **复用包内核心**：GUI 直接调用 `stories.main` / `sync.main` / `clean.main`，参数由**真实 CLI 解析器**构造（`gui.argv.parse`），与命令行语义完全一致，不会随 `argparse` 漂移
- **进度与取消**：核心通过 `on_progress(dict)` 回调上报进度；「停止」置位 `cancel`（`threading.Event`），核心在**下一个任务边界保存进度**后以退出码 `6` 返回 —— 不硬杀线程，避免留下半个结果文件
- **日志完整**：运行时接管 `sys.stdout` / `sys.stderr` 转发到日志面板 —— 核心既有 `logging`（如每份结果一行）也有 `print`（如 `fetch` 的校验报告、`clean` 的清单），**两者都会显示**；面板内容与 CLI 控制台一致，同时按 CLI 规则写入 `logs/`。接管期间 `report.interactive()` 为假，因此**进度条自动禁用**，面板里不会出现 `\r` 重绘
- 工作目录与 CLI 一致（`--data-dir` → `$STORY_TR_HOME` → 向上查找 → 当前目录），窗口顶部显示实际值；**启动后不可更改**（模块级路径常量在导入时确定）
- 每次开始任务会在日志面板打印**等效命令行**（如 `$ story-tr translate --all-characters …`），便于复制到终端复现
- **退出码 3 有出路**：若本次参数与上次批量任务不一致，核心会拒绝续跑（避免与已有进度冲突）。GUI 不弹原始 JSON，而是把**变化的项写成「上次 x → 本次 y」**打印到日志，并在工具条上给出「**重置批次并重跑**」按钮 —— 一键等价于 CLI 加 `--reset`
- **完成后不清零**：任务结束只在状态行追加「退出码 N（含义）」并**保留进度条数值**；窗口底部状态栏同时给出用时
- **记住上次参数**：表单配置（模式 / 链 / 后端 / 并发 / 间隔 / 各开关 / 页签 / 日志面板展开状态）经 `QSettings` 持久化，下次启动自动恢复；**不含**角色 id 与故事（避免误重跑上次的目标）
- **页签可滚动**：翻译 / 抓取 / 清理三页内容超出可视区域时出现滚动条，窗口可缩到很小 —— 高显示缩放（如 175%）或小屏下不会被顶出屏幕

> `--workers > 1` 时已提交的任务无法逐个中断，「停止」会在**下一个角色**边界生效；并发为 `1`（默认）时按份生效。

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

可选依赖（**桌面界面**，`pip install -e ".[gui]"`）：

| 依赖 | 用途 | 许可 |
| --- | --- | --- |
| [PySide6](https://doc.qt.io/qtforpython/) | 桌面界面（Qt for Python，官方绑定） | LGPL-3.0 / 商业双许可 |

可选依赖（**视频合成**，`pip install -e ".[video]"`）：

| 依赖 | 用途 | 许可 |
| --- | --- | --- |
| [Pillow](https://python-pillow.org/) | 逐帧合成与中文排版（编码调用系统 ffmpeg） | HPND（宽松） |

> 视频编码使用系统 **ffmpeg**（需在 `PATH` 中，建议含 `libx264`）；它不是 Python 依赖，不参与 pip 安装。

> 说明：`beautifulsoup4` 为 `deep-translator` 的**传递依赖**（MIT），由 pip 自动安装，无需单独声明。

> **核心依赖**选型原则：优先 **MIT / BSD / Apache-2.0** 等宽松许可，且**不引入 GPL / LGPL 的库**（例如 `edge-tts` 为 GPLv3 / LGPLv3，与 MIT 项目不兼容），以免给核心库带来传染性义务。

> **关于可选 GUI 依赖 `PySide6`（LGPL-3.0）**：`PySide6`（Qt for Python）采用 **LGPL-3.0**，另有商业授权可选。它**不属于核心依赖**，只有显式执行 `pip install "story-tr[gui]"` 才会安装；核心库（`pip install story-tr`）由清一色宽松许可的依赖构成。对 LGPL 组件的使用方式为**动态链接、不修改 Qt 源码**，Qt 以独立库形式存在且用户可自行替换其版本 —— 符合 LGPL-3.0「允许替换被链接库」的要求，因此不与本项目的 MIT 许可冲突。若所在组织对 LGPL 另有约束，可不安装该额外项，仅使用 CLI 与 MCP。

> 开发与测试：`pip install -e ".[dev,mcp,gui,video]"` → `ruff check .` + `pytest`。测试按依赖**自动分层跳过** —— 未装 `mcp` / `PySide6` / `Pillow` 或没有 `ffmpeg` 时相关用例自动跳过，因此基础测试在任意环境都能跑。
> CI（`.github/workflows/ci.yml`）：`test` job 在 Python 3.9 / 3.11 / 3.13 / 3.14 上装 `[dev,mcp]` 跑 ruff + pytest；`gui` job 只在 **3.13** 上装 `[dev,gui]`，并以 `QT_QPA_PLATFORM=offscreen` 离屏跑 GUI 测试（PySide6 约 200MB，不铺满矩阵）；`video` job 装 `[dev,video]` + `ffmpeg` + `fonts-noto-cjk` 跑视频测试；`build` job 验证 `python -m build` 与 `twine check`。

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

完整历史见 → [CHANGELOG.md](CHANGELOG.md)。
