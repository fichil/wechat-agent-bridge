# M1 异常恢复续查与 ClawBot 对照（2026-10-23）

2026-10-26 新证据：[原调试对象保留与原生事件接管](M1_DEBUG_OBJECT_RETENTION.md)。保留对象没有恢复 DbgEng 计数；独立原生事件候选在持有者实际崩溃／强杀等六例中恢复全线程及 DR。它尚未具备完整故障覆盖或栈采集，原 DbgEng 资格失败及真实诊断禁用保留。当前离线 680 项通过；下文计数和失败为此前阶段证据。

**调试器异常退出恢复仍失败；客户端顺序修复后的单次后台草稿已通过无干扰检查，完整 M1 未完成。** 最新完整离线 609 项通过（12.070 秒）；只读关联对照与候选初始化修复见 [UIA 关联报告](M1_UIA_CLIENT_CONTEXT.md)，修复后的新单次草稿已完整回读、零事件且用户确认无切屏；发送尚未验收。独立 SetFocus 自建 ETW 校准现已在固定三秒窗口及正常管道关闭修复后通过完整同步栈、Stop 和正常资源清理；本次合成续作没有操作微信、执行 DbgEng 或调用模型。该结果不改变原线程暂停／DR 恢复失败，也不代表所有联合故障恢复完成。详情见 [合成校准修复](M1_SETFOCUS_CALIBRATION.md)。此前真实 AutoSetFocus 及桌面 2 草稿均仍抢焦点，两个已授权单次尝试均消耗且已清稿，生产写入继续禁用。下文 488/445/423/318/283/238 项及旧账本状态均为此前阶段记录。

## 新附加选项的独立验证：仅主线程恢复，整体失败

新增 `scripts/probe_debugger_resume_flag.py`，用官方 `DEBUG_ATTACH_INVASIVE_RESUME_PROCESS=0x10` 检查是否能恢复已死亡 debugger 留下的暂停。该选项在官方文档中描述为附加时恢复线程；文档没有提供暂停计数所有权令牌，不能由 API 名称断言保留已有暂停。[官方附加标志](https://learn.microsoft.com/en-us/windows-hardware/drivers/debugger/debug-attach-xxx)、[暂停计数语义](https://learn.microsoft.com/en-us/windows-hardware/drivers/debugger/controlling-processes-and-threads)

脚本只创建无窗口自有目标、两个独立 bootstrap 和调试 worker，不接受任意 PID，不设置硬件断点。计划最多两场景：零暂停基线恢复通过后，才测试一个预先暂停的自建辅助线程能否保留原计数；首项失败就停止。进程和线程句柄核对所属 PID、存活及创建时间；线程检查只配对撤销自己刚加的一次暂停。负例仅在完整基线恢复后才释放自己预先增加的一次暂停，不按猜测修正计数。默认不启动原生逻辑，真实准入字段固定为 false。

2026-10-23 原生报告 `.local/live-acceptance/debugger-resume-flag-6b1e36e55f3b4f6897766c62426417de.json`：

| 检查 | 实际结果 |
| --- | --- |
| 初始基线 | 4 条线程，暂停计数全 0，DR 全 0，无 debugger |
| 原 owner 在 attached 阶段被终止 | 4 条原线程及 1 条新增线程计数全 1，DR 全 0，debugger 已解除 |
| `0x10` 救援 | 附加及正常分离成功；主线程计数降为 0，心跳连续两秒运行 |
| 最终完整恢复 | **失败**：另外 3 条原线程及 1 条新增线程计数仍为 1，线程集合也未回到初始值 |
| 后续场景 | 未运行原有暂停负例；未设置或测试硬件断点 |
| 清理 | 目标正常退出 0；旧 bootstrap 因暂停被终止；全部自建进程结束，cleanup errors 为空 |

这一结果不能证明 `0x10` 总是只恢复主线程，也不能解释其内部实现；它证明本机当前实验没有恢复完整目标状态。**心跳恢复不是全线程恢复。** 不增加额外 ResumeThread，不忽略残留线程，不重试相同实验，也不将此选项加入真实适配器。

环境为 OS `10.0.26300`、DbgEng SHA-256 `bfa188b10eb64a94f1eb59bfb0f8d85eb5dfc803cd0f6b5c554816fe311a236e`；脚本 SHA-256 `cd7c2d80e0cc76f8bbf04d1105b2b93c5f6f7823a595a5836b8f4de8c9d6326e`，DebugEngine 模块为 `6d9f11b483fc9393ba2bfe1b7b4255a57651e56fdf55e4fbbc9858bd7df26e6e`，运行前后指纹相同。20 项新增离线契约及完整 423 项回归通过；原生报告仍为失败。以下默认入口可检查关闭状态，不启动调试器：

```powershell
.\.venv\Scripts\python.exe -B -X utf8 scripts/probe_debugger_resume_flag.py
```

单次原生实验使用了 `--run-synthetic`，本轮已完成，不需要重跑。备用无暂停 ETW 的四项独立 owner 故障恢复另已通过，但它不能替代 DbgEng 的线程/DR 恢复；详见[用户态恢复证据](M1_USERMODE_FOCUS_REVIEW.md)。

## 2026-10-26：公开附加标志的有界静态复核

同一指纹的 DbgEng 磁盘分析确认 `DebugCreate` 的确切 IID/QI 返回 IDebugClient 对象偏移 `+8`，第 12 槽对应真正的 `AttachProcess` 入口 `0x1251f0`。原始 `0x10` 经参数校验、target 和 transport 原样传递，并在上层待处理记录 `+0x18` 映射为内部 `0x80` 位。因此不能用“项目封装丢失标志”解释现有恢复失败，也没有依据改用主对象的另一个虚表槽。

直接下游 `0x35e3a8` 与 `0x35e534` 的完整有界解码确认：transport 只检查 `0x8` 的初始 break-in 语义，不处理 `0x10`，没有 ResumeThread/NtResumeProcess；它负责调试对象创建和系统附加调用。随后限定另外 12 个函数，已闭合上层内部 `0x80` 的消费者；此前另找的单线程 ResumeThread 块及 NtResumeProcess helper 不属于这条证据链。

证据位于 `.local/live-acceptance/dbgeng-attach-path-static-20261026.json`（SHA-256 `80c2f94100288e2b34bb5ee80655fd02c90d9542f860d94208c41ef4f5a86b67`）、`dbgeng-attach-transport-20261026.json`（`f75648661168adcb091fdae167a6973120d4a1892f1e4a95c616b99045843876`）及新增 `dbgeng-pending-resume-static-20261026.json`（`2b38fa6da31cad29f8d6bf21dee4479f51342c846f3ecf541766a4fb2c4ba8a2`）。全部为同一 DbgEng 文件的磁盘分析，未访问运行进程、加载 debugger 或重跑原生矩阵。

| 静态位置 | 已绑定的行为 |
| --- | --- |
| `0x1bc6b0` 的 CREATE_PROCESS 分支 | 查询 pending 的 `0x80`，保存目标 PID，随后经 `0x167c8c` 删除记录 |
| target `+0x5f0 → 0x1b8a90` | 先派发当前 CREATE_PROCESS；`0x114260` 建立进程记录及一个初始线程记录 |
| target `+0x2b0 → 0x167d10` | 派发返回后遍历该进程**当时已登记**的线程链 `+0x170`，取其有效句柄 |
| transport `+0x118 → 0x365430` | 对传入的每个句柄执行一次 ResumeThread，不枚举操作系统全部线程 |
| 后续 CREATE_THREAD `→ 0x1148c4` | 加入其余线程记录，已删除的 pending 恢复动作不会再次执行 |

新建调试器模型通常在该恢复时点只有初始线程，这与此前实测“主线程恢复，其余仍暂停”吻合。[微软附加事件顺序](https://learn.microsoft.com/en-us/windows/win32/api/debugapi/nf-debugapi-debugactiveprocess)及[初始线程句柄定义](https://learn.microsoft.com/en-us/windows/win32/api/minwinbase/ns-minwinbase-create_process_debug_info)支持此解释。**这是确切 DLL 的静态机制和有条件推断，不是新取得的运行时调用栈**；未展开所有间接回调，若回调重入事件泵，已知线程集合可能不同，当前测试封装没有这种回调。

因此该函数本身不能概括为“硬编码只恢复主线程”。改变外层 WaitForEvent 的等待时点不会把已消费的动作挪到后续线程事件之后，也未找到公开的延迟恢复入口。额外恢复所有线程、清零 DR 或调用内部函数仍无法证明保留原状态，不能作为修复。此路径已收敛到明确限制，不重跑相同实验；真实调试准入继续关闭。

## 此前阶段记录

新增 UIA 用户态研究取得了不同于 Win32k 的受限实证：UiaRaiseAutomationEvent 的入口／返回事件在同一自建进程中可采到原调用栈，4 次 NULL provider 调用对应 8 条事件，0 拒绝和丢失，资源清理完成。先前 private session 的错误 87 已定位到本机对 EVENT_ID/STACKWALK filter 的拒绝分支；成功检查仍严格限定 PRIVATE_IN_PROC，没有扩大到其他进程。跨进程范围和真实通知尚未验证；详见[用户态入口证据](M1_USERMODE_FOCUS_REVIEW.md)。这不改变 DbgEng owner 强杀恢复失败的历史结果。

随后随机合成 provider 的私有跨进程 PID 双向对照通过，各方向收到 4 正例、0 负例；UIAutomationCore 自身的组合检查也已通过，两个阶段各 8 条目标事件及栈、0 条另一进程事件。固定系统 provider 的清理额外核验 Query 实际输出的会话名和临时文件路径，两个阶段均正常停止、零丢失并完成清理。下一步限于自建进程验证异常退出和超时清理，以及单元素监听条件，不能以正常退出结果替代 DbgEng 恢复或直接启用微信诊断。账本只读核验仍为发送 0、OpenAI 0、真实诊断预留 0。

## 已实现修正

- DbgEng 操作限定到创建接口的线程，跨线程调用在进入原生接口前拒绝。
- 断点成功移除后立即出队，部分失败后不再访问已移除的接口。
- 分离时仅将 breakpoint 回调视为已识别断点；普通应用异常使用 GO_NOT_HANDLED，不因异常代码相近而吞掉。
- 分离失败时保留接口供清理重试，不在仍附加时释放最后一个接口。
- 原生测试检查系统调试关联、全部目标线程的暂停计数和 DR0–DR3/DR6/DR7，心跳恢复后再观察两秒。线程集合变化不能判通过。
- 恢复报告 schema 2 绑定 OS、工具及系统 DLL 指纹，要求完整 24 场景各连续 10 次通过，拒绝重复、缺失、旧格式及 Win32 对照报告。开始新测试即令旧报告失效，测试中代码变化也不准入。

`diagnose_focus.py --run` 仍固定拒绝，不消耗真实诊断凭证。真实写入协调器、异步因果采集和新后台发送候选尚未开放。DbgEng 的分析/监护进程重构尚未实现，也不能仅凭该重构删掉调试进程自身强杀测试；下文 ETW 实验的 owner 不承担 DbgEng 恢复。

## 实测结果

环境：Windows 11 `10.0.26300`，Python 3.12.10 x64，系统 DbgEng 10.0.26100.1，诊断修订 `focus-diagnostic-2`。原生测试仅操作自己创建的合成进程，没有任意 PID 命令行入口。

DbgEng 的单目标和引导进程加目标两种拓扑，各测试正常、异常退出、强制终止，共 **24 场景：12 通过、12 失败**。

| 注入阶段 | 正常清理（两拓扑） | 异常退出 / 强杀（两拓扑） |
| --- | --- | --- |
| 附加后，尚未设置断点 | 2/2 通过 | 0/4；调试关联已解除，线程暂停计数由 0 变为 1 |
| 硬件断点命中并读栈后 | 2/2 通过 | 0/4；额外暂停及硬件断点寄存器残留 |
| RemoveBreakpoint 返回后，尚未分离 | 2/2 通过 | 0/4；线程上下文仍有残留 |
| 正常分离完成后 | 2/2 通过 | 4/4 通过；线程、心跳及寄存器恢复 |

失败不能仅归因于多目标引导，也不能靠只移除断点修复。这里证明的是当前 DbgEng 路径的残留状态，未定位系统 DLL 内产生暂停的精确指令，更不是微信激活窗口的实际调用链。

原生 Win32 对照正常分离 **1/1 通过**；断点处退出及强杀 **0/2**，合成目标抛出 `OSError: exception: single step` 并退出。此对照没有调用栈实现，不进入正式适配器。

初次矩阵已有确定失败，因此未运行“每场景连续 10 次通过”的资格测试。没有向微信尝试清零寄存器或额外 ResumeThread；合成检查只配对撤销自己刚增加的一次暂停，不减少此前已有暂停。微软的[退出分离选项](https://learn.microsoft.com/en-us/windows-hardware/drivers/debugger/debug-process-xxx)及[事件循环说明](https://learn.microsoft.com/en-us/windows/win32/debug/writing-the-debugger-s-main-loop)不能替代本机恢复核验。

## 再附加恢复与事件所有权续测

本轮又修正两项实际缺陷。先前的线程装饰器没有在 `DebugEngine.__init__` 中设置归属字段，导致真实构造路径可能跳过检查；现在构造时设置，字段缺失也拒绝，并用 mock COM 的真实构造及另一线程调用回归。另增加带序号的待处理事件：继续请求失败保留原事件，等待成功后才退休已继续事件，新到达的异常不被旧断点状态覆盖。

修正后重跑完整原生矩阵，仍为 **12/24 通过**；Win32 对照仍为 **1/3 通过**。所以这两项修正不等于异常退出恢复完成。

新增 `scripts/probe_debugger_recovery.py`，只接受其自行创建的目标，不提供任意 PID 参数。强杀后确认工作进程退出和调试关联解除，再由使用全新引导子进程的恢复调试器重新附加。启动即令同名旧报告失效，代码指纹变化及子进程退出无法确认均失败；所有报告固定 `live_eligible=false`。

| 自建目标上的恢复实验 | 结果 |
| --- | --- |
| 初始附加处强杀，普通重新附加并分离 | 接口成功，原线程仍暂停；所有最终线程暂停计数为 1 |
| 硬件断点处强杀，普通重新附加并分离 | 调试寄存器清理成功，但所有线程暂停计数仍为 1，心跳没有恢复 |
| 初始附加处强杀，使用 `INVASIVE_NO_INITIAL_BREAK` 再附加 | 等待初始事件超时，线程未恢复 |

这些目标的初始调试寄存器均为零；实验不证明能保留第三方已有断点。初始附加失败场景还存在额外线程，不能忽略线程集合变化。没有追加 `ResumeThread` 或手动清零寄存器来把失败改成通过。

微软文档说明，DbgEng 在附加/停止时增加线程暂停计数、执行/分离时减少；[重新附加流程](https://learn.microsoft.com/en-us/windows-hardware/drivers/debugger/reattaching-to-the-target-application)也明确提示暂停计数可能仍过高。[线程控制说明](https://learn.microsoft.com/en-us/windows-hardware/drivers/debugger/controlling-processes-and-threads)与本机残留吻合，但不能证明退出后的某一次暂停属于本项目。普通 `ResumeThread` 没有暂停所有者令牌；本轮没有将这种猜测用于微信恢复。

复现上述独立实验（当前均退出 1）：

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/probe_debugger_recovery.py --stage attached --attach default
.\.venv\Scripts\python.exe -X utf8 scripts/probe_debugger_recovery.py --stage breakpoint --attach default
.\.venv\Scripts\python.exe -X utf8 scripts/probe_debugger_recovery.py --stage attached --attach no-initial-break
```

结果保存于 `.local/live-acceptance/recovery-probe-*.json`，不被真实诊断准入读取。`diagnose_focus.py` 继续返回 `diagnostic_native_recovery_not_verified`，一次性真实凭证未使用。

## 无暂停诊断候选与静态复核

新增 `scripts/inspect_focus_events.py` 仅读取系统 TDH 清单。本机 Win32k 有 369 项事件，UIAutomationCore 有 237 项事件；焦点事件 2/26/258/259 的整数 payload 条件均可构造并释放。**构造成功不证明跟踪过滤有效，也不证明能采到用户态调用栈。** 初始元数据记录在 `.local/live-acceptance/focus-event-metadata.json`，没有启动跟踪。

Win32k 是内核 provider，微软的 [PID scope filter](https://learn.microsoft.com/en-us/windows/win32/api/evntprov/ns-evntprov-event_filter_descriptor) 不能用于启用这种注册；因此不能直接声称按 PID 过滤可用。[WPR 的 Win32k 栈示例](https://learn.microsoft.com/en-us/windows-hardware/test/wpt/eventprovider)仅作为合成实验依据。必须另外验证提交的 payload 条件确实排除非目标、捕获调用栈以及检测丢失；不允许移除过滤来取得结果。

静态分析新增 18 个明确函数边界的复核，覆盖先前遗漏的叶函数和条件尾跳。确认激活入口到平台虚表槽的对应关系，同时排除一条由空参数与非空分支误拼成的路径。它仍未证明本次 SetValue 经过该入口，详见 [激活候选复核](M1_ACTIVATION_STATIC_REVIEW.md)。

### 私有桌面 ETW 实验与当前停止点

`scripts/test_focus_etw.py` 默认只输出说明。显式 `--run-synthetic` 才创建两个自有窗口子进程，全部在新建的不可见桌面；没有 `SwitchDesktop`、键鼠模拟或剪贴板写入。仅启用 Win32k 事件 2 和指定自建线程的 payload 条件，两个线程互为正负对照，两阶段都通过才允许同事件限时采栈。非目标事件只计拒绝数并停止，不保留其字段或栈，不回退到无过滤采集。

独立 owner 负责创建、启用和停止唯一命名的会话；父进程超时后先确认 owner 已终止，才按随机名称与匹配 GUID 回收。事件元数据、x64 ABI、私有桌面关联恢复及清理路径已做独立代码审查。实验永远输出 `live_diagnostic_ready=false`，通过也不等于微信诊断获准。

实际执行结果：`StartTrace` 返回 **5 / ERROR_ACCESS_DENIED**，`trace_started=false`、`provider_enabled=false`、两个窗口的 exercise 次数均为 0，没有进入焦点切换或采栈阶段。`children_finished=true`、`desktop_closed=true`、`user_desktop_unchanged=true`；额外核对没有遗留实验进程或本项目命名的 ETW 会话。这是 Windows 当前 token 的权限限制，不是工具自动审批拒绝；没有尝试 UAC、修改用户组或放宽系统设置。

证据：`.local/live-acceptance/focus-etw-synthetic.json`。实跑前后脚本 SHA-256 均为 `981537ab7851c8ebe1ca747a01c04303d5e42def26748ad04d5bcc35a1edde4a`。

操作者随后在管理员 PowerShell 完成了原命令，证据为 `.local/live-acceptance/focus-etw-synthetic-admin.json`，脚本指纹与上述旧版一致。`StartTrace`、provider 启用、ProcessTrace 正常退出、会话与子进程清理均成功，丢失计数为 0；但两个线程各 exercise 19 次，accepted/rejected/malformed 全部为 0。没有进入第二阶段和采栈阶段。**权限问题已越过，零事件不构成过滤验证。不要原样重跑旧命令。**

### 事件源纠正与独立传输检查

旧脚本取得了 `SetForegroundWindow` 的返回值，却只判断线程内 `GetFocus`，并丢弃前者。新版保留操作返回、所属前台窗口回读及转换计数；同时输出消费回调总数和被忽略的会话元数据数。`--probe-source` 不启动 ETW，`--run-synthetic` 在事件源不合格时直接停止，不进入跟踪。

本机仅源验证 `.local/live-acceptance/focus-etw-source.json` 实际得到两个线程各 4 次 exercise：局部焦点全部成功，窗口激活 API、所属前台回读及前台转换全部为 0。子进程结束、桌面关闭、检查点的输入桌面/前台/剪贴板快照一致、源指纹始末一致。新版脚本 SHA-256 为 `55db8857b923b12d8b483edf9774e915fe34cf2b2f0b1abacbf26c9158cc78a1`。

确切系统文件的 12 个函数进一步交叉证明：已检查的 `SetForegroundWindow` 路径要求目标桌面等于输入桌面；事件 2 在会话前台线程更新处发出，而非每一次局部编辑框焦点变化。复核和限制见 [事件源报告](M1_FOCUS_ETW_SOURCE_REVIEW.md)。这纠正的是实验来源，不能据此认定微信抢焦点根因已找到，也没有证明过滤或用户态栈有效。

为独立检查过滤与实时消费链路，新增 `scripts/test_etw_transport.py`。它不创建窗口，使用随机 GUID 的自建 user-mode provider，只发送 20 条正例、20 条不匹配 payload、20 条错误事件 ID；预期只收到 20 条正例且每个序号恰好一次。清单仅载入本进程 TDH 缓存，不安装系统 provider；不启用 Win32k、抓栈、修改权限或创建文件跟踪。owner 子进程限时启停会话，输出释放、清单卸载、临时目录清理及源码始末指纹。始终 `win32k_verified=false`、`live_diagnostic_ready=false`。

`--check-metadata` 本机实际通过：两个事件的 schema 和身份、payload 构造、释放与清单卸载状态 0、临时目录删除及源指纹一致。证据 `.local/live-acceptance/etw-transport-metadata.json`。脚本 SHA-256 为 `2d79cd0c740fefb1c9271c7d5526e148dc2b5bfc1bb9e3490495f4740c744bb7`；复用的两个 helper 指纹同时记录在报告内。**此预检没有启动跟踪，不等于实际传输通过。**

操作者已在管理员 PowerShell 运行该无窗口检查，以下是复现命令（本轮无需再次执行）：

```powershell
Set-Location -LiteralPath 'D:\document\Github\one-person-company\wechat-agent-bridge'
.\.venv\Scripts\python.exe -X utf8 scripts/test_etw_transport.py --run-synthetic |
    Set-Content -Encoding UTF8 .local/live-acceptance/etw-transport-synthetic-admin.json
```

本机管理员报告 `.local/live-acceptance/etw-transport-synthetic-admin.json` **通过**：20 条正例各收到一次，20 条负例及 20 条错误事件 ID 均未进入消费，unexpected/malformed 为 0；启用至停止 1.016 秒，丢失为 0，ProcessTrace/CloseTrace/注销/过滤释放/清单卸载状态均为 0，owner 正常退出、会话确认不存在、临时目录已删除、三个源码指纹始末一致。这证明自建 user-mode provider 的实际过滤和实时消费通路可用，仍不证明 Win32k 的事件源、过滤和用户栈。

普通实时 ETW session 仍需要已有跟踪权限；不能用进程内私有 logger 绕过，因为微软明确规定其[不能实时交付](https://learn.microsoft.com/en-us/windows/win32/etw/logging-mode-constants)。这次没有自动提权、改用户组或新增系统配置。后续 Win32k 需要另一个有效事件源；操作者手动切换自建窗口可研究过滤，但点击事件的执行上下文未必属于目标进程，不能保证目标用户栈。不能因自建 user-mode provider 通过就跳过这些限制、保留其他进程的栈或启用微信写入。

零事件、过滤越界、未知来源栈、调用栈缺失、丢失事件或清理失败均不能用于真实诊断准入；新的候选仍需故障注入和实际因果关联。DbgEng 恢复门槛尚未通过，真实协调器继续关闭。

### 隐藏窗口的系统消息事件

新增固定文件的六函数复核及 `scripts/test_win32k_message_etw.py`。系统事件 60 的普通消息编号和参数会被清零，不能用 `message == WM_APP+n` 筛选；Pointer 类型的 HWND 不支持 TDH payload predicate。候选仅提交事件 ID 60 与接收者 PID/TID 两个整数 AND 条件，在消费端再次核对自建发送者和接收窗口。静态范围、53 字节布局与公开 API 限制见 [系统消息源复核](M1_WIN32K_MESSAGE_SOURCE_REVIEW.md)。

源检查在同一个私有非输入桌面创建两个隐藏的 message-only 窗口，使用单一固定消息和纯整数参数；每阶段有独立 nonce 和序号去重。发送前核对 HWND 对应的 PID/TID、隐藏状态和桌面，使用 `SendMessageTimeoutW`、200ms、`SMTO_BLOCK | SMTO_ABORTIFHUNG | SMTO_ERRORONEXIT`；不重试失败或未知结果，不显示/激活窗口，不修改剪贴板。

冻结版 `edfc032c776f90230a4d50f8116d4fe933449c0ce1cc4cf813b107658b594254` 的本机源预检和元数据预检均通过。两个子进程各发送、接收 4 次，bad=0；ACK、窗口销毁、类注销、线程桌面恢复、子进程退出、桌面关闭和检查点前台/剪贴板快照一致均通过。证据为 `.local/live-acceptance/win32k-message-source.json` 与 `win32k-message-metadata.json`，此时没有启动 ETW。

操作者已运行首轮管理员系统消息检查，证据 `.local/live-acceptance/win32k-message-synthetic-admin.json`，指纹与冻结版一致：

| 项目 | 实际结果 |
| --- | --- |
| 第一阶段实际刺激 | 两个进程各发送 4、接收 4、bad=0 |
| 消费回调 | 8 次；4 条 accepted，4 条因发送者 header 不匹配被拒绝 |
| 其他解析状态 | malformed=0；没有请求或读取调用栈 |
| 阶段结果 | 失败；没有继续反向过滤和采栈阶段 |
| 跟踪清理 | 启用至停止 0.125 秒，丢失 0，ProcessTrace/CloseTrace/过滤释放均状态 0，会话确认不存在 |
| 源资源与桌面 | 所有清理通过、源码始末一致、检查点前台/剪贴板快照一致 |

这次已捕获真实系统事件，但**过滤和来源归属未通过**。拒绝发生在 receiver payload 读取之前，所以旧报告不足以断言“反向接收者事件泄漏”或“内核 provider 一律不支持 payload 过滤”。不能将 user-mode canary 的通过结论推广到这里，也不能把拒绝事件改为 accepted。

下一项仅补充固定归属计数：若 header 精确匹配另一个已握手自建进程/线程，才检查三个 ownership 字段，分类为所选接收者、反向接收者或其他/混合；不输出身份数值、窗口句柄或正文，不读取其栈，该事件继续拒绝并中止。未知 header 仍在 payload 读取前拒绝。此分类需要新的限定运行才能得出事实，不能从旧报告倒推出结果；不改变提交的过滤条件或真实准入。

**分类实跑已完成，当前方案停止。** 最终脚本 SHA-256 为 `9b8af333fd43f3769080ddc6fd2628b72e6a568eeb43ee88e1a72f5176b50a2c`。操作者的 `.local/live-acceptance/win32k-message-classification-admin.json` 与该指纹一致，实际结果如下：

| 归属核验 | 数量 |
| --- | --- |
| 目标事件 accepted | 4 |
| 被拒事件指向所选自建接收者 | 0 |
| 被拒事件指向反向自建接收者 | **4** |
| 被拒事件指向其他/混合身份 | 0 |
| 未知栈 / 实际采栈阶段 | 0 / 未进入 |

这四条 header 和接收者 PID/TID/HWND 都能映射到本次另一组自建进程/窗口，已排除“只是同一接收者在另一执行上下文出现”的解释。**本机此事件 60 路径没有按已提交的 receiver PID/TID AND 条件排除负例**；不能用它做真实微信受限采集。该事实限定于记录的系统版本、事件及参数；没有声称所有内核 provider 都不支持 payload 过滤，也没有找到可据此直接修改的必需参数。

本次 enable 到 stop 仍为 0.125 秒，丢失 0，两个窗口和进程、私有桌面、会话均清理成功，检查点前台/剪贴板快照一致，源码指纹始末一致。没有读取被拒事件的栈或保存原始地址/正文，没有启动反向及采栈阶段，没有对微信进行附加、写草稿或发送。**不再原样重跑，不移除过滤，不通过消费端事后筛选宣称满足采集范围。**

最终复现入口如下，仅用于以后审查确切代码和自建实验；当前预期退出 1，不是要求操作者再次运行：

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/test_win32k_message_etw.py --run-synthetic |
    Set-Content -Encoding UTF8 .local/live-acceptance/win32k-message-classification-admin.json
```

后续若继续无暂停诊断，须先找到并验证确实限定目标进程/线程的事件来源或外部语义入口，且能关联到激活调用；当前没有已满足这些条件的候选。原 DbgEng 异常恢复门槛也仍失败，所以不能执行一次性真实草稿诊断或宣称已修复微信抢焦点。

## ClawBot 研究

固定腾讯仓库提交 [`874637a0dbc24050f72d1385add4fc6170a2d726`](https://github.com/Tencent/openclaw-weixin/tree/874637a0dbc24050f72d1385add4fc6170a2d726)，MIT 许可证；未复制或运行上游实现。

| 公开能力 | 本项目结论 |
| --- | --- |
| 后端长轮询、消息提交 | 不依赖桌面输入控件，可参考为独立机器人通道 |
| Bot 授权及上下文令牌 | Bot、个人账号、对端 ID 必须分开，不能用 wxid 冒充 Bot 对端 |
| `chatTypes: ["direct"]` | 不证明支持原 M1 群聊；类型中的 group_id 不是实现证明 |
| 媒体引用 | 仍需独立下载与解码验证；本轮没有真实媒体能力 |
| 后端受理成功 | 不等于界面呈现或对端收到 |

依据：[固定提交的渠道能力](https://github.com/Tencent/openclaw-weixin/blob/874637a0dbc24050f72d1385add4fc6170a2d726/src/channel.ts)、[公开协议](https://github.com/Tencent/openclaw-weixin/blob/874637a0dbc24050f72d1385add4fc6170a2d726/docs/protocol_zh_CN.md)。协议也区分客户端行为与完整服务端契约；本轮没有证明能代个人账号读取普通好友历史或向现有群发送。

新增 `clawbot_reference.py` 是未注册的纯数据实验，没有 HTTP、登录、凭证或下载能力。八项合成测试覆盖账号/上下文隔离、拒绝群字段、失败批次不推进游标、己方循环、媒体不编造、缺少上下文及受理不等于投递。严格拒绝规则是本项目策略，不冒充腾讯服务端要求；不声明是可用 ClawBot 适配器。

## 验证和下一步

最新完整离线 **283 项通过**（11.700 秒），最后增加 4 项已知自建来源的拒绝归属分类测试，并验证未知 header 在 payload 前拒绝。`git diff --check` 通过；无依赖变更，上一轮 `pip check` 通过。最终管理员报告证明此 Win32k 路径的接收者过滤未排除反向负例；真实读栈、微信激活因果链和后台写入仍未通过。

上一轮完整离线 **279 项通过**（13.068 秒）：在 260 项基础上增加 4 项系统消息静态布局及 15 项隐藏源、nonce、事件范围、栈与清理测试。首次完整发现因新脚本与单测同名导致导入冲突，单测已改名为 `test_win32k_message_contract.py` 后完整通过，没有修改测试断言。`pip check` 和 `git diff --check` 通过。

此前完整离线 **260 项通过**（10.775 秒），新增 5 项事件源判定、6 项系统静态边界、11 项合成传输/清理测试。没有重新运行未改变的 DbgEng 原生矩阵，也没有把其历史失败改写为通过。管理员实际自建 provider 传输已通过（20 正例、负例/错误 ID 接收 0、无丢失及清理通过）。账本只读核验仍为发送 0/3、OpenAI 0/10、真实诊断预留 0 次；客户端 DLL 及防撤回备份哈希与下列历史记录一致。

上一轮完整离线 **238 项通过**（11.984 秒）。原生报告匹配当时 helper 指纹；三个恢复探针均记录 `source_unchanged=true`、`recovered=false`，系统 dbgeng.dll 微软签名有效。

上一轮完整离线套件 **210 项通过**，`pip check` 通过。首次小套件因沙箱系统 TEMP 权限出现三个存储错误；改用项目内 TEMP 后通过，未修改测试断言。

收尾核验：两份原生报告均匹配最终工具指纹，未遗留合成调试/派生进程；无待恢复的无障碍日志，真实诊断凭证未使用，验收发送 0/3、OpenAI 0/10。Weixin.dll 与其 .h.bak SHA-256 分别仍为 `932dd6700f22e32faeb9b0cc9581a1f601e60ace151067680d3f6596496fab99`、`e0428a7b691853de496532523c2c09aa095f90941c37c9f4cfb3f137f3c66556`；系统 dbgeng.dll 微软签名有效。`git diff --check` 通过。

```powershell
New-Item -ItemType Directory -Path .local/test-temp -Force | Out-Null
$env:TEMP = (Resolve-Path .local/test-temp).Path
$env:TMP = $env:TEMP
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -q
.\.venv\Scripts\python.exe -X utf8 scripts/diagnose_focus.py
```

以下是显式原生合成测试，当前预期退出码 1，不属于默认 CI：

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/test_debug_engine.py --matrix
.\.venv\Scripts\python.exe -X utf8 scripts/test_debug_engine.py --backend win32-control
```

逐场景证据在 `.local/live-acceptance/debugger-selftest.json` 和 `win32-control.json`。产生可靠恢复候选后，才用 `--matrix --repetitions 10` 运行资格测试；不能把 210 项离线通过或正常分离通过说成异常恢复已修复。

仍需完成：异常退出恢复、一次限时真实因果诊断、调试器退出后的无干扰草稿/发送验证，以及原 M1 消息、附件、群 @、私聊、摘要和重启验收。ClawBot 专用对话不能替代原验收。
# 最新：分析工作进程隔离

2026-10-26 新增独立自建对照，分析进程正常／崩溃／超时三例均由存活的 DbgEng 持有者正常分离，四线程暂停计数和 DR 基线一致，心跳持续恢复；完整离线 662 项通过。**这不修复调试持有者本身的异常退出，旧 12/24 及 Win32 1/3 失败矩阵未变，真实调试仍禁用。** 源码、失败握手修正和确切报告见 [分析工作进程隔离](M1_DEBUGGER_ANALYSIS_GUARD.md)。
