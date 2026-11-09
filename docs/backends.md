# 翻译后端

## Translator 协议

统一 `Translator` 协议：`translate(text, src, tgt) -> str` + 可用性检查；四个实现各自负责自身的最小间隔 / 限流 / 错误分类。错误统一映射为 `TranslationError` 层级（`RateLimited` / `QuotaExceeded` / `ConnectionFailed` / `UnsupportedLanguage` / `BadOutput` / `ModelNotLoaded`）。

## 后端对照

| 后端 | 免 key 通道 | 官方通道（`.env` 配置） | 说明 |
| --- | --- | --- | --- |
| `google` | `clients5.google.com` 的 `dict-chrome-ex` 端点 | `GOOGLE_API_KEY` → Cloud Translation v2 | 长文本按 1500 字分块 |
| `bing` | 网页免 key 通道（`cn.bing.com` / `www.bing.com` 多域名轮换，可用 `BING_WEB_BASE` 覆盖） | `BING_API_KEY`（+ `BING_REGION`）→ Azure 官方接口 | — |
| `baidu` | 无（必须官方） | `BAIDU_APPID` + `BAIDU_APPKEY` | 标准版 QPS=1，内置最小间隔 1.1s |
| `rwkv` | — | 本地 RWKV-Runner（`RWKV_*` 环境变量） | 仅与 `--mode rwkv20` 搭配 |

## 选择与回退

`--provider auto`（默认）按 **google → bing → baidu** 顺序回退；`rwkv` 不参与 auto 回退，只通过 `--mode rwkv20` 或 `--provider rwkv` 显式使用。

- 缺 key 的 google / bing 自动走免 key 通道；baidu 未配 `BAIDU_APPID` / `BAIDU_APPKEY` 时报告不可用并被跳过。
- 连接类失败（超时 / DNS / 5xx）立即切换下一后端；限流（429 等）与业务失败按各后端策略处理；全部后端失败退出码 `5`。
- 代理：经 `HTTPS_PROXY` / `HTTP_PROXY` 环境变量（或 `.env`）生效。

## TTS

TTS 用 `httpx` 直连 Google TTS 端点（`translate.google.com/translate_tts`）生成 mp3，不再依赖 gTTS；`--no-tts` 可关闭；连接失败自动重试 3 次，仍失败时记录到 `logs/tts_failures_<时间戳>.json`，可用 `translate <id> --tts-only` 补生成。

## 本地 RWKV

本地模型（`translate-20times-3B`）的部署、参数与评测见 [rwkv.md](rwkv.md)；密钥模板见 [../.env.example](../.env.example)。
