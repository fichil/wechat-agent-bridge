# M1：Win32k FocusChange 事件源复核

2026-10-23，**本机这次私有、非输入桌面实验不能作为事件 2 的有效正向对照**。局部 `SetFocus/GetFocus` 成功，不能证明会话的前台线程发生变化。管理员实验收不到事件，不能据此宣布事件已弃用，也不能判断 payload 过滤是否有效。微信抢焦点的实际调用链仍未确定，真实诊断和写入准入不因此解除。

本报告结合主线已有合成记录与固定系统文件的静态证据。静态研究没有启动跟踪、操作窗口、读取内核内存、加载驱动、附加微信或下载符号。

## 本机文件及复核范围

| 文件 | 实际文件版本 | SHA-256 |
| --- | --- | --- |
| `C:\Windows\System32\win32kbase.sys` | `10.0.26100.9549` | `27cb5e74b2e05c3a2c10d00cc216285f9dca48854c6cfb57df5e4d3731ec9aa3` |
| `C:\Windows\System32\win32kfull.sys` | `10.0.26100.9711` | `94f00aabe76a4af3794e2134b806bf89e556b66814859c285de62173350a5173` |

研究环境的 OS 版本记录为 `10.0.26300`；不把 OS 版本替代驱动文件版本。所有地址是上述文件内的 RVA，偏移只用于解释确切文件，不是运行时内存读取布局或正式适配表。

新增 `scripts/review_focus_event_source.py` 固定两份 SHA-256，核验 `.pdata` 的 12 个明确函数边界后反汇编。输出 `.local/live-acceptance/focus-etw-static-trigger.json`，保存已复核指令和导入名称。工具使用已登记的 `capstone==5.0.6`，没有新增依赖、复制微软源码或重新分发系统文件。默认离线测试不会读取系统驱动。

| 模块 | 函数范围（末地址不含） | 用途 |
| --- | --- | --- |
| win32kbase | `0x11B2C0–0x11B323` | 导出 `EtwTraceFocusChange` |
| win32kbase | `0xA1F60–0xA1FD3` | 两个线程 ID 的事件数据封装 |
| win32kbase | `0x3C100–0x3C157` | 导入 `EtwWriteTransfer` 的写入包装 |
| win32kfull | `0x223F24–0x224613` | 会话前台线程更新及事件调用 |
| win32kfull | `0x2232A8–0x22374D` | 局部焦点路径及前台队列条件 |
| win32kfull | `0x254A10–0x2556EC` | 前台切换路径的输入桌面校验 |
| win32kfull | `0x2ADE80–0x2ADF10` | 导出 `NtUserSetForegroundWindow` |
| win32kfull | `0x26D148–0x26DEEC` | 上述导出到桌面校验的调用路径 |
| win32kfull | `0x2987A4–0x298822` | 获取输入桌面对象的内部函数 |
| win32kfull | `0x2A5EB0–0x2A5EE4` | 导出 `NtUserGetInputDesktop` |
| win32kfull | `0x2A5AA0–0x2A5B5C` | 导出 `NtUserGetForegroundWindow` |
| win32kfull | `0x28ECC0–0x28ED8F` | 导出 `xxxClearForegroundThread` |

## 事件仍有描述符与写入路径

`win32kbase!EtwTraceFocusChange` 引用 `0x26EFA0` 的描述符，其字段与本机 TDH 元数据一致：ID 2、Version 0、Channel 16、Level 4、Task 2、Keyword `0x8000000000002000`。

该导出检查关键词、级别、MatchAll 条件及启用位后，在 `0x11B31C` 调用 `0xA1F60`，后者在 `0xA1FBB` 调用 `0x3C100`，最终于 `0x3C136` 调用导入 `EtwWriteTransfer`。这说明该文件不是只剩无调用的事件清单。**启用状态的运行时取值没有读取，静态写入路径不等于本次事件已经发出。**

`win32kfull` 的已找到直接导入调用位于 `0x224111`。指令含 REX 前缀，解码长度为 7 字节；按固定 6 字节计算 RIP 地址会漏掉导入绑定。新脚本使用解码后的实际长度，并加入对应合成回归。

## 事件源依赖前台线程与输入桌面

以下结论来自同一文件的分支、导出名称和调用参数交叉核验，而非假设公开 Qt 或 Windows 内部结构通用。

1. `0x223F24` 通过 `W32GetUserSessionState` 取得会话状态；`0x223F5F` 比较新的线程对象与已有 `+0x4E70`，相同即返回。变化时分别经 `PsGetThreadId` 取得旧、新线程 ID，在 `0x224111` 调用事件 2，之后将新线程写回该会话字段。导出 `xxxClearForegroundThread` 也调用这个函数。事件描述的是这条会话前台线程更新路径，不能把编辑框之间的局部焦点变化当成同一种事件。
2. 局部焦点路径 `0x2232A8` 在 `0x22345C` 比较目标线程的队列与会话 `+0x4E60`；不相同则在 `0x223463` 跳过 `0x2234B0` 的前台线程更新。`NtUserGetForegroundWindow` 读取同一会话字段，并从对应队列取前台窗口，交叉确认该队列语义。
3. `NtUserSetForegroundWindow` 在 `0x2ADEDE` 调用 `0x26D148`，后者在 `0x26D67E` 调用 `0x254A10`。该路径于 `0x254ADC` 读取会话 `+0x4F68`，在 `0x254AE3` 与目标窗口的桌面对象比较。不同则经 `0x254AE7 → 0x254BA2 → 0x254C38` 清理，在 `0x254C5C` 返回 0，无法到达 `0x254F1E` 的前台线程更新。
4. `NtUserGetInputDesktop` 调用 `0x2987A4`，后者在 `0x2987BC` 读取同一会话 `+0x4F68`，再取得该对象的句柄。这提供了“输入桌面”字段的独立交叉证据。

微软文档也区分线程消息队列内的 [SetFocus](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setfocus) 与当前可见、接收用户输入的 [input desktop](https://learn.microsoft.com/en-us/windows/win32/winstation/desktops)。文档用于说明公开语义；上述具体事件触发分支由本机文件核验，不声称它是跨 Windows 版本稳定的公开合同。

本次没有穷尽所有系统模块、间接调用及其他事件写入来源；“无法建立正向对照”限定于已检查的调用路径和下面实际执行的自建窗口实验，不扩张为该事件在所有非输入桌面情形下绝不可能产生。

## 与合成实测对照

管理员记录 `.local/live-acceptance/focus-etw-synthetic-admin.json` 显示会话和 provider 成功启动、两个自建线程各执行 19 次，但没有收到正向事件。清理成功、丢失数为 0；这些结果不能证明有效过滤，因为没有合格的正向事件源。

随后主线单独执行源验证，记录在 `.local/live-acceptance/focus-etw-source.json`，**没有启动 ETW**：

| 检查 | 线程 A | 线程 B |
| --- | --- | --- |
| 私有桌面 exercise 次数 | 4 | 4 |
| 局部 GetFocus 核验成功 | 4 | 4 |
| SetForegroundWindow 返回成功 | 0 | 0 |
| 读回前台窗口属于本线程 | 0 | 0 |
| 已核验的前台线程切换 | 0 | 0 |

这与静态输入桌面门槛一致，足以判定本次实验没有建立事件 2 所需的正向对照。主线已让合成入口在源不合格时停止，不进入 ETW 阶段。记录中的子进程结束、私有桌面关闭、检查点桌面/前台/剪贴板快照一致均通过；快照不等同于所有瞬时变化都受到持续监控。

不能为了得到事件而自动切换用户桌面、抢前台或移除过滤。若后续采用隔离环境或操作者主动参与的合成前台切换，仍须先证明事件源、互为正负对照的过滤、用户态栈和清理；这些都没有被本次静态结果替代。也不能把事件 2 的限制当作微信抢焦点根因。

## 复现与测试

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -p test_focus_event_source.py -v
.\.venv\Scripts\python.exe -X utf8 scripts/review_focus_event_source.py --system-directory C:\Windows\System32 --output .local/live-acceptance/focus-etw-static-trigger.json
```

新增 6 项纯合成测试通过，覆盖错误指纹、边界不符、截断 unwind 记录、事件 keyword 不符、描述符越界和 7 字节 RIP 导入解析。确切文件的 12 函数静态实跑通过。完整回归由主线统一记录；M1 真实验收仍未完成。
