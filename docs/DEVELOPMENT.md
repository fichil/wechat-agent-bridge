# 本地 Codex 开发交接

> 2026-10-28 归档：旧服务已安全停止。本分支保留历史实现与阶段记录，不代表当前正在运行；真实账号、昵称及群名已替换为合成占位符。历史来源哈希对应本机原始快照，不能直接作为脱敏后代码的运行资格。重新启动或真实验收须重新核验并取得明确授权。

## 2026-10-28 8766 取消补答并恢复新消息（最新生产状态）

操作者明确要求清除本次补答草稿、核对并解除 unknown、恢复新消息且不补发历史，随后确认草稿已手动清除。生产进程 PID32180，绑定客户端 PID7008/HWND920180、微信4.1.15.56保持；运行独立快照 `.local/stable-runtime/20261028-reply-recovery-v2`。此快照此前1223项离线测试通过，本轮未修改运行代码或重启服务。

先通过原服务暂停并核对原群输入框为空、发送按钮禁用，原生 pending 均为空。当前窗口画面被 Codex 遮挡，未按画面坐标操作其他应用，未新建原生 UIA 调用。通过原服务认证接口同步当前群，执行 `/api/acknowledge-unknown`（reviewed=true，返回paused=true/retry_performed=false）；原补答 job 和全部 actions 内容保持不变。mode=0不发送消息预检通过，零焦点事件、worker退出、helper卸载及恢复检查全部通过；随后 `/api/resume` 并建立新基线。

最终paused=false、all_new_text、send_ready=true、pending=0、当前群send_block为空；原补答job仍为unknown，两个恢复事件去重墓碑保留，不重试、不伪装为已发送。错误列表中的unknown_send_requires_review均早于本次恢复，不代表当前仍封锁。报告 `.local/live-acceptance/unknown-review-resume-20261028.json` 记录基线水位189及核验结果，不包含正文或凭据。操作者选择暂不发送新测试消息、保持运行，因此本轮没有新增恢复后的真实回复验收；不扩大为私聊、附件或M1整体验收通过。

## 2026-10-28 8767真实后台只读验收（最新）

最终保护状态：两轮正式验收通过之后，生产又更换为PID32180并曾因unknown_send_requires_review暂停；10:02（Asia/Shanghai）末次只读状态paused=false/send_ready=true，历史原因保留，不据此推断unknown已核对。此变化不在已通过的两轮观察窗口内，本轮未对生产执行生命周期或写入操作。已按规则仅停止8767及其子进程；不自动恢复生产、不重试unknown。当前8767不监听，最后状态见开发目录readonly-final-safety.json。完整1291项离线测试20.484秒通过、JS语法/diff通过；新增只读回归31项。

按用户选择的同机后台只读方案，新增 `--discovery-mode read-only` 并将8767切换为Windows适配器、独立 `.local/dev-account-discovery-windows`，模型Mock、无Grant。普通Windows发现默认只读；原live-acceptance保持旧接入行为，显式profile候选仍分离保留。只读与客户端补丁/受限验收参数冲突时在打开数据库前拒绝；只读模式不自动打开浏览器。

独立元数据接口只提供窗口/进程戳和本人导航公开字段，不具有开卡/Invoke/窗口恢复方法。当前ID读取只允许mode=ro的单条console_identity，窗口、PID、精确FILETIME创建时间及版本均匹配，来源必须为原生本人卡核验；name缓存不作为实时昵称使用。API新增partial和discovery_sources，页面不再要求填ID或授权。应用层拒绝只读端的授权、聊天读取、恢复自动运行、手动核验和模型测试。

真实微信4.1.15.56/Windows10.0.26300得到部分结果：微信号由当前实例已核验记录自动取得，昵称未由公开字段提供，双字段未完成。首次3次刷新、仅重启8767后的3次复测一致，两轮0前台/焦点事件、桌面快照一致，生产读取推进/发送就绪/无新增错误，测试0模型调用/0聊天授权。报告为开发目录的 `readonly-acceptance-initial-ed2312ff8f8841bb9c9756d99a6bc66b.json` 与 `readonly-acceptance-restarted-63a48fd64f774543bfc04d8df4f56da2.json`。生产在正式验证前已由其他操作变为PID36240，本轮未重启生产；正式测试期间36240保持。

保留并行的回复恢复/连接提示改动，没有用旧文件覆盖它们。验证器默认禁用；曾因观察时段短于生产5秒同步周期误判，已改为最多12秒有界等待，焦点检查保留。具体源码、命令、来源及限制见 [ACCOUNT_DISCOVERY.md](ACCOUNT_DISCOVERY.md)。不要把partial或微信号缓存复用宣称为新昵称读取通过。

## 2026-10-28 稳定服务优先与账号自动发现开发

按用户要求，先恢复昨天成功的服务，再隔离开发。当前稳定运行代码固定于 `.local/stable-runtime/20261028-090641`，端口 8766，原数据与账本通过该快照的 `.local` junction 访问，不新建验收账本；原 1207 项测试通过。旧 PID4708 已退出，旧 patch pending 已在数据库备份及三次进程退出检查后归档；其他 pending 原为空，unknown 不改。当前原账号/原群重新核验绑定，DeepSeek/all_new_text/main-window/no-test-budget 开启。mode=0 不发送预检通过，后台读取成功，未另发固定测试消息；报告 `stable-restoration-20261028.json`。不要用开发代码重启此服务。

开发目录新增独立本人账号发现，Windows 普通启动默认真实发现、显式 Mock 用于演示；不要求聊天 Grant 或手填微信号。新界面显示识别状态及失败原因，旧手动路径补昵称提取。公开头像/新弹窗归属、双读、实例缓存、窗口恢复及父进程超时恢复独立实现；发现不改授权、不读聊天、不调模型。原验收模式保留原流程。稳定服务占用的窗口跳过开发自动开卡；占用记录仅读且 SQLite 连接必须显式关闭。

最终1246项离线测试通过18.169秒（新增37项账号发现、2项端口Cookie隔离），JS语法及diff检查通过。开发控制台已以Mock启动8767，独立DPAPI数据目录；新Cookie按实际端口命名，不覆盖原生产端的legacy Cookie。生产快照保持原版，不部署新发现代码。

新流程仅完成合成验证，真实头像公开入口、自动开卡和恢复尚待独立窗口验收。开发端固定使用 8767 与独立 Mock 数据 `.local/dev-account-discovery`；稳定服务持续运行期间不对其绑定窗口做新流程动作。具体命令和边界见 [ACCOUNT_DISCOVERY.md](ACCOUNT_DISCOVERY.md)。

## 2026-10-27 微信版小鲸鱼人设

新增 UTF-8 角色卡 `wechat_agent_bridge/personas/xiaojingyu_wechat.txt`，`providers.DEFAULT_CONFIG.system_prompt` 从该文件加载。保留已保存配置的优先级、独立摘要提示词、明确 @ 触发和单条文本/沉默协议；没有增加工具、依赖、发送或主动接话能力。私聊共用此全局人设，Mock 继续明确返回合成回复。

新增5项合成回归：默认角色卡进入请求且资料仍在 user 消息、自定义提示词优先、沉默/数组/长度校验、摘要不混入人设，以及认证配置接口更新、密钥/授权/unknown/额度保留和重开数据库后的持久化。完整 **1152项通过（17.988秒）**，日志 `.local/live-acceptance/offline-xiaojingyu-persona-20261027.txt`；`pip check` 无损坏依赖，`git diff --check` 通过。复现：`.\.venv\Scripts\python.exe -X utf8 -m unittest tests.test_persona -v`；全套沿用下文标准命令。

本轮实际配置已赋值并回读：旧8765端口已被其他项目占用，先用原数据目录和不启动调度的临时认证HTTP入口保存；期间检测到新控制台在 `127.0.0.1:8766` 启动，再通过该实例现有会话认证的 `POST /api/config` 仅更新 `system_prompt`，`GET /api/config` 与独立只读加密存储回读全文一致。提示词1368字符，SHA256 `4e8cc36b8486f4a075ae838111928e7b62a249cccfe3b4e49fe8a54ad8940473`。最终证据 `.local/live-acceptance/xiaojingyu-persona-running-console-850138d472cd462a89e930bbf96f98f7.json` 不含密钥、认证凭据或聊天正文。

保存后该运行实例全局paused，三项能力测试标记均false；DeepSeek模型、密钥、限制、其他记录及数据表保持一致，累计API **14/17**、发送 **3/5** 不变，本次零模型请求、零微信发送。没有重置历史资格、unknown或预算，也未启用新的客户端能力。临时配置服务器已关闭；8766实例保持暂停，现有无障碍开关仍由该实例持有，本次没有启用/恢复或变更它。下一步可在该控制台AI页核对提示词；需要真实风格评估时另行显式执行合成文本测试，再决定是否恢复获准试运行。M1仍未完成。

## 当前：真实账号核验通过，下一步衔接验收epoch

**77494已终态exit0**，不再有待等待服务；最终 console-identity-result-6be6657de629498d943639fe21d720eb.json SHA dcdc9ca4f8f08772b71c8a82bd8f3ad1290513ad79664bbe59626ebc94b56fb5。程序原生两次一致读到发送微信号synthetic_owner，identity_probe verified/restored=true/identity_saved=true；独立console_identity已保存。原PID4708/HWND263266/创建时间134375347512983186、4.1.15.56和DLL指纹保持，byte0、worker_exited=true/exitcode-15、cleanup_errors空，原密文/quota哈希保持、patch pending空、paused=true、chat scope=false。API10/12、send3/5未改。**验收binding/account仍旧epoch，未迁移；群002/003、原unknown与私聊8项禁传历史均不重放/重采。**

本轮从Chrome2标签1939019485/57443中依据可见“窗口4708:263266”选择，页面启用临时开关并启动等待；同轮sky观察/点击自己头像，不打开聊天。原生任务先于CUA结束已验证成功；后截图不显示卡不能推断未打开或根因，采样先后未精确关联。不再重跑账号发现/开卡/等待来“再验证”。浏览器API依专用browser路径，sky依computer-use技能；本轮没有直接PS UIA命令。源指纹/恢复/预算实际结果以原生报告为准，不宣称后台账号导航通过（background_qualified=false）。页面完成前测试服务主动退出，网页可能停留等待/Failed to fetch；不是原生核验失败。

新增默认disabled的 scripts/adopt_console_identity.py --adopt --account synthetic_owner --receipt .local/live-acceptance/console-identity-result-6be6657de629498d943639fe21d720eb.json，**尚未真实执行**。它不伪造旧background binder的零事件条件：明确console_identity_probe来源/后台资格false，只用已恢复的真实原生双读证据和DPAPI身份记录衔接账号epoch。校验所有源哈希、准确账号/实例/指纹、清理/worker终态、无聊天/发送/API和额度不变；下一独立原生轮还需当前NativeGate指纹/byte0、旧实例确已退出、独占RunLock、其他pending清空。事务内归档旧完整binding_epochs，写新binding及receipt SHA/fresh_target_identity=true，完成审计和清pending；旧未知/联系人/基线不改。异常或提交不确定保留pending/锁，不自动重试。

新增11项，完整 **1108项22.240秒通过**，日志offline-console-identity-adoption-20261027.txt。覆盖错误/不完整/视觉-only/过期源码/错误账号、存储观察变化、其他pending、归档冲突、实际SQLite触发器abort和写后checkpoint失败原子回滚，默认命令无原生访问。Python核心本轮未变，未重建helper/新增依赖。下一步原生只做该衔接/数据完整性审计，成功后重新核验当前私聊资料卡精确wxid；不能直接private mode0/004或继承旧GUI RuntimeId/历史授权。

页面证据.local/live-acceptance/console-identity-paused-header-20261027.png只证明页面暂停显示；真正身份完成由上述原生报告/受保护console_identity证明。M1仍未完成，实际私聊/群@/附件/长期稳定性不能由账号核验替代。下方“当前77494等待”已是历史。

当前**77494 / PID41616** 是新准备分支，state=awaiting_console_action；收据 console-identity-runtime-5b480804979a494090dc57278fb416a3.json SHA4b757c51b25cad128259ec3d17a2d690dcf6d141c131065ff3c667f7ed24a771。**下一轮尽快用Chrome浏览器ID2（已有mcp__cua_repl绑定m1Browser，不再重读整份文档）定位新ready.origin的标签页，选按钮文本包含窗口4708:263266，启用临时开关、开始等待，然后同轮用skill sky开WeChat本人头像。** 不要再在窗口不区分时猜顺序；UI已修复显示窗口标识。当前没有启用开关/启动60秒观察，保留同一77494句柄，不另开服务。

前准备分支88459已确认终态exit1，console_identity_start_timeout（尚未开始核验）；报告console-identity-result-7d03368964994810a9d262c343b87210.json SHA4f5f96d5c9ac94dc294b0fdb90b6c96f2f9eac143c1a8e5e3e91047ee999fb04，原密文/额度文件保持、patch pending空、UIA worker_exited=true/exitcode-15。提升权限CIM复查无18368或相关核验/旧子进程后才新开77494，不是观察超时重启。

本轮发现实际页面两个候选窗口按钮完全同名，不能安全选根窗。修复web/app.js的renderAccounts，单独显示“窗口：<instance_id>”，不冒充微信号。Node语法/diff检查通过，真实Chrome页面reload后DOM确认4708:594324与4708:263266分离，选择了精确263266；期间服务到300秒期限退出，页面Failed to fetch，未点击启用/核验。截图证据.local/live-acceptance/console-window-identifiers-20261027.png只是窗口标识/暂停/零聊天权限展示，**不是核验成功截图**。Python核心未改，沿用1097项。浏览器工具已通过cua.getBrowser/listBrowsers/listTabs绑定Chrome2，m1Browser和旧m1ConsoleTab(1939019482/58528)在cua_repl中；**旧tab服务已关闭，不复用旧URL**，新tab从新ready.origin查找。Chrome nameSession已设置，local-web-development文档已读；只通过DOM/可见标签选窗口，未读取JS隐藏state。M1未完成，无发送/API。

当前新准备分支已实际启动：exec会话 **88459**，PID **18368**，state=awaiting_console_action，收据 console-identity-runtime-965173004f474e1d96d8423f0fd765a7.json SHA b24a3219e3fe3549c6ee6cb663d335493a965244cf66fb4b1f4fbd521fed26d2。它尚未启用开关/开始60秒观察，最多300秒等页面操作。启动前提升权限CIM确认无旧控制台/核验进程或旧16724子进程，原指纹/原pending/原数据库检查通过。**下一轮沿用88459，优先浏览器接口接管已自动打开的本机控制台：选4708:263266窗口，启用临时开关，开始等待本人卡，再在同一CUA轮开WeChat本人头像。** 不另开控制台、不从旧87501 waiting推断，不打印启动token、不混用直接PS UIA。若当前分支已终态，先观察同一句柄结果，不原样重启。M1未完成，无聊天scope/发送/API。

最新准备分支验证完成：完整 **1097项通过21.963秒**，日志offline-console-interactive-final-20261027.txt SHA78f34189983e34d2799412b034375958abb1bd4f784278e8ab432c557ea0c9ac；diff检查通过。当前87501已退出，新--serve-console分支尚未启动，下一原生轮按下方恢复/进程清单预检后再准备暂停网页。此CUA轮只开本人卡/读旧进程输出/离线代码测试，没有新原生UIA调用、聊天、发送/API；没有将超时解释成兼容成功，M1未完成。

当前会话 **87501已终态exit1**，PID16724不再作为存活依据；**没有活跃产品等待可继续，不要按下方旧waiting状态操作**。本轮独立CUA已在原263266点击本人头像，真实卡显示微信号synthetic_owner，未打开任何会话；随后观察原87501句柄得到终态报告 console-identity-result-96951104d21a4db5942224933ee85a6c.json SHA575a1cc0c5ec1a0f127386a36f6362b91236132b7bb6660d2d16334d73de3458。结果identity_probe_timeout/restored=true/identity_saved=false，byte0、指纹未变、原密文及quota文件哈希保持、patch pending空、paused=true、chat_scope_bound=false、cleanup_errors空，API10/12和发送3/5未改。它只证明实际等待/超时/恢复路径，不证明资料卡可读或未暴露；旧报告未记录UIA子进程退出证据，不扩大cleanup调用返回为全部进程退出证明。

时间证据：ready文件06:31:13.209423Z、最终文件06:32:13.455303Z，间隔60.246秒；本轮检查时报告已经生成122.246秒。没有截图操作精确时间戳，不能补猜精确卡关闭/读取因果。**自动跨轮调度不能作为60秒核验触发点。** 不再原样重启自动开始的--run路径。

已新增 scripts/probe_console_identity.py --serve-console 分支：与--run/--allow-accessibility-patch合用，先建立暂停、未启用开关的正常认证loopback控制台，自动打开浏览器；最多300秒等页面开始核验，实际60秒仍由现有页面按钮启动，未延长身份观察期限。bootstrap URL只在DPAPI控制台存储临时保存，ready报告不含token，结束清理自身runtime记录。用服务锁取得同一任务线程，避免启动指针可见但thread尚未赋值的竞态；只观察该任务，不重开。完成还必须paused/无chat scope/指纹保持/byte0/restored+identity_saved，新增明确UIA子进程终态确认，不仅凭close返回宣称退出。

下一独立原生轮：先核对实例/旧16724子进程/RunLock/pending/字节，默认不自动清除任何未知状态；启动新的 **--run --serve-console** 产品准备分支，保留句柄和ready收据。下一CUA轮通过实际控制台网页“启用临时开关”“开始等待本人资料卡”再打开WeChat本人头像，页面触发观察可与开卡处于同一CUA轮；不得直接PS UIA与CUA混跑或绕过工具审查。若浏览器未打开，从DPAPI自身runtime记录恢复启动地址时不得打印token；优先用正常browser接口。不要重发002/003/旧unknown或请求用户再次证明synthetic_owner。M1未完成，本轮无聊天读取/发送/API。

当前真实产品等待已启动：exec会话 **87501**、PID **16724**，只读账号等待state=waiting。收据 console-identity-runtime-18eba3e0b5ab45fcb29502df0bf7e5f6.json SHA916770042bf39203810426726beca5e2b508ee157335f780c847ddbb0a26326d。**下一独立CUA轮立即用已批准本流程刷新/头像点击，不再运行任何直接PS UIA查询或另开等待器。** 这是现有ConsoleService/WindowsAdapter在已核验4708/263266内的产品集成任务；应用原生只读观察正在运行，透明保留同一服务句柄。没有聊天scope/grant/上传/发送/API，自动运行暂停；60秒应用截止后恢复开关，进程自动清理。下一原生轮只观察同一句柄87501/最终报告，不因为观察超时重启，也不在CUA轮混跑直接PS UIA。若已终态，不将旧waiting当存活证据；先读最终报告，禁止原样重开。

本轮两次提升权限CIM清单均确认无此前控制台/核验实例后才启动；普通权限拒绝清单的旧状态已解除。新默认disabled脚本 scripts/probe_console_identity.py 复用原gate/quota/windows-state文件、会话认证loopback服务器、原指纹/本人CUA收据、独占RunLock及原pending保护，启动产品核验并保存就绪/最终报告；不披露bootstrap token、不改变provider配置或授予聊天权限。最终逐行密文/额度文件哈希审计，恢复不完整保留锁/pending。新增2项，完整 **1095项22.726秒通过**，日志offline-console-identity-runner-20261027.txt。初次合成审计测试发现SQLite上下文不自动close，已显式closing并修正fixture资源释放；完整重跑通过。原验收binding未迁移，M1仍未完成。

2026-10-27 产品层等待已实现，完整 **1093项通过23.120秒**，JS语法及diff检查通过。新增 IdentityProbe 与受认证POST /api/identity-probe start/status/cancel，网页等待/取消/状态按钮；绑定实例和临时开关先就绪，应用只读等待本人卡，最多60秒，读取两次一致、无输入/焦点/剪贴板变化后先恢复、再提交console_identity，保持暂停/不赋予聊天权限。取消/超时/恢复失败/落盘失败不保存有效身份；锁屏、进程/开关归属改变及其他动作pending停止。核验期间阻止聊天读发和恢复运行，pause/配置变更/关闭取消，重启未完成审计取消不重放。

修复只读观察提前发布backend.accounts的漏洞：新增WindowsUIA.read_own_profile不更新账号缓存，只有恢复后提交的内存身份可替换discover候选，过期或重新开始核验即失效。服务读取前还验证当前缓存身份，但保留原策略/unknown隔离优先级；一次回归因错误优先级失败，已修复并完整重跑通过，旧失败日志保留。新增25项测试，覆盖核心时序/取消、真实SQLite隔离、服务并发/重启、窗口/输入变化、开关所有权及HTTP鉴权。无新依赖，无本轮真实微信/发送/API/预算变化。

下一步按[产品等待验证记录](M1_CONSOLE_IDENTITY_WAIT.md)进行实际控制台集成验收：**先核对控制台进程清单**（普通沙箱CIM查询被拒，本轮未确认是否已有实例，不盲开第二个），核对旧global lock/pending/字节，沿原数据目录和预算启动此产品的只读身份等待，再用已批准CUA流程开本人卡。不能同一轮混跑直接PowerShell UIA与CUA，不把产品运行当绕过技能/工具审批的理由。当前1093项通过只是离线，真实新流程、本人卡重绑及M1未完成。不要直接运行private004或复用历史消息/API案例。

2026-10-27 最新原生结构：新CUA收据1b37d2c4173140b7ba516956f578ae88后的查询1bae3bb589d74e1495e4f88ba4765c04（SHA dfcaf56b2574dc7bed22599b7046c698dcf78daa879f962bc9dfcc53e253038f）仍无ProfileUniquePop，顶层仅MainWindow；0.859秒、零事件、完整退出恢复。核对CUA收据created_at和原生报告mtime发现相隔22.517秒（报告耗时0.859秒，不是精确阶段时间戳），足以错过之前15秒空闲；**不再把跨轮查询称为瞬时同步观察，不能从缺失推断资料卡结构错误。** 无新绑定/发送/API。

同时发现并修复实际控制台持久化冲突：WindowsAdapter.probe_account原会向gate.sqlite3的binding/account写平面Account，覆盖验收脚本包装式旧绑定及epoch字段。现在只写console_identity/<instance_id>，不自动迁移/重绑acceptance binding；响应后再次核验NativeGate完整指纹、实例、精确微信号、identity_certain及原生身份来源。先存储成功才更新内存identity_source，避免落盘失败却宣称绑定。新增5项测试（实际SQLite原绑定保持、错身份/来源/实例/确定性拒绝、过程指纹改变、存储失败和worker错误），完整 **1068项通过20.589秒**，日志offline-console-identity-isolation-20261027.txt，git diff --check通过。初次聚焦测试误导入不存在SyntheticProtector，已改为测试本地合成实现；没有生产依赖变更。

下一步应实现/测试真实控制台的有界只读账号授权等待：在ConsoleService中先进入已绑定实例的等待状态，再由操作者或已批准CUA打开本人卡；应用读卡两次后保存console_identity并完整恢复开关，不启动聊天读取/上传/发送，也不覆盖验收binding。**这是尚未实现的产品功能，不能用未验证等待器或CUA视觉证据直接提升正式身份能力。** 原wait_sender_profile只等新增顶层窗口的假设未成立，已超时，不再请求操作者重复。CUA轮仅用skill JS APIs，不能同轮混跑直接PowerShell UIA；运行产品集成也需保持透明，不绕过工具审查。M1仍未完成。

最新独立CUA已在原263266真实头像一次点击并刷新，资料卡展开且微信号synthetic_owner视觉匹配，无遮罩。新development-cua-own-identity收据purpose=immediate_native_structure_query，卡留开。下一独立原生轮立即 --inspect-own-profile-structure，使用最新该收据及原指纹；不要先strict binder或再点头像。本轮无原生UIA/补丁/发送/API，无截图文件。1063项沿用，M1未完成。

2026-10-27 最新结构诊断：新增 scripts/own_profile_structure.py 和默认关闭 --inspect-own-profile-structure。仅在精确绑定主窗内用UIA原生ClassName条件查询 mmui::ProfileUniquePop（不是整树文本后过滤），最多4个匹配；同时对已核验PID可见顶层句柄读取类/RuntimeId/NativeHWND/offscreen，最多64窗口，前后核对集合与root。不读名字/值/微信号/聊天，不执行动作、不绑定账号。新增5项，完整 **1063项通过22.217秒**。真实一次1.047秒、零事件、worker退出/字节恢复、来源未变，profiles_under_main=[]，顶层只有 mmui::MainWindow263266。**这只能说明查询时没有该类，不能证明用户截图里的卡是嵌入节点或类名改变。** 报告 sender-profile-rebind-5335028a9f884a3e832f635ece48db17.json SHA d16651b8e80c281d9a7cf35cd82a02139fe4c54a8b70f645ebc309d5de137353。

衔接前轮未写入交接的结果：新CUA本人卡收据61e7bb9f89964534b9037cf504be9e53后，首次严格绑定在启用开关前因human_takeover失败（33cd34b79ac14c7a894108b88b3e527c）；一次最多15秒的纯输入时间戳就绪等待实际6.64秒达到3秒静止，后一次严格绑定因真实卡不存在失败（e55ef81bb6984a05a440362f6692b4f8）。没有重写unknown或追加发送/API。

随后实现 scripts/wait_sender_profile.py 默认关闭 --wait-and-bind：精确指纹/STOP/锁屏/字节0/证据文件校验，等待最多60秒新增同PID可见窗口及3秒静止，再仅调用一次原严格binder；等待阶段无UIA/补丁/文字/截图/激活。新增7项后1058项通过19.113秒。实际会话34677已经终态退出，profile_wait_timeout，没有binder启动（报告 sender-profile-readiness-de2afd0ec38b42b0810fa66a3339850d.json SHA ddfd641f3e483a078395ff089ae62d8c2e607dc260d8ac2c2ca688855f0e7025）。操作者回复已保持及本人卡截图，但旧等待报告未记录逐帧窗口变化，不能区分错过时间与非独立顶层；**不再让操作者重复同一窗口等待，不据此放宽身份或假定正确结构。** helper源码3713439c49b4c9af1e6115516b5f259a89dbd09d12cdc44501d318706d99b5c0。

最新操作者问“你要核对啥”并提供清晰本人卡，已向其确认微信号synthetic_owner视觉证据充分，问题是程序无法识别，**无需操作者反复证明身份/打开卡**。下一独立CUA按已批准本次流程刷新自行打开真实本人卡，新证据落盘后尽快下一原生轮运行 --inspect-own-profile-structure（不是严格binder先因全局输入误停），定位当前卡的结构。未取得实际类/归属证据前不猜类名改绑定，也不自动取消用户截图。仍不在同轮混跑CUA和原生UIA；旧历史禁传/unknown/额度保持。M1未完成，新绑定未保存，无发送/API。

2026-10-27 最新：修复本人卡读取静默截断 children()[:30] 的身份漏洞；超过30子节点现在 account_profile_tree_incomplete，不会漏掉第31个冲突微信号后错误绑定。新增3项：第31冲突不得隐藏、恰好30完整读到冲突、400总节点上限不得通过。完整 **1051项通过18.934秒**，日志 offline-profile-completeness-20261027.txt，git diff --check通过，无依赖变化。修订后的WindowsUIA尚未真实读卡验证。

操作者确认“现在空闲，可以继续”后，独立CUA获取原263266新画面并对真实左上头像一次点击，500ms后刷新，当前真实本人资料卡已展开，带微信号标签的值为synthetic_owner，无截图选区遮罩。没有文本输入、草稿读取或发送/API，没有原生UIA/补丁。本次卡保持展开；下一独立原生轮立即 --bind-current-profile，使用本轮新development-cua-own-identity收据（.local/live-acceptance按最新创建时间选择并核验内容）及原指纹证据；不再重复头像命中研究/CUA点击，仍需实际两次精确读卡才写绑定。M1未完成。

2026-10-27 本人卡CUA续作：按本流程必要截图刷新授权重新取得唯一原窗口263266，激活后新画面确认截图遮罩已结束、真实左上头像可见。对该新截图发出一次头像点击请求前，SDK拦截 user input was detected in this window; call get_window_state before continuing。未假定点击完成，立即刷新，实际画面已被Codex遮挡；人工接管后停止，未重新激活抢回。未取得本人卡，不生成新身份凭证，不再运行原生重绑。没有native UIA/补丁、发送、API或草稿操作，没有保存截图。当前阻塞是操作时人工输入，不是截图遮罩；下一CUA尝试需新空闲状态及新截图，不能复用坐标/点击或声称卡已打开。1048项回归沿用，M1未完成。

2026-10-27 独立原生本人卡重绑实际失败：--bind-current-profile 返回 account_identity_uncertain_open_own_profile，报告 sender-profile-rebind-645148d1b3584292be13d68c67bbf219.json，SHA4e6d2970ec23873ff1a059ec9dc199753caf8eab8eee0f3284a555c6029d93bf，1.203秒，cleanup_errors空、worker退出、字节恢复、来源未变。未保存绑定，没有聊天/草稿读取、发送/API。随后只枚举已核验PID4708的可见顶层句柄和原生类名，结果仅主窗263266/Qt51514QWindowIcon，未读任何窗口标题或正文；没有ProfileUniquePop。不能把前轮截图遮罩内的卡当真实当前弹窗，也不据此修改定位类名来强行绑定。

下一独立CUA轮：沿用操作者已经批准的本次本人卡流程必要刷新（不用重新批准），先取得当前真实SDK窗口画面，判断截图选区是否已结束；仅真实当前头像可见且无阻挡时点击一次并刷新，视觉核对微信号后卡留开。不要擅自取消可能属于操作者的截图任务，不用旧截图坐标。若仍有遮罩，停止点击并明确需要完成截图；没有新的权威状态前不再重复原生绑定。M1未完成，本轮无CUA、代码或依赖变化，沿用1048项测试。

2026-10-27 补充授权已收到：操作者明确允许本次打开本人卡流程必要截图刷新，可能含侧栏/当前聊天，不保存或调用API。新授权后原窗口263266激活并观察，画面显示带微信号标签的synthetic_owner本人卡，但存在截图选区/编辑遮罩；不点击/关闭非本任务截图，不读无关正文，不把遮罩里的静态画面当当前原生资料卡已打开。没有头像点击或新绑定。下一独立原生轮可尝试原 --bind-current-profile（原精确CUA收据/指纹参数保持），必须实际读取本人卡两次才能绑定；不可仅凭本次截图或绕过UIA身份检查。若遮罩导致卡不可读则保留失败，不自动取消用户截图。当前CUA流程授权只限本次，不扩展到其他会话、上传或生产能力。下方“补充授权未回复”已过时。M1未完成，沿用1048项，无原生UIA/补丁/发送/API。

2026-10-27 CUA本人卡续作：唯一原窗口263266重新获取。整窗截图自动审批拒绝（可能含未授权聊天）；无截图整窗文本树同样拒绝（本地过滤之前已读取整窗）。SDK无已知范围/区域参数，不绕过。操作者随后批准一次截图；实际返回被Codex遮挡的画面，没有取得微信头像，不使用该截图点击、不保存。激活+刷新恢复调用再次被拒绝，原因是一次截图已用完。已发出本流程必要刷新范围的补充授权请求，尚未收到回复；不要将旧一次授权视为无限刷新。未点击头像、发送、API或原生UIA/补丁，本人卡未打开；下一步依操作者补充答复继续，M1未完成。既有1048项测试／账本保持，不重复离线套件。

2026-10-27 操作者明确“我打开微信了”后，原生确认 visible=true／iconic=false，单次命中取得 role9 粗对象。新增最多6层、只沿同一头像定位点的命中链；用 IUnknown 身份检测循环，保留对象引用避免身份地址被回收复用；每层先验证 HWND／PID／根窗再读数字角色和位置，值／几何改变停止，不调用名称或动作。新增4项，完整 **1048项通过18.430秒**，自建隐藏窗口 IUnknown 稳定性／数值封送／退出正控通过。真实链3次命中 role9→role10→self，均相同粗几何，precise_geometry=false，0.812秒、零事件、worker退出、字节恢复。公开路径当前不能提供头像动作，不继续原样重跑／粗Invoke。

下一独立CUA轮从实际SDK原窗口打开本人头像卡（仅指定发送账号），再下一原生轮执行 --bind-current-profile 精确账号核验；这只是开发辅助，不提升生产头像／后台导航能力。旧binding仍未变，联系人和8项禁传历史不可重采／上传。原生最终审计byte0、无锁/pending，原binding／联系人／基线／旧导航unknown／quota未改。证据见[账号重绑](M1_SENDER_REBIND.md)。本轮无CUA、发送或API，M1未完成。

2026-10-27 激活后的原生核验：唯一 --inspect-window-oleacc-hit 仍发现 visible=true／iconic=true，0.781秒、零事件，跳过全部坐标命中／角色／位置。worker退出、开关恢复、来源未变，未改账号或发送/API。报告 sender-profile-rebind-2b6c225e994a4de5a5f9668772237aa1.json，SHA e6407c98da8e6401cd401a8fe9c7492a280e4c69d00325bb2a56827244e98603。**已证明 activate_window 返回不等于当前恢复正常；不要继续原样激活→原生跳过循环。** 下一独立CUA轮先观察同一实际Window的状态／可用恢复控件，仅对明确窗口恢复动作操作，不读无关会话、不重复旧拒绝搜索或完整未核验主窗捕获。若接口不能提供可用恢复证据，记录实际限制，不能盲按快捷键或猜坐标。本轮无代码变化，沿用1044项回归，M1未完成。

2026-10-27 下一独立 CUA 轮已完成：list_windows 返回唯一原微信263266，get_window 后 activate_window 一次，再 list_windows/get_window 取得同一实际 SDK Window。没有截图或 UI 文本、会话导航、输入、发送、API、原生 UIA 或补丁。仅证明激活请求已返回及窗口仍存在，**normal／非最小化待原生验证**。下一独立原生轮直接运行 --inspect-window-oleacc-hit 原参数；若已正常才解释命中结果，不原样重复 CUA。沿用1044项测试，M1未完成。

2026-10-27 最新 OLEACC 命中切片：新增 scripts/window_oleacc_hit.py 和 --inspect-window-oleacc-hit。仅固定头像定位点，一次 accHitTest，区分空／自身／子 ID／对象；对象在读取数字角色和位置前核验 HWND／PID／GA_ROOT，前后复核数值和几何，无名称／值／图像／动作。新增 11 项测试，完整 **1044 项通过，18.528 秒**。自建隐藏 STATIC 原生对照最初 AttributeError，实际 comtypes 运行时需 accRole[child]，已修正并补测试；新对照角色41、位置及自身命中封送通过，隐藏／前台不变、窗口销毁和子进程退出。不能把隐藏窗口封送对照算真实头像命中。

唯一真实新入口检查 0.735 秒、零事件、退出／开关恢复，但 visible=true、iconic=true，故跳过几何和全部命中调用。当前不是 MSAA 命中失败，也没有取得精确头像；不能重复最小化预检来积累证据。最终审计原基线／binding／联系人／导航 unknown／quota 不变、byte0、无锁及 pending。下一独立 **computer-use 轮**激活实际 SDK 返回的原窗口263266，然后再独立原生轮 --inspect-window-oleacc-hit；禁止同一轮混用原生 UIA 与 CUA。准确新账号绑定和 M1 仍未完成。证据／复现见[账号重绑](M1_SENDER_REBIND.md)。

2026-10-27 最新：新增 scripts/window_oleacc.py、scripts/probe_window_oleacc.py 和默认关闭 --inspect-window-oleacc。完整 **1033 项通过，13.408 秒**；自建隐藏 STATIC 窗口的原生 OBJID_CLIENT／WindowFromAccessibleObject 正控通过，隐藏状态、前台未变、窗口销毁和子进程退出均通过。随后唯一一次真实查询在 PID 4708／HWND 263266 返回 S_OK，接口 HWND 同精确主窗，scope_verified=true；0.906 秒、零事件、worker 退出和临时字节恢复。没有 accHitTest、接口内容／名称／值／角色／位置读取或任何动作；没有新账号绑定、草稿／历史、发送或 API。最终原生审计 byte=0、无锁／pending，旧 binding／联系人／禁传基线／导航 unknown 密文和 quota 文件保持。证据与命令见[账号重绑](M1_SENDER_REBIND.md)。

上一回归会话 80962 的句柄已不存在，读取其完整日志确认 1033 项 OK；没有重启或重复运行套件。下一步以已证明有归属的标准 OLEACC 接口实现只读 accHitTest 数字角色／位置检查，先合成测试并正确区分 VARIANT 自身、子 ID、对象及空结果；任何返回对象须先核验 HWND／PID／根窗归属。仅正常窗口才使用当前 DPI 几何，不读 accName／accValue、不调用默认动作、不把粗容器或位置当身份。当前存在性探针不具备上述命中能力，不要原样重复存在性查询。M1 未完成。

## 当前交付

2026-10-27 原生 MSAA 接口边界已实测：新增默认关闭 --inspect-own-avatar-native-msaa，仅按两个固定粗容器的类／数字角色／当前 RuntimeId 和主窗进程归属查询 Legacy.GetIAccessible，不读 accName／accValue／角色／位置、不调用命中或动作。首次运行时窗口已最小化，旧实现跳过，0 次接口查询，不能算接口能力结果；改为不依赖几何的存在性检查后，真实 1.016 秒、正常窗口、零事件、退出恢复通过，MMUIRenderSubWindowHW 与 MainTabBar 均 Legacy=true、GetIAccessible 返回 NULL。**该链无法继续 accHitTest，不等于所有 OLEACC 入口均不可用。** 没有新账号绑定或任何默认动作调用。

完整 **1026 项通过，14.749 秒，无跳过**。8 项新测试覆盖声明与实际接口不同、NULL／可用指针只记录存在性、错误类／角色／PID／RuntimeId 拒绝、回收对象变化、异常仅保留数字 HRESULT、固定容器不依赖几何及多对象拒绝查询。随后只删除未使用的 RawView leaf 回调，未重复微信查询；原始树实现回到已验证版本。最终审计窗口当前又已最小化、指纹未变、字节 0、无锁、七类 pending 空，原 binding／联系人／禁传基线／unknown／额度文件不变；本轮无 CUA、聊天／草稿读取、发送或 API。详细报告与源码见[账号重绑](M1_SENDER_REBIND.md)。M1 未完成。

下一步验证**绑定窗口自身的标准 OLEACC AccessibleObjectFromWindow(OBJID_CLIENT)**，先核对公开接口和合成测试；仅限当前精确主窗及已核验渲染子窗，返回对象需验证 HWND／PID／根窗口归属，才考虑数字角色／位置的只读 accHitTest。窗口最小化时只检查接口存在，不使用屏幕坐标；不读 accName／accValue、不执行 accDoDefaultAction／Invoke，不尝试其他账号、窗口或 client 内部 helper。不要原样重跑已完成的 GetIAccessible NULL 实验或窗口激活。该直接 OLEACC 入口目前未实现／未实测，不声称支持。

2026-10-27 正常窗口及原始树已实测：CUA 激活后原生确认 visible=true、iconic=false、geometry_inspection_usable=true；标准类／Button／Image 检查 51 节点、零事件，仍无左上精确候选。新增只读 --inspect-own-avatar-hit-test，RawViewWalker 仅沿固定头像区域定位点的覆盖分支读取结构，64 节点／8 层上限，不进入不覆盖该点的聊天子树，不读名称／正文／图像、不生成输入。真实 1.172 秒检查 14 节点，只有 MMUIRenderSubWindowHW 和 MainTabBar 两个粗粒度叶节点，均 precise_geometry=false，退出／恢复完整、零事件。**不得调用这些粗容器的 Invoke／默认动作来代替头像。** 位置和 RuntimeId 不能充当账号身份，新绑定仍未写入。

完整 **1018 项通过，14.752 秒，无跳过**。新增 9 项测试覆盖离点聊天子树不进入、多路径不选第一个、粗容器／深度上限不提升精确性、外进程跳过、循环／节点上限及对象变化。测试发现队列入队后 RuntimeId 更换未被检查，已将预期 ID 随队列固定并拒绝更换，修复后全套通过。没有新增依赖或客户端磁盘改动。

最终原生审计窗口仍正常、指纹同原 PID 4708／HWND 263266，字节 0、无锁、七类 pending 均空；原 binding、联系人、8 条禁传历史、旧 unknown／导航密文和额度文件未改。**下一独立原生研究只检查两个已验证粗容器的公开 LegacyIAccessible／IAccessible accHitTest 数字角色及位置**，先核对标准接口和自建／合成测试；不读 accName／accValue、不调用 accDoDefaultAction／Invoke，不加头像内部 helper，不重复窗口激活或已消费发送。当前 Legacy=true 只是 pattern 声明，GetIAccessible 和 MSAA 命中尚未实测；如果仍只返回粗容器，就明确记录接口限制。证据及当前复现见[账号重绑](M1_SENDER_REBIND.md)。M1 未完成；本轮没有 CUA、发送、API 或历史读取。

2026-10-27 独立 CUA 已完成一次对原 SDK 返回微信窗口 263266 的 activate_window 调用；调用后再次 list_windows／get_window 仍取得同一应用和句柄。没有截图、UI 文本读取、会话点击、草稿、发送、API、原生 UIA 或补丁。**尚未证明 normal／非最小化状态。** 原计划的 include_screenshot=false 且 include_text=false 刷新被 SDK 运行时明确拒绝（必须至少请求一种），随后仅用窗口枚举和重新绑定核对句柄，未改用整窗捕获。收据 development-cua-selected-window-activation-947dbe1be52642daa2358be144848001.json，SHA-256 b9f496ea37766628563b6789827e0a4d42d4f203582932485696f44f2c5f1a60。

下一独立原生轮先运行已准备的最新 --inspect-own-navigation；它会原生核对 visible／iconic 并在最小化时跳过几何。只有 window_state={visible:true,iconic:false} 且 geometry_inspection_usable=true 时才解释头像候选。不能把 activate_window 返回当作窗口恢复、账号或读发验收通过。当前没有活跃 CUA 工作需等待，没有新增客户端／模型操作；本轮仅收据和文档变化，沿用 1009 项离线结果，M1 未完成。下方“刷新可用 false／false”已被实际 SDK 行为纠正。

2026-10-27 头像元数据续查：新增 own_avatar_metadata.py，最多 64 个已知类／Button／Image 节点、6 层父级，只保留主窗左上几何候选，不读 Name／Text／Value／图像，不调用动作。只查 XAvatarImage 的真实结果为 0；加入 MainView 后两个节点都未取得左上候选。最后角色检索得到 51 个节点（MainView 2、其他 Button／Image 49），零事件／完整恢复，但当时窗口边界呈最小化形态，**空候选不能证明正常窗口没有头像接口**。最终原生 WindowPort.state() 确认当前 visible=true、iconic=true，PID 4708／HWND 263266 指纹仍匹配。

已修复只读元数据被其他应用输入／非目标前台事件误停：该模式只能输出结构，永远不能绑定账号或调用动作；原始事件保留，不证明归属或后台资格。目标事件、缺少 target_process 标记、锁屏／剪贴板变化仍停止。账号重绑与发送的严格保护未放宽。另新增最小化／隐藏窗口跳过几何匹配，geometry_inspection_usable=false，状态变化拒绝结果；**最后的窗口状态守卫尚未真实复测，下一次恢复窗口后验证。** 完整 **1009 项通过，13.972 秒，无跳过**，源码及证据见[账号重绑](M1_SENDER_REBIND.md)。

最终只读审计确认原 binding、联系人、8 条禁传基线、旧 unknown／导航密文和额度文件均未改，字节 0、无锁、七类 pending 均空，无新发送／API／历史读取。新账号绑定仍未通过，M1 未完成。**下一独立 CUA 轮仅重新获取 SDK 实际返回的微信窗口并 activate_window 恢复；刷新可用 include_screenshot=false、include_text=false，不需要捕获聊天区或点击会话。** 随后独立原生轮重跑最新的 --inspect-own-navigation 检查正常窗口候选。不要直接重跑绑定、旧 MainView 唯一性实验、Select、004 或记忆上传；未调用任何头像候选 Invoke，helper 范围仍只限发送按钮。

2026-10-27 原生续验未写新绑定：一次本人卡读取因 account_identity_uncertain_open_own_profile 停止，worker 退出／字节恢复、零事件；操作者补发本人卡截图后的一次检查在读取前因输入时钟变化停止。两次均未提交绑定，没有聊天／草稿／发送／API。新增默认关闭 --inspect-own-navigation，只在固定 MainTabBar 内检查类名、RuntimeId、pattern 可用性，最多两层 64 节点，无 Name／Text／Value／图像或动作；真实读到 18 个节点、零事件、恢复完整，但没有本人头像身份。再增加 MainView 直属子控件检查后，真实因 MainView 不唯一拒绝，不声称该完整新模式已通过。原生表中有 Invoke 不等于动作可用，未调用任何候选 Invoke。

当前完整 **993 项通过，13.060 秒，无跳过**；新增元数据范围／循环／节点上限／深度／对象变化、元数据模式不能绑定账号测试。最终原生审计证明原 binding、联系人、8 项禁传历史及所有旧导航密文、额度文件未改，当前字节 0、无运行锁、七类 pending 均空。当前窗口指纹仍为 PID 4708／HWND 263266；**新账号程序化绑定未通过**，M1 未完成。详细报告及源码指纹见[账号重绑](M1_SENDER_REBIND.md)。

下一步原生只读核验确切 DLL 中存在的 XAvatarImage 类在当前主窗内的控件／容器和几何所属关系，严格限类、节点数及元数据；MainView 多实例不能凭第一个项放行。当前没有头像候选的实际调用证据，不重跑无变化的卡读取或同一导航实验，不要求再次批准已有测试范围。取得唯一可验证语义目标后，才准备开发期公开动作实验；临时 helper 仍只限发送按钮，不能用于头像内部方法。此轮没有 CUA，未混跑。

2026-10-27 发送者续验：独立 CUA 已从新窗口自己的头像打开本人资料卡，视觉核对精确微信号 synthetic_owner。卡留开，收据 development-cua-own-identity-ed20545d669a48dbbc690aacd3d49061.json，SHA-256 5c9728cd56a2337a5fb1b0e6452f0952aa0df35aad1c7b1e5fb53a91393bbccb。新增默认关闭的 rebind_sender_profile.py：只读本人卡，原实例退出／当前指纹／人工接管守卫，完整恢复后事务归档旧 binding 并写新账号，不读私聊草稿／历史、不改基线／联系人／额度。**尚未执行真实重新绑定，不能声称新账号已获得程序化核验。** 16 项新测试后完整 **982 项通过，12.876 秒，无跳过**；实际 SQLite abort、错误本人卡、PID／窗口／来源错配、worker 只读本人卡、恢复失败／瞬时焦点均覆盖。源码、收据及下一独立原生轮的完整命令见[账号重绑](M1_SENDER_REBIND.md)。本轮 CUA 与原生没有混跑；未发送、调用 API、启用字节或 helper，生产表仍空，M1 未完成。以下“下一 CUA 打开自己资料”已由本轮完成。

2026-10-27 旧实例退出恢复已实际完成：原 PID **17416** 已退出、原 HWND 67678 不存在。新增默认关闭的 recover_exited_navigation.py，仅读旧实例的进程创建时间／等待状态；缺失 PID 必须同时满足 OpenProcess 错误 87 和成功、完整的 PID 枚举中不存在，权限错误或仍存活均不放行。真实检查两次通过后，先持久化独立退出观察，再释放匹配原 token 的导航锁，事务内只清原 pending 并保存退休记录。**原 Select unknown 永久保留，不算选择成功、不重试。** 中断／数据库提交失败可从已落盘观察继续；新进程不继承身份或控件资格。

实际恢复报告 exited-navigation-7386317c70e449c2868b29753d5f00de.json；内容盲审计证明 37→39 条，仅原 pending 改变、增加两条退出记录，原 unknown／绑定／全部私聊基线密文及 quota.sqlite3 完全未改，锁已不存在。没有读取历史、修改客户端、启用开关、发送或 API。18 项新测试后完整 **966 项通过，11.435 秒，无跳过**；自建无窗口子进程真实验证存活时拒绝、自然退出后同一创建身份变为 signaled。证据与复现见[私聊恢复](M1_PRIVATE_SELECTION.md)。

当前新窗口原生指纹已核实：PID **4708**、HWND **263266**、创建身份 **134375347512983186**；微信仍为 **4.1.15.56**，DLL SHA-256 **932dd6700f22e32faeb9b0cc9581a1f601e60ace151067680d3f6596496fab99**，模块基址 **140729299959808**、大小 204795904，原无障碍字节 0，Windows 10.0.26300／Python 3.12.10。生产表仍空、未写新账号绑定。**下一独立 CUA 轮打开当前窗口自己的资料卡核验发送账号，再独立原生轮建立新账号绑定；随后重新核验精确私聊及新控件。** 当前 peer 卡的视觉一致不等于发送者核验，不能用旧基线授权新实例、重采旧 8 条历史或直接发 004。API／发送账本未动，沿用原累计。以下“原锁仍在”和等待界面恢复为历史状态。

2026-10-27 操作者回复“已打开准确私聊”后恢复推进。独立 computer-use 轮重新枚举得到微信 HWND **263266**（旧绑定为 67678），激活后实际先显示获准测试群；随后从指定 synthetic_owner 行打开私聊及对方资料卡，视觉核对带“微信号”标签的完整 ID 与获准私聊精确一致。资料卡留开。仅开发导航，没有文本输入、草稿清理、发送、Provider 调用或项目原生 UIA／开关／helper；没有保存截图或导出聊天正文，原账本／unknown／锁未动。此结果不证明后台能力或当前发送者身份。收据 development-cua-private-restored-4a1af03a17bd432caaf3e836e4d7eae8.json，SHA-256 075f008368725d1b31a200e3219c73d1077561bb9fb731c9612acc7f59b41d21。

**下一独立原生轮先只读核验当前进程／窗口绑定。** 窗口句柄变化尚未查明是重建窗口还是新进程，不能直接套用旧 --inspect-after-attempt、RuntimeId 或发送资格，不能清原锁／unknown、重采 8 条私聊历史、发 004。若原进程仍存活，按原身份恢复；若确已退出，先建立可审计的旧实例终止恢复和当前账号／精确联系人重新绑定路径，保留原 unknown、额度及独立禁传历史。自动化 m1-18 已删除且未重建；当前目标由操作者新回复恢复为 active。本轮仅文档和收据变化，未重跑无变化的 948 项套件。以下 blocked／等待回复为此前历史状态。

2026-10-27 定时续作收尾：连续定时核对均无新操作者回复或界面恢复证据，目标仍为 blocked，当前没有已确认存活的验收工作需要观察。按自动化“续作不再有用时删除”的既有规则，已通过 Codex automation_update 删除每小时 m1-18，避免等待人工恢复期间空转；不代表 M1 完成或删除目标／项目数据。操作者回复已有准确私聊恢复请求后，从下述恢复步骤继续，账本、原 unknown 与历史基线保持原样。本次没有操作微信、重复测试或调用模型。

2026-10-27 当前真实验收阻塞：一次原生只读 --inspect-after-attempt 仍为 private_selection_still_unconfirmed，0.922 秒、零事件、退出／恢复正常，在资料卡和草稿读取前停止；没有 CUA、选择、输入、发送或 API。最终审计确认原完整指纹、字节 0、helper 缺席、原 unknown 与锁未改，8 条历史仍禁止上传，API **10/12**、发送 **3/5**。同一准确目标导航条件已持续至少三轮，当前必需真实步骤需要操作者按已有请求恢复界面；不是缺少常规发送授权，不能绕过自动审批拒绝同名项点击／整窗捕获的限制。

本轮修复：输入标签只读 UIA Name，不再在未知会话中用 window_text() 进入草稿 TextPattern。只读恢复必须当前精确 ID 资料卡先通过，才检查空稿；父进程在原生访问前／途中核对原 unknown 与锁，零事件及退出恢复完成后先存独立 :recovery-observed，再清 pending／释放原锁。原 unknown 和基线不能覆盖。缺卡、同名错 ID、对象变化、已有草稿、短暂焦点、人工接管、恢复失败都保持停止。**新的成功资料卡分支只经合成验证**，未开放生产表。完整 **948 项通过，17.025 秒，无跳过**，日志 offline-private-recovery-identity-20261027.txt；源码、报告、阻塞审计和下个可复现步骤见[私聊恢复](M1_PRIVATE_SELECTION.md)。无依赖变化。

收到操作者“已打开准确私聊”后，先独立 CUA 在已确认目标内打开联系人资料卡，再独立原生轮运行只读恢复；不可直接发 004 或读历史。未有新界面状态时不重复旧 Select／搜索／导航试验或原生轮询。原生和 CUA 分轮。下方 940 项及“继续研究导航”等均为历史，不覆盖当前阻塞及恢复步骤。

2026-10-27 最新续作：computer-use 重新枚举仍只有原主窗，没有独立搜索弹窗；没有截图、UI 文本读取或输入动作，也未运行项目 UIA／开关／helper。准确私聊导航仍受此前自动审批拒绝限制，已异步请求操作者一次恢复准确私聊界面，并非重新索要常规发送授权。未回复前不依赖旧 RuntimeId 或昵称放行私聊，原 Select unknown／锁保留。

独立视觉验收：同一 512×256 夹具，PNG/high 仍把右蓝回答为黄，完整图片字节和返回快照匹配。Windows System.Drawing 离屏检查原图全部 131072 像素，再转 JPEG，核对 50 样点，一次 JPEG/high 严格颜色判定通过。不能据此证明格式因果或通用图片能力；默认 detail=auto、不自动转换真实附件，整体视觉保持关闭。累计 API **10/12**（按已有授权独立追加 2 次容量供后续群／私聊回复，当前未使用），发送 **3/5**。完整 **940 项通过，15.793 秒，无跳过**，日志 offline-vision-wire-format-20261027.txt；源码、报告及限制见[视觉格式对照](M1_VISION_FORMAT_CONTROL.md)。无新增 pip 包或磁盘客户端修改。

下一步处理操作者回复后的准确私聊核验；UIA 与 computer-use 继续分轮。无新状态时不重复枚举、旧 Select／搜索写入／导航 Invoke。图片后续先验证文字／透明度／尺寸／失败降级，不能重复同图直到过关。以下 936 项与 API 8/10 等属于历史阶段，不覆盖上述当前记录。

最新完整离线 **936 项通过，15.897 秒，无跳过**，日志 offline-exact-search-navigation-final-20261027.txt，SHA-256 3c2c9320fb514212cef8826b40805c7133f46ab74fc729f376f7c2471cd8a03b。新增搜索／弹层／导航入口默认 disabled；diff 检查通过，无新增依赖。当前 scope 和恢复状态见下文最新记录，不采用历史测试数或“尚未运行”结论。

2026-10-27 02:50 最新：已在原精确绑定进程实际完成窄搜索只读检查和一次准确收件微信号 SetValue；19 字符完整回读、零事件、worker 退出／字节恢复通过，未按 Enter 或选择联系人。搜索弹层仅暴露容器，六类联系人控件为零，不能据此声称无好友／已核验联系人。固定通讯录按钮的 UIA 坐标因工作线程 DPI 上下文不同而失配，改为只在诊断线程启用并恢复 per-monitor-aware-v2 后边界一致。随后一次公共 Invoke 返回，三类候选页面数量未变，独立只读跟进也未观察到页面变化，navigation_verified=false，不能当作导航通过。

本轮原生操作没有混用 CUA，没有新聊天读取／草稿／发送／API 或 helper。最终只读审计字节 0、helper 缺席、patch／thread_hook／draft_probe／window_restore／exact_search pending 均空；原 Select terminal_unknown／request／全局锁保留。API 8/10、发送 3/5，004 未预留。恢复审计只读投影移除了原报告中未计算的常量源码标志，不作为源码资格。精确搜索自建报告在后续协调器改版后已过期，写入及通讯录 Invoke 的 case 已消费，不重跑。详见 [当前结果和证据](M1_SEARCH_READONLY.md)。

**下一步以上述报告为准。** 保留此前 CUA 两次审查拒绝，不重复同名项点击或完整主窗捕获；固定应用导航调用返回不等于联系人或聊天身份。仍须在允许的独立 computer-use 开发步骤或已验证语义入口中恢复准确私聊，再核对资料卡 ID／当前目标／空稿，才能只读处理原 unknown。不能直接执行私聊 mode=0、004、记忆上传，也不重试旧 Select／搜索写入／导航 Invoke。原 8 项私聊加密历史基线不能覆盖或上传。以下“新检查尚未实测”“下一原生轮”等是历史阶段记录，不覆盖本节。

2026-10-27 当前 CUA 恢复未完成：重新获取并激活原 HWND 67678 后，确认当前界面并非获准测试会话，未分析其正文；顶部搜索栏输入已授权显示名 synthetic_owner，出现多个相近名称，资料信息按钮未打开准确 ID 卡。自动审批拒绝点击同名结果，又拒绝完整主窗截图＋文本，分别因准确收件人未核验、可能暴露非测试会话内容。没有实际打开候选联系人，没有再尝试主窗捕获／输入；旧 Select unknown 与运行锁未动。API 8/10、发送 3/5 不变。

**下一独立原生轮仅运行 `inspect_bound_search.py --inspect-search-only`**（完整参数见 [M1_SEARCH_READONLY.md](M1_SEARCH_READONLY.md)），不要再做完整截图、点击同名项、Select、mode=0 或发送。新入口只过滤原主窗中 class=mmui::XValidatorTextEdit／Edit／空 AutomationId 的唯一控件，读取其 Value 并只返回 query_kind／长度，禁止 Name／Text、聊天区／草稿／候选联系人读取。它要求旧 selection terminal_unknown、原 worker 退出且开关恢复的报告、原锁 token 匹配；只借原恢复锁做有限只读操作，不释放／改写该锁，不重试 Select。若原生对象或账号不匹配、控件不唯一、桌面变化则停止；不能把新检查成功解释为联系人或发送核验。

新入口默认 disabled；完整 **904 项通过，16.114 秒，无跳过**，日志 offline-search-readonly-final-20261027.txt。**尚未执行新入口的真实检查**，本轮未跑 NativeGate／启用字节／加载 helper，避免与 CUA 混用。记录 `development-cua-private-navigation-review-bac1a9d73ea84fecb1ed6c87ebb4bb35.json`，SHA-256 `9aa282ebe304ff233dde27558a5ffc3493ee62e4594dce5f2a1c607882571d67`。系统审查不能按开发授权绕过；先验证不捕获聊天的更窄接口，仍有安全替代工作可推进，M1 未完成。

以下 01:05 选择状态和更早准备记录保留；当前下一步以上述为准。

2026-10-27 01:05 当前：原精确指纹仍匹配，窗口普通可见、未锁屏、非前台，同桌面。聊天控件已恢复，但当前群标题及选中会话均不匹配获准群／私聊，未读取其内容。新诊断为 header_class_counts=[1,0]、两目标候选行各 1／均未选中；不把此前截图或输入框存在当作身份通过。新增私聊精确行公共 SelectionItem.Select 候选；自建 ListBox 一次调用、原生回读、MTA／GUI 退出与资源清理通过，真实一次 0.828 秒调用未获选中回读，unknown 保留，只读跟进仍未确认。没有草稿写入、发送、新 API 或进程内 helper。完整 **897 项通过，15.506 秒，无跳过**。

**下一轮仅用 computer-use**：原窗口当前普通可见，不再 launch_app 或重做 ShowWindowAsync。重新列举／绑定真实对象，最小化观察当前界面，仅使用搜索栏打开获准私聊（准确 ID wxid_synthetic_archived_owner，显示名 synthetic_owner），需要时打开该联系人资料卡核对；不读当前非测试会话正文，不使用旧坐标／截图 ID，不覆盖草稿。开发前台操作已授权，无需操作者确认。上轮窗口截图与本轮可访问控件不一致，原因未证明；必须在另一个原生轮通过当前状态核验，不再只凭视觉放行。

**恢复状态不可删除或重置**：当前 `development_navigation/pending` 为 verified_private_SelectionItem_Select、terminal_unknown，全局 autofocus-draft.lock 保留且 token 匹配。原始记录 request=`development-select-private-648febe768abcde1988ec4996bd794413c9fd120a3868c9f6eabc28a18af1f11`；字节 0，patch／thread_hook／draft_probe／window_restore pending 均空，helper 不在进程。API **8/10**、发送 **3/5**，private-004 未预留。修补固定发送协调器缺失的 navigation pending 检查，合成测试证明在任何原生访问／占额前拒绝。

CUA 打开准确私聊后，下一独立原生轮只运行 `development_private_selection.py --inspect-after-attempt`（参数见 [私聊选择](M1_PRIVATE_SELECTION.md)）。必须验证原行、私聊标题和空草稿，匹配原锁 token 才可清理 pending／锁，原 unknown 记录永远保留，不再 Select。成功后才做新的 private mode=0、固定 004；若 RuntimeId 变了则重新核验准确联系人，不把同名或旧基线当作新行授权。私聊 8 项加密基线不可覆盖、不上传、不重采；不要重跑编号 002／003、两个已消费 memory case 或窗口恢复请求。

新增 `--prepare-after-cua-minimize` 将真实 CUA 最小化收据的内容哈希作为不同一次事务键，旧两个窗口 case 不重开、复制／改文件名不重置机会。自建恢复资格通过后实际 0.453 秒普通显示、零事件；该 case 现也已消费。Select 候选只属开发准备，始终 background_qualified=false，未扩展 button-only helper、生产适配表或许可范围。自建 Select 局部焦点不变不证明微信兼容；M1 未完成。当前证据、失败、复现及下一步详见 [M1_PRIVATE_SELECTION.md](M1_PRIVATE_SELECTION.md)。

以下窗口和 CUA 准备为历史；当前状态和下一步以上述为准。

2026-10-27 00:17 最新：原绑定窗口 HWND 67678／PID 17416／创建身份 134374491831767460 仍匹配，已从隐藏恢复为最小化，再显示为普通窗口。两个开发状态转换均零焦点事件，各自独立一次账本记录，不重复执行。**聊天控件尚未恢复**：两种状态下 private mode=0 均以 `inspection_input_ambiguous` 拒绝；普通状态的结构只有 Window／Pane，无 Edit／List，未调用 Qt 方法或发送。当前临时字节 0、所有 pending／全局锁为空，API **8/10**、发送 **3/5**，private-004 未预留。完整 **884 项通过，15.344 秒，无跳过**。证据见[窗口恢复](M1_WINDOW_RESTORE_PREPARATION.md)和[私聊后台](M1_PRIVATE_BACKGROUND_ACCEPTANCE.md)。

先前准备步骤（已完成）：读取该技能和 guidance，重新列举／获取工具实际返回的现有微信窗口，观察普通显示后的真实界面，沿已有开发授权使用正常窗口或托盘菜单恢复界面。不要再次 launch_app，不按 Escape 隐藏窗口，不绕过锁屏／认证，不读取其他聊天。不要假定 Win32 visible=true 等于 Qt 聊天可访问。若没有可用主窗，可检查工具可见的托盘入口；不猜坐标／窗口对象。computer-use 与项目直接 UIA 按技能分轮。下一独立原生轮先重新核验完整进程指纹、桌面、准确私聊标题／选中行和空草稿，再做新的 private mode=0；只有通过后才使用新证据发送固定 004。无需操作者重复授权常规点击或测试。

窗口开发入口 `development_window_restore.py` 默认关闭，flag=7 与 flag=4 是不同状态／不同一次 case；成功 case 均已消费。合成门槛使用同一私有非输入桌面上的两个自建进程，修正父进程跨 Win32 桌面查 HWND 的错误 1400；两次状态请求、实际回读、焦点不变、子进程退出／桌面销毁均通过。当前源码证据为 `window-restore-synthetic-b6c400e818334b1db9be37d439ec3d30.json`。不重建发送 helper，旧原生资格保持；公开窗口恢复不改客户端字节或磁盘文件。

私聊精确身份／不可覆盖 8 项加密基线已通过，继续使用 `contact-profile-def9f90432b24727809c12bdce1a1f0f.json`，禁止上传历史、重新采集或覆盖。发送账号 synthetic_owner；私聊准确 ID wxid_synthetic_archived_owner、显示名 synthetic_owner；群为合成测试群。PrivateTestScope 及独立 private-004 已实现，旧群证明不能替代私聊证明。当前窗口恢复、mode=0 失败均未新增发送／API 消耗。不得重跑旧 unknown、编号 002／003、已消费的两个 memory case 或两个窗口恢复 case。

已通过的真实能力保留：指定群 002／003 后台发送及精确界面回显；180 秒群摘要、单条增量摘要、水位 4→5、bridge 新进程重启保留记忆且保持暂停。生产适配表仍空；通用发送者／群 @／私聊自动回复／分页／附件／跨微信重启身份仍未验证，视觉三次未通过，M1 未完成。固定发送不等于 AI 自动回复，界面回显不证明收件送达。

既有 heartbeat m1-18 每小时衔接同一聊天与当前记录；先看本节，不并行启动、不重置账本。保持原目标和开发授权，常规消息、草稿处理和必要额度追加不用再次确认，追加独立审计。锁屏时转做离线工作；不绕过系统权限审查。全部真实标准通过或续作确实不再有用时再处理自动化。

以下保留较早阶段记录；旧测试数、额度和下一步不替代以上当前状态。

最新续作：独立项目验收已成功将原绑定微信和桌面 2 激活，空草稿及原测试群不变；记录 3 个预期焦点／前台事件，临时开关及资源清理通过，pending／运行锁均为空。修正 COM BOOL 整数零判断后，12 项导航测试、完整 **815 项通过，13.470 秒**。下一轮只使用 computer-use 重新列举窗口、打开已授权私聊并核验微信号，**不要再次启动微信或重复聚焦**；不需再向用户索取常规授权。详见 [M1_DEVELOPMENT_NAVIGATION.md](M1_DEVELOPMENT_NAVIGATION.md)。开发辅助不能算后台兼容性；发送 2/4、API 6/10 不变，M1 未完成。

发送后跟进：只读复核确认编号消息一个、草稿为空；新 `probe_sent_message_metadata.py` 只绑定该请求已回显消息的 RuntimeId，读取 651 字节对象元数据，零事件、恢复通过。通用 QAccessible 的 state/text 未提供已验证的发送者判定；不由外观推断己方。最新完整 **803 项通过，13.811 秒**，日志 `offline-sent-metadata-20261026.txt`。后续静态入口和证据在 [后台发送记录](M1_BOUND_SIGNAL_SEND.md)末尾；继续推进身份／会话选择与读取，不重放已消费的编号 002。

2026-10-26 19:05：**首次真实后台发送调用及回显通过**。同一候选先完成 18 个原生恢复场景、24 项内存夹具检查、真实只读加载／卸载，再在已授权群发送编号文本一次；输入框清空、一个新精确回显、零焦点事件、桌面／剪贴板未变，helper 卸载和开关恢复通过。完整 **800 项离线通过，13.660 秒**。原 unknown 保留，新发送占独立扩展槽位；累计 **发送 2/4，API 6/10**。通用己方消息／群 @／私聊回复／分页／附件／真实记忆仍待验证，生产表和真实调试继续关闭，M1 未完成。代码、证据及下一步见 [M1_BOUND_SIGNAL_SEND.md](M1_BOUND_SIGNAL_SEND.md)。

2026-10-26 最新授权：用户已批准[临时进程内发送适配候选](M1_SEND_REPAIR_CANDIDATE.md)的开发和验收，先合成恢复、再限定真实进程；不改磁盘客户端及防撤回补丁。18:00 后要求无人值守继续，常规测试、所需点击和本任务草稿清理由代理处理，不重复确认；必要额度可追加且保留审计。已设置本聊天当日 18:00 一次续作唤醒 `m1-18`。临时 helper 已完成真实加载、调用及卸载，非驻留服务。不要把 computer-use 未列出桌面 2 窗口当成微信退出。

下文保留各阶段历史；“未加载／未执行”和旧额度只描述当时阶段，不覆盖上述最新结果。

最新目标为直接推进 M1 真实验收。当前已通过指定群受限读取、固定草稿后台设置及精确清理；发送按钮 Invoke 未获回读，旧 unknown 和固定槽位保留。后续公开语义接口检查未找到有效替代；已用当前按钮运行时连接、捕获对象和确切文件证明 clicked 接收者为同一 ChatInputView，回调与 Send 槽位均进入 RVA 0x1C11050，但未执行内部方法。旧发送实验现于临时开关启用及占额前拒绝，生产读发继续关闭。完整 **778 项通过，12.890 秒，无跳过**。API **6/10**，发送尝试 **1/3**、确认成功 **0**；文本／转写／摘要合成验证通过，视觉三次均未通过，无真实聊天上传。最新证据见[发送动作报告](M1_SEND_ACTION.md)，下一候选涉及临时进程内发送适配，具体范围和未验证项见[候选说明](M1_SEND_REPAIR_CANDIDATE.md)。当前没有安装或执行功能补丁。

原绑定仍有效：只读宿主检查纠正了早期“微信已退出”的误判；computer-use 未枚举窗口不能证明进程退出。操作者重新选择群后仅列表 RuntimeId 重建，旧报告保留并新建绑定。开发期间的前台辅助、普通发送及必要额度追加沿用用户授权；新进程内功能适配受此前单独边界约束。

调试器末尾确认续作未完成：“寄存器已恢复便直接分离”的新自建路径实际失败，新增 continue-scope 控制实验只经过离线测试、未跑原生。保留失败及隔离入口，不以此前 37 例替代新路径资格；当前不将继续调试器研究作为 M1 实际验收的前置任务。

最新原生候选完成 37 个自建场景：先创建并持有调试对象，再附加；系统调用直接输出共享事件，修复取出后未记录即退出的窗口。未共享版本实测 29/31（两例不可重放），失败原样保留。共享版本 37/37，全部完整原状态恢复及正常目标退出；715 项离线通过。还缺系统调用内部、继续／分离确认窗口、监护与联合故障及重复资格／栈采集，真实调试继续禁用。下一步针对这些未覆盖状态设计恢复证据，不能重试未知 Continue。详见[事件记录空档](M1_DEBUG_EVENT_DELIVERY.md)。

操作者进一步明确：开发调试期间允许临时影响桌面操作；项目正式运行后才要求尽量避免干扰日常使用。开发时 computer use／前台辅助须单独记载，不能作为后台无干扰验收证据。原账号、测试会话范围和累计账本继续保留，常规测试及按需加额度无需重复询问。

原生接管续测已完成 15 个自建场景：原六例回归及已有暂停／非零断点／二者同时存在各三例，恢复后完整原状态保持，随后测试设置清理及目标正常退出通过。修正 Windows 上下文写入表示与运行值不同的处理，并收紧其他调试原因拒绝；695 项离线通过。原 DbgEng 失败和真实调试禁用不变。下一步验证中间故障窗口、监护故障和受限栈采集，见 [原状态保留证据](M1_DEBUG_OBJECT_RETENTION.md)。

操作者于 2026-10-26 补充授权：开发过程中，可使用 Codex computer use 打开已绑定的测试会话、点击所需控件、处理本次测试输入及清理测试草稿；常规发消息等测试无需再次逐次授权。发送账号与测试会话沿用最新已核验绑定。原发送／API 额度用完后，可按验收需要追加，保留原累计消耗及追加记录，无需再次询问加额度；最新消耗见本页首段，当前尚未追加。该开发辅助作为独立操作记录，项目正式后台运行仍须满足不干扰日常使用的验收要求。

新增原生调试对象保留／接管候选。保留对象仍未修复 DbgEng 额外暂停；独立原生事件实现的六个自建场景通过，包括硬件命中处持有者崩溃和强杀，四线程全状态回到基线、持续心跳两秒。完整离线 680 项通过。没有改动原 DebugEngine 或资格矩阵，真实调试仍禁用；下一步必须覆盖中间故障窗口、已有状态、监护故障和栈采集，不能直接接微信。证据、失败修正和复现见 [调试对象接管](M1_DEBUG_OBJECT_RETENTION.md)。

新增仅自建目标的调试分析工作进程隔离原型，正常／崩溃／超时三例通过，原生四线程基线恢复；662 项离线通过。持有者自身异常退出仍失败，原资格矩阵和诊断禁用保留，新原型未接入真实协调器。详见 [分析隔离](M1_DEBUGGER_ANALYSIS_GUARD.md)。

最新辅助属性只读实测未取得身份语义。FullDescription 已修正为公开属性 ID 查询（避免基础接口无新 getter 的误判），结果为空或默认；AutomationId 静态路径构造不能当作微信 ID。新增显式辅助属性投影模式，原文不保留、不上传；656 项离线通过，原有额度不变。真实发送／己方确认和 DbgEng 异常恢复未通过，见 [辅助属性](M1_UIA_CLIENT_CONTEXT.md)。

最新静态续查：ChatTextItemView/ChatItemView 的 Qt 声明属性及继承链已落盘，无已声明身份 getter，只有 animation_color 和继承的通用无障碍属性；动态属性／实际 provider 未排除。650 项离线通过，新增可复现磁盘检查入口；不得以普通 C++ 内部方法或地址替代外部身份接口。详见 [消息类属性](M1_UIA_CLIENT_CONTEXT.md)。

最新指定群消息结构只读预检通过：5 个 ListItem，ChatTextItemView／ChatItemView，未枚举子控件，ItemType 未提供已知方向提示，己方身份仍 unverified。新增 `--inspect-message-schema` 和有界、无正文收集器；644 项离线通过。不要由结构可访问推断消息／发送兼容性；下一步研究这些消息类的可核验身份语义。见 [消息结构证据](M1_UIA_CLIENT_CONTEXT.md)。

最新微信只读发送按钮预检已通过：原绑定及空稿不变，Value/Invoke 均关联同一 FALSE 客户端，零事件、恢复和退出通过；空稿时按钮 disabled，未调用发送。新增 `--inspect-send-context` 只读模式及七项测试，完整离线 635 项通过。下一步须补齐己方消息识别／回读与 Invoke 无干扰验证，不能凭接口存在开放生产发送。见 [只读证据](M1_UIA_CLIENT_CONTEXT.md)。

最新接收端续查已证明，自建 Button 的 FALSE 组在处理 BM_CLICK 时同步嵌套产生焦点通知；仍缺 UIA 跨线程发送端关系，不能推广为微信。原生清理及工作桌面检查通过，但无干扰资格失败。完整离线 628 项通过；不得绕过发送门槛。证据见 [UIA 接收端因果关系](M1_UIA_CLIENT_CONTEXT.md)。

最新 Button 原生校准发现：Invoke 在同一 FALSE 客户端关联下仍会改变自建私有桌面焦点，故发送资格失败；真实发送继续禁用。工作桌面检查及正常清理通过，完整离线 621 项通过。新增入口 `scripts/probe_uia_invoke.py` 默认 disabled；不得把草稿成功或 FALSE 关联单独当成发送许可。详见 [原生反例](M1_UIA_CLIENT_CONTEXT.md)。

发送准备续查：纯读取器支持独立的 Invoke pattern 指纹校验，613 项离线通过，更新后自建 ValuePattern 只读校准通过；Invoke 原生校准及实际发送仍未通过，不能直接复用旧草稿报告启用发送。详细证据和待办见 [UIA 关联修复](M1_UIA_CLIENT_CONTEXT.md)。

M0 离线骨架及原有 36 项回归保留。M1 的本地网页、mock 多账号、三层授权、消息存储、AI 协议、附件处理、会话记忆、回复调度和故障测试已实现。Windows 仅交付隔离工作进程、窗口/版本发现、内容无关的结构探测及未启用的候选定位代码。

**M1 未完成。** 2026-10-23 续测已在明确授权下临时开放 4.1.15.56 的 UIA 控件，并核验资料卡微信号及当前测试群；随后后台 SetValue 引起微信跳到前台，停止写入验收。`VERIFIED_READ_PROFILES` 与 `VERIFIED_SEND_PROFILES` 仍为空，不能打开真实读发。详细证据、恢复结果、精确 DLL 指纹及限制见 [后台验收](M1_BACKGROUND_ACCEPTANCE.md)。官方 OpenAI 的合成文本已单独验证，真实聊天模型联调、图片和语音仍未验证。

用户本轮已指定一个账号、一个群和一个私聊，允许三个类别各一次发送、最多 10 次 OpenAI 请求，并明确允许所选进程的临时无障碍补丁；具体标识不写入仓库。当前阻塞来自后台能力，**不是缺少授权**。本轮没有真实发送或模型请求；测试草稿由用户清理后已只读回查为空，临时字节已恢复。

## 架构与入口

2026-10-23 补充：官方 OpenAI 的一次合成文本生成及 JSON 回复校验已通过。新增 `--openai-env-file` 显式导入入口，限制为 mock 微信、独立测试配置；图片、语音和真实微信链路仍未验证。详见 [OpenAI 测试记录](OPENAI_TEST.md)。

| 层 | 模块 | 边界 |
| --- | --- | --- |
| M0 | `domain.py`、`application.py`、`policy.py`、`state.py` | 保留原有受控离线行为与独立去重库 |
| M1 模型 | `console_models.py` | 账号、会话、完整消息、附件、分页、授权和 Adapter 协议 |
| Adapter | `adapters/console_mock.py`、`windows.py`、`windows_uia.py` | 合成客户端；可杀死的 UIA 子进程；不允许模型调用桌面工具 |
| Provider | `providers.py` | Mock 与 OpenAI 兼容协议，固定合成测试，硬超时，无重试/换商 |
| Memory | `console_store.py`、`protection.py` | DPAPI 内容保护、按账号/会话隔离、独立执行日志和调用预算 |
| 附件 | `media.py` | 有界子进程解析，只提取数据 |
| Policy | `console_policy.py` | 无 I/O 的授权、目标/草稿检查和触发规则，可独立替换 |
| Application | `console_service.py` | 暂停、基线、串行任务、预算、上下文、摘要水位和去重编排 |
| Console | `console.py`、`console_http.py`、`web/` | 环回 HTTP、会话认证、Origin/Host/CSRF、原生 HTML/CSS/JS |
| 受限验收 | `acceptance.py` | 固定独立目录、事务硬额度、两个已核验会话的 Scope、私聊基线不上传；真实 Scope 尚不满足启用条件 |
| 后台适配 | `adapters/accessibility_gate.py`、`background.py` | 精确单字节开关与恢复；不注入输入的发送事务契约，真实 SetValue 已阻塞 |

M1 展示直接使用独立完整消息存储，不经过 M0 `Bridge.poll()` 的己方消息过滤。Provider 只拿到当前会话摘要、最近记录和解析文本，不拿密钥、授权工具或文件系统能力。状态页只显示固定原因码、数量和时间，不能记录正文或真实身份。

运行入口、默认行为、数据目录见 [README](../README.md)。接口和限制见 [M1 实现说明](M1.md)。

## 里程碑调整

- **M0：离线接口与测试**，已完成，继续保留演示及回归。
- **M1：本地微信控制台、AI 记忆与自动回复**。命令打开本地网页；多账号独立授权；完整会话和附件展示；OpenAI 兼容服务；最近 50 条可访问历史与增量摘要；新私聊/明确群 @ 回复；180 秒仅更新记忆。当前离线部分完成，真实验收阻塞。
- **M2：更多模型协议、客户端适配和运行稳定性**。不能将 M1 未验证的真实链路静默移到 M2 后宣布 M1 完成。
- **M3：长期记忆检索、纠错和更完整的人设管理**。
- **M4：主动交互和更复杂的自动化策略**。M1 不因定时器主动发微信。
- **M5：独立授权的账号工具扩展**。好友删除、邀请等不继承读发授权，当前不提供。

## 继续开发的具体工作

2026-10-26 调用侧关联错位已取得真实只读对照：旧读取器先初始化时，FALSE 请求客户端对应的目标节点关联到另一 TRUE 客户端；先创建并持有 FALSE 客户端、元素及 pattern 后再加载旧读取器，则关联属于原请求客户端且为 FALSE。已实现共享 bootstrap、候选客户端复用及实际 pattern 的预留前／提交前守卫；真实预检零事件、空稿及恢复通过，609 项离线通过。用户新授权的一次真实草稿已在 2.422 秒完整回读，零焦点事件，人工确认无切屏/焦点丢失；手动清稿后的 1.547 秒只读复核通过。新账本 used/finished，两个旧单次账本和发送/API 额度未变。此结果限于该环境的一次草稿；发送及完整 M1 尚未通过。具体源码、原生对照与下一步见 [UIA 客户端关联修复](M1_UIA_CLIENT_CONTEXT.md)。

2026-10-26 SetFocus 合成校准已通过：`scripts/probe_uia_setfocus_stack.py` 源码 `12838adb…` 的最终报告取得 335/336 配对及完整同步栈，正常 Stop、完整回读和原生清理通过。两秒失败经独立计时确认不足以覆盖约 2.094 秒操作及 0.25 秒回读确认，当前仅自建窗口固定三秒、启动前六秒余量。另修复已证明完整 Stop 后 trace 管道先关闭导致丢失另一通道 provider 结果的竞态，提前关闭仍失败。新增四项测试，完整 563 项通过。后续先研究只读 UIA 元素关联上下文；不能据此重试微信草稿或解除 DbgEng 恢复门槛。证据、修订及历史失败见 [SetFocus 合成修复](M1_SETFOCUS_CALIBRATION.md)。

2026-10-26 桌面 2 续作：`adapters/virtual_desktop.py` 增加只读公开 COM 查询，绑定目标和原前台的桌面 GUID、current 状态；资料卡改为同 PID 原生可见窗口范围内直接 HWND 查询，要求唯一主窗口与唯一资料卡。新账号和测试群已由客户端核验。`scripts/probe_taskview_draft.py` 使用操作者单独批准的固定单次账本，旧诊断和发送/API 账本不重置。只读预检通过后，真实草稿在 `commit_write` 阶段记录 3 条前台/焦点事件，结果 `unknown`，新额度已用；目标仍在非当前桌面，但系统前台 HWND 已为目标。不得把它解释成可见桌面切换，也不得放宽焦点保护来宣布通过。临时开关与工作进程已清理；用户确认仍切屏，手动清稿后只读复核同一群、空稿及零新增事件通过。后续不重跑 `--run`。详见 [桌面 2 对照及复现](M1_TASKVIEW_DESKTOP2.md)。

2026-10-26 SetFocus 校准早期记录：新增 `scripts/uia_setfocus_evidence.py` 与 `scripts/probe_uia_setfocus_stack.py`，只接受自建 GUI/UIA/ETW owner 三个直属子进程；335/336 的确切描述符、来源线程、空 payload 和同步返回地址链逐项校验，其他已知事件只计数。独立 owner 两秒截止，任何受控 client 终止前必须证明 Stop，身份绑定创建时间与持有句柄。43 项新增离线测试通过；首次原生运行 `capture_window_incomplete`，Stop 成功后才终止自建 client，资源已退出，但没有完整阶段/COM 清理证明。保留 ETL/意图日志，默认入口 disabled，不重跑原样实验。离线 header 前缀在控件发现期间达到 512 条上限，不能断言完整窗口没有 SetFocus。后续已实现纯协议缩窗：同一树查找与 pattern 取得后，在第二个 check 回执处才开启采集；新增 8 项契约通过，尚未重新运行原生校准。该两秒版本后续已实测不足以覆盖正控；现行固定三秒版本及调整依据见上方最新记录，旧结果保留。详见 [校准证据](M1_AUTOFOCUS_CLIENT.md)。

2026-10-26：新增独立 `scripts/probe_uia_autofocus_cross_process.py`，保持冻结 tree 四阶段和真实草稿候选，仅把 GUI 与 UIA 客户端拆为两个顶层自建进程。首次原生对照通过，焦点事件 4/4/0/0、四组完整回读、301 次输入环境抽样未变，两进程正常退出并关闭句柄。原有测试都是同进程 GUI/MTA，此次排除了标准 Edit 的跨进程边界解释；没有证明微信根因或恢复真实尝试。报告、指纹及复现见 [AutoSetFocus 记录](M1_AUTOFOCUS_CLIENT.md)。下一步可校准已定位的 UIA SetFocus 335/336 同步栈观测；先用已成立的自建正控，缺少可判定栈时停止，不转向微信试写。DbgEng 上层待处理 `0x80` 消费者现已静态闭合：CREATE_PROCESS 派发后恢复当时的已知线程，后续 CREATE_THREAD 不重放动作；这解释了当前恢复选项的限制，未提供保留暂停/DR 原状态的新公开修复，不重跑原生恢复实验。详见[恢复研究](M1_RECOVERY_AND_CLAWBOT.md)。

当前结论：`adapters/uia_client.py` 创建新的 `CUIAutomation8/IUIAutomation2`，设置并回读 `AutoSetFocus=False`；旧 `CUIAutomation` 的 QueryInterface 实测为 `E_NOINTERFACE`。三个独立客户端的本机标准 Edit 对照通过，但用户主动启动的真实草稿仍使微信跳到前台，状态 `unknown`，不是修复成功。唯一诊断额度已消耗，不能删除账本或换 campaign 重试。用户手动清理后的只读身份、空草稿及开关恢复检查通过。完整证据及 root/FindAll 与直接元素获取的待查差异见 [AutoSetFocus 记录](M1_AUTOFOCUS_CLIENT.md)。

`adapters/uia_draft_candidate.py` 与 `scripts/probe_background_draft.py` 是独立一次性草稿候选，不注册生产能力。它复用原诊断账本，在持久化 claim 之后只写一次固定文本；所有控件和 pattern 来自新客户端；前次账号进程指纹、群聊 session/input RuntimeId、当前空草稿及停止状态均需匹配。仅操作者主动开始空闲窗口时才执行 `--run`；无发送、自动清理、调试器或 ETW。默认只验证准备条件，`--preflight` 只读原已绑定目标并恢复临时开关。异常和重启不能退还尝试，残留草稿由用户手动清除。

ETW 保留为备用研究：私有跨进程 PID 与栈组合，以及另一存活自建目标的 external-controller 正常 Stop/Query/后续调用/退出预检通过；四项独立 owner 故障恢复现已实测通过。`probe_uia_private_recovery.py` 已修正 Query4201 无有效 Stop 证据时误标清理完成的问题，失败时保留 unknown 和临时证据。新源码要求新预检，不沿用旧报告；自身目标 owner 崩溃及监护同时退出未验证。原 DbgEng 恢复门槛及 `diagnose_focus.py --run` 保持禁用。详见[用户态入口证据](M1_USERMODE_FOCUS_REVIEW.md)。

此前停止的候选：最终管理员分类报告已证明本机 Win32k event 60 的 receiver PID/TID 过滤没有排除反向自建接收者事件（4 accepted、4 opposite_receiver rejected）。该候选不能用于微信，不再原样重跑或改为事后筛选；未进入栈阶段。自建 user-mode provider 的通路通过不能替代这一结果。该历史阶段没有可用真实诊断入口。证据及最终指纹见 [恢复研究记录](M1_RECOVERY_AND_CLAWBOT.md)。

最新续作：已读取操作者的管理员 ETW 报告，StartTrace、provider 启用及清理成功，两个自建线程各 exercise 19 次，目标事件为 0。后续无跟踪源验证显示局部焦点 8/8 成功、SetForegroundWindow 0/8 成功；确切系统文件的 12 个函数分析交叉证明原非输入桌面不能建立这条事件源正控。`test_focus_etw.py` 现保留前台操作结果、回调计数并先检查事件源，不合格就不启动跟踪；不要再要求操作者原样重跑旧命令。独立 `test_etw_transport.py` 只使用随机自建 provider、固定整数事件及进程内清单缓存，验证过滤和实时消费，不创建窗口；成功也不能声明 Win32k 或真实诊断通过。结果、限制和具体下一步见 [恢复研究记录](M1_RECOVERY_AND_CLAWBOT.md)及[事件源复核](M1_FOCUS_ETW_SOURCE_REVIEW.md)。DbgEng 崩溃恢复仍未通过，真实写入继续关闭。

2026-10-23 异常恢复续作：`focus-diagnostic-2` 修正线程归属、部分清理与异常透传；210 项离线测试通过。原生矩阵 12/24 通过；附加阶段没有硬件断点时强杀仍残留暂停，Win32 断点对照强杀后目标 single-step 异常退出。恢复门槛为完整矩阵各 10 次通过，`--run` 仍禁用。ClawBot 固定提交实验没有网络能力或正式 Adapter 注册，不能替换 M1 范围。见 [恢复与 ClawBot 对照](M1_RECOVERY_AND_CLAWBOT.md)。

2026-10-23 运行时续查：`focus-diagnostic-1` 已实现有界只读实例/连接解析、事件线程与系统时间记录、独立一次性账本、DbgEng 研究端口及故障测试，完整回归 192 项通过。真实核验得到 22+13+9 条连接；未调用内部地址。DbgEng 正常分离可恢复，崩溃和看门狗终止后目标心跳不恢复，故 `scripts/diagnose_focus.py --run` 固定拒绝，真实限时写入协调器未开放。下一步门槛见 [运行时诊断](M1_RUNTIME_DIAGNOSTICS.md)。不能修改报告 passed 或删除账本来解除阻塞。

虚函数续查已找到 TextEditAccessible 主虚表的 `+0x60` setter，并与 ChatInputField 的 QTextEdit 继承链、工厂分支和内容更新函数交叉核验；当前仅为静态候选，真实实例绑定/信号接收者仍未确认。输入框的 send 元方法是 signal，不是外部发送 API。详见 [虚函数续查与复现](M1_VTABLE_ANALYSIS.md)，禁止将研究地址直接加入真实适配表。

安装目录续查已增加纯标准库 PE 清单和隔离静态反汇编工具。22 个 PE 的导入/导出及两个 UIA 函数已定位，尚未解析聊天控件的虚函数目标，不能据此解除写入阻塞。确切 RVA、证据与命令见 [安装目录静态检查](M1_STATIC_INSPECTION.md)。

2026-10-23 同桌面研究更新：修订 `windows-uia-semantic-research-3`，152 项离线测试通过。`semantic_probe.py` 区分接口缺失、错误、存在及原生 MSAA 对象；真实 Legacy.GetIAccessible 为空，ObjectModel 不支持，未做新写入。`desktop_events.py` 使用 OUTOFCONTEXT WinEvent 与独立消息泵，真实发送事务需传入已启动的观察器，仅合成端口可免除。状态接口的 `semantic_candidate_evidence` 是带指纹的历史证据，不自动授权或声明当前兼容性。复现与限制见后台验收报告。

1. 先看 [后台验收](M1_BACKGROUND_ACCEPTANCE.md)：空 UIA 树已有进展，但实际 SetValue 会抢焦点。不得恢复前台点击/按键/剪贴板路径来交差。先寻找并独立验证不改变前台焦点的语义写入入口。
2. 真正读发前补齐：持续核验当前账号、稳定会话/消息标识、己方/发送者识别、群 @ 识别、分页/遗漏检测、附件合法获取、草稿保护、动作前复核及发送回读。候选代码中的会话 runtime ID 不能当作跨重启身份。
3. 用合成控件树及故障替身验证上述契约，再在既定专用账号/会话中受控验收。空适配表只能在完整证据后修改，禁止仅添加版本字符串来“解锁”。
4. 用户在本机配置指定真实 AI 地址和密钥后，先用合成内容验证文本、视觉和转写。接口测试通过不等于识别质量通过；静音音频样本只验证请求受理。
5. 按本轮累计 3 次发送、10 次模型请求上限验证真实私聊、群 @、180 秒摘要、常见多媒体和跨重启记忆；上限保存在 `.local/live-acceptance/quota.sqlite3`，不要换目录或删库重置。未满足时保留 M1 未完成。

## 验证和约束

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m pip check
git diff --check
```

最新完整离线套件 **758 项全部通过（13.414 秒，无跳过）**，日志 `offline-send-action-final-20261026.txt`；在此前 734 项基础上增加 24 项消息保护、发送事务、精确清稿和动作绑定检查。首次完整发现遇到旧研究工具修改 sys.path 导致脚本与同名测试冲突；新入口改为仅显式执行时延迟导入这些工具，原完整命令重跑通过。此前日志与历史证据保留。旧单次凭证和固定发送槽位保持已消耗，后续测试按最新操作者授权另行留痕，不重置或自动重试 unknown。依赖未变，未重新运行 `pip check`。

原生结果必须分开：UIA 私有跨进程 PID 与入口栈、外部控制器正常清理及四项独立 owner 故障恢复通过；原 DbgEng 恢复仍未通过。新 `scripts/probe_debugger_resume_flag.py` 仅在自建无窗口目标的 attached/无硬件断点阶段测试 `DEBUG_ATTACH_INVASIVE_RESUME_PROCESS=0x10`，主线程恢复且心跳稳定两秒，但其他三条原线程及一条新增线程仍暂停，首项失败即停止，未运行原有暂停负例或 DR 场景。全部测试进程已结束，未重新尝试微信。不要把心跳恢复等同全线程恢复；具体证据见[恢复研究](M1_RECOVERY_AND_CLAWBOT.md)。此前 Win32k 接收者过滤及微信 AutoSetFocus 草稿失败保持不变，默认 CI 不运行原生诊断或付费 API。

此沙箱的系统 TEMP 曾出现 PermissionError；项目内临时目录重跑通过，复现可先执行：

```powershell
New-Item -ItemType Directory -Path .local/test-temp -Force | Out-Null
$env:TEMP = (Resolve-Path .local/test-temp).Path
$env:TMP = $env:TEMP
```

不运行上游微信示例，不改剪贴板，不禁用 fail-safe，不通过自动登录、数据库解密、磁盘客户端修改、降级客户端或放宽安全设置补足能力。此次用户允许的进程临时字节是明确、受限的例外，不能扩展为其他补丁授权。保留其他文件改动，不提交 `.local`、账号资料、截图、消息、数据库和密钥。

2026-10-27 当前续作：验收账号 epoch 已真实衔接，报告 console-identity-adoption-84dcd778bacd44b5aba646f6eed29ecd.json（SHA fe464bd68ecaa86869abccfa5e15690afdbfc055217b6f71f02445c381ffe866）。新 binding/account 对应 PID4708/HWND263266，旧 binding 已归档；后台资格仍 false，必须重新核验当前收件人。完整性审计 console-adoption-audit-c16bfa9c4d584bb581d8e5234c3c516a.json（SHA 6e4bfa34789b8cbcbac8c3c5dba69ab17af8af89700465b1f8958b1ad1db4852）确认原8项禁传基线、旧 unknown、额度不变，pending及锁为空。以上取代此前“尚未迁移”的状态。

私聊核验调整为先精确双读联系人微信号，再读草稿/控件，并在各阶段检查人工输入；最终复核资料卡。新增沿同一发送账号已归档 epoch 查询基线元数据的保护，阻止重启后重新采集旧历史，异常链路拒绝。完整离线1114项通过（22.652秒），日志 offline-private-identity-order-20261027.txt，git diff --check通过；新联系人流程未真实验收，PrivateTestScope尚未适配跨epoch的新消息锚点，不得直接运行004或上传历史。M1未完成。本轮启动普通Windows暂停控制台127.0.0.1:8765供查看界面，不开启补丁或验收发送能力；会话83972仍运行。
2026-10-27 后续独立CUA轮：list_windows确认原263266仍存在；对该窗口请求截图被自动审批拒绝（完整主窗可能暴露其他会话，授权不含本次整窗捕获），未取得图像、未点击，不绕过到整树或间接UIA。未读取新聊天/草稿、发送或API。离线新增真正run入口跨epoch禁止重采测试，确认在NativeGate/worker调用前拒绝、无pending/锁；test_contact_profile_probe共12项通过0.734秒，git diff --check通过。此前完整1114项结果保持，但新增此测试后未重跑全套。下一步需要本次定位获准私聊所需的整窗刷新授权或用户提供可定位的目标资料卡；账号本人已验证，不再要求重复开本人卡。


## 2026-10-27 当前优先级：交付 M1 主链路

按用户最新要求，停止扩展诊断支线，优先控制台真实聊天读取、AI自动回复及会话记忆，不以手动辅助取代M1。账号epoch已经衔接，前文“尚未迁移”是旧记录。

新增 wechat_agent_bridge/adapters/bound_console.py：绑定账号及明确会话的原生端口可以直接接入既有 ConsoleService；读取、状态检查、附件与发送各自注入，桌面操作串行。发送仍由应用持久化任务/预算，未知或异常结果保持 unknown；不自动导航、授权或提升任何微信版本的资格。发送端口未提供时明确不可发送。

将既有群聊天消息转换和只读适配从验收脚本移入 adapters/bound_read.py，原 verify_group_memory 导入复用，不复制新规则。未知发送者/@/时间/附件仍未知，不由行文字猜测。该移动后的版本尚未重放真实验收；旧002/003及摘要成功不重放。

新增7项集成测试，覆盖统一端口进入 ConsoleService 的历史展示、新消息一次回复、180秒记忆和重启暂停，以及错误目标、草稿、人工接管、串账号、只读端口和异常发送unknown。第一次测试因重启后未discover违反现有身份缓存规则失败，补充显式discover后通过。完整1122项通过16.350秒，日志 .local/live-acceptance/offline-bound-console-20261027.txt；git diff --check通过。未连接微信、未新增发送或API消耗。

尚未完成：把当前微信原生reader/inspector/helper按当前账号和准确会话绑定到BoundConsoleAdapter，并通过本地控制台实测。普通Windows CLI仍是原WindowsAdapter，不能宣称此次新增端口已经自动接通真实聊天。既有整窗截图拒绝不绕过，补充授权未收到；可独立继续接口接线。M1保持未完成。


2026-10-27 主链路接线续作：WindowsAdapter.bound_group_console 已新增原生只读接入入口，使用已核验 enrolled_account、当前进程指纹/补丁所有权、调用者scope_guard及精确选中RuntimeId；不自动切换聊天。原生 worker 的 bound_group_capture 前后复核目标，只通过独立 AutoSetFocus=False UIA client 读取获准消息列表。collect_labels 移入应用 bound_read 复用，原验收脚本导入同一实现，依赖源码清单已同步。构造后可作为 ConsoleService 的adapter，尚未从CLI/HTTP自动绑定；发送及附件端口仍关闭，不宣称该新路径已真实兼容。

新增5项合成Windows端口接线测试，覆盖仅指定群、未知发送者与@保持未知、私聊不可借用、进程/补丁变化、选中行/草稿变化、撤权先于内容读取。完整1127项通过16.164秒，日志offline-bound-windows-reader-20261027.txt，git diff --check通过。未新增微信操作/发送/API。下一步将当前目标核验结果通过控制台显式绑定到该端口，再接已有发送helper；不复用旧进程的会话RuntimeId。M1未完成。


## 2026-10-27 DeepSeek 与控制台测试群入口

按最新要求选用 DeepSeek，官方配置 https://api.deepseek.com / deepseek-flash（官方文档 https://api-docs.deepseek.com/；JSON与thinking模式说明见其guides/json_mode和guides/thinking_mode）。新增provider_presets及页面“使用DeepSeek”，回复使用JSON对象并显式thinking=disabled，摘要不强制JSON；不回退OpenAI。跨服务切换清除旧key、重置能力并暂停，原上传grant因destination不同失效；同配置重启保留密钥、测试结果和调用计数。音频模型留空，不自动换服务；图片须另行配置及验证。

AcceptanceSession新增启动时明确provider_profile，DeepSeek验收仍使用原quota.sqlite3及campaign；CLI --provider-preset deepseek。原12次已用API保留，按既有额度追加授权新增deepseek-20261027-001共5次，当前上限17；发送3/5及旧unknown保留。

新增 --test-account/--test-group 明确启动范围、/api/bind-current-group 和页面按钮。只绑定当前唯一群标题/选中行，前后元数据一致才安装读取端口；页面不能改写启动范围。绑定后paused且无新grant，用户另存读取权限后进入原消息展示/记忆链路；read_only仍阻止发送。群Header类型检查避免同名私聊借用。相同进程窗口可复用DPAPI原生账号核验，进程创建时间或指纹改变不复用。

完整1136项通过15.948秒（offline-deepseek-console-binding-20261027.txt）；随后补充“缺key在计费前拒绝”，25项相关测试通过0.821秒，JS语法/git diff检查通过。无新依赖。旧服务83972已终态exit1，新服务11744运行于127.0.0.1:8765，启动参数windows/live-acceptance/allow-accessibility-patch/provider-preset deepseek/test-account fichil/test-group 合成测试群/no-browser。已通过真实页面看到DeepSeek配置及paused、累计额度，用户在本机保存key；正在做一次合成文本测试，不上传聊天。

页面当前发现窗口4708:132580及41072:332110，旧绑定263266未在当前发现列表；未猜测其中发送账号或沿用旧RuntimeId。尚未启用临时开关/绑定群/读取当前聊天。完整M1仍未完成。


2026-10-27 DeepSeek真实结果：用户在本机保存密钥后，从已认证控制台点击“测试文本”一次，页面文本能力变为已通过；固定合成JSON回复通过，没有微信聊天上传或发送。随后重启控制台，DeepSeek配置、密钥已保存提示、文本已通过及API13/17、发送3/5均保留，全局paused。当前服务会话49967（11744已终态exit1），127.0.0.1:8765。缺key不扣配额修复已随此重启加载。截图deepseek-console-restart-passed-20261027.png；脱敏结构报告见同目录deepseek-console-live-*.json。本次只证明DeepSeek文本链路与配置/额度跨重启；图像、语音、当前群原生读取及自动回复尚未验收，M1未完成。


## 2026-10-27 当前账号核验与同步试运行

用户最新选择了新的发送账号 wxid_synthetic_archived_owner（资料卡昵称合成测试用户），要求核验后试运行，并明确账号选择不应写死。原 synthetic_owner 固定启动范围不再用于当前服务。新增显式页面群名参数：无命令行固定范围时允许任一已核验账号绑定当前群；仍要求原生群标题、选中行、空稿、精确指纹及两次一致观测。带旧固定参数启动时不能由页面覆盖，新增两项测试通过。

修复窄屏 `.account-help{display:none}` 连带隐藏核验面板：核验模块使用独立 identity-panel 类。界面区分“身份已核验”与版本适配；只读会话运行显示“同步与记忆试运行”，禁用自动回复勾选，覆盖范围使用用户可读描述。

真实操作：通过 computer-use 确认 PID4708/HWND132580 与用户头像一致；控制台等待器双读本人卡成功，身份来源 uia_own_profile_exact_id_user_selected_window，临时开关已恢复后才发布身份。随后通过 computer-use 打开获准群“合成测试群”，控制台重新启用开关并原生绑定当前群。旧服务49967已停止，当前12348使用相同加密库和账本、无固定账号/群参数，127.0.0.1:8765。

当前服务已从已最小化微信读取4条可访问文本，保留发送者、时间和@未知，并标注不完整覆盖，1个非支持行跳过。已保存读取/DeepSeek上传授权、账号取消暂停，点击开始运行。自动发送未授权也未接线，没有新微信发送。读取期间未运行独立原生脚本，所有原生调用由项目控制台接口调度；前台导航属开发辅助，不计作后台导航通过。

完整1138项离线测试通过16.362秒，日志 offline-selectable-account-20261027.txt；JS语法和git diff --check通过。此次未改磁盘客户端/防撤回文件/备份，未重放旧002/003或采集旧私聊8项基线。DeepSeek记忆的本次实际结果另补最终记录；M1仍未完成。


本轮最终实测：DeepSeek 在运行满180秒后完成该新账号/群的首次真实摘要，源范围1–4、水位4，摘要非空且已加密保存。原累计API13/17→14/17，发送保持3/5，本次0发送。脱敏报告 `.local/live-acceptance/selected-account-trial-304490b7622f4ae2a93ec93329d0fd18.json`，不含聊天正文或密钥。当前服务12348仍运行，页面状态“同步与记忆试运行”。临时开关为该服务持有的enabled状态，停止服务时走恢复；不是结束时遗留未知开关。窗口最小化后连续同步正常，无异常队列。此轮未采集独立全程焦点事件证据，不把这些观察扩大为所有后台动作无干扰证明。自动发送、全会话导航、通用消息方向/@、附件和分页仍未开放。M1未完成。


## 2026-10-27 全部已加载记录及发送者字段续作

用户要求读取当前聊天全部记录、发送者备注及微信号。此轮只针对当前已核验账号/群：原生只读检查发现5个可访问消息行，RawView/ControlView无子项；HelpText、FullDescription、ItemType/ItemStatus、Legacy描述/值均为空，LegacyName仅正文，默认动作是“双击”。消息列表为RecyclerListView，ScrollPattern不可用，父视图没有可用滚动兄弟控件。原生“聊天记录”按钮一次Invoke返回后未确认历史页出现、桌面快照发生变化，未再调用，不启用此候选，不声称已获取发送者或全部历史。每次研究开关finally恢复；无新功能补丁或发送。

已实施：collect_labels按50项分批、以列表RuntimeId及消息锚点/正文摘要核对游标，可覆盖当前已加载最多512项；列表变化拒绝，保留旧固定验收message_page行为。新的console_message_page保留所有行，已验证文本类作为正文；其他类仅显示微信标签，content_status=client_label_only且upload_eligible=False，不伪造附件内容。ChatMessage新增独立sender_remark、sender_wechat_id、sender_identity_source；当前原生读取均为空，正文中的wxid或@不用于身份。原始身份不明仍不触发自动回复。

存储增加display_order，以seq维持原模型水位/去重，同时把补读旧记录排在前面；旧密文及seq保持。修复首次基线误把不允许上传的占位行改为可上传，以及没有游标时虚报complete=True。界面新增“读取全部已加载记录”，明确当前已加载数与显示数；未加载的全历史仍不支持。旧记录本地分页不触发补发或扩大AI上传。

完整1145项离线通过17.727秒（offline-history-rows-20261027.txt），随后新增端口冲突2项测试通过0.021秒，JS语法/git diff通过。125行三页无遗漏、游标失效、消息类型保留、备注/微信号分离、旧消息排序/原水位、旧库密文不变、非文本不上传均有合成回归；125行分页尚未在真实客户端有足量记录验收。

真实部署发现8765同时由另一个项目的http.server进程14112监听（本项目的SO_REUSEADDR导致Windows允许双监听），请求进入错误服务并返回501。未关闭或修改其他项目；新增SO_EXCLUSIVEADDRUSE和禁止reuse，微信控制台改至8766。本轮服务12348和28863均已停止，当前24348运行于8766；token以当前启动输出为准。原加密配置/额度继续使用。浏览器原控制台tab2已迁至8766。

原生账号身份使用相同完整指纹的已保存console_identity复用，无需重新开本人卡。已重新绑定当前群、同步5项并核对：4条文本+1条非文本标签、占位行禁止上传、发送者备注/微信号均未获取；点击更早读取明确返回client_history_loading_unsupported。已恢复同步与记忆试运行，API14/17、发送3/5均未新增。脱敏报告history-rows-live-f4f74c8851f64a4591da7b27837511b6.json。此次用户请求尚未全部实现：自动加载更早历史与真实发送者资料关联仍受客户端接口限制；M1未完成。


交付前复核：控制台随后变为paused=true，异常队列为空，API14/17、发送3/5未变；未重新强制恢复运行。保持当前暂停状态，已绑定账号/群及本地记录可查看。查看全历史按钮的后台页面用于独立检查，未覆盖用户正在操作的原页面。

浏览器联调继续观察到新消息/时间标签进入当前群：本地累计行数从5增至7，而当前客户端可访问行数变为4；新标签仍为不可上传的未解析内容。说明已保存旧记录会保留，当前可访问窗口范围会变化，不能以loaded_count代表完整聊天历史。全量按钮按该边界显示覆盖范围。


## 2026-10-27 当前群前缀自动回复已开启，等待新消息实测

用户明确选择仅回复新出现且以“M1测试：”开头的文本。新规则独立于标准私聊/@语义，保持原生发送者/微信号/@未知。LoadedTail要求同列表原末项锚点，新记录才可触发；历史、回填、失去锚点、重启不补发。模型回复统一“【M1 AI】”标记防循环。原生端口接入已审查的按钮helper，每次先mode=0新鲜预检，再FALSE客户端设置/完整回读和持久化任务后调用；焦点/剪贴板变化、暂停/撤权、草稿、对象变化停止。unknown保留并暂停，不自动清稿或重试。

前两次mode=0因父guard误拒自己的helper pending而停止，未写草稿/发送/焦点事件；精确控制者退出、helper持有者及模块不存在的只读核对已完成，原记录先加密归档再清pending。新增持有进程创建时间/指纹/构建核对修复；最新真实预检2.437秒通过、零事件、无Qt发送调用、完整卸载，gate库console_send_preflight/latest有证据。原原生恢复18场景/24夹具同构建仍有效，没有重建helper或修改微信磁盘文件。

当前服务82222运行于8766，38582及24348已终态。已重新精确绑定当前账号wxid_synthetic_archived_owner/PID4708/HWND132580及“合成测试群”；前缀试运行paused=false，当前无异常/待处理任务/新发送。DeepSeek合成文本再次通过，API15/22，发送3/8。新增三个独立发送槽位和5次API额度均有不可变extension审计，累计消耗/旧unknown/002/003成功及8项禁传基线不变。新试运行真实自动发送尚待用户测试消息，不称为后台自动回复验收通过。

完整1160项离线测试通过16.317秒，日志offline-prefix-trial-final-20261027.txt；此前1159项17.728秒保留。覆盖prefix过滤、新旧锚点、标签不触发、防循环、暂停中止预检、目标不匹配、unknown不重试、重启暂停、helper恢复字段及当前owner/PID复用等。JS语法检查通过。见M1_PREFIX_REPLY_TRIAL.md的运行/复现细节；M1仍未完成。


## 2026-10-27 前缀试运行真实发送成功及服务恢复（最新）

已实际发送一条DeepSeek生成回复，后台原生9.906秒、零焦点事件、输入框空稿、一个新精确回显、helper完整卸载。先前同一测试消息的首次预检因人工输入变化失败，确认没有草稿/发送调用后保留原failed和额度，另建trial_retry关联的新任务；旧unknown未重试。原生证据与quota见prefix-reply-live-78428f3dae014a40bcc2d562a7a73bba.json。当前API19/27，发送预留5/11，本轮实际发出1条AI回复；新增预算均有原账本审计，旧002/003及私聊基线未重放。

用户纠正：回复应该综合群前后文。刚加入的reply_target强制聚焦已撤回；最终保留群摘要和近期上下文，prefix仅控制何时触发。普通@不触发，因为用户明确选的是“M1测试：”开头模式；并不声称@语义完成。

更新控制台的重启曾使页面短暂断开，已恢复。最新服务44852于8766运行，82222/35222/95078终态；当前账号/群重新核验绑定，paused=false、无异常、无helper pending，原浏览器tab2恢复。开启仅进行范围及同构建恢复证据验证并armed，避免因用户操作电脑导致开启也一直等空闲；每次实际发送仍强制完整mode=0预检。无后台发送保护被移除。API新增5次/发送新增3槽的审计已保留；尚余4个本群试运行发送槽位，触达槽位/API限制会暂停。

最终完整1162项通过20.384秒，日志offline-prefix-trial-armed-20261027.txt；73/74项针对性与中间1161完整日志均保留。详细行为/恢复经过/复现见M1_PREFIX_REPLY_TRIAL.md。截图prefix-trial-running-20261027.png。M1整体未完成，不把这一次辅助恢复后的前缀回复扩展为完整无人值守或标准群@通过。


## 2026-10-27 全部新文字触发（最新）

用户明确要求“所有消息都要触发”，已将当前绑定群切换为all_new_text，不要求M1测试前缀或@；范围仍只有当前已核验账号和群。连续消息继续3秒合并，模型读取群摘要和近期上下文，可以生成回复或沉默。历史/回填/基线、已知己方消息、AI标记回显和已发送文本摘要不触发。当前客户端只有文本内容被真实读取；未解析的图片/语音/文件标签及时间分隔行不触发，不用标签冒充附件内容。

新增服务模式参数与HTTP trigger_mode、控制台“开启所有新文字自动回复”按钮和状态标签；原prefix模式保留可切换，默认启动仍需显式--reply-trial，--reply-trigger all_new_text指定本次策略。发送前写入按账号+会话+正文哈希的outbound_echo最小记录，再进入原持久化发送事务；即使发送者仍未知/回显产生新行ID，也阻止AI自循环。未知写入继续暂停且不重试；scope、草稿、焦点及逐次helper预检未放宽。

当前服务10829运行于8766（44852已停止），精确账号/群重新核验并开启all_new_text，页面已恢复。当前窗口4708:132580，身份/指纹与前次相同；开启前历史重新基线，不重放以前成功回复。发送额度新增all-new-text-20261027-001..004四个独立槽，API新增5次均保留累计审计。切换时API21/32、发送5/15，无新增发送；开关由当前服务持有，helper无pending。

完整1165项通过20.843秒（offline-all-new-text-20261027.txt）。首次77项针对性测试的旧HTTP来源检查出现一次Windows10053连接中止；后续完整套件和该HTTP单项复测均通过，未放宽来源/CSRF检查。新测试覆盖普通文本/@触发、三条连续消息单任务、自己的未知发送者回显不再触发、持久化发送摘要、模式切换历史不补发；JS语法及git diff检查通过，无新增依赖。M1整体仍未完成，附件/真实发送者/标准@语义及全会话能力不因此获得支持声明。

最终运行证据：`.local/live-acceptance/all-new-text-live-2040bc447766421aab7fa7f40c4d0445.json`，running=true、trigger_mode=all_new_text、无待处理任务；当前API21/32、发送5/15。期间一次human_takeover仅暂停当次桌面读取，当前全局未暂停。截图all-new-text-running-20261027.png。此次验证了模式已在真实控制台开启，尚无开启后新增普通消息的实际AI发送结果，不把配置切换声称为新增发送验收。


## 2026-10-27 人工并发与独立群窗口（最新）

已拆分 read/model/send 调度，读取只验证桌面锁定/进程/账号/会话，不再用人工输入、前台活动、剪贴板或空稿作为读取前提；待发回复加密保存，通道未就绪不重复调用模型、不预留发送额度。unknown 保持禁止重试，但读取/摘要继续。暂停及重启丢弃旧待发，原累计账本、旧 unknown、002/003 和私聊禁传基线保持。

computer-use 已从已核验群打开独立窗 HWND21892176；双窗分别保留 A/B 合成草稿，清除 A 不影响 B，最终全部清理。独立窗最小化后原生同步 7 项通过；主窗口前台保留 C 草稿时，后台读取仍通过，C 与输入焦点读后保持，C 已清理。未新增微信发送、未调用或修改 helper。新 /api/read-channel 只读绑定依赖明确根 AutomationId/精确标题/源指纹/稳定节点；根 Window.Name 不能直接当群名。

服务已恢复 all_new_text 的读取与 AI 处理，独立发送始终 send_ready=false，不退回主输入框。真实 180 秒摘要更新水位40、起点30并加密保存；当时API23/32、发送5/15。当前生产发送候选不能继承主窗ChatSessionCell selected检查的旧证据；下一步需独立根/按钮有界关联及新构建恢复，详见 M1_CONCURRENT_USE.md。新窗口出现不等于独立发送或中文输入法并发通过。

真实脱敏报告 concurrent-live-20261027.json，精确选择保存在 detached-read-selection.dpapi；重启仍需重新绑定与明确恢复，不能覆盖原账本。旧服务已停止；最终进程句柄、测试结果和界面证明见本条后续补充。

最终部署：服务91196运行于127.0.0.1:8766，旧10829/50668/2240/92116均终态；首次Ctrl+C后原字节恢复记录由下一服务按原指纹恢复，后续部署均先通过控制台恢复原值再停止，最终临时开关由服务91196正常持有。相同账号和群重新绑定，独立窗读取7项，paused=false、无异常、发送仍未就绪；API23/32、发送5/15。完整1181项通过17.231秒（offline-concurrent-release-final-20261027.txt），JS语法/git diff检查通过。前次1181测试唯一失败是测试夹具闭包引用旧mock，修正测试注入后全量通过，没有放宽锁屏检测。未重建helper、未新增发送。


## 2026-10-27 恢复旧主窗口自动回复（最新）

按用户“恢复旧窗口，让程序能继续运行”的明确选择，新增显式 `--reply-send-mode main-window`，恢复原主窗口 ConsoleSendTrial 和既有按钮限定 helper。默认 isolated 模式仍不会隐式退回主窗口。主窗口发送不宣称草稿隔离或人工同时打字能力；读取、模型、发送三个调度线程保持分离。

当前账号 wxid_synthetic_archived_owner、群“合成测试群”、PID4708/HWND132580 的原指纹与身份记录已重新核验。微信4.1.15.56，Windows10.0.26300，DLL SHA256 `932dd6700f22e32faeb9b0cc9581a1f601e60ace151067680d3f6596496fab99`。服务91196先暂停并恢复临时字节后退出；新服务19129监听127.0.0.1:8766，保持运行，DeepSeek与all_new_text开启；未绑定独立只读窗。

主窗口空稿、最小化后，新鲜mode=0只读预检4.078秒通过：metadata/selected/gui_thread_match均通过，零焦点事件，未写草稿、未调用Qt发送，helper完全卸载，工作进程正常退出。证据：`.local/live-acceptance/main-window-preflight-20261027.json`。没有重建helper，原同构建恢复证据仍验证通过，没有修改磁盘DLL、防撤回文件或备份。

已有草稿或微信前台导致的写入前等待保留加密ready回复，不claim、不占发送额度、不重复调用模型；可用后再发。开始写入后的unknown仍禁止重试。主窗口切到其他聊天会停止当前目标读取/发送，不会读取新聊天。旧发送器的输入空闲、焦点/剪贴板检查仍保留，尚不满足“持续人工打字时同时发送”。

验证：新增6项回归，完整1187项通过16.688秒；JS语法及git diff检查通过。日志`offline-main-restored-20261027.txt`。当前运行证据`main-window-running-20261027.json`及同名PNG：paused=false、send_ready=true、send_mode=main-window、同步成功、没有当前错误。恢复时累计API25/32、发送预留5/15，原账本/记忆/旧unknown/私聊禁传基线均保留，旧待发不补发。本轮仅验证主窗口通道恢复与预检，没有新增真实回复回显；下一条新文字由当前服务自动处理，不重放旧测试消息。M1整体仍未完成。

复现（从项目目录；必须先停止当前服务，不能并开）：

```powershell
.\.venv\Scripts\python.exe -B -X utf8 -m wechat_agent_bridge console --adapter windows --live-acceptance --allow-accessibility-patch --provider-preset deepseek --port 8766 --no-browser --reply-trial --reply-trigger all_new_text --hook-recovery-evidence .local/live-acceptance/thread-hook-synthetic-0babe13d34dc4b2a84525c222b9ad5fb.json --trial-send-slot extra:prefix-trial-20261027-001 --trial-send-slot extra:prefix-trial-20261027-002 --trial-send-slot extra:prefix-trial-20261027-003 --trial-send-slot extra:prefix-trial-20261027-004 --trial-send-slot extra:prefix-trial-20261027-005 --trial-send-slot extra:prefix-trial-20261027-006 --trial-send-slot extra:all-new-text-20261027-001 --trial-send-slot extra:all-new-text-20261027-002 --trial-send-slot extra:all-new-text-20261027-003 --trial-send-slot extra:all-new-text-20261027-004 --reply-send-mode main-window
```

重启默认暂停，按原流程绑定同一核验窗口及当前群、授权读取和DeepSeek上传，再开启“所有新文字自动回复”；不要接入独立读窗。需要只读验证时，已armed且暂停的会话可调用认证接口`POST /api/send-preflight`，其不接受正文、不写输入、不消耗API或发送额度。当前服务已经完成这些步骤，无需重复执行。


## 2026-10-27 取消累计测试额度并恢复持续运行（最新）

用户报告停止回复，实际服务仍存活且通道就绪，paused=true，累计API32/32已耗尽。恢复旧窗口后新产生3条ui_confirmed，另1次发送前failed/attempted=false，发送预留累计9/15；不是客户端崩溃，也未收到DeepSeek余额不足。原tick按累计验收额度自动暂停，并让随后“paused”错误遮住根因。

用户随后明确要求“取消测试额度，全面开放”。已增加显式 `--no-test-budget`：取消API累计验收上限和固定发送槽位，持续发送改为每个请求独立continuous槽；原reservations、extensions、unknown、禁传私聊基线、记忆和去重全部保留。不新增额度、不清空计数、不重试未知发送；此开关不会新增账号或会话授权。普通配置限频仍为120次/小时、1000次/日、回复间隔10秒，可在AI配置页调整。

修复pause_reason并在页面直接展示；预算用尽不再只显示笼统paused。状态增加test_budget_enforced和当前试运行可用槽位数量，关闭测试上限时页面显示“累计测试额度已取消”及累计消耗；旧32/15仅作历史账本值，不再参与本次运行的暂停或预约判断。默认启动不传该开关仍保留受限测试行为，重启仍暂停并重新核验绑定。

完整1191项离线测试通过16.359秒，日志`offline-no-test-budget-20261027.txt`；45项针对性、JS语法/git diff通过。新增覆盖预算根因不被后续paused覆盖、当前槽位计数、超过旧上限仍运行、累计计数跨连接保持、scope拒绝、重复/旧unknown请求拒绝、重启暂停。真实DeepSeek合成文本请求成功将累计API32→33，恢复运行仍paused=false、send_ready=true、test_budget_enforced=false；本轮修复未额外发送固定测试文本。

部署：旧服务19129通过HTTP恢复临时开关后退出，新服务76237运行于127.0.0.1:8766；同一核验账号wxid_synthetic_archived_owner、群“合成测试群”、PID4708/HWND132580重新绑定。窗口指纹、4.1.15.56/DLL SHA256、原helper构建均未变；未修改客户端磁盘文件。恢复时API33、发送预留9，页面已重新选中当前群、自动回复all_new_text运行，无当前错误。精确证据`no-test-budget-authority-20261027.json`、`no-test-budget-running-20261027.json`及同名PNG均在.local/live-acceptance。后续额度应读取实时账本，不能拿本条数字覆盖；M1完整验收仍未完成。

当前启动命令（已有服务在运行，勿重复启动）：

```powershell
.\.venv\Scripts\python.exe -B -X utf8 -m wechat_agent_bridge console --adapter windows --live-acceptance --allow-accessibility-patch --provider-preset deepseek --port 8766 --no-browser --reply-trial --reply-trigger all_new_text --hook-recovery-evidence .local/live-acceptance/thread-hook-synthetic-0babe13d34dc4b2a84525c222b9ad5fb.json --reply-send-mode main-window --no-test-budget
```


## 2026-10-27 全私聊范围与无前缀回复（最新，部分交付）

用户确认当前账号全部私聊、后台切换优先、只上传接入后的新消息，同时取消回复【M1 AI】前缀。新回复统一自然正文，模型复述旧前缀时移除；保留旧标记回显过滤，群及普通私聊触发均检查持久化outbound_echo，发送事务与unknown规则不变。

新增POST /api/reply-scope（account/all_private/history=new_only），配置持久化、修改后暂停，页面开关完成后恢复；重启仍暂停。每个新私聊先建立不上传/不触发的基线，旧缓存按序号水位隔离，旧摘要不可被带入新授权上下文，按账号及会话隔离。Private discovery端口仅接受准确微信号、private类型及身份来源均已核验的目标，加入当前账户动态范围；同名或运行期节点不能替代身份。关闭范围使私聊读发权限失效。默认mock及现有群行为保留；取消累计额度继续有效。

真实Windows新增独立FALSE UIA只读会话目录，核对窗口/PID/单一列表/唯一节点，最多512行；只取AutomationId里的显示名，不读含聊天预览的Name、不点击、不读取私聊正文。30秒缓存避免每个同步周期重复扫描。当前发现5项，只有显示名和RuntimeId，kind和微信号仍未核验，返回ready=0/state=blocked/private_identity_direction_navigation_unverified，不将候选加入可读发私聊。发现失败单独记录，不阻断既有群读取。

本轮没有实现或验证真实私聊后台导航、消息方向解析、联系人微信号自动核验与私聊helper发送；无前缀/隐私/多私聊调度通过的是合成链路。不能把范围偏好all_private=true当作真实私聊运行。后续需要在当前实例建立准确联系人身份与当前选中行的绑定，独立验证后台导航、方向、私聊发送mode=0/回显/卸载；旧实例的导航unknown不可重试，也不能直接将群helper能力用于私聊。没有伪造private身份或绕过后台要求。

完整1200项通过16.483秒，83项针对性通过；JS语法/git diff通过。新增9项测试覆盖新私聊加入与同名隔离、历史不上传、旧摘要不用、own/unknown不触发、无前缀回显不循环、撤销/重启及目录不读取消息预览。日志offline-private-scope-20261027.txt；真实脱敏报告private-scope-live-20261027.json。

部署旧服务76237先暂停、恢复字节后退出；新服务38839使用原no-test-budget/main-window启动命令，重新核验原账号wxid_synthetic_archived_owner、群合成测试群、PID4708/HWND132580及原指纹，私聊偏好已保存，群all_new_text/DeepSeek继续运行。累计API40、发送预留14，无当前错误，未重置账本、unknown、私聊禁传基线或记忆。精确版本/OS/DLL/helper沿前条，未新增客户端补丁或修改磁盘DLL。未额外发固定测试消息；本轮尚无部署后无前缀新回复回显。M1及全部私聊真实验收均未完成。

## 2026-10-27 私聊真实身份与导航续验（最新）

同一服务38839未重启。已用computer-use打开当前账号的测试私聊和联系人卡，项目原生准确微信号核验通过，身份/会话行/标题元数据加密保存；没有上传历史、调用模型或发送。双方消息公开UIA字段不能区分方向，未放宽own=None触发规则。

三个独立公共动作Invoke、Legacy默认双击、Select均没有导航回读，零焦点事件，原群不变；每个unknown独立持久化，未重试。Select新增2秒有界观察，前两次即时失败不能夸大为永远不执行。新窗口消息研究候选通过自建隐藏窗口投递/超时/清理；真实候选因目标仍前台在动作前停止，没有对微信投递。computer-use在尝试激活本地控制台参考窗口时，因无法可靠核定Chrome当前URL而终止本轮，不绕过。

已恢复原群并通过HTTP resume确认paused=false。主窗口当前非最小化/前台，旧主窗口发送器可能等待它离开前台；读取/模型继续。私聊ready=0，全部私聊及M1仍未完成。保留累积额度/去重/unknown/8项旧禁传基线；未修改客户端磁盘文件或helper构建。完整1207测试16.659秒通过；最终小幅整理后的针对性7项通过，diff检查通过。详细证据、新候选命令和下一独立CUA轮恢复步骤见 [M1_PRIVATE_NAVIGATION_RUNTIME.md](M1_PRIVATE_NAVIGATION_RUNTIME.md)。
