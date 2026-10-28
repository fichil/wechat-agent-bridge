# M1 私聊行选择与当前恢复状态

2026-10-27 当前：**原实例已退出，旧导航 pending／运行锁已按退出证据解除；原 unknown 未改、不得重试。** 新窗口 PID 4708／HWND 263266 的完整指纹已核验，但尚未核验新实例的发送账号。下一独立 CUA 打开自己的资料卡，再分轮原生重新绑定账号及准确联系人；下方基于旧 HWND 的 --inspect-after-attempt 不再适用。完整 **966 项通过，11.435 秒，无跳过**。本阶段没有历史读取、发送、API 或客户端修改。

## 原实例退出后的恢复

新增 scripts/recover_exited_navigation.py，默认 disabled；--inspect-exited-selection 仅检查，--recover-exited-selection 才持久化退出恢复。只允许既有已核验发送账号、原 terminal_unknown／worker_exited／patch_restored 和原锁 token；其他未恢复项、身份变化、权限错误、原实例仍存活、缺锁且无持久化证据均拒绝。进程读取仅使用 QUERY_LIMITED_INFORMATION 和 SYNCHRONIZE，无 VM／写入权限；参考 [Windows 进程权限](https://learn.microsoft.com/en-us/windows/win32/procthread/process-security-and-access-rights)及[等待返回值](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-waitforsingleobject)。PID 不存在的结论还要求成功完整枚举，而不是由任意 OpenProcess 失败推断。

原 PID 17416 已不存在，真实只读两次均为错误 87＋完整枚举未包含该 PID。原窗口也不存在。恢复先独立保存 :process-exit-observed，再释放原 token 锁；事务内只追加 :process-exit-retired 并清同一 pending，原 request 永远 unknown。若释放锁或提交失败，pending 保持，可从既有观察继续；异主锁不删除。18 项新增测试含真实 SQLite abort、观察存储失败、释放失败、中断、错误账号、状态变化、PID 复用及旧实例存活。

自建无窗口 Python 子进程的真实进程句柄检查：存活时 wait=258 且正确拒绝；自然退出后创建身份不变、wait=0，清理正常。这个结果只校准恢复 API，不证明微信读写。真实恢复后内容盲审计比较全部加密 records：37→39，仅原 pending 改变、两条退出记录新增；未知尝试、绑定及私聊基线密文、额度文件哈希未变。原 8 项历史继续独立禁传，没有解密／重采历史。

普通沙箱曾返回新旧窗口均不存在，不能作为全局退出依据；独立交互桌面查询确认新窗存在。新完整指纹为 PID 4708、HWND 263266、created=134375347512983186、微信 4.1.15.56、DLL SHA-256 932dd6700f22e32faeb9b0cc9581a1f601e60ace151067680d3f6596496fab99、module_base=140729299959808、module_size=204795904、flag_rva=188336600。字节 0、未锁屏／非前台，Windows 10.0.26300、Python 3.12.10；没有启用开关或读取控件。旧账号绑定保留为历史，当前实例身份未授予。

以下均位于 .local/live-acceptance/：

| 证据 | SHA-256 |
| --- | --- |
| owned-process-lifecycle-7c0ffe3d3d304ff5a5203f5b8ba1da40.json | 3eb6bd39e8d831b296045283c46c047a40ab6a9b190a8f8c2f5342d00dc616b3 |
| exited-navigation-dd00b6b7ffbe40c1abbab33d2bb93fa9.json | 91af88423d1c035c39639bab84023a420d70b2d7228c473e6f26a4e619f8935b |
| exited-navigation-7386317c70e449c2868b29753d5f00de.json | 9cf8a211f1e69cc2ad68a92d6db35ede6d758d88d9b17655eca4640de5f8d095 |
| exited-navigation-integrity-before-5dac695df40d4a28864dec0c02605302.json | 886aa06b00687168f54969b784339146253f1ada164d47da181c12133276e99e |
| exited-navigation-integrity-after-f6b47a3c3ca14c6caeb2e9494fef5f6d.json | 1bf4aab133728d22516877032480c705843133ffbc9038111a85e0f496f3a0f1 |
| replacement-window-fingerprint-ee693d583b8d446d90efbe72218dea4a.json | c5803598ec9618d8dc1388c9b15d8c222230e5b60cd9eb2a614c7e8157a8b20e |
| offline-exited-navigation-20261027.txt | 5b5dd3f0f3360d31f974b45f516f97e6cc279b59e84e077018c54507faded115 |

源码 recover_exited_navigation.py SHA-256 a537cdc3d80e665cac76351d6b8798fb706abad2b4420e55c1a1ab495ab540fd；test_exited_navigation.py=4bf1522363e84bef17011100ad5cd101d1d2e9550d6c5839320d2467ac7432c0。无新依赖或上游代码。已消费的真实恢复命令如下，**不要为验证而重跑，pending 已清会被拒绝**：

```powershell
.\.venv\Scripts\python.exe -B -X utf8 scripts/recover_exited_navigation.py --recover-exited-selection --account synthetic_owner
```

当前下一步必须新实例自身资料卡确认发送账号，重绑仅账号不读取私聊草稿／历史；之后精确联系人当前资料卡核验、新输入／行／标题定位和后台 mode=0 分别通过才可执行 004。原禁传基线不可转换成新实例上传权限。下面“原 pending／锁仍在”“等待操作者恢复”及原窗口恢复命令均为历史。

2026-10-27 最新：收到操作者准确私聊已打开的确认后，独立 CUA 开发轮已在指定 synthetic_owner 私聊中打开对方资料卡，视觉核对完整微信号与获准私聊一致，卡保持展开。当前 SDK 返回 HWND **263266**，不同于原绑定 67678，尚未原生核对进程身份。没有输入／发送／Provider 调用，没有保存截图或重采历史，没有项目 UIA／补丁／helper，原 unknown／pending／锁保持。收据 development-cua-private-restored-4a1af03a17bd432caaf3e836e4d7eae8.json，SHA-256 075f008368725d1b31a200e3219c73d1077561bb9fb731c9612acc7f59b41d21。

下一原生轮先核验旧进程是否存活及新窗口归属，**不得直接套用下方旧窗口的恢复命令**。新窗口不继承旧 RuntimeId 或资格；原实例若已退出，需独立审计退出恢复、保留旧 unknown 和锁关联证据，再做当前发送账号及精确私聊绑定。8 项禁传基线不覆盖／重采，004 仍未执行。此次只是开发前台导航和视觉身份，不是后台验收。以下“等待操作者回复”及旧指纹均为此前记录。

当前更新：新增当前资料卡精确 ID 恢复核验和草稿读取顺序修复；完整 **948 项通过，17.025 秒，无跳过**。一次真实只读复核仍为 private_selection_still_unconfirmed，已在资料卡／草稿读取前停止。原 unknown／pending／运行锁及 8 项禁止上传历史保留，API **10/12**、发送 **3/5**。准确私聊导航的同一阻塞已跨至少三轮，现需操作者按已有请求恢复一次准确私聊界面；不是重新请求常规发送授权，不能绕过 CUA 同名项点击／整窗捕获的自动审批拒绝。下文旧 897／904 项、API 8/10 等均为历史阶段。

## 当前恢复修复和边界

WindowsUIA 的输入标签原先使用 pywinauto.window_text()，可能进入 TextPattern.GetText(-1)，在核验会话前读取草稿。现只读 UIA Name；无法取得时按既有精确标题／选中行路径核对，不用草稿充当标签。新增合成测试证明未知会话的 TextPattern 与 get_value 均不被调用。**此新 Name 路径尚未在正确真实目标中通过读取验收。**

只读 unknown 恢复不能仅凭旧 RuntimeId、相同昵称或选中状态清锁。现在要求原私聊标题和原行先匹配，再核验当前打开的资料卡精确微信号；通过后才检查空稿，期间多次核对行／标题／资料卡及父进程 checkpoint。错误 ID、资料卡替换、原行或标题变化、人工接管都失败；没有重新抓取历史或更新发送账号／联系人授权／基线。

父进程在任何原生访问前核对原 terminal_unknown 报告和锁 token，运行中反复核对原日志、pending、来源和锁。只有完整退出／字节恢复、源码未变、零焦点事件以及精确 ID 证明都通过，才将新观察持久化为原 request 的 :recovery-observed 独立记录，然后清 pending／释放同一把锁；原 request 保持 unknown，不重放 Select。短暂焦点往返也不能作为成功；恢复失败保留原锁。协调器合成测试实际验证了持久化顺序、错误锁在 NativeGate 前拒绝、缺卡／焦点／恢复故障不解锁，历史和旧 unknown 不覆盖。

本轮没有 computer-use 或模型调用；只执行一次显式原生只读复核。0.922 秒到行选中检查即停止，未选择、激活、输入、发消息或读草稿，零事件、worker 退出、字节恢复、清理无错误。**新的精确资料卡成功分支仅通过合成测试，未获真实通过。** 随后独立审计确认原完整进程指纹匹配、字节 0、helper 缺席；仅 development_navigation pending 仍在，其余恢复项为空，8 项历史仍禁止上传。

| 本机证据（.local/live-acceptance/） | SHA-256 |
| --- | --- |
| development-private-selection-046af369800d4e5ea1f4cbcbc7c9e095.json | 8431d33c9e71a2eed74a530ba4a089294c6c97f3e176df24e8e87b30d63b7674 |
| private-recovery-final-audit-70669eaf790a45b7b8e4c47ae81168c8.json | 228ed7e04bbb14996bad3cc02f2b13d7e1a57438041f8779b8a7f03845ab68bd |
| offline-private-recovery-identity-20261027.txt | 86de83210ce20f427b4401235f1055c80fe08db209bd7e776e28f894d0d15403 |

最终源码：development_private_selection.py=22e62eff92c3f21baf7e007671a0564c059a3766d90c36fa51bef00e6ae99a88；contact_profile_identity.py=72f581dac78834d9f8eb177e5bc905237e663f990312e947a54fedc81ba30787；windows_uia.py=99c9f8503b42efaa5b653d4e3aa56f93c408ec191fa10a4a0c9f9e564f44628e。真实只读报告保存执行时的版本，之后仅补齐成功恢复的观察落盘逻辑，没有为此重复运行客户端。旧自建 Select 资格因源码变化不能用于新动作，旧 case 也不重开。无新增依赖。

## 阻塞审计

同一条件在至少三轮续作中未解除：精确查询没有暴露身份，固定导航 Invoke 没有业务回读；下一轮 CUA 只返回原主窗、没有独立搜索弹窗；本轮真实行检查仍未选中。旧同名点击及完整主窗捕获的自动审批拒绝仍有效，SDK 没有局部捕获入口，不能换工具或盲坐标重试。现有必需真实步骤依赖准确目标，不能用更多 mock 或单次 JPEG 成功替代；既有 ClawBot 纯数据实验也没有原群聊／个人账号历史能力的验收证据。

操作者按已发出的请求打开并核对准确私聊后，独立 computer-use 轮仅在已确认目标中打开联系人资料卡；再独立原生轮执行下文 --inspect-after-attempt。资料卡必须当前可读，原行／标题／空稿及恢复条件完整匹配后才清锁；若卡已关闭或对象重建则保持停止，不自动猜测。此前搜索与导航实测详见[搜索记录](M1_SEARCH_READONLY.md)。

2026-10-27 01:05：**聊天控件已恢复，但实际当前标题／选中会话不匹配获准目标。公共 Select 在自建控件通过，在微信一次实测未获得选中回读；unknown 保留，禁止重试或直接发送。M1 未完成。**

## 已验证事实

同一绑定进程 PID 17416、HWND 67678、创建身份 134374491831767460，微信 4.1.15.56，Windows 10.0.26300，DLL SHA-256 `932dd6700f22e32faeb9b0cc9581a1f601e60ace151067680d3f6596496fab99`。只读预检确认窗口启用、无挂起提示、无所属弹窗阻塞、非前台、未锁屏、目标和工作前台均属于原桌面 2 GUID；没有修改这些状态。

标准最小化后旧输入 RuntimeId 可找到，失败从 input_ambiguous 变为 target_mismatch。随后根据真实 CUA 最小化收据增加独立的 `--prepare-after-cua-minimize`：内容 SHA-256 决定一次事务键，复制或重命名收据不重开旧机会，旧 hidden／normal case 保留。自建两状态恢复检查通过后，真实普通显示 0.453 秒、零事件。该新窗口 case 也已消费，不再重放。

增加身份失败的脱敏诊断后，观察到群／私聊标题类数 `[1,0]`；两个已授权目标的行各找到 1 个，但均未选中，标题匹配均 false。不能由先前截图解释为当前私聊，也不能归因于 CUA、客户端或人工操作，原因尚未证明。只比较目标元数据，没有读取当前非测试会话正文／草稿。

## 开发候选和合成门槛

`scripts/development_private_selection.py` 默认关闭，仅允许原准确私聊证明、发送账号绑定、不可覆盖历史基线和既有 CUA 准备收据。只绑定原进程中同一 RuntimeId、AutomationId=`session_item_<已核验显示名>`、类 ChatSessionCell 和 ListItem 的行，禁止昵称单独授权。它调用公开 [IUIAutomationSelectionItemPattern.Select](https://learn.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationselectionitempattern-select)，不调用内部函数或加载 helper。接口文档只说明选择语义，不证明微信实现有效。

独立 `BackgroundUIAClient` 请求 AutoSetFocus=false，不沿用默认客户端元素。新 SelectionItem 的内部关联布局未核验，不套用已有 Value／Invoke 的静态布局结论。该候选始终 background_qualified=false，开发前台影响沿既有授权；生产读发／选择表继续关闭。

选择前持久化 request、unknown、原锁 token，再调用一次 Select。调用后重新核对原行、私聊标题、原输入和空草稿；不读取切换前的未知会话内容，不输入或清理任何草稿，不调用发送。账号／进程／来源变化、输入时钟、剪贴板序号、锁屏、超时或人工接管立即停止。当前实际 request：`development-select-private-648febe768abcde1988ec4996bd794413c9fd120a3868c9f6eabc28a18af1f11`。

自建 `probe_private_selection.py` 复用未修改的私有桌面 GUI／COM 生命周期，将一个自建 Edit 换为标准 ListBox，MTA 线程只选择第二个固定合成项；GUI 线程独立用 LB_GETCURSEL 验证索引 1。原 Edit 的文本回读不适用，没有伪造文本相等。实测 Select 1 次、选中回读通过、局部焦点和工作桌面不变；MTA、GUI、COM、窗口和私有桌面完整清理，没有强制退出。这个结果没有扩大成微信后台兼容结论。没有新增依赖、上游代码或许可要求，也没有重建原生发送 helper。

## 真实一次结果与保护

真实 Select 0.828 秒返回后，CurrentIsSelected 仍未确认，结果 unknown；零焦点事件，worker 退出、字节恢复、来源未变、清理错误为空。随后仅运行只读 `--inspect-after-attempt`，仍为 private_selection_still_unconfirmed，未再次调用 Select。没有写入文本、发送、上传聊天、新 API 或发送占额。

最终原指纹匹配、字节 0、helper 不在进程，patch／thread_hook／draft_probe／window_restore pending 为空。**development_navigation/pending=terminal_unknown，匹配 token 的 autofocus-draft.lock 仍在，原 unknown 尝试保留。** 普通发送补齐 navigation pending 守卫，合成协调器测试证明拒绝发生在 NativeGate／预算操作之前；群记忆入口也拒绝该 pending。API **8/10**、发送 **3/5**，private-004 未预留，额度没有重置或追加。

## 检查与证据

完整 **897 项通过，15.506 秒，无跳过**，默认测试只用合成数据／环回接口。新增覆盖收据身份／未输入／预览清理、复制改名不能重开 case、Name 与 Text 的差异仅做诊断、不读取未核验草稿、一次 Select、来源和行变化、已有选中但目标不明、超时／无回读／人工接管／已有私聊草稿、旧 unknown 不重试、源和恢复证据不匹配及导航 pending 阻断发送。两个新 CLI 默认 disabled，diff 检查通过。

均为 `.local/live-acceptance/` 下的本机脱敏报告，不保存截图文件或消息正文：

| 证据 | SHA-256 |
| --- | --- |
| `private-post-cua-precheck-1c99bf6bed3a446b90a3d60b677a158f.json` | `4c72bd68391f9f8ba260d376ef4cfdf3d4c211cf9170cb0df26ef0837b5e73cf` |
| `development-window-restore-061202cff49344a18cd2de355286697e.json` | `9c0e8c49f3dc1c8eb9629fa1ada0fb44f41bfba1ca141b778910abcdfdcf11ea` |
| `bound-thread-hook-b11aba80a0c04ee98ceb13029ed5e4b3.json` | `2b06b55ada1efb162fda67dd76bcc6113068734fd50bc123d5349652217e75c1` |
| `private-selection-synthetic-41447c23006c4a398aecc615d92dd467.json` | `abd211a99f409a1a6fde241362892243007e4b9a4611463751ca20814d0a0034` |
| `development-private-selection-9b648d43ad654465aceef35bb17c4d2f.json` | `13f5a4bc4f6c6cc7b9a41fc3e550bc9c9d95388d5fc8084046b82f4f9483eee3` |
| `development-private-selection-66ef4873dea444b99865126e94ae807b.json` | `b990719357e9487578186296cf811c36e2fedfeb88ee0882f1938f69bace51da` |
| `private-selection-recovery-audit-b3bb31ad9755428eb8f6664778fec5eb.json` | `b60d39c176801d7ec5bcb99eb9d58ede828792afe721191acf1790655b84c0cd` |
| `offline-private-selection-guard-20261027.txt` | `feaf2a5c94a6ebf6a34d3c5550571532f74c1d1a28677c2ae533ae4ff2c606bb` |

## 当前可复现下一步

当前先等待已请求的准确私聊界面恢复；不能继续用未核验主窗截图、旧坐标或点击同名项。操作者恢复后，重新获取 SDK 实际返回的原窗口，仅在已确认目标中打开联系人资料卡；准确 ID 为 wxid_synthetic_archived_owner、显示名 synthetic_owner，发送账号为 synthetic_owner。不启动／登录、不覆盖草稿；开发前台辅助不算后台兼容，computer-use 与直接 UIA 分轮。

随后独立原生轮只执行下面只读命令；不得再用 select-verified-private：

```powershell
.\.venv\Scripts\python.exe -B -X utf8 scripts/development_private_selection.py `
  --inspect-after-attempt --development-foreground --allow-accessibility-patch `
  --account synthetic_owner --private-id wxid_synthetic_archived_owner --private-label synthetic_owner `
  --target-evidence .local/live-acceptance/contact-profile-def9f90432b24727809c12bdce1a1f0f.json `
  --cua-ready-evidence .local/live-acceptance/development-cua-window-ready-3e9698900ced4f7ca4458d05a2f7504f.json `
  --context-evidence .local/live-acceptance/uia-node-context-faea0f12f99e4c6cbb69960274a76e4c.json
```

只有当前资料卡精确 ID、原行、私聊标题、空稿及全部无干扰恢复条件通过，原锁 token 匹配，且新观察先落盘后，才可清理 pending／运行锁，**不改写旧 unknown，不重放 Select**。对象重建则停止，不能用昵称或旧历史基线授权新行。正常恢复后才按[私聊后台验收](M1_PRIVATE_BACKGROUND_ACCEPTANCE.md)做新的 mode=0，完整通过后发固定 004；不重跑 002／003、旧发送 unknown 或已消费记忆测试。
2026-10-27 后续独立CUA轮：list_windows确认原263266仍存在；对该窗口请求截图被自动审批拒绝（完整主窗可能暴露其他会话，授权不含本次整窗捕获），未取得图像、未点击，不绕过到整树或间接UIA。未读取新聊天/草稿、发送或API。离线新增真正run入口跨epoch禁止重采测试，确认在NativeGate/worker调用前拒绝、无pending/锁；test_contact_profile_probe共12项通过0.734秒，git diff --check通过。此前完整1114项结果保持，但新增此测试后未重跑全套。下一步需要本次定位获准私聊所需的整窗刷新授权或用户提供可定位的目标资料卡；账号本人已验证，不再要求重复开本人卡。
