# Agent API 配置与全局默认验收（2026-10-29）

## 交付行为

Agent 页面提供 API 配置的新增、编辑、删除及默认选择。配置、角色卡与两项默认值全账号共用，单会话的选择、记忆、开关继续按账号隔离。首次创建自动默认，既有角色迁移时选择最早创建项；默认元数据不进入 V2 角色导出。会话角色与 API 独立解析为显式选择或持续继承全局默认，未配置或密钥不可读取时阻止启用并给出配置入口。

服务商预设涵盖 OpenAI、Anthropic、Gemini、Grok、DeepSeek 及自定义，包含四类协议；地址和模型名可编辑。Gemini 支持原生和 OpenAI 兼容入口，Anthropic 支持版本与工作空间字段。本期不实现模型请求、连通性测试、回复生成、微信发送或自动记忆，保存成功只表示配置层通过校验。

密钥由当前 Windows 用户 DPAPI 加密存入本机 SQLite，查询不回显明文或密文。编辑留空保留旧密钥，填写新值替换；解密失败提示重新填写，不退回明文。显式引用（包括 AI 已关闭的会话）和已开启会话的默认继承均阻止删除。未使用默认项删除后清空默认值，不自动提升其他条目。并发编辑通过修订号防止覆盖，跨页面通知保留草稿。

## 验证

- 后端 112 项通过（新增 13 项配置测试），覆盖全部服务商/协议预设、真实 Windows DPAPI 往返、密钥轮换/省略保留/不可读、无密钥回显、启用原子校验、独立继承、跨账号隔离、引用保护、并发默认变更、并发绑定与删除、重启持久化及版本 4 迁移失败回滚。
- 前端 29 项单元测试通过；TypeScript、构建、格式和 OpenAPI 类型一致性检查通过。
- 原有 15 项浏览器回归通过，覆盖账号、聊天历史、静态/GIF 表情、角色卡编辑导出、会话记忆、导航保护和迟到响应隔离。
- 新增 5 项真实前后端浏览器联调通过。独立 Vite 端口 15173、后端端口 18765、纯内存 SQLite、合成账号/密钥；覆盖 CRUD、跨账号共享、留空保留密钥、默认切换与多页面通知、缺失提示、引用删除保护、版本冲突与未保存导航、全部预设及 390px 窄屏。
- 截图 `.local/ai-config-acceptance/api-mobile.png` 仅含合成资料。联调服务由 Playwright 负责启停，不读取真实微信、用户归档或调用模型。

运行方式：后端 `unittest discover -s backend/tests`，前端 `npm test`、`npm run build`、`npm run check:api`、`npm run format:check`、`npm run test:e2e`、`npm run test:e2e:ai`。

## 本机切换

北京时间 2026-10-29 14:11，先以 SQLite backup API 生成在线一致性备份并检查完整性。原有隐藏启动器没有平滑退出入口，因此在核实其脚本、PID 与进程身份后停止拥有本项目 Windows Job 的启动器；没有结束或操作微信。确认后端端口关闭、数据库所有者锁可取得后，再复制主库与仍存在的 WAL/SHM，逐文件校验 SHA-256。

完整备份位于 `.local/ai-config-acceptance/backup-before-v4-20261029-141138/`。版本 3→4 迁移后，核对全部账号资料、会话、消息、媒体、同步状态、角色卡及原会话设置的逐行摘要一致。保留 2 个账号、350 个会话、1,404 条消息、256 个媒体条目、304 条同步状态、1 张角色卡与 5 条会话设置。`quick_check=ok`，外键违规为 0。

现有角色卡已设为全局默认。真实 API 配置仍为空，没有写入测试密钥或替用户创建服务商密钥。两账号的会话配置查询都能返回有效角色与缺失 API 的提示。

新服务已隐藏启动；前端 5173 返回 200，后端健康检查通过。迁移与只读服务验证证据位于 `.local/ai-config-acceptance/migration.json` 和 `running-service.json`。数据库、密钥、聊天及角色内容仅留本机；源码备份不包含这些运行数据。

## 参考

配置组织借鉴 [SillyTavern Connection Profiles](https://docs.sillytavern.app/usage/core-concepts/connection-profiles/)。服务商预设依据 [OpenAI](https://developers.openai.com/api/docs/guides/migrate-to-responses)、[Anthropic](https://platform.claude.com/docs/en/api/overview)、[Gemini](https://ai.google.dev/api/generate-content)、[Gemini 兼容入口](https://ai.google.dev/gemini-api/docs/openai)、[Grok](https://docs.x.ai/developers/quickstart)、[DeepSeek](https://api-docs.deepseek.com/) 官方文档；密钥存储采用 [Windows DPAPI](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata)。这些配置测试不构成真实模型调用的兼容性验收。
