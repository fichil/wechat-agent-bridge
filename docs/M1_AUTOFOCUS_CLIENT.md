# M1：UIA AutoSetFocus 合成通过与真实草稿失败记录

2026-10-23，**公开客户端配置 `IUIAutomation2.AutoSetFocus=False` 在标准 Edit 合成对照中有效，但随后一次获准微信草稿仍导致抢前台。** 协调器停止并保留 `unknown`，用户手动清理后只读复核空草稿及开关恢复通过。唯一诊断尝试已使用，发送与模型调用均为 0；真实写入保持禁用，M1 未完成。下文区分合成证据、真实失败和仍待核验的调用链。

## 公开机制与客户端隔离

微软说明，UIA 默认会在多数执行动作的方法前设置元素焦点，明确列举了 `IUIAutomationValuePattern::SetValue` 和 `IUIAutomationInvokePattern::Invoke`；`AutoSetFocus=False` 可以关闭 UIA 的这项前置行为。这是客户端实例上的公开配置，不是修改窗口焦点属性、屏蔽系统焦点、事后恢复前台或客户端补丁。它不能阻止第三方 provider 在处理动作时自行激活窗口。[AutoSetFocus 官方说明](https://learn.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomation2-put_autosetfocus)

新增 [uia_client.py](../wechat_agent_bridge/adapters/uia_client.py) 使用 fresh `CUIAutomation8`，直接请求 `IUIAutomation2`，保存初始值、设为 FALSE 并回读。设置不受支持、读取失败、值类型异常、模式改变或跨线程使用时停止；没有回退到旧默认客户端。目标元素必须由这个新实例的 `ElementFromHandle` 获取，pattern 再从该元素派生；不能将 pywinauto 旧实例生成的 element/pattern 混入新配置。每次动作前还须核对配置保持关闭。[IUIAutomation2 接口](https://learn.microsoft.com/en-us/windows/win32/api/uiautomationclient/nn-uiautomationclient-iuiautomation2)

配置本身不授予账号、会话、上传或发送权限，也不代替目标身份、草稿、暂停及预算检查。当前候选只验证文本设置；`Invoke` 仅有公开接口说明，尚未通过发送控件的合成或真实验收。

## 本机配置验证

证据：`.local/live-acceptance/uia-autofocus-configuration.json`。只创建 COM 客户端并读取/修改该客户端的选项，`elements_requested=0`、`actions_requested=0`。

| 项目 | 实测结果 |
| --- | --- |
| 旧 `CUIAutomation` 查询 `IUIAutomation2` | `0x80004002`，`E_NOINTERFACE` |
| 新 `CUIAutomation8` 的初始 `AutoSetFocus` | TRUE |
| 新实例设置 FALSE 后回读 | 已确认关闭 |
| 真实诊断准入 | `live_diagnostic_ready=false` |

旧对象不能直接查询到新接口的结果限于本次环境。新实例的设置不会自动改变已存在的旧客户端、旧 element 或旧 pattern。

## 三阶段自建控件对照

[probe_uia_autofocus.py](../scripts/probe_uia_autofocus.py) 默认关闭。显式启用后只创建一个随机命名的 Windows private non-input desktop、一个普通宿主和两个标准 Edit 控件 A/B。控件在这个私有桌面上可见，但脚本不调用 `SwitchDesktop`，不会显示到用户当前输入桌面；也没有输入桌面回退、前台请求、模拟键盘或剪贴板写入。

GUI 线程确认桌面仍非输入桌面后，仅给自己的 B 设置局部焦点作为基线。另一个无窗口 MTA 线程在 COM 初始化前绑定同一桌面，分别创建三个独立 UIA 客户端，只从确切自有 HWND 获取 A，并核验 PID、HWND、RuntimeId、空文本与 ValuePattern。每阶段只调用一次 `SetValue`，由 UIA ValuePattern 与 GUI 线程 `GetWindowTextW` 分别完整回读并比较固定合成文本；报告只保存是否相等，不保存文本正文。[UIA 线程要求](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-threading)

报告 `.local/live-acceptance/uia-autofocus-synthetic.json` 的实际结果：

| 阶段 | 客户端配置 | GUI 线程焦点 | Edit 焦点通知 | UIA / GUI 完整回读 |
| --- | --- | --- | --- | --- |
| `legacy_default` | fresh `CUIAutomation` 默认 | B → A | 4 条 | 均相等 |
| `modern_default` | fresh `CUIAutomation8`，初始及操作前均为 TRUE | B → A | 4 条 | 均相等 |
| `modern_no_focus` | fresh `CUIAutomation8`，初始 TRUE、配置及动作前确认 FALSE | 保持 B | 0 条 | 均相等 |

两组默认阶段的标准 Edit 通知序列均为 `B kill → A set → A kill → A set`；关闭选项阶段为空，且最终 `GetFocus` 仍为 B。通知来自自有宿主收到的 `EN_SETFOCUS`/`EN_KILLFOCUS`，没有替换 Edit 的窗口过程、注册全局 hook 或 UIA handler。不能只看最终焦点而忽略中途变化。[GetFocus 的线程局部范围](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getfocus)

两组默认焦点正例及关闭后的反例必须同时成立；若默认方式本来就不能在私有桌面上聚焦，检查会保留 `source_not_verified`，不将“没有发生变化”视作关闭配置有效的证明。本次三阶段身份均保持一致，均无事件溢出。

## 观察、清理与文件指纹

本次父进程记录 190 个检查点，输入桌面名称、前台窗口及剪贴板序号均未改变。**这是检查点观察，不是对检查点之间瞬时变化的完整证明**；局部 Edit 通知也不等同于用户桌面的全局焦点监控。

原生清理全部确认：MTA 退出及 COM 反初始化、主线程 COM 反初始化、窗口销毁、类注销、线程桌面恢复、desktop handle 关闭；父进程另确认自有三个 HWND 已不存在、私有桌面名称无法再次打开。子进程正常退出，没有强制终止，`cleanup.errors=[]`。父进程保留 20 秒硬时限，只能终止自己创建的 `multiprocessing.Process`，不能接受外部 PID 作为清理目标；强制退出或缺失任一原生清理确认均不能通过。

comtypes 导入会在导入线程初始化 COM。合成脚本仅对固定的 `comtypes==1.4.17`，在 worker 已结束、引用释放及窗口销毁后，提前在原导入线程调用库自己的 `_shutdown`，并先注销同一个 `atexit` callback，避免桌面恢复晚于 COM 清理或重复反初始化。这一私有库生命周期处理仅限一次性合成子进程，没有成为生产适配接口。

| 实测环境 / 文件 | 版本或 SHA-256 |
| --- | --- |
| OS | `10.0.26300` |
| comtypes | `1.4.17` |
| 系统 `UIAutomationCore.dll` | `be5037bf46e61566d3e5a83a6c628cebe9035771e4ae741a7b9aa27b5696d989` |
| `scripts/probe_uia_autofocus.py` | `2bd0994cb22dcbba594e2cd266574c67a06903711e6da05acbfe77d919e14138` |
| `wechat_agent_bridge/adapters/uia_client.py` | `e15901ba6e8802ee7921002d441d9287ab3ad598a26a1c5f4228dd1a5e347c84` |

运行前后 UIAutomationCore 的 SHA-256 相等，四个相关源码文件也未改变；另外两个复用 helper 的完整指纹见原始报告。未修改系统或微信磁盘文件。

首次受限沙箱运行在创建 private desktop 时失败，保留于 `uia-autofocus-create-desktop-failed.json`，状态为 `source_not_verified`，没有进入控件操作。随后在沙箱外以同一个冻结脚本完成上述成功验证。失败记录没有被改称成功，脚本也没有为绕过桌面限制切换到用户桌面。

## 离线验证与复现

此候选新增 15 项离线契约测试，连同客户端配置的 8 项测试，共 23 项通过。覆盖设置被忽略、模式变化、不同 PID、RuntimeId 改变、已有文本、未知写入不重试、默认正控缺失、瞬时焦点变化、读回不一致、清理未确认、源码或 DLL 变化，以及默认关闭入口。没有单元测试创建真实窗口或调用付费 API。

```powershell
.\.venv\Scripts\python.exe -B -X utf8 -m unittest tests.test_uia_autofocus_contract tests.test_uia_client -v
.\.venv\Scripts\python.exe -B -X utf8 scripts/probe_uia_autofocus.py
```

第二条只输出 `state=disabled`。以下命令显式运行自建控件检查，结果写入忽略目录；它不连接微信，不启用真实发送：

```powershell
.\.venv\Scripts\python.exe -B -X utf8 scripts/probe_uia_autofocus.py --run-synthetic
```

## 真实草稿结果：未通过

当前报告仍为 `live_client_verified=false`、`focus_cause_verified=false`。标准 Edit 的对照证明本机公开配置能改变 UIA 的前置聚焦行为，**不能据此宣布微信原先抢焦点的原因已经确定**；微信 provider 也可能独立请求激活。

独立候选现已实现并执行一次。`adapters/uia_draft_candidate.py` 从新 FALSE 客户端取得绑定窗口，再以 Edit 类型和 `chat_input_field` 条件查找唯一输入框；不复用旧 pywinauto pattern。精确核对 PID、窗口和 root/input RuntimeId，重复确认空草稿，持久化 claim 后做最后停止检查，仅一次固定 `SetValue`。任何写后异常保留 `unknown`，没有 Invoke、重试或清理草稿的代码。

`scripts/probe_background_draft.py` 负责原资料卡账号绑定、进程创建时间／模块指纹、历史群聊 session/input RuntimeId、桌面观察及 45 秒采集上限，剩余时间用于终止自有工作进程及恢复一个临时无障碍字节。新协调器的 preflight/run 使用独占运行锁；该锁不代表所有旧控制台入口都已具备跨进程互斥。真实运行仍用原诊断 campaign，开始写入前持久化占用，不能因重启、超时或失败退还。此候选没有调试器，也未修改 DbgEng 准入条件。

操作者明确选择开始一次空闲测试后，记录如下：

| 阶段 | 实测结果 | 本机证据 |
| --- | --- | --- |
| 只读预检 | 1.703 秒；原账号进程及群聊控件匹配，空草稿，0 个事件，开关恢复，诊断未使用 | `autofocus-preflight.json` |
| 唯一草稿试验 | 2.125 秒；持久化 claim 后观察到 3 个桌面事件，停止，结果 `unknown`；工作进程退出、开关恢复、源码未变 | `autofocus-live-draft.json` |
| 操作者反馈 | 微信仍跳到前台，已手动清除固定草稿 | 同上报告的反馈字段 |
| 清理后只读复核 | 1.531 秒；相同目标和空草稿确认，0 个新事件，开关恢复；诊断已使用 | `autofocus-after-manual-cleanup.json` |

上述文件在 `.local/live-acceptance/`。最后两个运行的协调器 SHA-256 为 `fa11d4ef1c5b3b0c3f4102d52d9629fffa86028fdf8a1742750735ecf0a9e63c`，草稿候选为 `72c80edf243ed92a696859679ceb91a388d689a4cca6d7f098621f57ebfef1ea`。第一次只读预检使用 `7a094ac37f67862d4bdb650ded15e8cc66b1bd9470ea8fc12d0bb3753ffaa946`；随后仅将脚本导入改为包名路径，解决 unittest 发现阶段的同名导入冲突，完整回归后才运行真实草稿。各报告保存八个关联文件的完整指纹。

**这条实际候选没有解决微信抢前台。** 父端 `attempted=true` 是 durable claim 后的保守记账，报告未保存 worker 返回、事件类型／来源／阶段或实际调用栈；不能单靠事件数量确定 SetValue 内部执行时刻、判断 FALSE 被忽略，或断言某个微信接收者必然强制激活。用户反馈确认了本次微信窗口激活，但尚无完整写后内容回读证据。

清理后账本核验为诊断已用、发送 0/3、OpenAI 0/10；没有调用发送或模型。Weixin.dll SHA-256 仍为 `932dd6700f22e32faeb9b0cc9581a1f601e60ace151067680d3f6596496fab99`，防撤回备份仍为 `e0428a7b691853de496532523c2c09aa095f90941c37c9f4cfb3f137f3c66556`。不能删除账本、换 campaign 或再次运行真实草稿试探。M1 仍未完成，生产读取／发送 profile 继续关闭。

当前只能继续合成与只读研究。优先检查原标准控件测试的 `ElementFromHandle(A)` 与真实候选 `root.FindAll(...)` 路径是否存在配置继承差异，以及旧客户端读对象同时存活是否影响新客户端。即使这些合成对照通过，也不能拼接成微信根因或批准第二次试写。

默认入口只验证已有合成报告，不操作微信：

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/probe_background_draft.py
```

默认 `prepared` 仅表示合成前置证据有效，不表示有可用真实尝试。完整离线回归在本次真实操作前为 **391 项通过（12.154 秒，无跳过）**，包含 16 项草稿候选及 20 项协调器测试；单测不执行原生调用或付费 API。

后续不以继续扩大 ETW 跟踪作为这条公开客户端配置路线的前提。现有 ETW 合成证据保留作诊断参考；外部控制器清理预检虽已通过，仍不是完整故障矩阵或 DbgEng 恢复证明，详见 [用户态焦点研究](M1_USERMODE_FOCUS_REVIEW.md)。

## 树路径与旧读取对象共存：独立合成脚本

新增 [probe_uia_autofocus_tree.py](../scripts/probe_uia_autofocus_tree.py)，只复用冻结标准控件脚本的自有 private non-input desktop、GUI/MTA 分离、时限、焦点通知及资源清理。它不修改旧脚本或覆盖旧原生报告；运行前要求旧脚本 SHA-256 精确一致。新的控件 pattern 均由绑定宿主的 `root.FindAll(Edit + AutomationId)` 取得，用于检查直接 `ElementFromHandle(A)` 与真实候选查找路径的差异。

四个阶段分别是旧 `CUIAutomation` 默认、新 `CUIAutomation8` 默认、新客户端 FALSE，以及 FALSE 与旧版原始 COM 读取对象共存。前两组必须提供实际局部焦点正例；后两组要求完整文本回读且没有局部焦点通知。共存组保留旧客户端、root、匹配集合、element 和 pattern，在与真实候选相同的六个检查点读取自有控件，最后再核对固定文本。这里没有导入 pywinauto 或重建其全局缓存，**不能把原始 COM 共存对照称为完整 pywinauto wrapper 复现**。

FALSE 两组直接调用既有 `write_fixed_draft`。只在自有子进程的 Python 模块内，把查找常量临时替换为已经核验的自建 Edit AutomationId `101`，退出时恢复；不修改固定草稿、候选源文件或客户端二进制。合成 claim 只统计本次自有控件调用，不接触真实诊断账本，也不会恢复已耗尽的真实尝试。独立报告路径为 `.local/live-acceptance/uia-autofocus-tree-synthetic.json`，包含新脚本、真实草稿候选和复用文件的指纹；`full_pywinauto_wrapper_verified`、`live_allowance_touched`、`live_retry_authorized` 均保持 false。

该脚本 SHA-256 为 `661ba674fb8f980292ab67ff331a3943c98ab349c8003eb16a38897f4af052b4`。新增 10 项纯离线契约测试，验证所有写 pattern 来自树查找、实际复用草稿候选、独立新客户端、旧读取对象共存、歧义/错误 AutomationId 拒绝、未知结果不重试、模块常量恢复、焦点正反例及默认关闭。相应测试与既有客户端/草稿/源测试合计 49 项通过；原生结果须由独立运行记录判定，不由离线测试推出。

```powershell
.\.venv\Scripts\python.exe -B -X utf8 -m unittest tests.test_uia_autofocus_tree_contract -v
.\.venv\Scripts\python.exe -B -X utf8 scripts/probe_uia_autofocus_tree.py
```

第二条已验证只输出 `state=disabled`，不创建窗口。唯一可选运行开关为 `--run-synthetic`，它只允许自建控件，不接受外部 PID、HWND、账号或真实验收凭证。

最终完整离线发现运行使用项目内 `.local/test-tmp-autofocus-tree` 作为 TEMP/TMP：**401 项通过（11.749 秒，无失败、无错误、无跳过）**。命令为 `python -B -X utf8 -m unittest discover -s tests -v`，完整输出保留在 `.local/live-acceptance/offline-autofocus-tree-final.txt`。此次完整回归没有运行原生 UIA/ETW canary、真实微信或付费模型；它不改变上文唯一真实草稿失败及诊断已使用的结论。

随后独立原生运行通过，报告 `uia-autofocus-tree-synthetic.json`：四阶段焦点事件分别为 **4 / 4 / 0 / 0**，FALSE 两组调用真实候选均 `readback_confirmed`，旧 reader 共存组完成 6 次检查和最终完整回读。294 个输入桌面／前台／剪贴板抽样未变，全部清理确认、无强杀、DLL 前后及六项源码指纹相同。此结果排除了本机标准 Edit 中“FindAll 或一个旧 raw reader 必然破坏 FALSE”的简单解释；没有复现完整 pywinauto 缓存和微信 provider，不能认定根因已定位。

## 有界系统 DLL 静态续查

同一 SHA 的 UIAutomationCore 中，`get/put_AutoSetFocus` 的 RVA 为 `0x1ac360 / 0x1acbf0`，直接读写客户端 `+0x364`。`FindAll`（`0x10bd20`）与 `ElementFromHandle`（`0x10c200`）均调用元素包装 helper `0x10c904`，当前片段未发现 FindAll 直接把配置改回 TRUE。底层 node 的上下文传递未完整闭合，不能只凭共有 helper 证明所有路径都继承正确。

限定检查 `CValuePattern::SetValue`（`0x1c6670–0x1c67f4`）及两个直接 helper：先经虚表 `+0x28` 检查只读状态，在 `0x1c6746` 调用 `0x226940(pattern+0x50)`，随后选择远程 operation `0x25` 或 `0x19c9b4(pattern+0x58,text)`。后者的内嵌字符串为 `IValueProvider::SetValue`，调用对象虚表 `+0x18`；前者在系统／窗口条件下查询 `IInternalAutomationElement` 并调用虚表 `+0x20`。这个内部虚方法与 AutoSetFocus 的具体关系尚未解析，不能直接命名为 SetFocus，更没有证明真实失败经过哪个分支。

复核工具为本机忽略目录 `.local/uia_static_probe.py`，使用固定 Capstone 5.0.6、确切 DLL SHA 和 PE `.pdata` 函数边界；以模块方式调用 `inspect(0x1c6670)`、`inspect(0x226940)`、`inspect(0x19c9b4)`。本次反汇编仅在工具 stdout，没有新增静态转储文件，也未执行这些地址。

后续独立有界报告 `uia-autofocus-internal-branch-static.json` 已将上述内部虚调用解析到 `0x226880`。QI 的确切 IID 分支返回元素对象 `+0x10`，构造函数将该处虚表安装为 `0x2db2e0`，`+0x20` 槽为 `0x226880`；本机生成 COM 接口的 AST 布局与三个磁盘候选虚表共同将客户端 `+0x1d0` 槽对应到 `get_AutoSetFocus`。内部 `0x2298e0` 同时由公开元素虚表的首方法和 `CAutomationElement::SetFocus` 字符串定名。

该函数存在两条不同分支：关联对象非空且 getter 返回 FALSE 时，`0x2268f2` 跳过这次 SetFocus；**node 查询成功却返回空关联对象时，`0x2268bd` 直接进入 SetFocus，不读取 AutoSetFocus**。这是确切二进制中的静态分支，不是微信失败的运行时证据。node 的虚表 `+0x30` 及返回对象到客户端的可写指针来源仍未闭合，不能宣称微信命中了空对象分支、FALSE 被忽略或客户端强制聚焦已经定位。真实试验没有采到该调用栈，已用诊断额度也不会因此恢复。

该 JSON SHA-256 为 `e4d1a909a6aac9595174546e25b2b728d9891e9bb0a01ee58ee3608522b52878`，保存 11 个显式 `.pdata` 函数、一个以直接调用/ret/填充边界限定的构造叶函数、虚表及限制。系统 DLL 前后指纹相同；没有加载接口、访问实时进程或执行研究地址。后续需要核对 node 关联来源，不能用这条静态 NULL 分支替代实际因果链。

另一个独立增量报告 `uia-autofocus-node-context-static.json`（SHA-256 `239c5d9909e13ec19184808b8c888da517d1f8a7d88f3efcab3ff20147d26b92`）限定新增 8 个函数。`ElementFromHandle → 0x10c694 → 0x79e3c` 创建关联记录，`0x79e84` 将记录 `+0x10` 设为 `client+0x2e0` 的地址；经 `0x98e04 → 0x19ac0` 把关联参数传到 `0x28960`。当前仅证明参数传递，尚未解析返回 node 的虚表 `+0x30`。创建流程中的空 node 会报错，不能把它等同于上述有效 node 返回空关联的分支。FindAll 的结果封装和 node 内部关联仍未闭合，没有据此形成可验证的新公开写入入口。

## 操作者将微信移到“桌面2”后的只读检查

操作者随后手动将目标微信移到 Windows“桌面2”，这是新的环境条件，尚未做跨虚拟桌面激活或微信写入试验。只读报告 `.local/live-acceptance/virtual-desktop-readonly.json` 核验原进程创建身份仍匹配，目标与当前程序处于同一登录会话，目标 GUI 线程及当前输入的 Win32 desktop 名称均为 `Default`。检查仅使用进程时间及桌面／会话元数据，没有 UIA、聊天读取、临时字节修改、移动窗口或注入输入。

Windows 任务视图的虚拟桌面是窗口分组，隐藏非当前组的窗口；不是另一个登录会话或独立 Win32 输入桌面。[微软虚拟桌面接口](https://learn.microsoft.com/en-us/windows/win32/api/shobjidl_core/nn-shobjidl_core-ivirtualdesktopmanager)说明了此分组行为。交互 window station 中只有当前输入桌面接收用户输入，剪贴板属于 window station；本报告未取得 shell 虚拟桌面成员 ID，也未实测跨桌面激活效果，因此不能宣称“桌面2必然抢焦点”，同样不能保证随意点击／打字不影响桌面1。[输入桌面](https://learn.microsoft.com/en-us/windows/win32/winstation/desktops)、[window station](https://learn.microsoft.com/en-us/windows/win32/winstation/window-stations)

这与前文自建 canary 的独立 private Win32 desktop 不同。后续若研究任务视图隔离，应先验证自建窗口的可见性、激活和输入边界，不能直接以移动到“桌面2”为由重试微信或重置既有账本。

## 2026-10-26：关联对象与观测点的静态复核

新报告 `uia-autofocus-node-return-static.json`（SHA-256 `877ad0573e06f8cd473627db9bd7374ec92ab8010e130da81c71af112e81d6c9`）在 12 个新增函数范围内闭合了新建 node 的返回接口：主体 `N+0x50` 保存传入关联记录 A，接口位于 `N+0x30`，其虚表 `+0x30` 指向 `0x15b790`。该 getter 读取 `this+0x20`，等于 `N+0x50`；A 为空也返回 S_OK。因此前文 NULL 聚焦分支确实可以是“有效 node 返回空关联”，而非创建失败。关键 getter、构造函数及虚表由第二名审查者重新从相同 SHA 文件解码核对。

这不证明微信命中了该分支。新建路径中 `A+0x10` 保存 `client+0x2e0` 的地址，后续读取 `client+0x318` 处的指针；目前只闭合了地址计算，没有证明这个字段保存哪个接口、对应本次客户端，或 FindAll 的复用／远程 node 属于同一构造类。较大的遍历函数超出既定 8192 字节边界后未继续解码。没有将研究地址或内部调用加入适配器。

另一个仅磁盘报告 `uia-setfocus-etw-static-20261026.json`（SHA-256 `fe4f085b284f19d17b77b185a507c2307372afc1e31018d1cfaedddec6005034`）精确匹配 SetFocus 的 335/336 事件描述符，无 payload。入口写后返回 `0x229928`；退出经 `0x2299da → 0x226604`，写后返回 `0x22663c`、外层返回 `0x2299df`。这是可供后续校准的观测点，不是已经采到的微信调用栈。本轮没有新增 ETW 会话或 TextChanged 订阅。

对照脚本审查发现更直接的实验缺口：此前 direct/tree canary 的 UIA MTA 和 GUI 控件同属一个进程，真实候选与微信分属不同进程。下一项独立实验保持冻结 tree 四阶段、真实草稿候选、private non-input desktop 和正反例要求，仅拆开 GUI 与 UIA 进程；即使通过，也只排除标准 Edit 中“跨进程本身足以破坏 FALSE”的解释。

## 跨进程树路径对照：首次原生通过

`scripts/probe_uia_autofocus_cross_process.py` 由顶层直接持有两个自建 Process，GUI 宿主继续使用冻结基线的消息泵，实际 UIA 调用在另一 MTA 进程执行。同一个远端进程顺序执行四阶段，每阶段仍使用新的客户端，避免重启进程同时改变缓存条件。两组 FALSE 直接复用冻结 `tree_phase` 中的 `write_fixed_draft`；第四组的旧 raw reader 与新客户端在远端共存，没有重建完整 pywinauto 缓存。

每个进程核对创建身份，UIA 仅绑定本次自建的三个 HWND；relay 使用 nonce、严格序号、阶段和动作顺序。客户端确认 COM 结束、恢复线程桌面、关闭桌面／进程句柄并实际退出后，宿主才允许正常销毁控件。根和 relay 两路清理报告必须相同；任何强杀、退出不确认或句柄关闭失败都不能通过。极端故障保留 failed/recovery_required，持有 Python 对象不代表恢复成功。该脚本不是调试器恢复框架。

原生报告 `.local/live-acceptance/uia-autofocus-cross-process-4505c68e833a40ab8850d6701033cf48.json`，SHA-256 `a4d99dbeb4c1fd93066761d9a2ad5ca473bb011a28637dd13b7c9cf524926a8c`：

| 阶段 | 局部焦点事件 | UIA／原生完整回读 |
| --- | --- | --- |
| 旧客户端默认，树查找 | 4 | 均通过 |
| 新客户端默认，树查找 | 4 | 均通过 |
| 新客户端 FALSE，真实候选 | 0 | 均通过 |
| FALSE 加旧 raw reader | 0 | 均通过；6 个 reader 检查和最终回读通过 |

OS `10.0.26300`，系统 UIAutomationCore SHA 仍为 `be5037bf46e61566d3e5a83a6c628cebe9035771e4ae741a7b9aa27b5696d989`。客户端与 GUI 的进程身份确实不同，四阶段客户端身份相同；301 次输入环境抽样未变，所有窗口、私有桌面、COM 和进程句柄清理确认，两进程正常退出 0，无强杀。七个关联源码及 DLL 前后哈希相同。抽样不等于连续的无干扰证明，更不能代表用户的桌面 2 或真实微信。

新脚本 SHA-256 `793d70854906bf80cf0f2fc0c4ee9144551ab1d950205070dd2120b5275f4675`，新增 22 项纯契约测试；完整离线 **445 项通过（12.546 秒，无跳过）**，输出保存在 `offline-cross-process-20261026.txt`。未增加依赖或改动冻结基线／树脚本、生产适配器、真实凭证和客户端文件。

```powershell
.\.venv\Scripts\python.exe -B -X utf8 -m unittest tests.test_uia_autofocus_cross_process_contract -v
.\.venv\Scripts\python.exe -B -X utf8 scripts/probe_uia_autofocus_cross_process.py
```

第二条默认只返回 disabled。单次原生复现需显式追加 `--run-synthetic`，只接受自建窗口，每次保存不同名字的报告，不覆盖旧证据；首次运行已完成，无需原样重跑。此结果把剩余问题收敛到尚未验证的 provider／内部 node 关联等差异，不能因此断言微信 receiver 强制激活。下一项可先在已成立的自建正控校准 335/336 SetFocus 同步栈，实际微信调用链仍缺证据，真实写入继续禁用。

## 2026-10-26：SetFocus 同步栈采集器与首次未完成校准

新增 `scripts/uia_setfocus_evidence.py`（SHA-256 `9cfebdb4e7aa5d99500db2c957668559297d86b14bb6341e81c6f70ead67cf45`）及独立运行器 `scripts/probe_uia_setfocus_stack.py`（`1448ed7bbf3f175a51dff454c907eba5ca055913767330dc5fe55254bee520ea`）。冻结 cross/tree/base 及真实草稿候选文件均未修改，生产读取和发送表仍为空。环境 OS `10.0.26300`、Python 3.12.10，UIAutomationCore 指纹仍为前述 `be5037bf...5696d989`。

该运行器只做 `modern_default_tree` 正控，GUI、UIA MTA 客户端和私有 ETW owner 均为根协调器直接创建的进程。启动身份绑定 PID、创建时间及持有句柄；没有外部 PID 参数。事件只使用一个自建 UIA PID 的现有私有会话过滤，335/336 还必须匹配 MTA 线程、确切描述符及空 payload；只有这些事件才可读有界 x64 栈并匹配先前静态定位的返回地址链。已知非目标事件仅计数，不读取正文或栈；未知来源/schema 及超过 512 条均失败。

owner 从 StartTrace 前开始两秒截止，启动时距离 client 原有 15 秒看门狗至少五秒；根协调器的受控 client 终止必须先取得匹配的 Stop、Query 和目标仍存活证据。提前目标死亡或仅 Query4201 不能判恢复成功。记录未完成时保留 ETL 和意图文件，不增加真实额度。此设计不等于所有联合故障已验证，更不打开微信诊断。

首次自建原生报告：`.local/live-acceptance/uia-setfocus-stack-50b0e8167a4643d6bb9d473439506f46/report.json`，SHA-256 `3eea253cac96fa561153f8a6c6bcefebd9a9cf98b6f7cb383ce28b4b287b7b76`。

| 检查 | 实际结果 |
| --- | --- |
| 总体 / 观测窗口 | **失败**，`capture_window_incomplete`；owner 因两秒截止停止 |
| 跟踪停止 | 名称/文件/GUID 匹配，Stop 0、Query 4201、零丢失；目标在停止前后和 ACK 时仍存活 |
| 后续 client | 根在 Stop 证实后终止其自建 UIA client，退出 `-15`；没有取得正常 COM 清理 ACK |
| 其他资源 | owner 正常退出 0；GUI 进程退出，窗口及 private desktop 消失，三个 Process 和目标句柄关闭 |
| 完整原生清理 | `native_cleanup_confirmed=false`，不能用资源消失替代全部正常清理步骤 |
| 输入环境 | 504 次输入桌面、前台、剪贴板抽样未变；不等于连续证明或“桌面 2”隔离验证 |
| 指纹 | 12 个来源及系统 UIA 文件前后一致；没有微信、debugger 或 OpenAI 操作 |

缺少 final snapshot、完整 phase result 和合格栈观测，因此不能证明 SetValue 已调用、卡住或没有触发 SetFocus。随后只对保留 ETL 做离线 header 检查，未读取 payload 或栈：`.local/live-acceptance/uia-setfocus-stack-50b0e8167a4643d6bb9d473439506f46/partial-header-review.json`（SHA-256 `ea32f06fe3051075a552d435c49ba4baf47c3421cd6480dd75e0c3b694ef834b`）。文件消费/关闭成功，前 512 条均为预期线程的已知非目标事件，第 513 条触发上限，此后停止计数。该有限前缀中有三组 ElementFromHandle、三组 CreateCondition 及 FindAll 入口，符合准备后进入控件发现的顺序；**不能从前缀中的零 335/336 推断整个窗口没有 SetFocus**。

23 项纯事件测试及 20 项运行器契约测试通过；完整离线 **488 项通过（11.558 秒，无跳过）**，日志 `.local/live-acceptance/offline-setfocus-stack-20261026.txt`。覆盖来源/线程/schema、坏栈、丢失/顺序/上限、未配对事件、错误进程创建身份、过晚启动、Stop 身份/存活性/未知状态及完整资格检查。默认入口与离线复现：

```powershell
.\.venv\Scripts\python.exe -B -X utf8 scripts/probe_uia_setfocus_stack.py
.\.venv\Scripts\python.exe -B -X utf8 -m unittest discover -s tests -p 'test_uia_setfocus*.py' -v
```

首条只输出 disabled；上述原生校准已结束，不原样重跑。后续已把开窗移到现代正控第二个 `check` 回执后：此时树查找、pattern 取得和空文本检查已完成，下一动作才是同一 pattern 的 SetValue。新增纯协议 `CaptureWindow` 严格接受 prepare/check/check/complete，验证身份、焦点和余下存活时间；乱序、重放和旧边界报告拒绝。运行器契约从 20 项增至 28 项，合并事件解析共 51 项通过；当前全套 559 项通过。旧运行器和对应测试归档在上述失败报告目录，保留原证据指纹。后续缩窗版本的原生测试确认所选正控本身超过两秒；仅自建采集上限据此调整为固定三秒、启动余量六秒，512 条限制不变，正常管道关闭竞态修复后完整校准通过。完整依据、阶段失败和最终指纹见 [SetFocus 合成校准](M1_SETFOCUS_CALIBRATION.md)，不能使用 TextChanged 或真实微信补足自建正控。

## 2026-10-26 任务视图桌面 2 实测

操作者另行指定发送账号、专用群与私聊，并明确授权一次桌面 2 固定草稿；原已消费尝试不重置。新增独立固定单次账本，跨重启和桌面 GUID 不产生新额度。资料卡精确身份、选中群和空稿只读预检通过。真实草稿于 commit_write 阶段记录 1 次前台及 2 次焦点事件，1.672 秒停止，结果 unknown，没有完成写后回读。目标最终仍报告 current=false，但系统前台 HWND 已变为该目标；用户随后确认“还是切换屏幕了”。因此桌面 2 没有使本次路径满足无干扰要求，不能作为修复。

用户手动清稿后，1.469 秒只读复核确认同一目标、空稿、零新增事件和临时开关恢复。新增尝试已消耗，旧诊断与发送/API 账本哈希未变，发送及请求仍为 0。具体环境、修订与本机证据见 [任务视图对照](M1_TASKVIEW_DESKTOP2.md)。生产写入仍关闭；后续继续自建正控的调用链研究，不能重跑这次真实草稿或通过事后恢复焦点掩盖切屏。

## 2026-10-26 客户端关联续查

新的真实只读证据确认：旧读取器先初始化时，FALSE 请求客户端的微信输入框节点关联到另一 TRUE 客户端；先创建并持有 FALSE 客户端与目标节点后，关联归属和配置均一致。已实现初始化、对象复用和实际 pattern 守卫，真实预检通过，尚未写入验证。完整运行时布局、对照、605 项回归和待验收边界见 [UIA 关联修复](M1_UIA_CLIENT_CONTEXT.md)。此前真实切屏与两个已消费账本仍有效，不能以此重试旧 unknown。
