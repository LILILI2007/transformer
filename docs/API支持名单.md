# API 支持名单与兼容性说明（0.4.0）

第四次 GitHub 更新重点增加厂商预设和协议适配。**支持指：按官方文档实现文本请求，并通过本地真实 HTTP 协议测试；不表示已经使用各厂商真实密钥验收，也不保证该厂商所有模型都可用。** 本次环境没有可用真实 API 凭据，商业云端测试全部为“待验收”。

## 支持名单

| 设置中的厂商 | 协议 | 预设基础地址 | 主要注意事项 | 本地 HTTP / 真实服务 |
|---|---|---|---|---|
| DeepSeek | Chat Completions | https://api.deepseek.com | 按控制台填写模型 ID；默认保留模型思考设置 | 通过 / 待验收 |
| 千问 / 百炼（北京） | Chat Completions | https://dashscope.aliyuncs.com/compatible-mode/v1 | 默认关闭思考；密钥与地域一致 | 通过 / 待验收 |
| 千问 / 百炼（新加坡） | Chat Completions | https://dashscope-intl.aliyuncs.com/compatible-mode/v1 | 新加坡密钥；可换业务空间专属地址 | 通过 / 待验收 |
| Kimi / 月之暗面 | Chat Completions | https://api.moonshot.cn/v1 | 使用开放平台密钥，不是网页会员凭据 | 通过 / 待验收 |
| OpenAI · Responses | Responses | https://api.openai.com/v1 | 独立处理 input/output；不自动切换模型或协议 | 通过 / 待验收 |
| OpenAI · Chat Completions | Chat Completions | https://api.openai.com/v1 | 使用 max_completion_tokens；仅限支持该接口的文本模型 | 通过 / 待验收 |
| Claude / Anthropic · 原生 | Messages | https://api.anthropic.com/v1 | x-api-key、版本头、顶层 system、max_tokens；支持工作区 ID | 通过 / 待验收 |
| Gemini · 兼容接口 | Chat Completions | https://generativelanguage.googleapis.com/v1beta/openai | Gemini API 密钥；不支持原生 generateContent 地址 | 通过 / 待验收 |
| 硅基流动 | Chat Completions | https://api.siliconflow.cn/v1 | 模型 ID 通常含提供方前缀 | 通过 / 待验收 |
| OpenRouter | Chat Completions | https://openrouter.ai/api/v1 | OpenRouter 自己的密钥，厂商/模型形式的 ID | 通过 / 待验收 |
| Ollama（本机） | Chat Completions | http://127.0.0.1:11434/v1 | 先启动服务并下载模型；无鉴权时密钥可空 | 通过 / 本机模型待验收 |
| LM Studio（本机） | Chat Completions | http://127.0.0.1:1234/v1 | 先加载模型及启动服务；按服务设置填写密钥 | 通过 / 本机模型待验收 |
| 自定义接口 | 三种协议任选 | 用户填写 | 只保证程序支持这些协议，不对未知网关作保证 | 通用边界测试 / 由用户测试 |

共 10 个厂商/本机服务、12 个预设，加 1 个自定义入口。第三方平台上的 Claude、千问等模型应选择**平台本身**的预设，不是模型原厂的预设。不要把厂商网页聊天地址填成 API 地址。

## 玩家如何配置

1. 在主界面或单 mod 编辑器打开 API 设置，选择实际提供密钥的厂商。
2. 保留预设地址，或复制控制台给出的同协议最终基础地址。程序也接受完整接口地址；地址和所选协议不一致会拒绝。
3. 从控制台填写准确模型 ID 与密钥。预设不自动选模型。切换厂商会清空密钥与模型，需重新填写。
4. 输出上限默认 8192 tokens，须在该模型限制以内；每次超时默认 60 秒，可调整至 1–600 秒。较慢的推理模型可先改为 120 秒并少量试译。
5. 点“保存并测试（可能计费）”。通过只代表这次简单请求、JSON 响应与标记检查成功，随后先勾选少量 mod 验证。

密钥只存于进程内存，也可从 MODLINGO_API_KEY 环境变量读取，不随项目、翻译包或结果记录保存。远程只允许 HTTPS；HTTP 仅限 localhost/loopback，不跟随重定向。自定义协议入口支持 Chat Completions、Responses、Claude Messages，不支持任意请求头、任意认证协议或 Azure/Bedrock/Vertex 的专用认证流程。

## 千问、思考模式与地域

千问预设默认发送 enable_thinking=false，以适配可关闭思考的非流式文本模型。必须思考或只支持流式的模型不在当前范围；请选可关闭思考的模型。北京和新加坡密钥不能混用。

官方已建议使用业务空间专属地址，旧 dashscope 域名仍可使用。例如北京可以将基础地址替换为 https://你的WorkspaceId.cn-beijing.maas.aliyuncs.com/compatible-mode/v1，新加坡使用对应 ap-southeast-1 地址；复制控制台实际值，不能保留模板字样。

DeepSeek、Kimi 默认不发送思考开关，保留模型默认行为。可按模型文档勾选关闭思考，发送 thinking.type=disabled；仅在该模型支持关闭时使用。该选项不会发送给其他厂商。程序只取最终文本，不把 reasoning_content、Claude thinking 或 Responses reasoning 当作译文。

## Claude 与 OpenAI

Claude 使用原生 Messages，无需依赖兼容层。多工作区密钥需填写 anthropic-workspace-id；单工作区密钥可留空。本工具不支持云平台 IAM 或 OAuth 登录代替 API 密钥。

OpenAI 的 Responses 和 Chat Completions 分别列为预设，不能混填完整地址。Responses 使用 instructions/input/max_output_tokens/store:false，读取 output 数组中的最终文本；Chat 使用 messages/max_completion_tokens。均不添加 temperature，以减少不接受该参数的模型发生配置错误。

## 兼容性边界与失败

- 只处理非流式文本输出，不支持必须流式的模型、图片/音频、工具调用或登录态聊天接口。
- 截断、内容过滤、拒绝、工具结果、空文本、只有思考无最终文本均不写入译文，即使截断片段碰巧是合法 JSON。
- 401/403/404/429 以及参数/协议错误 400 会停止剩余批量请求并逐个记入名单，避免配置错误重复消费。
- 408/429/500/502/503/504/529 和网络错误最多三次尝试；重试可能计费。错误正文不写日志或报告。
- 不会自动换厂商、模型或协议来“试通”，避免额外调用及数据发往非预期目的地。
- 厂商协议兼容不代表模型始终返回合法 JSON、翻译语义准确、账户有额度或网络地区可用。网页会员也不保证包含 API 额度。

## 本次如何测试

使用独立于运行时代码的预期请求路径，通过本机 HTTP 服务实际收发请求。逐项核对 12 个预设的 URL、鉴权、请求字段、非流式开关及三种响应结构。覆盖占位符和换行往返、文本块、思考分离、截断拒绝、鉴权错误脱敏、重定向不转发凭据、Claude 529 限次重试、协议地址冲突、输出/超时限制以及设置界面切换行为。

测试入口：python -m unittest discover -s tests -v。测试不读取真实服务密钥，不调用商业模型，不产生 API 费用。本次本地 90 项测试：89 项通过、1 项链接测试因系统权限跳过；云端结果见 Actions 和验证记录。

## 自愿运行真实服务验收

便携版用户可使用界面的“保存并测试”。开发者可在源码环境中用 scripts/check_live_api.py 发送三条原创短文本（占位符、换行、游戏标记），并保存脱敏检查结果和服务返回的 token 用量。

先自行将对应密钥放进环境变量，再运行，例如：

```powershell
python scripts/check_live_api.py --provider deepseek --model 你的模型ID --key-env DEEPSEEK_API_KEY --allow-paid
python scripts/check_live_api.py --provider qwen --model 你的模型ID --key-env DASHSCOPE_API_KEY --allow-paid
python scripts/check_live_api.py --provider kimi --model 你的模型ID --key-env MOONSHOT_API_KEY --allow-paid
python scripts/check_live_api.py --provider openai --model 你的模型ID --key-env OPENAI_API_KEY --allow-paid
python scripts/check_live_api.py --provider claude --model 你的模型ID --key-env ANTHROPIC_API_KEY --allow-paid
```

没有 --allow-paid 会直接退出，不发送网络请求。需要时用 --base-url 指定专属地址、--workspace-id 指定 Claude 工作区。结果默认存本机 ModLingo/api-checks，记录厂商、协议、模型、主机、用量及检查结果，不含密钥、原始响应或译文。真实验收只覆盖该账号、地址和模型的一次测试，不能推导整个厂商全部可用。

## 官方依据

核对日期：2026-10-16。模型列表、地址和服务政策变化时应重新核验。

- [DeepSeek 官方接口](https://api-docs.deepseek.com/)
- [千问兼容接口与地域](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope)、[思考模式](https://help.aliyun.com/zh/model-studio/deep-thinking/)
- [Kimi Chat Completions](https://platform.kimi.com/docs/api/chat)
- [OpenAI 文本生成与 Responses](https://developers.openai.com/api/docs/guides/text)
- [Claude Messages](https://platform.claude.com/docs/en/api/messages/create)、[鉴权及工作区](https://platform.claude.com/docs/en/api/overview)
- [Gemini 兼容接口](https://ai.google.dev/gemini-api/docs/openai)
- [硅基流动 Chat Completions](https://docs.siliconflow.cn/docs/api/chat-completions-post)
- [OpenRouter 接入](https://openrouter.ai/developers)
- [Ollama 兼容协议](https://docs.ollama.com/api/openai-compatibility)
- [LM Studio 兼容协议](https://lmstudio.ai/docs/developer/openai-compat)
