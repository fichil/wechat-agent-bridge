# 本人账号发现与运行隔离（2026-10-28）

## 当前：8767真实后台只读

收尾保护状态：两轮验收完成后，监测到生产端另已更换为PID32180，随后曾出现 `unknown_send_requires_review` 并暂停。10:02（Asia/Shanghai）末次只读检查为paused=false、send_ready=true，历史unknown原因仍保留；本轮未核对其结果，也不据运行状态推断unknown已解除。本轮未执行生产启动/重启/暂停/恢复或发送；按保护规则已停止8767及其工作进程。8767目前不监听，待生产恢复工作稳定后再复用下方命令启动测试；已有两轮证据仍保留，其“0新增错误”只指各自验收窗口。末次状态记录为开发目录的 `readonly-final-safety.json`。

测试端已切换为 `--adapter windows --discovery-mode read-only`，独立数据目录 `.local/dev-account-discovery-windows`。生产8766只接受状态观察，本轮未向生产调用暂停、授权、配置、身份绑定或模型测试接口。开发期间监测到生产监听进程由10876变为36240；本轮没有执行该重启。正式真实验证以36240为基线，测试端切换及重启前后均保持该生产进程。

**真实结果为部分识别：微信号已取得，昵称未获取。** 微信号来源是 `current_process_verified_own_identity_record`，每次按窗口、PID、精确FILETIME创建时间和版本重新核验；仅读原 gate 数据库的该窗口 `console_identity` 一条记录，不读取其他生产记录、不创建或写入生产表。昵称只接受本人导航栏唯一头像节点公开且明确标注的字段，未从其他导航文字、联系人、聊天或旧name字段推断。原name等于微信号的旧错误不会变为昵称成功。

只读模式默认用于普通Windows账号发现，与原自动开卡模式分离，元数据适配器没有开卡、弹窗读取或UI动作方法。该模式不创建聊天授权，拒绝授权写入、恢复自动运行、手动资料核验及模型测试，不自动打开浏览器。页面隐藏手填/授权/自动回复控件，新增 `partial` 状态及字段级 `discovery_sources`。显式 `--discovery-mode profile` 保留原开卡候选，但不会被只读模式调用；生产原受限验收入口兼容。

环境为微信 **4.1.15.56**、Windows **10.0.26300**，只读适配器 revision **windows-own-metadata-readonly-1**。首次3次发现与测试端重启后的3次发现完全一致；两轮均观察到0个前台/焦点事件，桌面快照一致，生产读取推进、发送通道就绪、无新增错误，测试端0模型调用/0聊天授权。生产实例及本人核验密文跨测试重启保持不变。结果不能被描述为昵称识别或完整M1通过。

脱敏报告位于开发数据目录：

- `readonly-acceptance-initial-ed2312ff8f8841bb9c9756d99a6bc66b.json`
- `readonly-acceptance-restarted-63a48fd64f774543bfc04d8df4f56da2.json`

报告不含完整昵称、微信号、聊天正文、API密钥或登录Cookie。首次诊断曾停止，后续一次观察窗口短于生产5秒同步周期，误判读取未推进；验证器现以最多12秒有界等待检查真实读取推进，焦点观察不放宽。默认验证脚本禁用，只有显式 `--run` 才访问本机两个控制台；日志中的仅本机启动认证地址不要提交到Git。

```powershell
# 仅当8767未运行时启动；不停止或重启8766。
.\.venv\Scripts\python.exe -B -X utf8 -m wechat_agent_bridge console --adapter windows --discovery-mode read-only --port 8767 --data-dir .local/dev-account-discovery-windows --no-browser
.\.venv\Scripts\python.exe -B -X utf8 -m unittest tests.test_readonly_account -q
# 验证器使用 .local/startup/dev-readonly-8767.out.log 中的本轮启动地址。
.\.venv\Scripts\python.exe -B -X utf8 scripts/verify_readonly_discovery.py --run --phase initial
```

下一步仍限于公开且可证明属于本人的后台只读字段；若客户端不暴露昵称，就保留partial及具体原因。不得因字段缺失对生产窗口自动开卡、临时改字节、截图/OCR或调用外部模型。完整自动开卡验收仍需独立环境。

本轮新增31项只读字段/模式/文件只读/接口拒绝/前端隐藏/验证器保护测试。完整 **1291 项通过，20.484秒**；JavaScript语法和diff检查通过，保留了目录内并行的回复恢复及连接提示改动。无需新增依赖。

## 历史：早期稳定恢复与profile候选

- 稳定服务：`http://127.0.0.1:8766/`，来自 `.local/stable-runtime/20261028-090641` 的固定代码快照；共享原 `.local/live-acceptance` 加密数据和累计账本。快照已通过原有 1207 项离线测试。
- 原微信实例已退出，原服务没有存活；恢复时先备份 gate 数据库，三次确认旧进程不存在后归档旧临时字节恢复记录，未修改旧 unknown 或历史。当前微信 4.1.15.56、Windows 10.0.26300，原账号经双读核验后重新绑定原获准群。
- DeepSeek、all_new_text、main-window、no-test-budget 已恢复。当前实例的 mode=0 发送预检通过，读取成功，恢复后未用固定文本另做发送测试。报告为 `.local/live-acceptance/stable-restoration-20261028.json`；历史暂停事件保留，不清除审计。
- 开发端：8767，显式 Mock 和 `.local/dev-account-discovery`。禁止让开发服务共享真实验收数据库；不在稳定服务使用的微信窗口进行自动开卡验收。
- 新开发端的登录 Cookie 按端口命名，避免同一浏览器打开8767后覆盖旧稳定端的 Cookie。稳定快照未变更。

## 新账号发现流程

Windows 普通控制台启动选择真实发现适配器；`--adapter mock` 明确选择合成演示。原带 `--live-acceptance` 的稳定试运行仍使用原身份接入流程，避免自动发现动作干扰已运行会话。

`/api/discover` 不检查聊天 Grant，不需要预期微信号。新流程只枚举微信主窗口元数据，定位主窗口导航栏中的唯一 `mmui::XAvatarImage`，使用其公开 Invoke；不调用粗容器动作、不猜坐标。只读取本次动作打开、且归属于同一主窗口的新资料卡。已有资料卡不读取或关闭，避免把联系人冒充本人。当前实现不包含不能证明属于本人的“已打开资料卡”读取捷径。

昵称和明确标注的微信号独立解析，两次结果和当前进程创建时间/窗口必须一致；恢复窗口后才返回成功。既有手动核验也会尝试提取昵称，缺失时显示“昵称未获取”，不再用微信号冒充昵称。账号缓存只在本进程中保存 15 秒，不跨客户端实例复用。

Account 新增 `discovery_state` 和 `discovery_reason`，原字段及旧核验接口兼容。页面展示识别中、已识别、暂无法读取及原因。发现不会创建聊天授权、上传真实聊天、调用模型或启用发送兼容表。

## 停止和恢复边界

后台工作进程有统一期限；窗口恢复凭据在动作前交给父进程，工作进程超时/断连时父进程尝试恢复。不能确认恢复时拒绝成功；人工输入或锁屏会停止自动操作。只关闭本次打开并确认归属的弹窗，保留剪贴板和聊天草稿。

开发发现只读检查原 gate 数据库中的临时开关占用记录；被稳定服务使用的窗口返回 `account_discovery_client_busy`，不打开资料卡，也不更改占用记录。占用记录不可读时同样停止界面动作。

## 验证和下一步

新增合成测试覆盖独立字段解析、缺失/冲突、未授权发现、同名账号隔离、实例变化、缓存/重复刷新、断连/超时父进程恢复、联系人卡保护、恢复失败、人工输入/锁屏和稳定窗口占用；默认测试不会连接微信或付费 API。

最终完整 **1246 项通过，18.169 秒**；其中新增账号发现37项、端口Cookie隔离2项。JavaScript语法和 `git diff --check` 通过。测试还发现SQLite上下文管理器不会关闭连接，导致Windows临时文件占用；已改为显式关闭后通过，没有忽略清理错误。

```powershell
.\.venv\Scripts\python.exe -B -X utf8 -m unittest tests.test_account_discovery -q
.\.venv\Scripts\python.exe -B -X utf8 -m unittest discover -s tests -q
node --check wechat_agent_bridge/web/app.js
.\.venv\Scripts\python.exe -B -X utf8 -m wechat_agent_bridge console --adapter mock --port 8767 --data-dir .local/dev-account-discovery
```

**真实新流程尚未通过验收。** 当前版本的公开头像节点和 Invoke 能否提供可用入口仍需验证；未暴露时明确返回 `account_avatar_not_exposed`。无客户端补丁、内存读取、云端识别、OCR 或新依赖作为降级。后续应在不被稳定服务占用的独立登录测试窗口，核对自动开卡、昵称/微信号全文和原窗口恢复，再做项目重启复测。不能用本轮 CUA 辅助旧流程核验通过，宣称新的无人干预发现已通过。
