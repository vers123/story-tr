# story-tr 文档

`story-tr` 是「原神角色故事双语（中 / 英）文本抓取 + 来回翻译 N 次」链路工具，当前版本 **v2.0.0**。根目录 [README](../README.md) 是项目入口与 5 分钟上手，本目录收纳全部细化文档。

## 文档索引

| 文档 | 一句话说明 | 适合 |
| --- | --- | --- |
| [usage.md](usage.md) | 安装、快速开始、8 个子命令与参数表、退出码 | 只想把命令跑起来 |
| [architecture.md](architecture.md) | 五层架构、`services` / `cli` 内部拆分、结构守护与目录树 | 要读或改代码 |
| [data-contract.md](data-contract.md) | `data/` 目录结构、各 JSON / Markdown 字段契约、状态文件 | 要消费数据或写周边工具 |
| [backends.md](backends.md) | 翻译后端（google / bing / baidu / rwkv）、错误分类、代理与 TTS | 配置后端或排查网络问题 |
| [rwkv.md](rwkv.md) | 本地 RWKV（`--mode rwkv20`）部署与 `eval` 评测 | 想离线批量翻译 |
| [development.md](development.md) | 开发环境、本地校验、Breaking changes、版本与发布流程 | 贡献者 / 维护者 |

## 阅读顺序建议

1. 第一次使用：[usage.md](usage.md)；需要密钥或切换后端时看 [backends.md](backends.md)。
2. 想离线跑：直接看 [rwkv.md](rwkv.md)。
3. 要写脚本消费产物：读 [data-contract.md](data-contract.md)。
4. 准备改代码：先读 [architecture.md](architecture.md)，再看 [development.md](development.md)。

## 其他入口

- 项目入口与 5 分钟上手：[../README.md](../README.md)
- 变更记录：[../CHANGELOG.md](../CHANGELOG.md)
- 许可证（MIT）：[../LICENSE](../LICENSE)
- 示例环境变量：[../.env.example](../.env.example)
