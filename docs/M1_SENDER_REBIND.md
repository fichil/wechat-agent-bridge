# M1 重启后的发送账号重新绑定

最新真实发送账号核验已通过：控制台页面启动后原生双读synthetic_owner一致、恢复后console_identity保存，报告console-identity-result-6be6657de629498d943639fe21d720eb.json SHA dcdc9ca4f8f08772b71c8a82bd8f3ad1290513ad79664bbe59626ebc94b56fb5。当前4708/263266指纹保持，byte0、worker退出、清理无错、旧密文及额度保持。**验收binding/account尚未衔接**；无需再让用户证明/重开本人卡。新增adopt_console_identity默认disabled入口，用准确原生报告/受保护记录、当前指纹、旧实例退出及原子归档衔接，不伪装零焦点后台验证。11项新增、完整1108项22.240秒通过；下一独立原生轮才实际adopt，成功后重核当前私聊精确ID，禁止直接004。M1仍未完成。

最新：CUA收据1b37d2c4173140b7ba516956f578ae88到原生报告1bae3bb589d74e1495e4f88ba4765c04相隔22.517秒，非同步观察；后者0.859秒、零事件、退出恢复完整，仅主窗/无资料卡。不能由此证明卡结构错误，也不再要求用户重复证明身份。当前需产品层先准备只读授权等待，再打开本人卡，尚未实现。

已修复控制台实际数据冲突：WindowsAdapter.probe_account保存到console_identity/<instance_id>，不再覆盖gate.sqlite3旧binding/account及其epoch格式；响应后再次核验指纹、精确ID/实例/确定性/原生来源，落盘成功后才更新状态。新增5项，完整1068项20.589秒通过，未以该离线修复声称真实本人卡绑定通过。旧验收binding不迁移，无新发送/API，M1未完成。

最新：用户提供清晰本人卡“微信号synthetic_owner”，视觉身份无需再反复验证。当前问题在程序资料卡检测；新增 --inspect-own-profile-structure 只查询精确ProfileUniquePop类及顶层类/归属，不读名称/值/聊天。完整1063项22.217秒通过，实际1.047秒零事件、完整退出恢复；查询时root下无ProfileUniquePop、顶层仅MainWindow263266，不能据此证明截图中的卡是内嵌或另一类。报告5335028a9f884a3e832f635ece48db17 SHA d16651b8e80c281d9a7cf35cd82a02139fe4c54a8b70f645ebc309d5de137353。下一独立CUA由代理打开卡后立即独立原生结构诊断，不再要求用户重复开卡，不先运行全局输入严格binder。

此前资料卡CUA61e7bb9f89964534b9037cf504be9e53后的绑定先因输入保护失败、输入静止就绪后因无卡失败，均未写新binding。新增默认关闭wait_sender_profile.py（7项新测试、1058项回归通过）的60秒新增顶层窗口等待已终态超时，没有启动binder；旧报告缺少变化轨迹，不能推断用户未操作或确定卡的实现。用户已回复“已保持”，不重复原等待。结构诊断新增5项测试后1063项通过；M1未完成，无发送/API。

最新本人卡已实际打开：操作者确认空闲后，CUA原窗口263266一次真实头像点击及500ms刷新，视觉核对微信号synthetic_owner，卡保持展开，无截图选区遮罩。新development-cua-own-identity收据记录本次来源，不保存图像；下一独立原生轮立即两次读卡绑定，不再用旧遮罩/旧收据猜当前状态。本轮原生修改仅本人卡 children()超30时拒绝而不截断，补3项边界/冲突测试，完整1051项18.934秒通过；未做真实原生读卡或保存新绑定、发送/API。M1未完成。

最新CUA：原窗口263266的新画面已无截图遮罩；本人头像点击请求被SDK人工输入保护拦截。按要求刷新后画面转为Codex遮挡，已停止操作，未强行激活或重试，不推定头像点击已发生，也未取得卡/保存身份凭证。下一次须空闲状态和全新观察，不得继续原生绑卡或复用截图坐标。必要刷新授权仍仅本次本人卡流程，原授权不扩大为无关聊天操作；无native UIA/补丁、发送/API或草稿操作，M1未完成。

最新独立原生重绑：报告 sender-profile-rebind-645148d1b3584292be13d68c67bbf219.json SHA4e6d2970ec23873ff1a059ec9dc199753caf8eab8eee0f3284a555c6029d93bf，1.203秒，失败原因 account_identity_uncertain_open_own_profile；worker正常退出、字节恢复、来源未变，未保存新绑定、未读取聊天或草稿、无发送/API。仅限已核验PID4708的可见原生顶层类名枚举返回主窗263266/Qt51514QWindowIcon，无资料卡。**截图选区中的本人卡不是当前真实弹窗证据。** 下次独立CUA沿已批准本流程刷新，先检查遮罩是否已消失，再依据新画面打开本人头像；不擅自丢弃操作者截图、不猜坐标、不重复原生绑定。M1未完成，1048项回归沿用。

最新CUA观察：整窗截图和整窗文本最初均被自动审批拒绝，未绕过。操作者先批准一次截图，因Codex遮挡未取得头像；恢复刷新因一次额度已用被拒绝。随后操作者明确批准本次本人卡流程必要刷新。新授权后原SDK窗口263266激活观察，画面存在本人卡且微信号标签匹配synthetic_owner，同时有截图选区/编辑遮罩；未点击头像或关闭非本任务截图、不保存图像、不读无关正文。**静态遮罩画面不能证明真实卡当前可读**。下一独立原生轮只能用原两次本人卡实际读取完成绑定，不得将视觉证据替代程序化身份。旧CUA精确身份收据及指纹参数不改；本人卡被遮罩阻塞时不擅自取消用户截图。本轮无原生UIA/补丁、聊天/草稿修改、发送/API，M1仍未完成。

## 最新：正常窗口命中链止于粗客户端，转回本人卡重绑

操作者明确恢复微信后，原生确认原PID4708／HWND263266正常，单层只读命中得到role9、对象HWND262632，窗口归属通过，逻辑矩形[1,0,722,738]，不是头像。0.828秒、3条明确非目标事件，不能称零事件或后台资格；仍无目标焦点事件，退出／恢复通过。

随后新增最多6层固定点命中链，IUnknown规范身份用于同对象终止及循环拒绝，持有已见对象直到检查结束，不保存COM地址；几何和元数据每层复核，不枚举其他点或读取名称／值。依据[微软QueryInterface身份约定](https://learn.microsoft.com/en-us/windows/win32/api/unknwn/nf-unknwn-iunknown-queryinterface%28refiid_void%29)，不同接口指针本身不当身份。新增4项合成覆盖深入子ID、同对象停止、循环拒绝、深度上限不得提升精确性及无效身份。完整1048项通过18.430秒，自建隐藏原生身份及数值正控通过。

真实有界链完成3次命中：role9对象→role10对象→self，均同一粗矩形和对象窗口262632，precise_geometry=false；0.812秒、零事件、worker退出及字节恢复、来源未变。这条路径当前未提供精确头像，不调用其默认动作、Invoke或扩展内部helper。下一独立CUA轮按已授权开发辅助打开原窗口本人头像卡，再独立原生 --bind-current-profile 核验；不把CUA辅助算生产后台导航通过。

最终审计byte0、无锁及七类pending，旧binding、联系人、8项禁传基线、导航unknown密文和quota保持。新账号绑定仍未保存，无历史／草稿读取、发送或API。M1仍未完成。

| .local/live-acceptance/ 证据 | SHA-256 |
| --- | --- |
| sender-profile-rebind-73dfb2807e5c45b5bf96d136431985bd.json（正常单层） | b9d6d45178062e7266388f8571338a9765a514aa808035acdc0090ecaf92b8f7 |
| owned-window-oleacc-1abd9af2412342c790403f1f291472f5.json | 0980895cc82df11e8dd9c51cd44076362c9099fdbd15e07183559f2ef9b798e9 |
| sender-profile-rebind-95accf941b2442b5bf9db798986c4124.json（3层粗对象） | 7a2bc13282b099ed03bbc9d040b3c9aaae83de494213b0e8bec82fe76bae959e |
| oleacc-chain-audit-85fc8c2257584d7cb682d0d567f2f43e.json | 870ca422ff3dc6f6fbaad5b5a46c7b8f9a27be197235e1b93939e69bb7c4a12f |
| offline-oleacc-chain-20261027.txt | 08d98cf34f8c42bc063cf33bbe8f717324a9bbe19b2b277f4e9002395c9d13af |

当前window_oleacc_hit.py=53fe998b1db8b4bb8b184fecb317686aea39e5e0fcc9830e0410fd03a59dee53，原生对照harness=18995cecd9bdec2d1845decbd9731722c3ffb8e22f014a8bffcf7a9c2524a9bb。没有新增依赖或复制上游代码。下方“等待窗口恢复／命中未实测”均为此前状态。

最新原生观察：CUA激活后窗口仍 iconic=true，故只读命中再次跳过（0.781秒、零事件、退出／恢复通过）。报告 sender-profile-rebind-2b6c225e994a4de5a5f9668772237aa1.json SHA e6407c98da8e6401cd401a8fe9c7492a280e4c69d00325bb2a56827244e98603。这不是正常窗口的空命中，也未证实激活调用曾恢复窗口。下一独立CUA轮需要可观察的窗口恢复动作／结果，不再原样重复 activate_window；无新代码、聊天读取、发送/API，沿用1044项，M1未完成。

2026-10-27 独立 CUA 已为命中验证发出一次窗口激活：仅从最新 list_windows 唯一返回的263266获取实际 Window，activate_window 后重新枚举／get_window，仍为同一窗口；未截图、读 UI 文本、导航会话、改草稿、发送或调用 API，没有原生 UIA／补丁混用。**这不证明 normal 状态**，下一独立原生轮运行 --inspect-window-oleacc-hit 核验当前状态并命中；不重复已完成激活。M1保持未完成。

## 最新：只读命中已实现，真实窗口最小化而跳过

新增 scripts/window_oleacc_hit.py，默认关闭 --inspect-window-oleacc-hit。仅在主窗正常可见时，从当前 frame／DPI 转换固定逻辑定位点(31,59)，调用已归属验证的 root.accHitTest 一次。[微软返回值契约](https://learn.microsoft.com/en-us/windows/win32/api/oleacc/nf-oleacc-iaccessible-acchittest)区分 None、自身0、正整数子 ID 和 IDispatch 对象；对象转 IAccessible 后先核验窗口／PID／GA_ROOT，再读数字角色和位置两次及复核归属。窗口或数值变化、外部对象、无归属、无效数值停止。位置只用于候选，不证明头像或账号；没有名称／值／图像或默认动作调用，不新增内部 helper。

新增 11 项测试，完整 **1044 项通过，18.528 秒**。首次本机隐藏 STATIC 对照发生 AttributeError：生成文件的 TYPE_CHECKING getter 不等于运行时 API；实际改为 accRole[child]，并新增适配端口测试。修正后的原生 role=41、location=[20000,20000,320,200]、hit_kind=self，隐藏状态和前台不变、窗口销毁及子进程退出。该对照只证明数值封送，不证明可见头像命中。首轮聚焦套件因系统 TEMP 权限失败，改用项目 .local/test-tmp 后通过，未以改代码掩盖环境错误。

真实新入口唯一一次运行：PID4708／HWND263266 原指纹保持，visible=true、iconic=true，geometry_inspection_usable=false，故 **未调用命中／角色／位置**。0.735 秒、零事件、worker 退出、开关恢复及来源不变。最终 byte=0、无锁、七类 pending 空；旧 binding、联系人、8 项禁传基线及导航 unknown 密文、额度哈希保持，无新账号绑定、历史／草稿读取、发送或 API。

| .local/live-acceptance/ 下证据 | SHA-256 |
| --- | --- |
| owned-window-oleacc-ee6dd7654d764be9af005b941d5137bd.json（属性失败） | 8a1b5bea089174aebdf28e94334cf0bc5ff505af4b66db3b718bc13ea0995180 |
| owned-window-oleacc-62faad19dcb941f4ac7da2e47ae125ef.json（原生合成通过） | ced97d7eeee92f0256074fa308ecd34463147517be43a55c8d7d716a435102bb |
| sender-profile-rebind-6bf1fea613034424a88b419173bf4f3b.json（最小化跳过） | b4ee7c131a17ec52b182531e5a6e5e91f71ebbf20191c31d92f6300831e5040d |
| oleacc-hit-skipped-audit-d704580b17b246e5813b892c5fb2d1b4.json | 308e7aa965fed58a2c95b70926dc5accc14d9dc2bcbd199fc6eb24bd620372ec |
| offline-window-oleacc-hit-20261027.txt | e9de141acb3740709acf6da1f101779d58539888e531decae6a3d8a20ee514fe |

源码 window_oleacc_hit.py=a1e85d58891fe236b2992eba8983bd90655b5ba636a7976f8f7070f93fe5db06；rebind_sender_profile.py=49f3904f787dc4f69231b9b871724db22a1055a485deb7eb7a21be464c194d47。无依赖增加或上游复制。

下一步：独立 computer-use 轮按实际 SDK Window 对象恢复263266，不捕获无关聊天；下一独立原生轮再运行上述相同参数，但模式改为 --inspect-window-oleacc-hit。不要本轮混跑 CUA，也不要在最小化未变化时重复原生预检。正常窗口结果仍可能是空或粗对象，均不能调用动作。新绑定／真实命中／M1 尚未通过。下方存在性研究及“命中尚未实现”为此前状态。

## 最新：标准窗口 OLEACC 归属通过，命中尚未实现

2026-10-27：新增默认关闭 --inspect-window-oleacc，精确主窗 OBJID_CLIENT 的 AccessibleObjectFromWindow 返回 S_OK／非空 IAccessible，WindowFromAccessibleObject 返回同一 HWND 263266；PID／GA_ROOT 范围核验通过。当前指纹仍 PID 4708、创建身份 134375347512983186、微信 4.1.15.56、DLL SHA-256 932dd6700f22e32faeb9b0cc9581a1f601e60ace151067680d3f6596496fab99，OS 10.0.26300、Python 3.12.10 x64。真实 0.906 秒，窗口当时正常、零事件、工作进程退出、字节恢复、来源未变。

该入口与此前两个 Legacy.GetIAccessible NULL 不同，只验证标准对象存在与 HWND 归属；没有读取 accName、accValue、accRole、accLocation，没有 accHitTest 或默认动作。角色、位置、头像和账号身份都未通过，不能启用后台导航／绑定／发送能力。新脚本无上游代码复制或依赖添加，公共接口依据微软 AccessibleObjectFromWindow 和 WindowFromAccessibleObject 文档。

先通过 7 项新增合成测试与自建隐藏 STATIC 窗口原生正控：接口及归属正确、hidden 前后不变、前台未变、窗口销毁／子进程退出。完整 1033 项通过，13.408 秒，无跳过。上轮回归句柄已不存在，本轮直接读终态日志而未重启套件。真实查询后最终原生审计：byte=0、无锁、七类 pending 空，原 binding、联系人、8 项禁传基线和旧导航 unknown 密文保持，额度文件哈希保持；没有新绑定、历史／草稿读取、发送或 API。

| .local/live-acceptance/ 下证据 | SHA-256 |
| --- | --- |
| owned-window-oleacc-28d960aadfb24825828d0c5ad1d9437c.json | b23dfbbb19bcd7ca8bab697bea999c571b205e419a62deeb682123e209dd7008 |
| sender-profile-rebind-9068c062b6c54f3aa3db89e0927d00c6.json | 6bd6e82343e4e2debfbedf38a16f00d2c07b18512a5f4d55c05479edf4fd9540 |
| window-oleacc-final-audit-9eedbe9f0c9644b6a60d19192d5aab4f.json | 4214497fab76ccc33ae594e0f2ffcd38c6b517390c5ec794676403188e45d09f |

真实存在性命令已执行，无需重复：

```powershell
.\.venv\Scripts\python.exe -B -X utf8 scripts/rebind_sender_profile.py `
  --inspect-window-oleacc --allow-accessibility-patch --account synthetic_owner `
  --fingerprint-evidence .local/live-acceptance/replacement-window-fingerprint-ee693d583b8d446d90efbe72218dea4a.json `
  --cua-evidence .local/live-acceptance/development-cua-own-identity-ed20545d669a48dbbc690aacd3d49061.json
```

下一项是只读命中及数字角色／位置的独立实现：先合成覆盖 VARIANT 返回类别及对象归属，再仅正常窗口使用当前 DPI 的已限定头像定位点。不读取名称／值，不调用动作，粗容器不升级为头像，任何对象窗口归属不明停止。当前尚未实现，M1 未完成。下方“窗口入口尚未实现”及 1026 项是此前状态。

2026-10-27 当前：两个固定容器的 Legacy pattern 虽声明存在，实际 GetIAccessible 均返回空指针；这条原生接口链不能继续命中。完整 **1026 项通过，14.749 秒，无跳过**；新账号绑定／M1 未完成。最终审计窗口当前已最小化，不需为接口存在性诊断恢复它。

## Legacy 原生接口存在性实测

新增 scripts/own_avatar_msaa.py 及 --inspect-own-avatar-native-msaa，只检索绑定主窗后代中 MMUIRenderSubWindowHW／MainTabBar 两个固定类。对象必须唯一，当前 PID、数字角色、RuntimeId 前后匹配；最多两次查询，接口结果仅记录可用／NULL／固定失败码和数字 HRESULT，不读取其内容或默认动作。该检查不依赖头像几何，既不把 pattern=true 当作接口可用，也不把接口存在当作头像身份。

最初版本按几何检查设计，实际遇到最小化窗口而跳过，接口查询 0 次；此记录保留，不作为能力通过。存在性检查改为固定类／角色后，真实窗口当时为 visible=true、iconic=false，1.016 秒、零事件、worker 退出、字节恢复、来源不变，两个唯一容器均返回 NULL：

| 对象 | RuntimeId | Legacy 声明 | GetIAccessible |
| --- | --- | --- | --- |
| MMUIRenderSubWindowHW | [42,197096] | true | NULL |
| mmui::MainTabBar | [42,263266,4,-2147483600] | true | NULL |

[微软 GetIAccessible 文档](https://learn.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationlegacyiaccessiblepattern-getiaccessible)明确允许底层非原生 MSAA 服务时返回 NULL。本次未证明返回 NULL 的内部原因，也没有证明直接 OLEACC 窗口入口不可用。没有尝试在空接口上调用 [accHitTest](https://learn.microsoft.com/en-us/windows/win32/api/oleacc/nf-oleacc-iaccessible-acchittest)，没有 accName、accValue、accRole、accLocation 或 accDoDefaultAction 调用。

新增 8 项合成测试，完整 1026 项通过；固定容器测试不提供几何或文字属性，证明接口存在性不依赖这些数据，接口可用模拟对象禁止其他属性读取。错误角色／类／PID／ID、多对象、不匹配源和回收变化均不能取得授权。真实记录后仅移除旧 RawView 实现中未使用的 leaf 回调，RawView 回到此前版本，不为这个清理重复操作客户端。没有新增依赖或上游代码。

| .local/live-acceptance/ 下证据 | SHA-256 |
| --- | --- |
| sender-profile-rebind-4fde1475633e46f5b006c0bac132438f.json（最小化跳过） | 9d951b4e0c038d052b220c5688481bd7cefdadd09964a7c74adfee42c023b7cc |
| sender-profile-rebind-44123ef5c76048d28085aa46dde3619e.json（两个 NULL） | 9238fc627f0dbdf9d6aed7866213fe1535394fedb482353b36d7d920322f215a |
| native-msaa-final-audit-a4c1801052cb4169b7f555a9de33d9f8.json | fffd62448f0cb654a0dc0c47d834b420ce39d5678a100192a067e9e98e4bc94d |
| offline-own-avatar-msaa-final-20261027.txt | f38d8a9ab49bf65746c09c73f02026686383c5f963146fc0ba5377af7eedd7b8 |

当前源码 SHA-256：own_avatar_msaa.py=cd9c33dde59f805d04b75aa20088cc565d21fcee740a6fc9af11535a12d6b692；rebind_sender_profile.py=5498b4a07de28c9cd58ad29bdd8a501cd93bf42602ee8c3578dba83c50da7d92；own_avatar_hit_test.py=fe4ac8c4d808cc656de242dce40d2e5005d47839f3f868dbf20383c6d14e4f3e；test_own_avatar_msaa.py=b9b2fb15c9c0748b0ae496797db61148e413424f12a62ac80472a15969bc7ad3。执行报告保留当时完整来源映射，不将之后未调用代码的清理等同于重做真实验收。

最终审计窗口 visible=true、iconic=true，原完整指纹保持，字节 0、无锁、七类 pending 空；原 binding、联系人、8 项禁传历史、全部旧导航／unknown 密文和 quota.sqlite3 哈希未改。无新绑定、聊天读取、发送或 API。

当前已完成的命令如下，无需原样重复：

```powershell
.\.venv\Scripts\python.exe -B -X utf8 scripts/rebind_sender_profile.py `
  --inspect-own-avatar-native-msaa --allow-accessibility-patch --account synthetic_owner `
  --fingerprint-evidence .local/live-acceptance/replacement-window-fingerprint-ee693d583b8d446d90efbe72218dea4a.json `
  --cua-evidence .local/live-acceptance/development-cua-own-identity-ed20545d669a48dbbc690aacd3d49061.json
```

下一项是标准 AccessibleObjectFromWindow(OBJID_CLIENT) 的只读窗口入口，尚未实现／测试。只允许精确主窗或已验证渲染子窗；返回对象须验证 HWND、PID、根窗口归属后才能读取数字角色和位置，最小化时不做坐标命中。不得读名称／值或调用默认动作，不添加头像内部功能补丁。接口或角色证据不足就保留限制，不用 coarse Invoke 代替头像。下方 1018／1009 项及“GetIAccessible 尚未测试”均为历史状态。

2026-10-27 当前：**窗口已原生确认恢复正常，精确头像节点仍未取得。** 标准查询 51 节点、正常几何下无候选；新增 RawViewWalker 在头像位置命中两个粗容器，不能调用它们的动作或视为账号。当前完整 **1018 项通过，14.752 秒，无跳过**，新账号绑定／M1 仍未通过。

## 正常窗口及 RawView 命中证据

独立原生轮首次检查因输入变化在读取前停止，未启用字节；随后在同一进程正常显示且处于后台的实际条件下只启动一次读取，1.015 秒、零事件、worker 退出及字节恢复通过。window_state={visible:true,iconic:false}、geometry_inspection_usable=true，DWM frame=[22,280,1468,1758]、DPI=192；标准类／Button／Image 共 51 节点、没有左上候选。由此证实 CUA 后窗口已恢复，不再依赖激活调用返回推定。

新增 scripts/own_avatar_hit_test.py 和默认关闭 --inspect-own-avatar-hit-test。使用公开 [RawViewWalker](https://learn.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomation-get_rawviewwalker) 及[子节点](https://learn.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationtreewalker-getfirstchildelement)／[兄弟节点](https://learn.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationtreewalker-getnextsiblingelement)读取。固定窗口相对位置 [31,59] 只是与已观察本人头像对应的发现定位点，不是账号身份或输入坐标；根据当前帧和 DPI 换算，只沿覆盖该点的原始树分支读取类／类型／RuntimeId／边界／pattern 状态。最多检查 64 子节点、8 层，不进入离点聊天子树，外进程节点跳过；多重覆盖保留多条路径，不选第一个。最小化或隐藏时跳过。

真实 1.172 秒、14 节点、零事件、完整恢复，命中结果如下。两个容器均过大，precise_geometry=false、identity_verified=false；没有触发任何 Invoke／MSAA 默认动作。

| 叶节点 | RuntimeId | 角色／声明能力 | 当前几何（窗口逻辑坐标） |
| --- | --- | --- | --- |
| MMUIRenderSubWindowHW | [42,197096] | Pane 50033；Invoke=false、Legacy=true | [1,0,722,738] |
| mmui::MainTabBar | [42,263266,4,-2147483600] | MenuBar 50021；Invoke=true、Legacy=true | [1,0,61,738] |

第二条路径经过 MainWindow → QWidget → QStackedWidget → MainView → MainTabBar。以上仅证明本次原始树读取中的粗容器命中，不证明所有公开接口均无精确头像；Legacy=true 不证明 GetIAccessible 成功，更不证明默认动作对应头像。

新增 9 项测试；一次 RuntimeId 更换负例发现入队后对象 ID 未固定，已随队列携带预期 ID，在出队时核对，修复后完整 1018 项通过。覆盖粗节点不提升身份、离点聊天不遍历、多路径、外进程、循环、节点／深度上限、窗口／对象变化。默认测试仍不接触微信或付费 API。

| .local/live-acceptance/ 下证据 | SHA-256 |
| --- | --- |
| sender-profile-rebind-51b5f0cce8604d59bedab05d20b869e4.json | 6fb0f1494882982738a64beaaa60ebb8e892ce3719960548139b5eab2b32e4c7 |
| sender-profile-rebind-879d6a53a3fc43b0b08bbe8df0cdced6.json | 596dbc3050ce2174f774e02461fab5093077613dc3423bb6fb9105c1cfe6ca29 |
| sender-profile-rebind-6d6816d97129455b8542d6778a348142.json | 733a3b7805e39dadf2f4760dbb9628bad2bccd99e38721ef87d5eed7aed07206 |
| own-avatar-raw-final-audit-778120137c19459a93dbfb9992e7afbf.json | d6ac2cf56b797bcab61be866634076b6ac1438e891267c6758a75f26e892d9ff |
| offline-own-avatar-raw-hit-20261027.txt | 32dd11568094310cb52d3074f9bd533895cade790c6ce60840f6e1ea04450395 |

当前源码 SHA-256：own_avatar_hit_test.py=fe4ac8c4d808cc656de242dce40d2e5005d47839f3f868dbf20383c6d14e4f3e；rebind_sender_profile.py=ad823e664d50a289701ae1f82d7ccbd5b85653f163597d6ffe904389ee04dd16；test_own_avatar_hit_test.py=d405bb1218cc4a59f2c43962ed52e5cfbe42000b34b1d72708bc5b028a321ba4。

最终审计窗口正常、完整指纹不变、字节 0，原 binding、联系人、禁传基线、全部旧导航／unknown 及 quota.sqlite3 哈希保持；七类 pending 全空、无锁，没有历史／草稿读取、发送或 API。

下一步仅验证两个已核验容器的公开 LegacyIAccessible → GetIAccessible → accHitTest 只读链，先核对标准接口和合成测试，再看数字角色／位置能否给出更精确对象；不读名称或 Value，不执行默认动作，不扩大仅限发送按钮的内部 helper。粗容器不能作为动作目标。最新只读命令为：

```powershell
.\.venv\Scripts\python.exe -B -X utf8 scripts/rebind_sender_profile.py `
  --inspect-own-avatar-hit-test --allow-accessibility-patch --account synthetic_owner `
  --fingerprint-evidence .local/live-acceptance/replacement-window-fingerprint-ee693d583b8d446d90efbe72218dea4a.json `
  --cua-evidence .local/live-acceptance/development-cua-own-identity-ed20545d669a48dbbc690aacd3d49061.json
```

本次结果已保存，无需原样重复。下方“窗口是否恢复未验证”、1009／993 项及最小化状态均属于此前阶段。

2026-10-27 当前续作：独立 CUA 对 SDK 实际返回的原微信 Window（263266）调用一次 activate_window，调用返回；随后重新枚举和 get_window 仍取得同一窗口。没有捕获聊天截图或文本，没有项目 UIA／开关／helper、草稿或发送。**是否已恢复正常窗口仍待原生核验。** get_window_state 的两个 include 选项都设 false 被 SDK 明确拒绝，不能按下方旧建议再次使用；仅刷新窗口枚举不证明非最小化状态。收据 development-cua-selected-window-activation-947dbe1be52642daa2358be144848001.json，SHA-256 b9f496ea37766628563b6789827e0a4d42d4f203582932485696f44f2c5f1a60。

下一独立原生轮运行下方最新 --inspect-own-navigation，要求 window_state.visible=true、iconic=false、geometry_inspection_usable=true 才解读候选。当前不再重复 CUA 激活；若仍最小化，先按实际状态诊断，不假设失败调用、进程退出或账号变化。本轮无代码变化，不重跑测试，沿用 1009 项结果；M1 未完成。

2026-10-27 最新：当前微信窗口已最小化（原生 visible=true、iconic=true），指纹仍匹配。角色元数据读取成功但未取得有效的正常窗口头像候选；新账号绑定仍未通过。完整 **1009 项通过，13.972 秒，无跳过**。下一独立 CUA 仅恢复 SDK 返回的这个窗口，再独立原生检查几何；不需要再次要求操作者开卡或重新批准范围。

## 当前头像定位及只读办公保护

新增 own_avatar_metadata.py：在同一精确主窗内按 XAvatarImage、MainView 及公开 Button／Image 角色过滤，总计最多 64 节点；只对左上区域候选读取最多 6 层父级类与 RuntimeId，核验主窗归属，不读名称、正文、Value、头像二进制或任意内存。DPI 由既有 per-monitor-aware-v2 诊断线程上下文转换并恢复，不改系统设置。位置、父级和 pattern 只是候选元数据，不是账号身份，也没有产生点击坐标或执行动作。

三个分阶段真实结果：XAvatarImage 为 0；加入 MainView 后共 2 个节点、无左上候选；最后扩展到 Button／Image 后共 51 节点（MainView 2、其他角色 49），1.078 秒，零事件、退出／恢复正常。最后记录的 DWM frame=[-31989,-32000,-31697,-31961]，之后独立原生状态核验 confirmed iconic=true。因此该空候选不用于否定正常窗口的头像语义接口，最新实现先核对 native visible／iconic 状态，最小化／隐藏时跳过几何并标注不可用。**最后新增状态跳过分支只经合成测试，尚未真实执行。**

此前一次只读检查因全局输入变化停止，另一回在 worker 启动前记录一条 target_process=false 的前台事件。现只读诊断允许微信持续后台时的其他应用输入和这类事件，原始记录保留、background_qualified 永远 false；不将未标注归属的事件视为安全，也不把字段 false 当作已证明自动化无干扰。目标事件、未给 target_process 字段、锁屏、剪贴板变化仍拒绝；如果微信在前台，保持严格桌面一致及输入保护。账号绑定／写入分支完全没有继承该诊断例外。并发办公规则目前只有合成证明；本轮成功真实报告自身均为零事件，不能称作真实并发压力验收。

增加 16 项测试（993→1009），包括几何／DPI 错误、循环／深度／节点限额、错窗口／跨进程、对象变化、多候选不提升身份、其他应用输入只允许元数据、原始非目标事件保留且不能绑定、最小化／隐藏不读取几何等。完整 1009 项通过后仅文档更新；无新增依赖、helper 构建或客户端磁盘修改。

| .local/live-acceptance/ 下证据 | SHA-256 |
| --- | --- |
| sender-profile-rebind-74b172b649e34c9c926fd22d33463229.json | 6adb33264ef9e648e52c2da77f8b9c50896d185f9672c6af1dbfabf735b6a901 |
| sender-profile-rebind-89f6deb64d0d4e84b75238bdd7aa221f.json | 296058566aa59d2dd18fc03812ef95e00ab3ee7ce50db1c32107473d4e941b79 |
| sender-profile-rebind-91941d66f17f404cae1321fc3c7796d2.json | 354dd6850ef1bbe377ab2460a7815814def86c4268afb254c96bc5ddf0f029f4 |
| sender-profile-rebind-eb865761b06a451f891b7bcabbb8367a.json | e6ea4c27647882e01b64bd733f61f9cf31529191cc4fbe2126b2527a47c0cf12 |
| sender-profile-rebind-cc532d797ef7476d8a56bc8c0a06df63.json | 3ee197a2aca27b2f33b60098ba9c05aa665dbfac01e25125289a182778413bf8 |
| own-avatar-final-audit-0348483dffaf470b82311e0811271728.json | e6ac652a63c5a6e97b7da348fa150aba59fad0a45190afd810da7acf00396e55 |
| offline-own-avatar-final-20261027.txt | 89920145e787ce018f3039d0bc7ba719391f3d252574e99a31d0d7bfb02199a2 |

当前源码 SHA-256：rebind_sender_profile.py=c6dcd267171494b9661a02ac34463db52871b182885639467a8557c70045bc4a；own_navigation_metadata.py=5a60941197241881d9bb0c857adb1d29c76f8864d68df1e0122859af596cca4e；own_avatar_metadata.py=6ab2398d56fe1311356596df34f846b10263e2384586a2490e36c7c23b8f68be；test_sender_rebind.py=8eb5fa6aef531f87bbd7aecad53967e699c7c6066dd89117e270eb8eced9f2a0；test_own_avatar_metadata.py=fc8eba1d9bd05b4746a7c2d1587c5f24ca2c131679f5e6136e057911a5428804。

最终审计：字节 0、无锁，patch／thread_hook／draft_probe／development_navigation／development_window_restore／development_exact_search／account_rebind pending 全空；原 binding、联系人、全部旧导航、私聊基线密文及额度文件哈希不变，没有历史／草稿读取、发送或 API。

下一 CUA 轮只选 SDK 实际返回的微信 Window，activate_window 后以 include_screenshot=false、include_text=false 刷新；无需捕获未重新核验的聊天区。下一独立原生轮运行下列最新只读模式，检查窗口已正常、geometry_inspection_usable=true 及候选数量／所属关系；尚无任何头像动作通过，不能直接发 004 或绑定账号：

```powershell
.\.venv\Scripts\python.exe -B -X utf8 scripts/rebind_sender_profile.py `
  --inspect-own-navigation --allow-accessibility-patch --account synthetic_owner `
  --fingerprint-evidence .local/live-acceptance/replacement-window-fingerprint-ee693d583b8d446d90efbe72218dea4a.json `
  --cua-evidence .local/live-acceptance/development-cua-own-identity-ed20545d669a48dbbc690aacd3d49061.json
```

下方 993／982 项和 MainView 唯一性检查均是历史阶段；新 worker 不再调用要求 MainView 唯一的旧 collector。

2026-10-27 当前：真实新绑定未通过。本人卡首次读取时没有可读弹出卡；操作者补发截图后，另一次检查在读取前检测到输入变化而停止。两次均没有新绑定、聊天／草稿读取、发送或 API。最终所有 pending／锁为空、字节 0，原 binding 和历史／联系人／unknown／额度完整保留。完整 **993 项通过，13.060 秒，无跳过**。

## 本轮真实结果及下一步

首次卡读取 1.172 秒，worker 正常退出、字节恢复、零事件，reason=account_identity_uncertain_open_own_profile。新截图中的本人微信号与获准发送账号一致；随后一次运行在 0.422 秒因 sender_rebind_human_takeover 停止，尚未启用字节／启动读取 worker。截图说明当时显示的 ID，不能替代程序对当前进程／卡的核验，也不能用来忽略输入变化。

新增 --inspect-own-navigation，复用原指纹和恢复守卫，禁止元数据响应进入绑定提交分支。scripts/own_navigation_metadata.py 只查看 MainTabBar 内最多两层、64 节点的类型／类名／RuntimeId／pattern 状态；不读 Name、Text、Value、AutomationId、头像或聊天。一次真实检查得到 18 节点，0.843 秒、零事件、worker 退出及字节恢复正常；没有可确认的本人头像身份。provider 为多个不同类型节点报告 Invoke 可用，不能因此声称动作有效。没有调用 Invoke、选择、点击或窗口激活。

确切 DLL 磁盘字符串存在 mmui::MainView、mmui::XAvatarImage 等类名，只是静态线索。后续增加 MainView 直属子控件的有界读取，但真实检查因 MainView 不唯一拒绝，故**当前包含 MainView 的完整检查模式尚未通过**。不能把先前仅 MainTabBar 的成功当成最终版本通过，也不能选第一个 MainView。

下一步改为对已绑定新主窗内 XAvatarImage 的有界类／容器／几何元数据核验，验证候选是否属于本人左上头像区域；只读定位，不按旧截图盲点，也不使用仅限发送按钮的内部 helper。取得唯一语义目标并通过合成动作／恢复检查后，才考虑一次开发期公开调用。没有头像动作实际调用证据，不重复原本人卡读取或同一已失败导航实验。

最终内容盲比较 39→44 条 records：旧 binding、verified_contacts、private_baselines、全部旧 development_navigation 密文及 quota.sqlite3 哈希均未变化；所有七类 pending=false、锁不存在，当前指纹不变、字节 0。新增条目是本轮失败／元数据检查的审计，不是账号授权或消息。原 8 条禁传基线未读取／覆盖／重采。新账号仍须程序化绑定，004 未运行。

| 本机证据，位于 .local/live-acceptance/ | SHA-256 |
| --- | --- |
| sender-profile-rebind-848316ade1034d938d45177c3b854391.json | a51b354938c2ac46d46aa9eaa9bb7ccac54158e385bea0be20fec7c276a8e7c9 |
| sender-profile-rebind-172d841a76ef45218288ecbe8e342d9c.json | 93f3820ec9fb358de4385810f3cb66fc3164931d32f1d39f45c3d207c6508b3c |
| sender-profile-rebind-3073a9ee6b514c908fc813e3ae58f279.json | 12333235cdfebc4fb836b2483fd17adc430e6ae327e083ae67dd6d1889818993 |
| sender-profile-rebind-287c3dc3443c45a293e43553bfc67428.json | a5dac7d57d5648a0928852157e2a454a2ea31f562e71e271c7e849ac0440da34 |
| sender-rebind-final-audit-b5cb8ec7d1b2465a932b8fecc833315a.json | 74049c21808d086561dce57ee75c827433206b7ebd48ed9fe41b25efc207e630 |
| offline-own-navigation-final-20261027.txt | 0354ffa845319960728739ce05cc9f0e07994adfc5ff7a099a45d948d2911b11 |

当前源码 rebind_sender_profile.py=45920ebe03e119da1d909cb1c890d202f80ac7171f3a9238bf695784bbda6d0d；own_navigation_metadata.py=6b9fe863e3b98aa024eac904a9a4d439da40a276fe98ed666c1a63da6f2fe395；test_own_navigation_metadata.py=0932bb860d9c40b7d65e7da7a5b1e85e16abbf71dfd1af8ffd9f3fe7a2271929；test_sender_rebind.py=18a2d21ac1b9fde6a9917099a213a9473ec0d9d84282ae65de9959e9f6b1eee6。新增 11 项测试覆盖固定范围无文本读取、深度／节点数限制、循环、身份变化、未知 pattern 不提升能力、元数据模式不绑定账号。下方 982 项及“真实入口尚未执行”属于此前阶段。

2026-10-27 当前：独立 computer-use 已从 HWND 263266 左上角自己的头像打开本人资料卡，视觉核对带“微信号”标签的值为获准发送账号。卡留开；**程序化重新绑定尚未执行，M1 未完成。** 第一次有限观察未取得卡，随后窗口被遮挡；没有据此声称身份通过。准备代码和离线测试后，重新获取同一实际 SDK 窗口，一次头像点击并等待 500 ms 渲染，最终观察取得本人卡。此过程是开发前台辅助，不是后台能力。

新增 scripts/rebind_sender_profile.py，默认 disabled，只允许明确 --bind-current-profile 与 --allow-accessibility-patch。它核对当前原生指纹收据、绑定同一收据哈希和窗口的本人卡 CUA 证明；私聊对方卡、未核验卡、错账号、错窗口和旧版本不能替代。首次读的是本人卡，不读当前私聊标题、选中行、草稿或历史。

真实执行时必须重新核验原实例已退出、无其他 pending、同一新 PID／HWND／创建时间／DLL 指纹，持有独占运行锁。只读工作进程两次精确读取本人卡；检测人工输入、焦点／剪贴板变化、锁屏、超时、来源变化立即停止。临时无障碍字节和工作进程完整恢复、零瞬时焦点事件之后，才在一个 SQLite 事务内归档完整旧 binding、写新 binding 及完成记录、清本次 pending。新 binding 明确 requires_fresh_target_identity；不写 verified_contacts、private_baselines、消息或额度，也不启用生产表。

恢复失败保留 pending／锁；事务异常不猜测哪次绑定生效，必须核对记录再恢复，不能盲目重跑。旧 binding epoch 有冲突时拒绝覆盖。自身卡变化或精确 ID 错误不会写新 binding。即使账号重新绑定成功，新联系人、控件、后台读取、发送、私聊记忆及群 @ 仍需各自真实验收，旧历史禁传和 unknown 不变。

## 离线验证与证据

新增 16 项测试，完整 **982 项通过，12.876 秒，无跳过**。真实 SQLite abort 验证旧 binding、归档及完成记录原子回滚；协调器端口验证恢复之前不提交、错误账号不绑定、旧实例存活及其他 pending 在 NativeGate 前拒绝、恢复故障／瞬时焦点保留锁；实际工作函数用受限模拟 backend 只允许调用本人卡两次，禁止草稿或聊天方法。默认测试不连接微信／付费 API。没有新增依赖、上游代码或 helper 构建。

| 本机证据，位于 .local/live-acceptance/ | SHA-256 |
| --- | --- |
| development-cua-own-identity-ed20545d669a48dbbc690aacd3d49061.json | 5c9728cd56a2337a5fb1b0e6452f0952aa0df35aad1c7b1e5fb53a91393bbccb |
| replacement-window-fingerprint-ee693d583b8d446d90efbe72218dea4a.json | c5803598ec9618d8dc1388c9b15d8c222230e5b60cd9eb2a614c7e8157a8b20e |
| offline-sender-rebind-20261027.txt | 14b33a9f4e9acc4899f76de40dc95a789366e301363a8362d6984f5a2b32ad89 |

源码 rebind_sender_profile.py SHA-256 5a6bb851b23dde612cd9fcb477a7e76ae7255f8fa9a5c357a46e4b247eb07e4b；test_sender_rebind.py=8e7143eba6233ac7321e2b008d19962435d6ea1815c046a750fb58b83abffa4f。CUA 收据没有保存截图、个人资料原文或聊天正文；账号只记录哈希。新指纹当前参考：PID 4708、HWND 263266、created=134375347512983186、微信 4.1.15.56、DLL SHA-256 932dd6700f22e32faeb9b0cc9581a1f601e60ace151067680d3f6596496fab99；运行入口会重新核对，不依赖旧观察猜测。

## 下一独立原生轮

本轮仅 CUA 与离线代码，没有 NativeGate／补丁／helper／新绑定写入、发送或 API，沿用原累计额度。以下命令尚未执行，需当前自己的资料卡仍可读；不可在 CUA 同一轮运行：

```powershell
.\.venv\Scripts\python.exe -B -X utf8 scripts/rebind_sender_profile.py `
  --bind-current-profile --allow-accessibility-patch --account synthetic_owner `
  --fingerprint-evidence .local/live-acceptance/replacement-window-fingerprint-ee693d583b8d446d90efbe72218dea4a.json `
  --cua-evidence .local/live-acceptance/development-cua-own-identity-ed20545d669a48dbbc690aacd3d49061.json
```

成功后独立 CUA 回到准确私聊并打开对方卡，再用新的原生精确 ID／控件核验更新当前联系人绑定；不能覆盖或重采原 8 项禁传基线，也不能直接发送 004。若资料卡已被人工切换关闭，读取应停止，再安排独立 CUA 恢复；不要求用户重新批准既有常规测试。
