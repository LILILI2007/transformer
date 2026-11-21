# 贡献指南

优先提交能复现的导入、格式保护、更新保留和安装问题。请附游戏/SMAPI/mod 版本、文件布局、最小脱敏文本、预期与实际结果。不要上传 API 密钥、完整私有 mod、未授权游戏素材或个人存档。

开发环境：Python 3.9+，GUI 需要 Tkinter，运行依赖 HTTPX；使用 `pip install -e .` 安装。Anaconda 环境与固定构建依赖见 `environment.yml` 和 `requirements-environment.lock.txt`。

从空环境建立、解释器查找、Bridge SDK/游戏位置和发布验收见[开发指南](docs/开发指南.md)。脚本不要求固定D/E盘路径；本机历史实测脚本不属于通用开发入口。

```powershell
python -m unittest discover -s tests -v
python -m modlingo --smoke
```

核心/接口测试可以无显示器运行；GUI 测试在 Windows 或有 DISPLAY 的系统执行。HTTP 集成测试使用本机临时服务器，不调用真实云模型、不消耗额度。

新增语法支持时同时提供代表性正反例、结构保持规则和实机验证记录。不能仅放宽正则让错误不再被检测。新增 API 供应商先实现清晰适配，不能在日志或报错中输出原始授权头和服务端错误体。

项目编辑文件与 Bridge 交换文件有独立版本；改变对应持久化字段时需要迁移与回归测试。保持 UI 线程独占 Tk 和项目写回，后台线程不能操作界面。

提交 PR 时说明具体行为变化、输入边界和实际验证，不把尚未运行的 CI 或游戏测试写成通过。独立 EXE、依赖环境、个人项目和密钥不提交 Git。`bridge/package/ModLingo.Bridge.dll` 是仓库已有的自有插件交付文件，修改 Bridge 时需同步构建并更新；游戏与 SMAPI 程序集不上传。
