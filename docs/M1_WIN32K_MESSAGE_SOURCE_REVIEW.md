# M1：私有桌面的 Win32k SendMessage 事件源复核

2026-10-23，固定本机系统文件的静态证据支持把跨线程 `SendMessageTimeoutW` 列为下一步合成事件源候选。在此次限定的六个函数中，没有找到事件 2 已确认的输入桌面或前台队列比较；**尚未实测事件 60 能否在私有桌面发出，也未证明它的用户态栈可用**。这份报告不能解除微信真实诊断、文本写入或发送的阻塞。

最重要的字段限制是：本机事件 60 对普通消息把 `message`、`WParam`、`LParam` 清为 0。不能用 `message == WM_APP + n` 过滤正例，否则有效源也可能全部被过滤。接收者 PID/TID 来自实际接收线程对象，可作为自建进程范围的候选过滤条件，仍须运行时正负对照。

本次只读取磁盘文件及已有 TDH 元数据；没有操作窗口、启动跟踪、读取内核内存、加载驱动、附加微信、下载符号或调用模型。所有地址都是下表确切文件的 RVA。

## 文件及范围

| 文件 | 文件版本 | SHA-256 |
| --- | --- | --- |
| `C:\Windows\System32\win32kbase.sys` | `10.0.26100.9549` | `27cb5e74b2e05c3a2c10d00cc216285f9dca48854c6cfb57df5e4d3731ec9aa3` |
| `C:\Windows\System32\win32kfull.sys` | `10.0.26100.9711` | `94f00aabe76a4af3794e2134b806bf89e556b66814859c285de62173350a5173` |

`scripts/review_message_event_source.py` 固定上述哈希，复用已有只读 PE 边界检查，核验以下六段 `.pdata` 边界后反汇编。使用已登记的 `capstone==5.0.6`，无新增依赖或上游源码复制。默认测试只使用合成元数据，不读取系统文件。

| 模块 | 范围（末地址不含） | 已核验用途 |
| --- | --- | --- |
| win32kbase | `0xE3870–0xE3A26` | 导出 `EtwTraceBeginSendMessage`，检查启用状态、取接收者身份、处理消息字段 |
| win32kbase | `0xE4868–0xE496D` | 事件 60 描述符与数据描述符封装 |
| win32kbase | `0x3C100–0x3C157` | 导入 `EtwWriteTransfer` 的包装 |
| win32kfull | `0x547D8–0x55BB1` | 构造并发送跨线程队列消息，调用上述导出 |
| win32kfull | `0x54208–0x547D2` | 发送分派，在 `0x543FD` 直接调用 `0x547D8` |
| win32kfull | `0x34B40–0x35145` | 另一发送分派，在 `0x34EE2` 直接调用 `0x547D8` |

输出 `.local/live-acceptance/sendmessage-etw-static.json` 保存这些明确范围的指令、导入绑定、字段布局与限制。它不是全模块调用图；也没有从用户态 `SendMessageTimeoutW` 开始穷尽所有上游、间接调用或分离 unwind 片段。

## 已闭合的静态写入段

`win32kfull` 的 `0x547D8` 路径在 `0x55354` 把原消息编号存入队列消息对象 `+0x68`，在 `0x55360`、`0x55364` 分别保存 `WParam` 和 `LParam` 到 `+0x58`、`+0x60`，在 `0x55457` 保存接收线程对象到 `+0x28`。随后 `0x5560E` 经 IAT `0x3DAA70` 调用 `EtwTraceBeginSendMessage`，之后才进入接收方唤醒、等待与超时处理。该导入调用带 REX 前缀，长度为 7 字节，脚本按解码后的实际长度解析 RIP。

`win32kbase!EtwTraceBeginSendMessage` 的入口检查关键词、级别、MatchAll 和事件启用位。它从队列消息 `+0x28` 取得接收线程，在 `0xE390B` 经 `PsGetThreadId` 得到接收 TID；在 `0xE3932` 读取该线程 `+0x1D0` 的进程指针，在 `0xE393C` 读取进程 `+0x38` 的 PID。这里只把偏移作为当前文件的数据流证据，不把它作为运行时内核内存读取接口。

事件包装函数在 `0xE48B3` 引用 `0x26E788` 的描述符，在 `0xE4952` 调用 `0x3C100`，后者于 `0x3C136` 调用导入 `EtwWriteTransfer`。描述符与已有 TDH 记录一致：ID 60、Version 0、Channel 21、Level 4、Opcode 1、Task 60、Keyword `0x400000000400000`，任务名 `SendMessage`。

这些是具体磁盘函数之间的可复核调用关系。事件启用位的运行时取值、API 是否确实走到该段、成功记录与否都没有由静态分析证明。

## 普通消息被清零，接收者身份保留

`EtwTraceBeginSendMessage` 仅对一个特殊系统消息组合保留消息与参数；对普通消息：

- `0xE3948` 把消息字段清为 0。
- `0xE3953` 把 `LParam` 清为 0。
- `0xE395E` 把 `WParam` 清为 0。

例外是 `message == 0x112` 且 `WParam == 0xF170`，即显示器电源相关的 `WM_SYSCOMMAND / SC_MONITORPOWER`。**不得为了保留字段而用这个系统命令作刺激**。普通 `WM_APP + n` 足以承载自建 WNDPROC 的固定整数请求；事件筛选应该使用未被上述分支清零的接收者 PID/TID。

已有 `.local/live-acceptance/focus-event-metadata.json` 与本机事件封装一致。x64 字段为逐个数据描述符串接，不是带 C 结构体对齐的布局：

| 字段 | TDH 输入类型 | 字节数 | 字节偏移 |
| --- | --- | --- | --- |
| CallbackCount | 3 | 1 | 0 |
| pqmsg | 16，Pointer | 8 | 1 |
| hwnd | 16，Pointer | 8 | 9 |
| WParam | 16，Pointer | 8 | 17 |
| LParam | 16，Pointer | 8 | 25 |
| message | 8，UInt32 | 4 | 33 |
| inputReadyTimeMs | 8，UInt32 | 4 | 37 |
| flags | 8，UInt32 | 4 | 41 |
| pidReceiver | 8，UInt32 | 4 | 45 |
| tidReceiver | 8，UInt32 | 4 | 49 |

总长 53 字节。脚本拒绝版本、描述符、字段顺序、数量、宽度、顶层标志或输入类型不符；不将该布局推广到其他 Windows 文件或 32 位事件。

## 桌面条件及最小候选

这六段已复核函数没有事件 2 路径中 `SessionState + 0x4F68` 的输入桌面比较，也没有 `+0x4E60` 的前台队列比较。发送路径仍有窗口、权限、线程状态和消息种类等条件；**未找到上述两个门槛只限本次范围，不能证明全部调用者或系统策略均无限制**。

推荐先做无 ETW 的源验证：两个自有子进程在同一个私有非输入桌面，各用独立、未附加的线程队列；接收方建立自定义 WNDPROC，只处理一个 `WM_APP + n` 和固定整数参数并返回固定整数，发送方对该精确 HWND 调用有限超时的 `SendMessageTimeoutW`。两方应同完整性级别，不广播，不传跨进程指针或真实文本，不需要显示窗口、设焦点、设前台或附加输入线程。

微软说明消息可以在同一桌面内的进程之间交换，而输入桌面是当前可见并接收输入的桌面；因此不能把两个子进程放到不同桌面后期待相互发送。[Desktops](https://learn.microsoft.com/en-us/windows/win32/winstation/desktops)

`SendMessageTimeoutW` 对不同线程队列有等待与超时语义，同队列直接处理则忽略超时；自定义消息不由系统自动封送，所以只用双方约定的整数。候选可采用 `SMTO_ABORTIFHUNG | SMTO_BLOCK | SMTO_ERRORONEXIT` 和有限超时，不用 `SMTO_NOTIMEOUTIFNOTHUNG`。调用前清除 last-error，分别检查 API 结果与 WNDPROC 的固定返回值。[SendMessageTimeoutW](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendmessagetimeoutw)、[WM_APP](https://learn.microsoft.com/en-us/windows/win32/winmsg/wm-app)

源验证必须先证明实际收到了请求且发送方读回正确响应，再考虑管理员跟踪。ETW 正例条件为事件 60 加精确的 `pidReceiver` 和 `tidReceiver`；以另一个自有接收者分别交换 PID/TID 做反例。不能只靠“启动成功、零丢失、零事件”认定通过，也不宜在没有阶段关联的情况下要求总数恰好等于刺激数。事件头线程与自有用户栈可用于后续核验发出方，不需要保存队列内核地址等指针字段。

即使事件 60 全部通过，它也只能验证相应内核 provider 路径的过滤、栈和清理机制；不能替代事件 2 的正向对照，不能建立微信文本写入与窗口激活之间的实际因果链。

## 复现与本轮验证

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -p test_message_event_source.py -v
.\.venv\Scripts\python.exe -X utf8 scripts/review_message_event_source.py --system-directory C:\Windows\System32 --metadata .local/live-acceptance/focus-event-metadata.json --output .local/live-acceptance/sendmessage-etw-static.json
```

4 项纯合成测试通过，覆盖无填充偏移、版本和位宽不符、字段缺失或换序、可变长度及接收者身份类型不符。确切文件六函数静态实跑通过，输出保持 `runtime_emission_verified=false`、`live_diagnostic_ready=false`。没有执行窗口源验证或 ETW；后续运行证据由主线单独记录。M1 真实验收继续未完成。
