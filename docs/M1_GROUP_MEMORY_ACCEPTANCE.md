# M1 单群读取、定时与增量记忆验收

2026-10-26：**真实 180 秒摘要、仅一条新增记录的增量摘要、两次 bridge 新进程重启暂停均通过。M1 整体仍未完成。** 本次为单项读取／记忆验收，不取代群 @、私聊回复、附件、分页、全文语义或完整身份验证。

## 已实际通过

环境为 Windows `10.0.26300`、Python `3.12.10 x64`、微信 `4.1.15.56`；DLL SHA-256 `932dd6700f22e32faeb9b0cc9581a1f601e60ace151067680d3f6596496fab99`。同一已绑定进程、创建身份、窗口与群选中行核验通过。文本模型为既有 `gpt-4.1-mini-2025-04-14`，官方 OpenAI 接口，未更换模型或密钥。

| 验收 | 真实结果 |
| --- | --- |
| 首次定时记忆 | 单调时钟 182.219 秒后调用一次摘要，4 条已核验文本行，source_start=1、watermark=4、407 字；36 次只读采集通过 |
| 新固定记录 | 编号 003 在当前桌面后台发送、一个精确回显、空稿、零事件、完整卸载；见[发送报告](M1_BOUND_SIGNAL_SEND.md) |
| 增量记忆 | 单调时钟 180.125 秒后调用一次，仅旧摘要＋1 条编号 003 新文本，source_start=5、watermark=5、725 字；35 次只读采集通过 |
| 固定事实判定 | 新摘要保留旧标记 M1／002、项目编号 BRIDGE-MEM-003、测试日期 2026-10-27 和时间 14:30 |
| 两次新进程重启 | 实际子进程重新打开 DPAPI 数据库：摘要哈希和水位一致、全局及账号暂停、范围未绑定、未重新绑定时 resume 被拒绝、调用额度未增加 |
| 清理 | 71 次成功采集报告哈希、零事件、开关恢复、子进程退出及空清理错误均复核；最终 pending 和运行锁均为空 |

摘要正文和消息保存在 `.local/live-acceptance/group-memory.sqlite3` 的 DPAPI 加密负载内，不写入本页或操作日志。第二次调用记录的旧摘要哈希等于第一次产物，且 `incremental_context_verified=true`，没有重新上传全部旧消息。四项判定只证明明确合成事实保留，`summary_semantics_verified=false` 保留，不声称整段人类聊天已逐句复核。

当前 API **8/10**，发送 **3/5**，原 fixed unknown 和两个独立扩展审计保留；重启和清除记忆不能退还额度。两个 memory case 均已持久消费，**不要再次执行以下历史命令或删除账本重放**。

## 原始证据

以下文件均在 `.local/live-acceptance/`；每次只读报告及加密捕获的哈希由主报告引用。

| 文件 | SHA-256 |
| --- | --- |
| `group-memory-693df177caf94a0facb1116a73662d55.json` | `1df886861707c7f23652eea8560b5684ff1ab485d27118d266c12fa0129444f0` |
| `group-memory-23b8c4eb929e4f9685b972f081f0a5a9.json` | `42e18342224e7ee23ae82311b53da4128c0217b9dee1ccd406ef6ddfbb828e64` |
| `signal-send-3c666444adc34465ae4394c9c5cb27c2.json` | `c12a64db1127c4709c0414a751c4f641c73178655432f2a2fd805f3aefe85226` |
| `offline-incremental-memory-tail-20261026.txt` | `767ca065898a083a47d848355cb8c2a2985c439cd971ebfaccc53ce93fc91358` |

首次脚本修订 SHA-256 `7bd10bb8efd4a8fdfb0f4eef3c634d9a7afa23a212cc69e89faef9b0cd902289`，增量修订 `ae7a194b2cb3b5b0d5b9d2885c9a55f80f06d6c3165ce2c83b3fe18f26c34eb7`。首次摘要哈希 `d8457830d43c383d229f2a6f89af94a64dcb3fac28036f73cb3cc5aa865c280e`，增量摘要哈希 `3b4f3d695847838b2816c6e1609ddd717056b349a004beef43bd6d4e13401c23`。

保留一次真实失败 `group-memory-5ffcf432a7e64c20aac2ffa7c5428a34.json`：`group_memory_incremental_rows_changed` 在上传和 case 消费前拒绝，API 仍 7/10。对照受保护捕获表明仅最早旧文本移出加载尾部，其他三条旧文本的 RuntimeId、哈希及顺序未变，新 003 的 RuntimeId 与发送回显相同。现改为要求已知旧序列的**连续有序后缀＋唯一新回显**，至少保留末条旧记录；缺失中间项、乱序、标识重建、额外文本或正文变化仍拒绝。成功报告明确记载一个旧前缀项不在当前加载范围，不补读、不重新上传，也不声称分页完整。

## 环境准备的实际结果

computer-use 已从工具返回列表取得微信窗口 67678，并在自己的资料卡确认微信号与原绑定发送账号一致。对获准私聊微信号的本地搜索没有结果，网络查询返回 `No related account or content found.`；未添加好友、未打开其他私聊。操作者随后提供了同名联系人的显示名线索，后续仍须由资料卡微信号核验，不能把备注当身份。

关闭查询和资料卡后返回原测试群，目视输入框为空，未发送。通过 computer-use 点击最小化后，独立项目验收已重新核验后台焦点条件并完成上表结果；没有把工具成功返回当作后台能力证明。不得把未列出窗口当成进程退出，也不要重复启动微信。

观察记录 `.local/live-acceptance/development-cua-bbf9228d3e114410b63d1f1369e67dde.json`，SHA-256 `641a66d6c607e6c7cf251ecc17e97fe9dee553857aa87d45fc6fb2deffe0f8ac`。这是 Codex 的界面观察记录，不是项目 UIA 能力证明；没有复制截图、获取头像文件或导出联系人。

## 代码变更

- `AcceptanceSession.bind_group_reading()` 只接受一个已有身份依据的群，允许明确授权的读取和上传，禁止回复授权和所有发送占额。原完整验收 `bind()` 仍要求群／私聊两个已核验对象，不能用此入口宣布完整验收完成，也没有新增 HTTP 授权入口。
- 控制台恢复运行改为读取持久账本的实际 API／发送上限，修复追加 API 额度后仍被写死的 10 次门槛挡住的问题。额度耗尽仍拒绝，旧消耗、unknown 和槽位不重置。
- 消息 `own=None` 明确表示发送者未核验；这类消息居中显示并标注未核验，不触发私聊或群聊自动回复。原布尔状态保持原行为。
- 新增 `scripts/verify_group_memory.py`，默认 disabled。显式运行时仅复用已绑定群的只读采集、现有 ConsoleService 180 秒调度、受保护存储及既有 OpenAI 文本模型；只允许一次摘要，没有会话切换、输入或发送路径。

真实运行前需重新匹配账号／窗口／进程创建身份／DLL 指纹和群输入、选中行 RuntimeId，检查空稿、未接管、无恢复记录及停止文件。每次读取由有界工作进程采集最近最多 50 个可访问项，临时开关每次恢复；父进程核对无焦点事件、恢复、来源及加密文件哈希。只有已识别的 ChatTextItemView 文本进入摘要，通用行不当作附件正文或时间，覆盖范围明确不完整。发送者保持未知，所有记录在该入口标为历史，不据此触发回复。

会话存储键按已核验进程创建身份和内部会话行标识隔离，不使用昵称冒充微信 ID。此处只验证同一微信进程内的 bridge 重启保留；**不能据此声称跨微信重启的稳定群身份已经解决**。

脚本要求显式 `--upload-group-to-openai`，沿用操作者已指定的 `.env.local` 密钥、OpenAI 官方地址及 `gpt-4.1-mini-2025-04-14`。检查密钥存在、地址和模型匹配；没有显示密钥或创建新密钥。实际两次请求已计账，旧文本成功报告仅放行文本能力；视觉失败仍不启用视觉。

调用前使用原账本扣额；包装器另外要求实际单调时钟已过去至少 180 秒、最近读取不超过 10 秒，且全程只调用一次摘要。暂停／接管／停止或错误会退出；同一验收 case 记录持久化，不自动重放。摘要成功存储后，新子进程重新打开实际受保护数据库，检查摘要哈希及水位保留、全局和账号暂停、目标未绑定、未重新绑定时无法恢复、没有新增 API 消耗。该过程不操作微信。

产出会区分“定时摘要已保存／重启暂停”和摘要语义是否人工复核；非空文本、HTTP 成功或存储成功均不能独自证明摘要正确。每次采集报告和正文分离，正文只在 DPAPI 文件／数据库中。

## 已运行测试

最新完整离线 **838 项通过，13.879 秒，无跳过**；日志和哈希见上表。此前 829 项／14.185 秒准备日志 `offline-group-memory-ready-20261026.txt` 原样保留。`node --check wechat_agent_bridge/web/app.js`、`git diff --check` 通过；默认脚本返回 disabled。

新增测试覆盖单群范围／发送禁止、追加额度的控制台恢复、未知发送者不触发、错误窗口及重复 RuntimeId、原文／加密文件限制、恢复或哈希失败、180 秒前拒绝、超时不再调用、取消后丢弃、应用实际调度方法只汇总新增记录。增量阶段另覆盖旧摘要被改、错误／unknown 发送证据、连续尾部裁剪／中间丢失、标识重建、额外文本、只传单条新上下文、旧新事实丢失和第二次调度不再上传。测试使用合成数据和模拟时钟，**不作为真实计时和模型验收**。

## 历史真实命令与安全复核

下面是已完成的首次 case 命令，供复现环境和审计使用；本机已消费，重跑会拒绝，不可清账本解锁。只读取群，私聊参数仅沿用原目标校验接口，不读取或上传私聊。增量 case 在相同参数上另加 `--incremental-fixture --previous-memory-evidence .local/live-acceptance/group-memory-693df177caf94a0facb1116a73662d55.json --fixture-send-evidence .local/live-acceptance/signal-send-3c666444adc34465ae4394c9c5cb27c2.json`，也已消费。

```powershell
.\.venv\Scripts\python.exe -B -X utf8 scripts/verify_group_memory.py `
  --run-bound-memory --upload-group-to-openai --allow-accessibility-patch `
  --account '<已绑定发送账号>' --group '<已绑定测试群>' --private '<已授权私聊微信号>' `
  --env-file .env.local `
  --provider-evidence .local/live-acceptance/provider-acceptance-241efabbd3054ba1bfb84a7eb29da5e1.json `
  --target-evidence .local/live-acceptance/m1-target-control-refresh-20261026.json `
  --synthetic-evidence .local/live-acceptance/uia-node-context-faea0f12f99e4c6cbb69960274a76e4c.json
```

同桌面最小化读取使用既有普通后台检查，不把它标成虚拟桌面隔离验收。出现 `human_takeover`、控制标识变化或其他拒绝时先读取明确错误证据，不自动激活微信、清空用户草稿或重试 unknown。保留两次后台发送证据；生产适配表继续为空。

安全复核只运行离线测试并读取结果元数据，不连接微信或调用模型：

```powershell
$env:TEMP = Join-Path (Get-Location) '.local/test-tmp'
$env:TMP = $env:TEMP
.\.venv\Scripts\python.exe -B -X utf8 -m unittest discover -s tests -v
$m1Memory = Get-Content -Raw -Encoding UTF8 .local/live-acceptance/group-memory-23b8c4eb929e4f9685b972f081f0a5a9.json | ConvertFrom-Json
$m1Memory | Select-Object state,calls,fixture_checks,restart,quota_after | ConvertTo-Json -Depth 5
```

下一步先用 computer-use 复核指定私聊联系人资料卡，再独立进入项目适配验收；开发辅助可以临时影响桌面，不能代替后台能力。群 @、通用己方／对方身份、历史分页、附件取得和完整自动回复仍保持未通过。
