# 安全策略

## 支持的版本

| 版本 | 支持状态 |
| --- | --- |
| 2.x | 支持：接收安全修复 |
| 1.x 及更早 | 不再维护，请先升级到 2.x |

## 私密上报漏洞

请通过 GitHub Security Advisories 私密上报：

`https://github.com/vers123/story-tr/security/advisories/new`

- 不要在公开 issue、讨论区或 PR 中披露漏洞细节。
- 若暂时无法使用上述入口，可先在公开 issue 中仅说明「希望私下上报安全问题」，等待维护者提供渠道，切勿附带技术细节。

## 报告应包含的信息

- 受影响版本（可在 `src/story_tr/__init__.py` 的 `__version__` 或 `CHANGELOG.md` 中确认）与运行平台
- 复现步骤：命令、脱敏后的配置与最小复现样例
- 影响范围：可能泄露或篡改的数据、是否可远程触发、是否需要特定配置
- 可选：你建议的修复方向

## 响应预期

- 收到报告后我们会尽快确认并回复。
- 随后评估影响范围与严重程度，必要时与你沟通补充信息。
- 修复完成后在发布中披露：同步更新 `CHANGELOG.md`，并在对应的 GitHub Security Advisory 中说明问题与受影响版本。
- 披露节奏取决于问题复杂度与修复进度；细节会在修复可用后公开，在此之前请保持私密。

## 密钥与数据提示

- `.env` 不入库（已在 `.gitignore` 中忽略），请参考 `.env.example` 填写 `GOOGLE_API_KEY`、`BING_API_KEY`、`BAIDU_APPID`、`BAIDU_APPKEY`、代理与 `RWKV_*` 等配置。
- 提交日志、issue 与报告前先脱敏：API Key、代理凭据以及带凭据的 URL 一律替换为占位符。
- 抓取数据版权归米哈游 / Project Amber 所有，本项目不分发任何游戏资源；请勿在报告中附带完整抓取文本或资源文件。
