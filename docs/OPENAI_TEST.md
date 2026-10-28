# OpenAI 合成文本测试

2026-10-26 新进展：使用原本机密钥和固定模型完成文本、语音转写、摘要真实验证；视觉未通过。受限验收账本累计 5/10 次，未上传真实聊天，完整离线 734 项通过。下文 2026-10-23 的一次文本测试为历史记录，不能与本轮账本混用；最新证据见 [M1 真实验收](M1_LIVE_ACCEPTANCE_20261026.md)。

## 使用方式

在 Windows 的仓库根目录运行：

```powershell
python -X utf8 -m wechat_agent_bridge console --adapter mock --data-dir .local/openai-test --openai-env-file .env.local
```

`.env.local` 由 OpenAI Developers 安全设置流程创建，包含 `OPENAI_API_KEY`，已被 Git 忽略。程序只读取显式指定文件中的一个字面量赋值，支持单/双引号及 `export` 前缀，不执行命令、不展开变量、不回退到进程已有密钥。该文件仍是本机明文秘密文件，不能提交、分享或放入截图。导入后控制台配置使用当前 Windows 用户的 DPAPI 加密保存，页面只返回是否配置了密钥。

普通 `--openai-env-file` 导入仍只允许 `--adapter mock`；未指定 `--data-dir` 时默认使用 `.local/openai-test`。导入固定配置：OpenAI 官方 `https://api.openai.com/v1`、`gpt-4.1-mini-2025-04-14`、每小时及过去 24 小时各两次调用，视觉与转写模型留空。相同配置再次导入保留能力验证状态和调用计数；改变配置会清除能力验证状态。启动始终暂停，不创建读取、上传或回复授权。

后台续作的例外为显式 `--adapter windows --live-acceptance`：固定独立验收目录及累计最多 10 次请求，视觉指定同一 `gpt-4.1-mini-2025-04-14`、转写指定 `whisper-1`；请求前扣减持久额度，不自动换模型。真实后台写入当前阻塞，本轮未调用这些模型，详情见 [后台验收](M1_BACKGROUND_ACCEPTANCE.md)。

在控制台 AI 页点击“测试文本”，会发出一次可能计费的请求。测试只使用固定合成消息，通过 `/chat/completions` 验证实际生成及 JSON 回复格式，最大输出 128 tokens，设置 `store=false`。使用与正常回复相同的格式校验；空回复、沉默、格式错误或截断不能通过文本测试。只有已配置模型时才可点击图片或语音测试，本轮不验证这两项。

测试会暂停自动处理，取消尚未执行的队列任务。30 秒超时后终止模型工作进程，不重试、不切换模型或服务。调用名额在请求前持久化，认证、限流、额度、超时等失败也消耗名额；这些是调用次数限制，不是美元费用限额。认证失败、额度不足、超时和格式错误只显示固定原因码，不显示服务端原始错误正文。

## 2026-10-23 实测

| 项目 | 结果 |
| --- | --- |
| 系统 / Python | Windows 11，10.0.26300；Python 3.12.10 |
| 基准 Git HEAD | `f5d8df40993beb7fe897a7cb3b7339e23dd1cec1`，包含本地未提交实现 |
| 微信适配器 | `MockConsoleAdapter`，合成数据；真实微信读取/发送均未执行 |
| 官方模型 | `gpt-4.1-mini-2025-04-14` |
| 真实请求 | 1 次，通过经过认证的本地 `/api/provider-test` 路由发起 |
| 测试时间 / 耗时 | 2026-10-23 10:03:33（Asia/Shanghai）；3.109 秒 |
| 结果 | HTTP 200，文本能力通过；图片、语音未启用 |
| 请求后状态 | 全局暂停，账号授权数 0，mock 发送数 0；小时/日调用数均为 1 |
| 密钥检查 | 未出现在 HTTP 响应或加密状态库的明文中；未改变进程原有密钥 |
| 离线测试 | 系统 Python：102 项成功、其中 2 项可选解析器测试跳过；项目 `.venv`：102 项全部通过 |

实际测试源码 SHA-256（防止把未提交实现误认为仅由 Git HEAD 标识）：

- `providers.py`：`5e482aed60a9a4525e4e00ae9a0d1c531c4f12cd518129aa5e8a227822681207`
- `openai_config.py`：`cf4b0d381e48abc01ee5c3994d07082f101818130d7c4c1fb243aa56eacdc8bc`
- `console_service.py`：`fbbf5d526f358f49e2c648be038028b5c0ca5157cbab7a94910bddfb9e5bd4cc`
- `adapters/console_mock.py`：`673e60f9e735d1e14235a65500d2d40f1858ccb40778164215dc384f72883899`

本地脱敏记录为 `.local/openai-test/verification-result.json`。一次性验证标记 `.local/openai-test/verification-attempt.json` 在请求前写入；本次验证脚本再次执行会拒绝重试。手动再次点击控制台按钮属于新的真实请求。

真实请求后，工作区的另一项改动将授权规则提取到了 `ConsolePolicy`，使 `console_service.py` 的 SHA-256 变为 `328f5307917efcf46714a7d65d8e69778daa03cdc5d21d83adbf4488048fe16a`；Provider、导入模块和 mock adapter 与上述真实请求时一致。保留该改动并对当前状态再次运行完整套件，102 项全部通过，没有再次调用付费 API。启动页面也确认文本已通过、图片/语音按钮禁用、全局暂停；测试服务随后关闭。

最终脱敏检查记录为 `.local/openai-test/final-audit.json`：Git 可见文件和本地诊断文件的密钥匹配数均为 0，DPAPI 配置能在另一个进程正常读取，真实请求计数仍为 1，临时解密私钥已移除。

首次创建但未保存的同名密钥已在平台撤销；后续替代密钥已保存在 `.env.local` 并用于上述成功请求。没有把密钥内容、平台账号标识或聊天内容写入仓库。

本证据只确认该模型的合成文本请求和格式校验通过，不证明真实微信兼容、自动回复、图片理解、语音转写、摘要质量或其他模型可用。M1 的真实客户端验收仍未完成。

## 离线复核

```powershell
python -X utf8 -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -v
node --check wechat_agent_bridge/web/app.js
```

默认测试只使用合成密钥、注入的 HTTP 或本机环回服务，不读取 `.env.local`，不访问付费 API。若执行沙箱的临时目录不可写，可将当前命令进程的 `TEMP` / `TMP` 指向项目内已忽略的 `.local/test-tmp` 后重跑，不需要更改全局环境。
