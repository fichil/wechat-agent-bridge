# SetFocus 合成调用栈校准：窗口与收尾修复

2026-10-26，`scripts/probe_uia_setfocus_stack.py` 在不可见私有 Win32 桌面的自建标准 Edit 正控上完整通过。它证明当前环境能够有界采集 UIAutomationCore 自动焦点分支的同步入口／出口栈，并正常停止及清理。**它不证明微信经过同一路径，也不修复 DbgEng 异常退出的线程暂停或 DR 残留。M1 仍未完成，真实诊断与生产写入继续禁用。**

## 本次修复

此前两秒窗口并非只被控件发现占用。把开窗移到第二次写前检查之后，仍只能得到 SetFocus 入口；新的 owner 时间记录表明 StartTrace 与 Enable 总共约 15 毫秒，余下时间在操作路径中。

独立的无跟踪计时保留冻结的四个阶段和原有客户端／控件：现代默认组从第二次检查回执到请求完成检查为 2.094 秒，旧默认组为 2.109 秒；原生完整回读确认另需冻结实现的 0.25 秒。两秒不足以覆盖所选正控。因此仅将自建采集窗口调整为固定三秒，启动前要求至少六秒剩余存活时间，保留三秒清理余量。真实草稿的 45 秒采集边界、独立单次额度与禁用状态均未改变。

第一次三秒运行取得完整栈，但根协调器遇到 `BrokenPipeError`，总体仍判失败。客户端在收到已证实的 Stop 回执后关闭 trace 管道，provider 的操作及清理结果通过另一管道稍后返回；此时关闭的 trace 管道可能先报告 EOF／断管。新增 `read_trace_message` 只在完整观测窗口和有效 Stop 均已证明后接受这种关闭，继续收集 provider 结果。提前关闭及其他异常仍失败；只有所有操作和清理证据齐全才允许通过。

新增四项离线测试覆盖 poll／recv 的 EOF 与断管、三个不完整停止状态、关闭后不再轮询、异常消息及其他 OSError 不被隐藏。现有来源、配对、栈、事件上限、截止、进程身份与清理要求保持有效。

## 原生证据

环境为 Windows `10.0.26300`、Python `3.12.10`、comtypes `1.4.17`；确切 UIAutomationCore SHA-256 为 `be5037bf46e61566d3e5a83a6c628cebe9035771e4ae741a7b9aa27b5696d989`。

本机报告位于 `.local/live-acceptance/`，原始失败结果保留，不修改为通过：

| 报告 | 实际结果 |
| --- | --- |
| `uia-setfocus-stack-257ea8723fd34c4dbd99d1d6c516eae9/report.json` | 缩窗后仍两秒截止；有入口无配对出口。仅离线解析该报告的受限入口栈，链匹配但完整观察未通过 |
| `uia-setfocus-stack-84cff1ff8b474febbdb45393147a24c4/report.json` | 加入 owner／controller 时间记录；启动和启用约 15 ms，2.000 秒发起截止停止；自建客户端正常退出，完整阶段证据缺失 |
| `uia-owned-focus-timing-f9ac6905b64d4070a6e0acb8f0dbb0ea.json` | 四组原有操作及清理通过，但初版包装只在 GUI 进程，没有远端客户端计时字段；不作为计时证据 |
| `uia-owned-focus-timing-459405c9d1b74e2e9a44643c1e6f35cb.json` | 包装绑定到实际远端客户端；四组操作、计时字段和完整清理通过，305 次当前输入环境抽样未变 |
| `uia-setfocus-stack-f26496c473194f0d8ce95ef7325716dd/report.json` | 三秒窗口取得 1 对完整同步栈、98 条事件；停止成功，但断管竞态丢失完整 provider 收尾，整体失败 |
| `uia-setfocus-stack-7c4264009f6d4138a73d3338771610f6/report.json` | 修复收尾竞态后完整通过，`synthetic_setfocus_stack_verified` |

最终通过报告 SHA-256：`1c203dbc6c67950aa46b094fe219aa5ab022f3d7adef66343c5bcbea35c67d14`。对应采集器源码 SHA-256：`12838adb6960ae08b806c9bd9dcc2464702a64fb2abed28b9ec8feb77a40867a`；契约测试源码：`2617d82080c3fe7b708be3c9e55684bda2514ac63d1cdf03e427685401220590`。各失败版本源码及测试归档在相应本机报告目录，另保留原计时包装，避免用新源码冒充旧结果。

最终报告逐项满足：

- 335／336 各一次，同一自建 UIA PID、MTA 线程及确切事件描述符；空 payload，98 条事件未超 512 上限，无丢失。
- 两条栈分别包含预先静态定位的完整有序返回地址链；仅保留模块名和 RVA。
- 写后 UIA 与原生完整文本回读、控件身份和正向局部焦点事件均通过。
- 完成请求在开启后约 2.390 秒收到，主动请求 Stop，约 2.703 秒停止返回；Stop 0、Query 4201，名称／文件／GUID 匹配，目标在停止前后及 ACK 时仍存活。
- 客户端与 provider 以及 owner 正常退出 0，无强制终止；原生完整清理、窗口消失、私有桌面关闭及消失、三个进程句柄和持有目标句柄关闭均通过。
- 673 次当前输入桌面、前台及剪贴板抽样未变。抽样不是连续隔离证明，也不等同任务视图“桌面 2”。

这些实验没有接触微信、启用其无障碍字节、写草稿、发送、调用模型或执行 DbgEng。原诊断、桌面 2 单次尝试和发送／API 三个账本的 SHA-256 与上一轮一致。

## 离线回归与复现

完整离线 **563 项通过，12.572 秒，无跳过**，日志 `.local/live-acceptance/offline-setfocus-window-fix-20261026.txt`。其中 SetFocus 解析及采集契约 **55 项通过**；默认测试不创建原生桌面、不调用微信或付费接口。

```powershell
.\.venv\Scripts\python.exe -B -X utf8 scripts/probe_uia_setfocus_stack.py
.\.venv\Scripts\python.exe -B -X utf8 -m unittest discover -s tests -p 'test_uia_setfocus*.py' -v
```

首条默认只输出 disabled。显式自建校准入口是同一脚本的 `--run-synthetic`，不接受外部 PID。当前正常校准已通过，无需原样重复；故障研究应针对新的明确缺口执行。

## 下一步及限制

微软文档说明 AutoSetFocus 控制 Invoke、SetValue 等动作前的自动焦点行为；这不能排除客户端或 provider 的其他激活路径。[官方 AutoSetFocus 语义](https://learn.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomation2-get_autosetfocus)

本次先证实了自建正控的实际 UIA 分支，尚未观测微信的分支或 Qt 接收者。下一步先在自建／只读条件下核验当前 UIA 元素的关联上下文与客户端来源，区分自动焦点分支和 provider 内部激活；研究地址不能进入生产适配表。任何进一步真实写入仍须满足现有恢复、账号／会话、无干扰和单次授权边界，不得重用已消费额度。

原 DbgEng 恢复仍以失败的原生矩阵为准，不盲目 ResumeThread 或清零 DR。独立 ETW 的正常校准通过也不等同全部 owner／监护联合故障场景通过。发送、群 @、私聊 AI、附件、180 秒记忆与重启恢复仍须独立真实验收。
