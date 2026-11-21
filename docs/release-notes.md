# ModLingo 0.6.15 Beta · 公开测试版

配套Bridge0.5.9。完整解压 `ModLingo-Windows-x64.zip`，运行 `ModLingo/ModLingo.exe`；无需Python或Anaconda。

## 本版变化

- 矿车显示资源适配、跨模组目的地译名与同轮逐级回填。
- Python和Bridge共用资源规则，按稳定Id定位，保护配置和自定义值。
- Windows长短路径兼容、缓存复用、原文更新与备份恢复。
- 玩家指南、兼容清单、常见问题及可重建的开发环境。

## 下载文件

- `ModLingo-Windows-x64.zip`：程序、配套Bridge及许可文件。
- `ModLingo-source.zip`：对应源码、测试和开发文档。
- `SHA256SUMS.txt`：下载完整性校验。
- `build-environment.txt`、`release-manifest.json`：构建环境、源码提交和资产对应关系。
- `portable-acceptance.json`：自动化首次使用验收及其边界。

## 验证与限制

源码回归、跨语言契约、隔离游戏探针和打包后启动已有记录；新增自动化冷缓存、复跑、更新及恢复检查。模拟模型不代表真实翻译质量，第二台电脑及全部游戏显示仍待人工验收，见包内Beta验收说明。

需自备API，验证和翻译可能计费。输出Token上限不是整轮费用预算。程序未签名，先确认来源及校验值；不要关闭全局防护。

升级保留 `%LOCALAPPDATA%/ModLingo/`，退出游戏后更新Bridge。不要向公开Issue上传密钥、完整mod、存档或含私人文本的原始报告。
