# 微信 Agent Bridge

Windows 本机微信工作台：Vue 3 / TypeScript 前端，Python 3.12 / FastAPI 后端。
账号、会话、聊天历史和按需读取的图片、静态表情及 GIF 保存到本地 SQLite。页面和 API 优先读取数据库，微信退出、后端重启后仍能查询已保存历史。

## 启动

要求 Windows x64、Python 3.12、Node.js 22.12+ 或 24+。当前真实兼容基线为微信 **4.1.15.56**。

```powershell
python -m venv .venv-backend
.\.venv-backend\Scripts\python.exe -m pip install -r backend\requirements.lock.txt
Push-Location frontend
npm ci
Pop-Location
.\scripts\dev.ps1 -Check
.\scripts\dev.ps1
```

访问 <http://127.0.0.1:5173>，接口文档 <http://127.0.0.1:8765/docs>。
`-NoBrowser` 不打开浏览器；`-SmokeTest` 只检查服务启动与清理，禁用真实监控。
Ctrl+C 停止本次启动拥有的服务及读取子进程，不结束微信或其他服务。端口被占用时不抢占。
也可用独立终端启动：

```powershell
.\.venv-backend\Scripts\python.exe -B -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8765 --workers 1 --no-access-log
# 另一个终端，位于 frontend
npm run dev
```

后端只允许一个 worker，同一数据库有独占所有者锁。仅绑定本机地址，并检查 HTTP 与 WebSocket 的 Host/Origin。
正常服务启动后异步识别一次，随后常驻每十秒检查。页面关闭不停止监控；服务停止则停止同步。
首次识别可能需要约三分钟，但已有数据库内容无需等待识别即可显示。

## 存储与同步

- 数据库：`.local/bridge.sqlite3`。这是用户选择的普通明文 SQLite；数据库、WAL、锁、图片及诊断结果均不进入 Git。
- 稳定 ID：账号、会话、消息 ID 跨重启保持不变。微信 PID、创建时间及登录身份只作为当前读取绑定重新确认。
- 首次基线：逐个消息分片记录每个消息表的最高本地编号，不回灌此前全部正文。基线逐步完成期间，页面可照常查询缓存。
- 自动新增：所有可解析会话的新消息均入库，包含未打开及被页面过滤的会话。服务重启从已提交水位续读。
- 按需历史：打开会话先显示数据库结果；缺失的最新窗口、更早历史通过后台任务补录。尚未完整缓存的区间明确标为部分覆盖。
- 轻量检查：每十秒检查文件元数据和已缓存的少量账号身份指针。没有变化时不解密、不查询微信消息正文。有变化才处理相关来源，单批最多 100 条，重任务共用一个队列。
- 更新通知：事务提交后通过 WebSocket 发送 JSON 变更通知，前端随后从 SQLite 获取变化；断线重连重新查询。前端不再定时重读微信。
- 图片和表情：只缓存实际请求且成功读取的资源，单图最多 8 MiB，共享 512 MiB LRU 配额；头像不占此限额。SQLite 空闲页及 WAL 可能使文件占用高于有效图片容量。
- 表情读取：优先查询本地归档缓存，未命中时只读对应微信消息 XML；缺少原图地址则按 MD5 精确补查当前账号的 `emoticon.db`。后端按需从微信 CDN 下载，返回含 `mime_type` 和 `data_base64` 的 JSON。GIF 原字节保存，不抽帧、不转码；原图不可用时才降级缩略图并标注。缓存命中不访问微信或网络；未缓存资源首次获取需要微信在线。
- 表情仅在页面可见且进入可视区域时获取，离开可视区域或隐藏页面会卸载图片节点、保留布局；重新显示复用缓存。前端图片和表情共享 32 MiB Base64／64 项限额，失败仅手动重试，不全量预下载历史。
- 语音：仅保存微信已有的转写，不触发识别或调用外部服务。其他附件仅保存已解析的说明，不自动下载。
- 删除与撤回：本地归档保留已经获取的历史，不跟随源库删除；新系统消息和近期变化按增量读取保存。

微信源数据仍使用共享只读句柄、已提交 WAL 校验、逐页认证和内存解密。
已捕获并验证的快照不会因为解密期间发生后续写入而被丢弃；后续检查继续追上新提交。
工作进程无窗口、低优先级，文件/内存读取限速 32 MiB/s；沿用 384 MiB 解密缓存、单库/WAL 256 MiB、单分片 60 秒及消息任务 180 秒上限。
这些是处理预算，不代表进程总内存上限。不操作窗口、焦点、鼠标、键盘、剪贴板，不注入、不自动提权、不发送消息或标记已读。

表情下载只接受来自绑定消息或同账号表情库的地址，不接受 API 调用方提供 URL。允许 `qpic.cn` 及其子域，另精确允许本机 4.1.15.56 样本实际使用的 `vweixinf.tc.qq.com`。每次跳转都重新检查域名及 DNS 公网地址，并将连接固定至已验证 IP；不使用系统代理或改变代理设置，不携带 Cookie。最多两次跳转，网络总预算 20 秒、限速 1 MiB/s，仍受单媒体任务 60 秒预算约束。表情校验最多 500 帧、累计解码 1 亿像素；超限、未知加密格式、下载失败均返回独立原因，不把静态缩略图当作完整动画。

旧 `.local/conversation-settings.json` 首次迁入数据库并保留原文件；以后设置以数据库为准。损坏的旧文件不自动覆盖。
数据库损坏、版本过高、磁盘写入失败或其他进程占用时返回明确错误，不清空或自动重建归档。
数据库设计、事务边界和恢复方式见 [数据库说明](docs/database.md)。
本机测试结果和当前服务切换步骤见 [2026-10-28 验收记录](docs/acceptance-2026-10-28.md)。

## 角色卡与会话配置

工作台侧栏“会话”下方的“角色卡”由所有微信账号共用。支持新增、编辑、删除及 Character Card V2 JSON 文件导入导出；每次新增／导入保存生成不可修改的 `RC_<UUID>` 编码，名称可以重复。中文表单与高级 JSON 共用草稿，角色书和未知扩展字段在表单编辑后仍保留。导入只建立草稿，不覆盖已有卡；导出仅包含 V2 文档。PNG、V1 和 V3 暂不支持。使用中的角色卡不能删除，必须先在相关会话中取消或更换角色卡。

每条会话右侧可切换 AI 开关或打开设置。设置占用右侧聊天详情区域，选择器只显示角色名称和编码；记忆是可随时修改的长文本，初始为空。开关立即保存，角色选择和记忆通过“保存会话设置”提交；关闭 AI、更换或取消角色卡均保留记忆。离开未保存的编辑时可保存、放弃或关闭提示继续编辑。

这些功能仅管理本地配置，当前不调用模型、不自动回复、不生成记忆。会话配置按账号和会话隔离，微信退出或服务重启后仍保留；微信同步不会覆盖配置。角色卡修改对全部引用会话生效。多页面编辑使用修订号避免覆盖，冲突时保留草稿并提示重新加载。

数据保存在 `.local/bridge.sqlite3`，数据库版本升级至 3，和聊天归档一样不进入源码备份。升级前备份方式见 [数据库说明](docs/database.md)。实现参考 [Character Card V2 规范](https://github.com/malfoyslastname/character-card-spec-v2/blob/main/spec_v2.md)及 [SillyTavern 可编辑会话记忆](https://docs.sillytavern.app/extensions/summarize/)，本项目仅实现配置存储和编辑，不执行规范中的模型提示或开场白行为。

## JSON 接口

业务接口统一前缀 `/api/v1`，使用 `POST` 和 `Content-Type: application/json`。只有无入参健康检查保留 `GET /health`。
旧账号 GET 接口已移除；Python 账号识别模块及 CLI 保持原契约。

| 接口 | JSON 请求 | 行为 |
| --- | --- | --- |
| `/accounts/query` | `{}` | 已保存账号及当前在线/离线/未知状态 |
| `/accounts/get` | `{"account_id":"…"}` | 单个已保存账号 |
| `/account-scans` | `{}` | 显式重新识别，202；在途任务合并 |
| `/account-scans/query` | `{"scan_id":"…"}` | 识别任务状态 |
| `/conversations/query` | `{"account_id":"…","cursor":null,"page_size":50}` | 数据库会话分页 |
| `/conversation-refreshes` | `{"account_id":"…"}` | 显式后台刷新会话 |
| `/conversation-refreshes/query` | `{"account_id":"…","refresh_id":"…"}` | 刷新任务状态 |
| `/messages/query` | `{"account_id":"…","conversation_id":"…","cursor":null,"page_size":50}` | 数据库历史或变更分页，不访问微信 |
| `/message-reads` | 同上，可加 `"refresh":true` | 缺失历史补录；强制刷新用于复核已有窗口 |
| `/message-images/query` | `{"account_id":"…","conversation_id":"…","message_id":"…"}` | 图片／表情缓存；未命中 `needs_sync=true` |
| `/message-image-reads` | 同上 | 读取本机已有图片，或按需获取表情并缓存 |
| `/message-read-tasks/query` | `{"account_id":"…","task_id":"…"}` | 消息或图片任务结果 |
| `/conversation-settings/query` | `{"account_id":"…"}` | 七项过滤设置 |
| `/conversation-settings/update` | 账号和完整 `exclusions` 对象 | 保存账号级设置 |
| `/role-cards/query` | `{}` | 全局卡片摘要，含名称、编码、引用数和修订号 |
| `/role-cards/get` | `{"card_code":"RC_…"}` | 完整 V2 `card` 和本地元数据 |
| `/role-cards/create` | `{"card":{"spec":"chara_card_v2","spec_version":"2.0","data":{…}}}` | 创建角色卡，201；返回自动生成的编码 |
| `/role-cards/update` | `card_code`、`expected_revision`、完整 `card` | 保存完整角色卡；保留未知字段 |
| `/role-cards/delete` | `card_code`、`expected_revision` | 删除未被引用的卡片，返回 JSON 结果 |
| `/conversation-preferences/query` | `account_id`、`conversation_id` | 当前会话的开关、角色编码、记忆和修订号 |
| `/conversation-preferences/update` | 身份、`expected_revision`、待修改字段 | 保存会话配置，不修改省略的字段 |

会话列表额外返回 `ai_enabled` 和 `preferences_revision`，不携带记忆。配置未保存时返回 `ai_enabled=false`、`role_card_code=null`、`memory=""`、`revision=0`。更新可提交 `ai_enabled`、`role_card_code` 和 `memory` 中一项或多项；`role_card_code=null` 取消角色选择，`memory=""` 明确清空记忆，其余字段不能用 `null` 清空。未变化的配置保存不递增修订号。

新增错误包括 `409 revision_conflict`、`409 role_card_in_use` 和 `404 role_card_not_found`。输入不合法继续返回 `422 invalid_request`。角色卡通知使用全局 `resources=["role_cards"]`；配置通知包含账号、会话 ID 和 `conversation_preferences/conversations` 资源名，不包含角色正文或记忆。重连重新读取，正在编辑的草稿不会被通知覆盖。

每页 1–100 条，默认 50。分页游标不透明、绑定账号和会话、24 小时有效；过滤条件变化或游标过期返回 `409 cursor_expired`。
消息 `older_cursor` 用于历史，`newer_cursor` 用于 SQLite 中的变更流，后者可捕获低排序值迟到插入及已存在消息的内容更新；`has_more=true` 时继续排空该变更批次。
历史 `coverage` 区分 `not_synced/partial/complete`；`needs_sync` 表示当前请求的窗口是否需要补录，不等同于全会话历史是否完整。
响应包含 `last_synced_at`、`source_state`、`revision`。离线历史继续返回 200；需要访问源微信的任务会返回 `source_offline`。
账号还返回 `monitoring_started_at`、`sync_pending` 和 `sync_issues`，未知来源不能冒充完整同步。
任务状态为 `queued/running/completed/failed`；重启时未完成任务标为 `sync_interrupted`，持久化水位用于后续重试。
所有错误采用 `{code,message}` JSON，不回显原始 SQL、路径、密钥或异常详情。HTTP 响应均为 `no-store`。

WebSocket 地址为 `/api/v1/events`；连接后十秒内发送：

```json
{"type":"subscribe","account_ids":[]}
```

空数组订阅所有本机归档账号，也可提供指定账号 ID。服务返回 `ready`，提交后发送 `changed`（账号、资源类型、会话 ID、版本）或 `task` 通知。
通知队列有上限，溢出返回 `resync`；首次连接、重连或 `resync` 都重新调用数据库查询接口。通知不包含正文或头像。

## 验证与维护

```powershell
.\.venv-backend\Scripts\python.exe -B -X utf8 -m unittest discover -s tests_account_probe -v
.\.venv-backend\Scripts\python.exe -B -X utf8 -m unittest discover -s backend\tests -v
.\.venv-backend\Scripts\python.exe -B -m backend.export_openapi
Push-Location frontend
npm run generate:api
npm run check:api
npm test
npm run build
npm run test:e2e
Pop-Location
# 十万条合成消息，临时数据库，绝不访问微信
.\.venv-backend\Scripts\python.exe -B -X utf8 scripts\verify_archive_performance.py
# 显式真实验收：需先停止使用同一数据库的后端；验证后台读取及重启后的离线访问
.\.venv-backend\Scripts\python.exe -B -X utf8 scripts\verify_archive_live.py --run
# 真实表情验收：当前服务须在线；使用归档的隔离副本，不停止服务或微信
.\.venv-backend\Scripts\python.exe -B -X utf8 scripts\verify_stickers_live.py --run
```

真实验收会将选中会话的数据保存至本地归档，报告只保存计数、耗时、原因码、资源指标和被动焦点观察，位于 `.local/archive-acceptance/`。
用户活动附近的焦点事件不能据此归因给读取器；报告可为 `inconclusive`，不会虚报零干扰。浏览器截图只使用合成资料。
兼容的原会话/消息验收脚本使用隔离临时数据库；真实 API/UI 验收脚本需要匹配此版 JSON 契约。

主要实现：`backend/app/storage.py` 管理数据库，`runtime.py` 管理共享队列与十秒调度，`events.py` 管理通知，`wechat_messages/incremental.py` 管理来源基线与增量。
前端类型由 Pydantic/OpenAPI 生成，变更契约必须重新导出和运行 `check:api`。

静态表情与 GIF 的实现及验收见 [2026-10-29 表情验收记录](docs/acceptance-stickers-2026-10-29.md)。参考 [wx4auto 媒体说明](https://wx4auto.lightisle.cn/docs/multimodal/)、[WeFlow 表情解析和缓存流程](https://github.com/hicccc77/WeFlow/blob/main/electron/services/chatService.ts)、[微信 4.x 表情库字段](https://github.com/Rikkkum/wx_emoji_windows/blob/main/scripts/export/wxemoticon-js.mjs)；本项目独立实现受限下载与原有读取链路集成，没有引入上述客户端库或复制其实现。

## 独立账号识别模块

独立实验模块，面向 Windows x64 / Python 3.12。枚举当前 Windows 用户
在全部会话中运行的微信，分别读取 **wxid、微信号、微信昵称和本人头像**。
首版真实验证基线为微信 **4.1.15.56**；不宣称所有 4.x 版本兼容。

## 安装与运行

在项目根目录执行；独立环境不会修改旧服务的 `.venv`：

```powershell
.\.venv\Scripts\python.exe -m venv .venv-account-probe
.\.venv-account-probe\Scripts\python.exe -m pip install -r requirements-account-probe.txt

# 显式开始真实扫描；默认响应是 UTF-8 JSON
.\.venv-account-probe\Scripts\python.exe -B -X utf8 -m wechat_account_probe --run

# 可选的人类阅读格式
.\.venv-account-probe\Scripts\python.exe -B -X utf8 -m wechat_account_probe --run --format table

# 仅显式指定时另存 JSON；必须在 .local 内，且不会覆盖已有文件
.\.venv-account-probe\Scripts\python.exe -B -X utf8 -m wechat_account_probe --run --output .local/account-probe/result-new.json
```

独立 CLI 不带 `--run` 只显示帮助，不启动自动轮询、开机任务或 HTTP 服务。
上方框架通过独立适配器调用同一识别函数。

Python 后端接口返回可直接交给 JSON 响应器的字典，成功、部分识别和错误都
可以 `json.dumps()`。工作进程使用显式无窗口的独立解释器，不会重新执行调用方的脚本：

```python
from wechat_account_probe import discover_accounts

if __name__ == '__main__':
    result = discover_accounts()
    # 在已有后端中直接返回 result，由框架序列化为 JSON。
```

成功响应示意（全部为合成数据）：

```json
{
  "schema_version": 2,
  "scope": "current_windows_user_all_sessions",
  "accounts": [
    {
      "wxid": "wxid_synthetic_owner",
      "wechat_id": null,
      "nickname": "合成昵称",
      "sources": {
        "wxid": "current_process_configuration_and_mapped_directory",
        "nickname": "hmac_verified_current_account_contact_row",
        "avatar": "hmac_verified_current_account_head_image_row"
      },
      "status": "complete",
      "reason": "",
      "avatar": {
        "mime_type": "image/png",
        "data_base64": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jF1kAAAAASUVORK5CYII="
      },
      "avatar_status": "available",
      "avatar_reason": "",
      "instances": [{"pid": 100, "created": 134000000000000001, "session_id": 1, "version": "4.1.15.56"}]
    }
  ],
  "issues": [],
  "summary": {
    "confirmed_accounts": 1,
    "complete_accounts": 1,
    "avatar_available_accounts": 1,
    "unresolved_instances": 0,
    "coverage_complete": true
  },
  "exit_code": 0
}
```

实际响应另含被排除的未登录实例、全部客户端实例、扫描耗时和被动事件观察。
`created` 为精确 FILETIME，不是 Unix 时间。空字段为 `null`。
`complete` 表示账号归属、wxid 和昵称已确认；`partial` 表示账号资料未完整取得；
`unavailable` 表示身份未确认。`contact.alias` 为空时正常返回 `wechat_id: null`，
不尝试其他来源补值，不以 wxid 代填。未知实例不会被静默丢弃。
相同 wxid 跨会话合并，昵称相同不合并。
退出码 `0`：账号识别覆盖完整（也包括确认没有微信）；`2`：账号识别有缺失或不确定项；
`1`：模块级错误，响应含 `error` 常量原因。后端接入应同时检查 `exit_code`
和 `coverage_complete`，不要用进程数或列表长度代替账号数。

### v2 头像与状态

`avatar` 是 `{mime_type, data_base64}` 对象或 `null`。Base64 采用标准字符集、
无换行，可以直接封装在 JSON 中；解码后是本地缓存图片的原始字节。
支持 JPEG、PNG、GIF、WebP 文件签名，不缩放或重编码，不猜测未知格式。
这里检查图片签名及大小，不进行完整的图像解码或联网刷新。

`avatar_status` 为 `available`（已取得）、`missing`（没有可用缓存）或
`unavailable`（读取失败、超时、冲突或不支持的格式）。`avatar_reason` 是独立原因码。
原始图片上限 2 MiB；缺失或失败时返回 `avatar: null`，不影响已确认的账号身份。
`summary.avatar_available_accounts` 单独统计取得头像的账号数。
跨会话同一 wxid 的头像不一致时，仅将头像标记为冲突，不清除账号字段。

前端可用 `"data:" + avatar.mime_type + ";base64," + avatar.data_base64` 构造图片地址。
显式 `--format table` 仅显示头像状态、格式和原始字节数，不打印 Base64。
默认终端 JSON 和显式 `--output` 文件会包含头像；验收摘要不包含头像内容。

这是响应版本 2 的状态语义变化：版本 1 会将空微信号判为 `partial`，版本 2
允许 `wechat_id: null`，头像使用独立状态；使用方应按版本及各字段状态判断结果。

## 读取路径与操作边界

1. 按 SID、Windows 会话、进程角色和精确创建时间发现实例，不依赖可见窗口。
2. 从同一 PID 的当前账号配置读取内部 ID，与其映射的 `db_storage` 路径比对。
   不依据磁盘目录新旧、旧缓存或窗口昵称猜测账号。当前配置漂移会明确失败。
3. 在同一 PID 中只读定位 WCDB 密钥候选，仅验证已绑定账号的 `contact.db`
   和 `head_image.db`，在当前工作进程内复用候选，避免重复扫描。
   SQLCipher 每页 HMAC、WAL 头/帧校验、盐世代和提交边界都验证。
4. 双读主库和捕获的 WAL 前缀，最多重试三次；仅在内存中解密，用 SQLite
   精确查询本人一行的 `username / alias / nick_name`。结束前再次核对账号归属，
   扫描前后再次核对进程集合。微信号和昵称不以 wxid 代填。
5. 按当前 wxid 精确查询头像库的 `head_image.image_buffer`，只接受唯一记录，
   不依据昵称或其他账号的头像推断。图片同样经过加密页及 WAL 校验。

源文件以共享读写/删除的只读句柄打开，不锁住微信写入。不保存密钥、原始内存
或解密库，不读取聊天正文、不导出联系人。运行时不联网，不调用任何模型服务。
命令行和显式结果文件包含本人账号信息；代码、测试和验收摘要使用合成或脱敏资料。

工作进程设为低优先级，串行执行，读取块上限 1 MiB，跨进程内容读取最多
32 MiB/s；单实例 60 秒，全轮工作预算 180 秒，清理另有最多约 2 秒宽限。
头像阶段额外最多 15 秒，并为最终归属复核预留 3 秒；该预算仍包含在单实例
60 秒内。单个加密库/WAL 上限各 256 MiB。头像超限或失败只影响头像状态；
整体实例超时或身份变化仍会拒绝返回该实例的身份和头像。
不会主动提权、要求重新登录、暂停微信、注入、激活无障碍、改变系统设置，
也不触碰焦点、鼠标、剪贴板或会话界面。扫描会消耗少量 CPU/内存和读取 I/O；
限速与低优先级用于控制负载，不能承诺任何机器上资源影响绝对为零。

使用独立的 OUTOFCONTEXT Windows 事件观察器记录当前桌面微信的前台/焦点事件，
不向微信注入回调。用户正常操作允许发生；输入邻近统计仅供观察，不据此归因。
其他 Windows 会话的界面事件无法由此观察器覆盖。它也不是权限/身份校验的替代品。
进程权限不够时结果明确标记，模块不会自动以管理员身份重新启动。

## 验证

```powershell
# 合成测试，不连接微信，不调用网络
.\.venv-account-probe\Scripts\python.exe -B -X utf8 -m unittest discover -s tests_account_probe -v

# 显式真实验收：同进程两次扫描，再启动全新 CLI 进程扫描
.\.venv-account-probe\Scripts\python.exe -B -X utf8 scripts/verify_account_probe.py --run

# 当前双账号头像验收：要求恰好两个账号、且头像均可用
.\.venv-account-probe\Scripts\python.exe -B -X utf8 scripts/verify_account_probe.py --run --expected-accounts 2 --require-avatars
```

验收脚本只在 `.local/account-probe/avatar-acceptance-*.json` 写入脱敏报告：
字段组合指纹、实例元数据、头像大小/MIME/摘要/状态、事件计数和本轮源码哈希；
不保存完整账号字段、Base64 或头像文件。头像在内存中解码 Base64、核对签名，
三轮比较每个账号的图片摘要，校验 JSON 传输后的稳定性。
三轮字段一致且全部识别完整才通过账号验收；`--require-avatars` 额外要求头像均可用。
有界面事件时记录为观察不确定，
不把用户操作直接当成模块干扰。

本机只有一个已确认账号时，不宣称真实多账号/跨会话验收通过；这些场景由
合成测试覆盖并在报告中单独标记。旧 `.local` 身份记录、归档服务及历史数据
不参与本模块发现流程。

算法来源与许可证见 [第三方声明](third_party/wechatauto-replica/NOTICE.md)。
头像数据库字段和 Base64 输出思路参考现有
[extract_avatars.py](https://github.com/xiangruiai/xiangrui-toolkit/blob/main/skills/%E5%86%85%E5%AE%B9%E8%AE%BE%E8%AE%A1/group-daily/scripts/extract_avatars.py)。
本模块独立实现了加密快照、本人精确查询、独立错误状态和时间/大小限制，
没有引入该项目的 CLI、转写或模型依赖。
