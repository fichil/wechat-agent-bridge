# M1：用户态焦点入口与 UIA 同步事件复核

2026-10-23，**本机 User32 的两个焦点入口没有用户态 ETW 写入；另一路 UIAutomationCore API 已在两个自建进程中通过 PID 范围与同步调用栈的组合验证。** 后者使用空 provider 的错误返回路径，没有操作窗口；当前仅证明正常运行及清理，尚未证明异常恢复、微信的实际调用链或抢焦点原因。M1 真实诊断及写入准入继续保持关闭。

`SetForegroundWindow` 和 `SetFocus` 两者直接跳到 `win32u` 的系统调用桩。这一排除结论仅限下表四个明确入口，不代表 Qt、UIAutomationCore 或其他用户态通知路径没有事件。

## 确切文件与边界

| 文件 | 文件版本 | SHA-256 |
| --- | --- | --- |
| `C:\Windows\System32\user32.dll` | `10.0.26100.8875` | `f285937030bcb325cad39ee5be4818f6666241229adeadea4ff1ae469dfd7ef3` |
| `C:\Windows\System32\win32u.dll` | `10.0.26100.9711` | `d46a695db805e808efa63d1095ce3e1b6be9c0155dad405d444530efc1ed0362` |

所有地址是这些磁盘文件的 RVA。没有读取进程映像，不能据此判断运行时钩子状态。

| 入口 | 已完整复核的指令范围（末地址不含） | 实际路径 |
| --- | --- | --- |
| user32 `SetForegroundWindow` | `0x5DB20–0x5DB27` | 单条 7 字节间接 `jmp`，IAT `0xBD548` → `win32u!NtUserSetForegroundWindow` |
| user32 `SetFocus` | `0x730D0–0x730D6` | 单条 6 字节间接 `jmp`，IAT `0xBD978` → `win32u!NtUserSetFocus` |
| win32u `NtUserSetForegroundWindow` | `0xBE30–0xBE48` | 设置系统调用号 `0x155B`；共享用户数据条件决定 `syscall` 或 `int 0x2E`；两支均 `ret` |
| win32u `NtUserSetFocus` | `0x1C50–0x1C68` | 相同桩结构，系统调用号 `0x104C` |

两个 user32 跳转入口没有覆盖它们的 `.pdata` 记录，边界由精确导出地址、实际解码长度及终止跳转确定。两个 win32u 桩的 `.pdata` 边界恰为表中 24 字节范围，覆盖两条返回分支，排除后续对齐填充；没有把邻近导出间距当作函数边界。它们没有调用 `EventWrite`、`EventWriteTransfer` 或其他用户态函数，不能从这条 API 入口路径获取用户态 provider 的原调用栈。

## 本机 User32 元数据

注册表中的实际 publisher 名称为 `User32`，GUID 为 `b0aa8734-56f7-41cc-b2f4-de228e98b946`，资源文件就是上述 `user32.dll`；未发现注册名为 `Microsoft-Windows-User32` 的 publisher。复用现有 `scripts/inspect_focus_events.py` 的有界 TDH 元数据解析，仅枚举该 GUID，得到五个事件：

| ID | 本机消息模板的主题 |
| --- | --- |
| 1073 | 重新启动或关机尝试失败 |
| 1074 | 某进程代表用户发起重新启动或关机，包含原因 |
| 1075 | 用户终止先前的重新启动或关机请求 |
| 1076 | 用户说明上次意外关机的原因 |
| 1077 | 注销尝试失败 |

五者均为 Version 0、Task 0，字段是字符串参数，不是焦点调用事件。元数据只包含资源模板，没有读取任何实际关机、注销日志或用户事件。

## User32 证据与限制

本次输出 `.local/live-acceptance/usermode-focus-static.json` 保存文件指纹、原始指令字节、导入绑定、边界依据与五项 TDH 元数据。使用已登记的 Capstone 5.0.6，仅检查四段固定磁盘字节及单一 provider；没有修改生产接口或新增依赖。

上述 User32 静态执行和元数据读取成功；该复核没有启动 ETW、操作窗口、扫描进程、附加调试器或修改客户端。**不应为这两个入口再安排 User32 跟踪试验。** 以下 UIA 结果来自独立的磁盘研究及自建进程合成实验。

## UIAutomationCore：同步 API 事件

| 文件 | 文件版本 | SHA-256 |
| --- | --- | --- |
| `C:\Windows\System32\UIAutomationCore.dll` | `7.2.26100.8875` | `be5037bf46e61566d3e5a83a6c628cebe9035771e4ae741a7b9aa27b5696d989` |
| `C:\Windows\System32\sechost.dll` | `10.0.26100.1` | `b4a345ad9af0635f50e27eea8ae0ff37255dca90e6d5991bdbc4806fb02aaa72` |
| `C:\Windows\System32\ntdll.dll` | `10.0.26100.9549` | `651ac9041af8e6b5ebd331d53163244c0a27d36d1433f0f15cac1f28c5f2e82c` |

`.local/live-acceptance/uia-all-event-metadata.json` 保存该 UIAutomationCore 文件的 237 项 manifest 事件。provider 为 `Microsoft-Windows-UIAutomationCore`，GUID `820a42d8-38c4-465d-b64e-d7d56ea1d612`。本轮采用的事件 119/120 都是 Version 0、Channel 17、Level 4、Task 119、Keyword `0x4000000000010000`，分别以 Opcode 1/2 表示 `UIAutomationCoreAPI_UiaRaiseEvent` 的进入/退出，**两者均无 payload 字段，无法单凭事件辨认焦点类型或控件**。

`.local/live-acceptance/uia-raise-static.json` 核验导出 `UiaRaiseAutomationEvent` 的 `.pdata` 范围 `0x104270–0x104904`。入口在 `0x10429F` 检查启用位；`0x104740 → 0xDD188 → 0xDD1D1` 同步调用 `EventWriteTransfer` 写事件 119，随后才在 `0x1042ED` 检查 provider 指针。空指针分支转到 `0x10455D`，在 `0x104670` 发事件 120，返回 `0x80070057`（`E_INVALIDARG`），没有解引用空 provider。事件描述符分别位于 `0x306C00` 和 `0x305748`，与 TDH 元数据逐字节一致。

注册叶子函数 `0x11C40C–0x11C438` 将上述 GUID、回调 `0x1297F0` 和句柄位置传给 `EventRegister`。这建立了确切 DLL 的同步发出路径；真实通知成功、消费者投递及其他线程的行为不能由此推断。

## 两次错误 87 与限定修正

初次带事件 ID/栈筛选的实验，以及随后取消栈但保留事件 ID 筛选的 entry 实验，都在 `EnableTraceEx2` 返回 87，尚未执行采集阶段的四次 API 调用。记录分别保存在 `uia-event-source-private-initial-stack.json` 和 `uia-event-source-private-entry.json`；`uia-event-source-private.json` 保留初次记录。两次会话停止、临时文件清理与零丢失检查均通过。

`.local/live-acceptance/private-etw-enable-static.json` 给出与此一致的本机校验分支：

- `sechost!EnableTraceEx2` 在 `0x10B67–0x10B74` 将句柄第 24 位作为 private 标志传给校验器；`StartTraceW` 也在 `PRIVATE_LOGGER` 模式下传入同一标志。
- 事件 ID filter 在 `0x22EE5–0x22EFC`、stackwalk filter 在 `0x22E3B–0x22E4E`，分别因 private 标志非零转到返回 87 的分支 `0x22DCF`，无需先检查 filter 内容。
- private 路径检查 `EnableProperty` 的 `0x20` 和 `0x100` 位，没有同样拒绝栈请求位 `0x4`；后者沿 `sechost → ntdll` 通知路径保留。

这是磁盘分支证据，不是前两次失败调用的实测栈；旧报告没有保存原句柄，不能反推那次句柄的位值。新版成功实验实际记录 `private_handle_bit24=true`，补充验证了当前 private 会话特征。

修正只用于**自有子进程内的合成源**：保留 `PRIVATE_LOGGER | PRIVATE_IN_PROC | FILE_SEQUENTIAL`，不传事件 ID/stackwalk filter，仅通过 `EnableProperty=0x4` 请求栈。采集范围仍是当前自有进程；停会话后读取有界临时 ETL，消费者逐条要求本进程本线程、119/120 交替、空 payload 及已识别模块栈，最后删除临时文件。此处没有放宽真实目标的范围，也没有执行或证明跨进程 PID 筛选。

进程范围依据微软的[私有记录模式约束](https://learn.microsoft.com/en-us/windows/win32/etw/logging-mode-constants)；没有将其与不支持的实时模式混用。[API 文档](https://learn.microsoft.com/en-us/windows/win32/api/uiautomationcoreapi/nf-uiautomationcoreapi-uiaraiseautomationevent)描述的是通知接口，入口 ETW 的确切同步位置与错误路径则由本机静态和合成证据建立。

## 已通过的无窗口合成结果

`.local/live-acceptance/uia-event-source-private-stack.json` 在 OS `10.0.26300` 记录：

| 检查 | 实测结果 |
| --- | --- |
| 四次 `UiaRaiseAutomationEvent(NULL, 20015)` | 全部返回 `0x80070057` |
| 事件 119 / 120 | 各 4 条，按进入/退出交替，合计 8 条 |
| 同线程调用栈 | 8 条均通过，包含 UIAutomationCore、libffi、`_ctypes` 和 Python 调用者 |
| 拒绝、丢失 | 均为 0 |
| 停止、CloseTrace、ProcessTrace | 状态均为 0；停止后查询 4201，确认会话不存在 |
| 子进程、临时文件、源码及 DLL | 正常退出并清理；指纹未变；无 cleanup error |

栈中的 UIAutomationCore 返回位置为 `0xDD1D8 → 0x104745`（进入）和 `0xDD1D8 → 0x104675`（退出），与静态调用位置相符。开始会话前另有一次相同空参数调用用于初始化 provider；它不属于采集阶段四次调用，也不构造真实 UI provider。实验没有创建窗口、产生成功的 UI 通知或调用微信。

复现限定为当前自建 source，未显式启用时不运行跟踪：

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/probe_uia_event_source.py
.\.venv\Scripts\python.exe -X utf8 scripts/probe_uia_event_source.py --run-synthetic --stage entry
.\.venv\Scripts\python.exe -X utf8 scripts/probe_uia_event_source.py --run-synthetic --stage stack
```

第一条只输出默认关闭状态；后两条仅在一次性自有子进程运行，并分别写入 entry/stack 报告。已通过 stack 报告的脚本 SHA-256 为 `9392ee620c6521f35d13d1b106d0ff1fd254d0691a8e1108fc2f21139da9e9c7`。当前存档的 entry 报告仍是旧筛选版本的失败记录；stack 报告内 `entry_validation_passed=true` 已验证其自身进入/退出阶段，不能把旧 entry 文件改称成功。

## 微信同步通知候选及尚缺证据

对 `Weixin.dll` 4.1.15.56（SHA-256 `932dd6700f22e32faeb9b0cc9581a1f601e60ace151067680d3f6596496fab99`）已有有界磁盘复核：

- 构造路径 `0x4C55BA0–0x4C55E67` 在 `0x4C55D2C` 引用 `UiaRaiseAutomationEvent` 字符串，经 `GetProcAddress` 后于 `0x4C55D3E` 存入 wrapper `+0x20`。
- 平台通知候选 `0x261D990–0x261DB77` 的跳表 `0x94A7DA8` 将事件值 `0x8005` 导向 `0x261DB26`；`0x261DB29` 调用 `0x4C52F70–0x4C52FDE` 的 notifier。后者取得 provider 候选，设置参数 `0x4E25`，在 `0x4C52FD3` 尾跳 `0x4C56000`，再经 wrapper `+0x20` 同步尾调用 API。尾调用可能不保留 wrapper/notifier 栈帧；平台调用返回位置 `0x261DB2E` 是候选栈核对点。
- 这条通知路径还有 `UiaClientsAreListening` 门槛；开启 ETW 不会自动创建 UIA 事件监听者，不能保证微信会走到 raise。函数名由字符串和 Qt 结构对照提出，不能声称该二进制与公开 Qt 源码完全相同。

这些证明的是**候选通知到 API 的同步结构**。当前没有证据证明原先的 SetValue 已执行该通知路径，也没有证明所见通知一定导致前台激活。不得把微信静态链与自建空参数实验拼接为微信实际调用链。

上述进程内源报告 `uia-event-source-private-stack.json` 保持 `cross_process_scope_verified=false`、`focus_cause_verified=false`、`live_diagnostic_ready=false`，不因下文独立实验通过而改写历史结果。必要的通知条件及真实因果关联仍待验证；不会用取消筛选的方法开展微信跟踪，也不能将错误路径的合成成功认定为 M1 真实验收通过。

## 私有跨进程 PID 对照：随机合成 provider

新增 `scripts/probe_private_etw_scope.py`，只让两个新建进程注册同一个随机 provider，不加载 UIAutomationCore 或操作窗口。控制器使用 144 字节 x64 `EVENT_TRACE_PROPERTIES_V2`，设置 `PRIVATE_LOGGER | FILE_SEQUENTIAL`（无 IN_PROC、无实时），并在 Start、Enable、Query、Stop 始终传入同一单 PID filter。会话、provider 和临时文件都只属于这对自建进程；每阶段最长 5 秒、文件上限 4 MB。

两阶段交换被选 PID，每个源都写入四条带阶段 nonce 与序号的固定整数事件，避免用未执行的负例伪称范围正确。消费全部允许的 ETL 文件，先检查 provider、进程、线程和 descriptor，再读八字节合成字段；负例和未知来源只计拒绝，不读 payload 或栈。

普通权限原生报告 `.local/live-acceptance/private-etw-scope.json` 通过：两阶段均 `positive=4, negative=0, unknown=0, malformed=0`，另一源实际写入均为 4；各阶段约 0.313 秒，均只有一个 ETL，无丢失。Start、Enable、Stop、ProcessTrace、CloseTrace 均为 0，停止后 Query=4201。两个子进程正常退出，provider 注销、清单卸载、临时文件删除完成，源码指纹未变。

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/probe_private_etw_scope.py --run-synthetic
```

本次脚本 SHA-256：`1f3752419b13495ae4e5d6664344a3d8fd6fe9482d7053124fd639aa0b1f5046`。新增 14 项纯离线测试通过；连同 UIA 源的 9 项新增测试，该阶段完整回归为 306 项通过（10.609 秒）。默认运行不启动跟踪，测试和 CI 不执行原生跟踪。

**随机 provider 结果不是 UIAutomationCore 的跨进程验证。** 该报告保持 `uia_scope_verified=false`、`live_diagnostic_ready=false`；不能将它与另一个进程内 UIA 栈通过拼接为联合能力。随后另行执行了下节的实际系统 provider 组合实验，未改写这份历史报告。

## UIAutomationCore 私有跨进程范围与栈：组合通过

新增 `scripts/probe_uia_private_scope.py`，复用上述两个冻结脚本的 ABI、PID filter、元数据验证与栈解码。仅两个新建子进程加载确切 UIAutomationCore.dll，各预热一次 NULL provider 调用；每个阶段两个源各调用四次，并在第二阶段交换被选 PID。没有创建窗口、真实 provider 对象或 UIA 树，也没有调试器、系统事件 provider、外部 PID、提权或焦点操作。

每阶段的 Start、Enable、Query、Stop 始终携带同一单 PID filter，仅以 `EnableProperty=4` 请求栈。两个自建源的创建时间和存活状态通过只读进程句柄核验。消费时先核验进程／线程和事件描述符，负例、未知来源或异常事件在读取扩展栈前终止验证；两阶段使用不同文件，保留 4 MB 大小及 5 秒采集上限。

系统 UIA GUID 是共享值，不能用它单独证明清理权限：Start 明确失败时不 Stop；正常或后备清理先清空 Query 的字符串输出区，再核验实际返回的本轮随机会话名及临时 ETL 路径（仅允许指定 PID 的文件后缀）。名称和路径均有界解析，不记录原文；失败时记录有限归属分类并保持 `cleanup_unknown`。此逻辑符合微软要求的[各会话接口传相同范围 filter](https://learn.microsoft.com/en-us/windows/win32/etw/configuring-and-starting-a-private-logger-session)，[ControlTrace 查询结果](https://learn.microsoft.com/en-us/windows/win32/api/evntrace/nf-evntrace-controltracew)的实际回填也已在本机正常路径验证。

普通权限报告 `.local/live-acceptance/uia-private-scope-stack.json`：

| 项目 | 阶段 0 | 阶段 1 |
| --- | --- | --- |
| 两个源各四次调用 | 均为 `E_INVALIDARG` | 均为 `E_INVALIDARG` |
| 目标事件 / 另一源事件 / 未知 / 异常 | `8 / 0 / 0 / 0` | `8 / 0 / 0 / 0` |
| 有效调用栈 | 8 条 | 8 条 |
| 采集时长 | 0.312 秒 | 0.297 秒 |
| 实际会话名、文件、GUID 核验 | 全部通过 | 全部通过 |
| 丢失 / Stop / ProcessTrace / CloseTrace | 均为 0 | 均为 0 |
| 停止后 Query | 4201 | 4201 |

每条栈包含 UIAutomationCore 的进入／退出返回位置 `0x104745` 或 `0x104675`，并保留 libffi／`_ctypes` 调用者。两子进程正常退出且未强杀，两个进程句柄关闭成功，临时文件删除、DLL 与五项源码指纹复核均通过，`cleanup_unknown=false`。独立复核得到相同结论。

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/probe_uia_private_scope.py --run-synthetic
```

不加开关只输出关闭状态。组合脚本 SHA-256 为 `37784b48b650d8209eb466fa7a1804942d34a2db2b3fce5ed0315d03c43e5a65`；五个文件的完整指纹位于报告中。新增 12 项离线契约测试，完整回归 **318 项通过（10.974 秒）**；默认单测不运行 ETW、真实 UIA 或付费 API。

**组合通过仍只覆盖正常退出。** `focus_cause_verified=false`、`live_diagnostic_ready=false`，未启用生产能力或使用真实诊断凭证。下一步仅做自建源的故障矩阵：Start 成功但确认前 owner 退出、Enable 后 owner 退出（目标为自身／另一个自建进程）、owner 超时以及父管道 EOF；分别核验独立停止、会话消失和清理。父进程与阻塞中的 owner 同时消失尚未覆盖。ETW 清理结果也不能替代原 DbgEng 的目标线程暂停与断点寄存器恢复要求。

## 后续候选：仅订阅一个控件的 TextChanged

**此候选尚未实现，也没有向微信注册 handler。** 官方 `AddAutomationEventHandler` 支持指定事件、元素和树范围；可对已核验的单个输入元素注册 `UIA_Text_TextChangedEventId`（20015），使用 `TreeScope_Element`（1），仅处理该元素的文本变化。普通 TextChanged 与 TextEdit 自动纠错事件不同。全局 `AddFocusChangedEventHandler` 则明确不能缩小范围，因此不作为替代方案。[事件注册](https://learn.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomation-addautomationeventhandler)、[事件编号](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-event-ids)、[焦点事件范围](https://learn.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomation-addfocuschangedeventhandler)

Qt 5.15 的 `qwindowsuiaaccessibility.cpp` 第 145–155 行统一检查有效性、active、ready 和 `clientsAreListening`，第 156–179 行才分别派发 Focus/TextChanged。`qwindowsuiamainprovider.cpp` 第 217–235 行在存在 `textInterface()` 时，将相应文本更新转换成 TextChanged。微软对 `UiaClientsAreListening` 的定义也不限于焦点订阅。由此可以提出“单控件文本订阅满足监听者门槛”的机制候选；这些源码结构与 API 合同不能保证微信控件确实实现、发出或交付对应事件。[Qt 通知入口](https://raw.githubusercontent.com/qt/qtbase/5.15/src/plugins/platforms/windows/uiautomation/qwindowsuiaaccessibility.cpp)、[Qt 文本通知](https://raw.githubusercontent.com/qt/qtbase/5.15/src/plugins/platforms/windows/uiautomation/qwindowsuiamainprovider.cpp)、[监听者判断](https://learn.microsoft.com/en-us/windows/win32/api/uiautomationcoreapi/nf-uiautomationcoreapi-uiaclientsarelistening)

订阅 API 不要求调用 `SetFocus`、设置文本或激活窗口，但注册过程仍会进入 COM/provider；**订阅不是对任意第三方控件无抢焦点副作用的保证**。元素范围限定客户端回调，不会把目标进程的 ETW 119/120 也限定到该元素，且不会为无 payload 的事件增加控件或事件类型字段。

最小下一步仅是自建双控件合成验证：对 A 注册 Element/20015，A 的文本事件作正例，B 的同类事件作反例；只记自有身份与计数，不读取正文。分别验证订阅、超时、撤销及晚到回调，观察这些操作是否改变前台、焦点或剪贴板，不操作微信。使用同一个无窗口 MTA 工作线程串行注册与撤销；在 callback 外撤销，按 COM 引用寿命保留 handler，对停止后的晚到回调直接丢弃。[线程要求](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-threading)、[撤销与晚到事件](https://learn.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomation-removeautomationeventhandler)、[callback 限制](https://learn.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationeventhandler-handleautomationevent)

客户端 callback 运行在非 UI 线程，其栈不是微信原调用栈；provider 的 notify/ETW 栈也可能只是激活之后的通知处理。仍需区分文本通知与焦点通知、局部控件焦点与顶层窗口激活，并补齐 setter 到激活请求的实际关联。不能因订阅成功、收到通知或时间相近，就宣布找到抢焦点根因。

## AutoSetFocus 路线：合成通过，真实草稿失败

后续发现微软明确提供 `IUIAutomation2.AutoSetFocus=False`，可关闭 UIA 在多数 `SetValue`/`Invoke` 动作前的自动聚焦。2026-10-23，本机新客户端配置回读通过；旧 `CUIAutomation` 查询新接口返回 `E_NOINTERFACE`，因此采用 fresh `CUIAutomation8`，从同一个新实例获取 element/pattern，不混用旧 wrapper。[官方配置说明](https://learn.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomation2-put_autosetfocus)

独立自建 private non-input desktop 上的三组标准 Edit 对照已通过：旧客户端默认及新客户端默认都使局部焦点 B→A，各记录 4 条 Edit 焦点通知；新客户端 FALSE 阶段焦点保持 B、通知为 0，三组文本设置均经 UIA 与 GUI 完整回读相等。190 个输入桌面/前台/剪贴板检查点未变，清理及 DLL/源码指纹复核通过；检查点不等于瞬态全覆盖。确切报告、脚本指纹、首次沙箱创建桌面失败记录及复现命令见 [M1_AUTOFOCUS_CLIENT.md](M1_AUTOFOCUS_CLIENT.md)。这是公开客户端配置的合成证据，未操作微信，不能与上面的静态或 ETW 链拼接为微信实际根因。

随后已执行用户主动开始的唯一一次真实 FALSE 草稿试验：协调器记录 3 个桌面事件，保留 `unknown`，用户确认微信仍跳前台；手动清理后只读复核空草稿、身份和开关恢复通过。真实诊断账本已使用，不再试写。事件计数不含源／阶段或调用栈，不能将这一失败直接解释为特定微信接收者强制激活。当前仅继续自建控件的树路径对照和有界静态分析，具体证据见上面的 AutoSetFocus 报告。

现有历史报告继续保留：新完成的 `.local/live-acceptance/uia-private-recovery-precheck-847d507473e1.json` 仅证明自建源的外部控制器预检，实际会话身份核验后 Stop=0、Query=4201，目标仍存活且可继续执行合成 API；**它不是故障矩阵通过，也不是 DbgEng 断点/暂停恢复验证**。两个方向均未授予真实发送能力，M1 仍以真实验收标准判定。

## 独立 owner 的四项故障恢复：实测通过

2026-10-23 续作先修复一项证据处理缺陷：旧 `run_case` 将 Query4201 的 `absent=true` 当作清理完成，虽然最终测试判据拒绝通过，收尾仍可能删除诊断文件。新版本区分会话不存在与有效 Stop 证据；只有核验实际会话名和文件路径、Stop=0、后续 Query4201 且无事件丢失，才证明清理。EOF 场景可以使用 owner 在 finally 中保存的同等证据。证据不足则保留 `cleanup_unknown=true`、临时目录和持久化 intent，不自动重试。

修订 SHA-256 为 `6f7a3f3d2e3db40c2df2f0595c8db0e44a82a7d09dd21162ee06106ea7a7a5f4`；冻结 normal 脚本仍为 `37784b48b650d8209eb466fa7a1804942d34a2db2b3fce5ed0315d03c43e5a65`。16 项纯离线契约测试通过，包括完整 mock 收尾验证。旧预检不匹配新指纹；新预检及矩阵分别保存为 `uia-private-recovery-precheck-50d89ece493e.json` 和 `uia-private-recovery-matrix-50d89ece493e.json`，未覆盖历史报告。

| 故障注入 | 实际清理证据 | 存活目标后续调用与退出 |
| --- | --- | --- |
| Start 成功但正常确认前 owner 退出 | 外部控制器核验名称/路径，Stop=0，Query4201 | 4 次固定 NULL 调用成功返回预期错误，正常退出 0 |
| Enable 成功后 owner 退出 | 同上 | 同上 |
| Enable 后 owner 超时并被父进程终止 | 终止确认后外部 Stop=0，Query4201 | 同上 |
| 控制管道 EOF | owner finally 核验后 Stop=0，外部再次查询不存在 | 同上 |

四项 `session_cleanup_proven=true`、`cleanup_unknown=false`，无事件丢失；全部自建子进程结束、目标句柄关闭、临时文件清理、六项源码及系统 DLL 指纹复核通过。环境仍为 OS `10.0.26300`、UIAutomationCore SHA-256 `be5037bf46e61566d3e5a83a6c628cebe9035771e4ae741a7b9aa27b5696d989`。没有创建窗口、附加调试器、暂停目标线程、写入断点、操作微信或调用模型。

矩阵报告刻意保持 `passed=false`、`all_five_cases_verified=false`，仅 `remote_owner_recovery_passed=true`：自身目标 owner 退出不在准入范围，父监护与 owner 同时消失也未验证。这不是原 DbgEng 的暂停/DR 恢复证明，不证明有效控件通知或微信激活因果链。真实诊断及发送能力均未开启。

以下命令各对确切源码只允许一次运行；现有报告已占用对应路径，不应删除或改名后重跑：

```powershell
.\.venv\Scripts\python.exe -B -X utf8 scripts/probe_uia_private_recovery.py --run-synthetic --stage precheck
.\.venv\Scripts\python.exe -B -X utf8 scripts/probe_uia_private_recovery.py --run-synthetic --stage matrix
```
