# WeChat Agent Bridge

**让不同 AI 智能体通过统一、可控的工具与微信交互。**  
A model-agnostic framework for controlled WeChat interactions by AI agents.

> 非腾讯或微信官方项目。已实现 M0 离线骨架与合成数据 mock；没有真实微信适配器或 AI 服务，未验证任何微信版本的实际兼容性。

## M0 本地运行

使用 Python 3.12，仅依赖标准库，无需安装包、微信、账号或 API key。在仓库根目录运行：

```powershell
python -X utf8 -m unittest discover -s tests -v
python -X utf8 -m wechat_agent_bridge
python -X utf8 -m wechat_agent_bridge --mode dry-run
```

默认模式为 `read-only`，演示动作返回 `denied / read_only`；`dry-run` 校验账号、会话、草稿和连接状态后返回 `dry_run`，不会提交动作。演示只读取内置合成事件，过滤重复事件和自己发言，不自动回复。

显式选择 `mock` 可在内存中演练尝试、核验和不确定结果，仍不会操作任何客户端：

```powershell
python -X utf8 -m wechat_agent_bridge --mode mock --outcome verify-timeout --state-db .local/unknown-demo.sqlite3
python -X utf8 -m wechat_agent_bridge --mode mock --state-db .local/unknown-demo.sqlite3
```

使用新的示例数据库时，第一条命令返回 `unknown`；第二条仍为 `unknown`，且 `replayed=true`、`mock_submissions=0`。固定演示请求 ID 不会因为重启或切换 mock 结果而再次提交。

默认状态文件为 `.local/m0-state.sqlite3`，仅保存去重哈希与结果，不保存消息正文；已从 Git 排除。`--state-db :memory:` 适合相互独立的合成演示，不提供跨进程去重。`--paused` 演示暂停。输出中的 `ui_confirmed` 只是 **mock 核验结果**，不代表真实发送或收件人送达。

实现与测试环境见 [M0 实现说明](docs/M0.md)，后续验证见 [M1 测试计划](docs/M1_TEST_PLAN.md)。

## 项目目标

把微信交互能力与模型、人设、记忆分离：同一套工具层可以供不同模型或智能体使用，而不绑定单一 AI 厂商或角色。

规划能力：
- 消息：在指定会话读取新消息、发送回复、去重、核验结果。
- 模型：可替换模型提供方，不把业务逻辑绑定到某一家 SDK。
- 智能体：可配置人设、上下文与按会话隔离的记忆；允许决定不回复。
- 账号操作：后续探索好友申请与联系人管理，默认关闭，独立授权。
- 控制：会话白名单、只读/预演模式、操作确认、暂停开关与审计记录。

除上文列出的 M0 离线能力外，以上仍为规划。项目不提供微信账号，不解决移动端登录资格，也不能保证客户端长期在线。

## 设计原则

1. 优先研究原版客户端的外部 UI 自动化，不修改客户端、不提取凭证。
2. 工具可调用不等于有权限执行；模型只提出操作，执行层独立校验账号、目标与授权。
3. 默认只读或预演。真实发送需显式启用，先在专用测试会话验证。
4. 不确定是否发送成功时返回 `unknown` 并检查，不盲目重发。
5. 聊天内容是不可信输入，不能变更授权、执行 shell、读取任意文件或取得密钥。
6. 不向模型提供方默认上传全量聊天；明确告知数据流向，按会话隔离、最小化存储。
7. 不在仓库提交真实聊天、联系人、截图、登录状态、数据库或 API 密钥。
8. 官方客户端并不意味着自动化获平台认可；仍可能出现账号限制、兼容性失效和隐私风险。

## 初期技术方向

建议从 Windows 桌面客户端外部适配器开始；实现技术以本机验证为准。Android、macOS 适配器作为未来独立候选，不承诺当前可用。

建议分层：微信适配器 → 标准消息事件/操作结果 → 策略与工具路由 → 模型提供方 → 人设与记忆。
AI 不是直接控制所有界面动作的唯一决策者；目标核对、去重、超时、暂停和结果核验由确定性代码承担。

## 第一阶段范围

只构建最小消息读发链路：
- mock 适配器与合成测试数据；
- 一个明确允许的测试会话；
- 一条短文本的发送与回读核验；
- 已有草稿、目标不确定、掉线或超时时安全停止；
- 不做自动回复、不添加/删除好友、不连接真实模型。

其中 mock 与离线策略属于已实现的 M0，真实读发属于尚未执行的 M1。见 [开发交接](docs/DEVELOPMENT.md) 和 [AGENTS.md](AGENTS.md)。本地 Codex 应先阅读这些文件。

## 可参考项目

这些是调研入口，不是已验证的依赖，也不是官方微信接口：
- [pywechat / pyweixin](https://github.com/Hello-Mr-Crab/pywechat)：Windows UI 自动化研究参考。
- [wxauto 文档](https://docs.wxauto.org/)：Windows 界面读发能力参考，部分功能有付费限制。

不要直接运行示例或复制代码。采用具体版本前核对许可证、依赖、默认副作用与当前微信版本兼容性。不同版本/账号的可访问性树可能不同。

## 运行限制

运行消息处理的设备或环境必须保持在线。关机、休眠、客户端掉线、界面变化都可能中断处理。更换模型或使用云 API 不会让本地客户端在关机后继续工作。

## 许可证

本项目原创内容采用 [MIT](LICENSE) 许可证。第三方代码及依赖保留其各自许可证；本项目许可证不覆盖第三方权益。
