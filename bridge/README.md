# ModLingo.Bridge 0.5.9

0.5.9 使用与 Python 同源的 `modlingo/resource_rules.json`，构建时嵌入 DLL。统一资源名称边界、可显示字段、矿车/地图结构路径与列表 Id；采集文件新增规则版本和 SHA256，便于确认两端一致。无需另装规则文件。见 `docs/0.6.15依赖回填与共享提取规则.md`。

0.5.8 新增 `Data/Minecarts` 资源适配。目的地列表按唯一 Id 定位，只采集或替换明确的显示名称、购票/选择提示；Id、地图、坐标、条件和价格保持原样。自定义配置名称不采集、不替换。中文游戏可解析简单的 `LocalizedText Strings/...:Key`，采集附带原始引用、解析结果哈希及语言，供桌面端验证名称来源。见 `docs/0.6.14矿车资源适配.md`。

0.5.4 新增世界地图 Data/WorldMap 的 MapAreas/Id/ScrollText 和 MapAreas/Id/Tooltips/Id/Text。只对该资源使用唯一静态Id列表路径，不依赖列表序号；字段名规范大小写，Id保持原样。同词不同位置不会串译，替换仍检查完整原文及哈希。重复/缺失/动态Id跳过，不猜测；其他资源的列表ID和地图#序号选择器尚未适配。隔离标题探针验证中文资源、采集归属、非文字参数不变和重复Id保护，真实地图UI排版仍待验收。

0.5.3 为静态 CP TargetField 索引保留完整路径，真实标题探针验证嵌套名称/说明及非文字字段不变。含动态令牌的路径不建立猜测归属。按ID索引的列表定位、斜线字符串 Fields 等复杂场景仍待适配。

0.5.2 修复随机字段ID跨启动失配、数字格式显示和早期常量注册采集，添加精确匹配的名称别名。数字模板仅支持单数字段，复杂多数字保持原文。

新增 GMCM 的 mod 显示名称与下拉选项显示文字适配。名称只在 GMCM 的 `ModConfig.ModName` getter 中替换，不改 manifest 或 UniqueID；下拉框只包装显示格式委托，不改 `allowedValues`、`getValue`、`setValue` 或玩家输入。兼容注册时缓存的显示文字；运行时包装器按选项缓存，避免逐帧重复建立集合。隔离游戏探针验证名称、选项显示、原始配置值和不采集任意输入。完整玩家界面布局仍需验收。

离线 SMAPI 伴随插件，目标框架 .NET 6。使用软件的“安装采集插件”，或把 `package` 中自己的 DLL 和 manifest 放到 `Mods/ModLingo.Bridge`。要求 SMAPI 4.1+；本机探针环境 SMAPI 4.5.2、游戏 1.6.15、GMCM 1.16.0、CP 2.9.1。

首次访问相关界面后，`captures.json` 写入 schema 1 采集清单。桌面软件生成 `translations/*.json`（schema 1）。字段包含 mod 身份/版本、adapter、context、source/source_hash、asset/path；译文记录增加 target。软件产生 schema 2 恢复凭据。交换文件不含密钥。

只替换当前原文与上下文匹配的中文记录。GMCM 包装文字委托；绘制适配使用归属明确的 DLL 方法和常量白名单；资源适配在 SMAPI Late 内容编辑阶段处理可识别文字字段。未知来源保持原文。捕获最多 20,000 条，后台原子写盘，去重；字典按资源字段建立索引，绘制查找不遍历全部记录。

构建自己的插件：`powershell -File scripts/build_bridge.ps1 -GamePath <已安装SMAPI的游戏目录>`。游戏/SMAPI/Harmony/MonoGame/Cecil 程序集仅作为本机构建引用，不随包分发。运行时使用 SMAPI 已提供的组件。源码受项目根目录 MIT 许可约束。

`probe` 是受控测试 mod，不应安装到玩家 Mods。`scripts/probe_game.py` 使用独立 Mods 配置，不加载存档、不调用 API；测试目录中的第三方 mod 不纳入发行包。
