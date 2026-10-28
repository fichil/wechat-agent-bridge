# 调试分析工作进程隔离对照：2026-10-26

**仅分析工作进程故障隔离通过；持有调试会话的进程自身异常退出仍未恢复，真实调试准入继续关闭。** 新对照没有替换原 24 场景矩阵或把正常分离改称异常恢复。

## 实现与边界

新增 `scripts/probe_debugger_analysis_guard.py`，默认 disabled，只有完整 `--run-synthetic` 可启用，无 PID／窗口参数。根进程仅创建自己的目标、引导进程和 DbgEng 持有者；持有者在硬件断点处创建独立的分析工作进程。工作进程仅运行固定完成／退出／挂起协议，没有目标句柄、调试接口、原始栈或写操作。

分析工作进程退出或超过固定两秒上限时，仍然存活的持有者按原正常路径清除断点并分离。仅结束持有 Process 对象的自建分析进程；不靠重开 PID、额外 ResumeThread 或清零 DR 恢复。原始 `test_debug_engine.py`、`debug_engine.py` 和 Win32 对照源文件不变；脚本只在自己根进程的调用范围内替换合成 worker 函数，并在 finally 中还原。

最初报告 `debugger-analysis-guard-ca3c27988abc443f8be9d9521edb1baa.json` 为失败：崩溃、超时通过，正常分支遇到 BrokenPipeError；目标仍恢复，不能把部分通过判为总通过。已用 nonce 绑定的 ready → complete → ack 握手修复完成消息与关闭管道的竞态；非法顺序或重复消息失败，不接收恢复／权限命令。

## 实测

环境 Windows 10.0.26300、Python 3.12.10 x64；DbgEng SHA-256 `bfa188b10eb64a94f1eb59bfb0f8d85eb5dfc803cd0f6b5c554816fe311a236e`。新报告：

`.local/live-acceptance/debugger-analysis-guard-00378e26cea54dc79e43d8df3c80f598.json`

SHA-256 `ccf036a4003021e615a5be4f5d53ea0be3a931ae0b922f72c1db489e8759670f`。

| 分析工作进程 | 实际观察 | 目标恢复 |
| --- | --- | --- |
| 正常 | ready／complete／ack，exit 0，句柄关闭 | 四线程状态与基线相同，正常分离，心跳恢复后持续两秒 |
| 崩溃 | ready 后 exit 17，无 complete，句柄关闭 | 同上 |
| 超时 | ready 后未完成，超时，只终止分析工作进程，exit -15，句柄关闭 | 同上 |

三例都实际命中硬件断点并获取 23 个栈帧；逐一检查原自建目标的全部线程、暂停计数、DR0–DR3/DR6/DR7 以及系统调试关联。来源指纹前后一致。仅为当前基线上的一次合成对照，未增加预先暂停／非零硬件寄存器等新原生覆盖，不声明任意目标或并发状态都可恢复。

旧原生矩阵保留原样：DbgEng 12/24 通过、Win32 1/3 通过，仍存在确定失败。文件 SHA-256 分别为 `02d18da76db34cc650be4cec3ecc7feddb9d691e9b0865cea08d6732413b4e38` 与 `be96c0e202f8e1f660ec4d9daa3eaf315a2042060163395a7925f5cbf9fcbb91`。新报告始终 `owner_failure_recovered=false`、`live_eligible=false`，不能进入原真实恢复资格门槛。

## 验证与下一步

新增六项纯协议／资格测试；完整离线 **662 项通过，12.388 秒**，日志 `offline-analysis-guard-20261026.txt`。默认 CI 不调用 native 分支，不访问微信或模型。

```powershell
.\.venv\Scripts\python.exe -B -X utf8 scripts/probe_debugger_analysis_guard.py --run-synthetic
```

三例完成约十余秒，结果写入新的 UUID 文件，保留旧失败证据。程序只操作自己的无界面子进程，不操作桌面、微信、剪贴板或网络，不改变诊断／发送／API 额度。

这为避免“分析工作进程失败就强杀调试持有者”提供了实际隔离证据，但尚未接入真实诊断协调器。仍须解决持有者本身被强杀／崩溃时的状态保全，并保留原完整矩阵及连续资格测试；不能删去这些失败案例换取准入。M1 的己方消息确认、后台 Invoke、模型联调等未完成项也不由本对照替代。
