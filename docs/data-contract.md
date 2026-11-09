# 数据契约

`data/` 由 `story-tr fetch` 生成，**不入库**（见 `.gitignore`）。以下为 **v2.0 数据契约**，全部结构经 pydantic v2 校验后写入。

## 目录结构

```
<工作目录>/
├── .env # 密钥（不入库，见 .env.example）
├── data/
│ ├── character.json # 角色索引：CharacterRef 列表
│ ├── _fetch_all.json # 抓取缓存（断点续抓 + 内容指纹）
│ ├── manifest.json # 数据清单（story-tr status --manifest 生成）
│ └── story/character/{id}/
│ ├── profile/
│ │ ├── meta.json # CharacterMeta
│ │ ├── text.json # CharacterText
│ │ └── story/{story}/
│ │ ├── en.md # frontmatter + 段落
│ │ └── zh.md
│ └── result/{story}/
│ ├── title.json / title.mp3 # 标题译文（TranslationResult）
│ ├── full.json / full.mp3 # 全文译文（TranslationResult）
│ ├── seg_01.json / seg_01.mp3 # 各段译文（TranslationResult）
│ ├── … # seg_NN 依次编号
│ └── segments.json # 汇总（标题/全文/各段 索引）
├── out/ # story-tr chain 结果（{时间戳}.json / .mp3）
├── logs/ # 运行日志 + failures_*.json + rwkv_eval_*
├── .translate_stories_state.json # 批量续跑状态（BatchState）
└── .translate_state.json # 单文本链路续跑状态（ChainState）
```

## `data/character.json`

角色**索引**，`CharacterRef` 列表：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | `str` | 角色 id（与目录名一致） |
| `name_en` / `name_zh` | `str` | 中英文名 |
| `path` | `str` | 角色目录（相对工作目录），如 `data/story/character/10000021` |
| `stories` | `list[str]` | `profile/story/` 下的故事文件夹名列表 |

## `profile/meta.json`（`CharacterMeta`）

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

## `profile/text.json`（`CharacterText`）

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

## 故事文件 `en.md` / `zh.md`

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

## `result/{story}/{title,full,seg_NN}.json`（`TranslationResult`）

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

`segments.json` 为该故事的**汇总**（标题 / 全文 / 各段的源文、语言、最终结果与文件名索引）。`*.mp3` 为可选语音（`--no-tts` 时不生成；缺失时可用 `--tts-only` 单独补齐，`segments.json` 的 `mp3` 字段会同步刷新）。

## 状态文件

- `.translate_stories_state.json`（`BatchState`）：`signature`（参数签名，防混跑）+ `completed`（已完成任务键 `id|story|key`）+ 起止时间。
- `.translate_state.json`（`ChainState`）：单文本链路的签名、源文、语言、当前文本与已完成步骤。

两者均为原子写（临时文件 + 上一版 `.bak` 备份 + 损坏隔离恢复）。

## 日志与输出

- `logs/`：运行日志、`failures_<时间戳>.json`（翻译失败清单）、`tts_failures_<时间戳>.json`（语音失败清单）、`rwkv_eval_<时间戳>.json|md`（`story-tr eval` 报告）。
- `out/`：`story-tr chain` 的结果（`{时间戳}.json` + 可选 `.mp3`）。
