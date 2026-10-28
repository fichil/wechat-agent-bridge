# 控制台先等待、再打开本人资料卡

## 最新真实通过：发送账号双读及恢复保存

页面触发分支77494/PID41616已终态exit0；最终报告 `.local/live-acceptance/console-identity-result-6be6657de629498d943639fe21d720eb.json` SHA-256 `dcdc9ca4f8f08772b71c8a82bd8f3ad1290513ad79664bbe59626ebc94b56fb5`。

Windows10.0.26300、Python3.12.10/64bit、微信4.1.15.56，DLL `932dd6700f22e32faeb9b0cc9581a1f601e60ace151067680d3f6596496fab99`。目标PID4708/HWND263266/创建时间134375347512983186。实际WindowsAdapter源码f4d47174f43530be5496a8a689e58b3d43a7a795a548968fbc3e591115e6bd7e，WindowsUIA994b080797b685c4ab5a65633298a176865671f50163d0ce45aa9377595fd915，IdentityProbe8556c0c16ff1aa28138ea43d5d405cbead8dd91ddb0de1a750af2afe1a889ca5；完整源映射保存在报告。

- 浏览器页面按内部窗口4708:263266精确选择（不是按同名按钮顺序），实际启用许可的临时开关、开始资料卡等待。同轮computer-use只操作自己的头像，不打开聊天。
- 程序原生两次资料卡读取一致，准确发送微信号synthetic_owner，`identity_saved=true/restored=true`。独立受保护console_identity保存成功，不是仅视觉核对。
- 当前指纹未变，恢复byte0，UIA工作进程确已退出/exitcode-15，清理无错误，patch pending清空。
- 旧验收binding、unknown、联系人和禁传基线密文保持；quota文件哈希保持，API10/12、发送3/5，无新请求或发送。
- 全局暂停、无聊天scope，background_qualified=false。本验证不证明后台导航、聊天读取、私聊/群@自动回复、附件或完整M1。

测试集成服务在完成后主动关闭，网页可能未收到最后状态；暂停页头证据不是完成状态证明。应以原生报告及加密身份记录核对，不能因后续截图无卡否定此前成功，也不能从图像推断程序精确采样时序。

新增 `adopt_console_identity.py` 只负责后续验收epoch衔接，尚未真实执行。它验证当前完整指纹及byte0、旧实例退出、账本/source/pending完整性，独立标注用户开卡的console_identity_probe来源，不伪造原后台binder零事件条件。事务归档原binding并要求新联系人身份；不修改任何历史、unknown或额度。11项新增测试后完整1108项22.240秒通过，SQLite故障回滚通过；M1未完成。

## 首次真实等待的结果与准备方式修正

新准备分支完整离线 **1097项通过21.963秒**，git diff --check通过；日志offline-console-interactive-final-20261027.txt SHA78f34189983e34d2799412b034375958abb1bd4f784278e8ab432c557ea0c9ac。新增测试确保单独serve选项仍默认关闭，工作进程退出需终态handle/exitcode，不仅cleanup返回。新分支本轮未真实启动；当前无待观察的87501进程，禁止复用其旧waiting状态。

首次原生产品会话87501/PID16724已终态exit1。Windows10.0.26300、Python3.12.10/64bit，微信4.1.15.56，DLL932dd6700f22e32faeb9b0cc9581a1f601e60ace151067680d3f6596496fab99。最终报告 console-identity-result-96951104d21a4db5942224933ee85a6c.json SHA575a1cc0c5ec1a0f127386a36f6362b91236132b7bb6660d2d16334d73de3458：60秒超时、身份未保存；恢复成功、byte0、指纹保持、原密文及quota文件未变、无patch pending、全局暂停/无聊天范围，API10/12、发送3/5不变。CUA实际开过本人卡，但缺少与原生采样关联的精确时间戳，不能把超时解释成卡无接口或归因客户端。ready→result文件间隔60.246秒，自动续作的调度不适合触发短等待。

新增受限集成准备选项：

```powershell
.\.venv\Scripts\python.exe -B -X utf8 scripts/probe_console_identity.py --run --serve-console --allow-accessibility-patch --account <已核验发送微信号> --fingerprint-evidence <原指纹收据> --cua-evidence <本人卡收据>
```

先启动正常认证控制台并打开浏览器，此时不启用临时开关、不观察资料卡，最多300秒等页面开始。由页面按钮启用开关、启动原60秒观察，再打开本人卡。bootstrap只临时DPAPI保存，不打印或进ready报告；结束清理自身会话记录。旧自动开始路径的失败保留，不原样重跑。新分支尚未真实启动/浏览器验收，不把代码或测试当成功。

原报告cleanup_errors空没有单独记录UIA子进程终态。新修订明确检查原process对象is_alive和exitcode；不确定时失败并保留恢复边界。完成还校验paused/无聊天scope/指纹/byte0及保存状态，不能仅凭任务返回verified。仍沿原账本和所有unknown/禁传基线。

## 当前实现及边界

旧的CUA开卡与下一轮原生查询之间实测相隔约22.5秒，不能视为同步观察。新增产品层只读核验任务，让应用先进入等待，再打开本人资料卡，避免先开卡后切回控制台才开始读。

此流程仍要求显式Windows受限验收和临时无障碍开关许可。它只作用于已选择并绑定的实例和预期精确微信号。没有客户端内部函数调用、前台输入、截图、剪贴板、聊天读取、模型请求或发送。

1. 启动现有控制台：

   ```powershell
   .\.venv\Scripts\python.exe -X utf8 -m wechat_agent_bridge console --adapter windows --live-acceptance --allow-accessibility-patch
   ```

2. 刷新并选择目标微信窗口，填写自己的精确微信号，绑定所选窗口、启用已许可的临时开关。
3. 点击“开始等待本人资料卡”，再到该微信打开自己的头像资料卡，保持展开。应用最多等待60秒；只有“未打开本人资料卡”会继续等，身份冲突、断线、超时、锁屏或读取期间输入/焦点/剪贴板改变直接停止。
4. 成功需两次原生资料卡读取一致，每次都核对实例、微信号、身份来源、确定性与当前进程指纹。读取只是观察，不能提前更新底层账号缓存。
5. 先恢复临时开关，再重新核验指纹及恢复状态，才保存独立 `console_identity/<instance_id>`。恢复或落盘失败不宣称成功。取消结果丢弃；取消与提交串行，不把在途结果作为后续授权。
6. 刷新账号列表后重新选择已核验账号。读取/上传/回复权限不自动开启，整个过程保持暂停。

核验任务占有本应用桌面操作锁；期间禁止恢复自动运行及聊天读发。全局暂停、配置/授权变更和退出会取消任务；关闭时等待正在进行的有界调用及恢复。任务启动/终态审计加密保存，重启将未完成任务标为取消，不自动重开。旧验收 `binding/account`、epoch、unknown、禁传历史和额度记录不迁移或覆盖。

临时开关必须与当前实例的原始恢复记录一致；其他未恢复动作阻止开始。如果发现开关恢复记录被其他操作替换，不清除对方记录或写入未知状态。恢复不完整时保留pending以便恢复。内存身份缓存只在成功提交后提供给发现接口，重启不自动继承。

## 实现与验证

- `identity_probe.py`：有界等待、取消、先恢复后提交；无适配器/模型实现依赖。
- `WindowsAdapter`：两次只读观察、输入/桌面变化检查、开关归属与恢复检查、独立身份提交。
- `WindowsUIA.read_own_profile`：不发布账号缓存；原立即核验路径仍单独保留。
- `ConsoleService`：暂停与桌面互斥、任务审计、重启取消、关闭等待、聊天读取前身份核验。
- `POST /api/identity-probe`：`start/status/cancel`，沿用会话、Origin和CSRF保护。普通mock/未显式验收模式不能启动。
- 网页新增等待/取消按钮及状态，使用文本节点渲染。

完整 **1093项通过23.120秒**，新增25项；默认套件不调用微信或付费API。覆盖无卡等待、错误身份不重试、截止时间、读取后/恢复中取消、恢复失败、落盘失败、锁屏/进程变化、不同资料卡/输入/剪贴板、其他pending/开关所有权、临时身份不发布、过期发现缓存、任务恢复及接口鉴权。测试还发现新增身份检查遮蔽旧unknown隔离原因，现保留策略/unknown优先，旧任务仍禁止重发；失败日志保留，未以更改测试期望掩盖回归。

JavaScript `node --check` 和 `git diff --check` 通过。没有新增依赖或复制上游代码。

本机日志 `.local/live-acceptance/offline-console-identity-wait-verified-20261027.txt` SHA-256 `d9b8992a6a8dc510a6ad5dcd27b52fa4b9f563062bf4af930a09e6ebfa04e1c1`。本轮只有代码/离线合成与loopback HTTP测试，没有启动新真实核验或更改验收预算。

## 下一项真实验证

先检查是否已有本项目控制台/核验任务存活，沿用其句柄；本轮普通权限CIM进程查询被拒，尚未完成控制台进程清单，不能据旧记录重复开服务。确认原全局锁、各pending和字节状态后，用已经授权的发送账号synthetic_owner和现有原账本启动此产品流程，不开启任何聊天授权、上传或回复。

在真实控制台已进入等待后，再由操作者或合规computer-use操作打开本人卡。CUA轮仅使用skill允许的JS接口，不同轮混跑直接PowerShell UIA；不得以启动产品为理由绕过工具审查或隐瞒并行操作。先验证真实两次读卡、取消/恢复、独立存储，再决定如何用新原生证据衔接验收epoch；不能仅凭本功能的离线通过提升真实适配表或直接发004。

**尚未进行新流程的真实微信/网页运行验收。** 真实卡结构、当前账号重绑、私聊AI回复、群@、附件及完整M1仍有未完成项；既有群002/003和摘要成功不重放，私聊8项历史不重采/上传，unknown不重试。
