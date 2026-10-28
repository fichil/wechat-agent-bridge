# 调试对象保留与原生事件接管：2026-10-26

后续新增附加前持有对象与系统直接输出共享事件的候选，**37 个自建场景、715 项离线通过**；修复已取事件来不及记录的具体空档。旧无共享版本 29/31 的失败保留，原 DbgEng 及真实门槛不变。详见[事件记录空档修复](M1_DEBUG_EVENT_DELIVERY.md)。下文是此前原状态保留与六例对照的历史证据。

## 续测：原有暂停和非零状态保留

**新候选当前 15 个自建场景通过，完整离线 695 项通过；真实调试仍未开放。** 新增 `scripts/probe_debug_object_preservation.py`，先回归原六个零基线场景，再验证已有暂停、已有非零调试寄存器、二者同时存在，各含正常／崩溃／强杀三例。

基线在附加前构造：一条仅等待原生事件的辅助线程保留一次暂停；主线程 DR0/DR2/DR3 为非零但未启用，DR1 为启用的八字节写观察点，指向自建目标中从不改写的固定存储。新增故障场景每例五条线程，恢复后辅助线程的原暂停仍为 1、已有观察点和其他寄存器仍存在。先完成全状态严格比较和两秒心跳，才撤销测试构造的原有状态；撤销后再次与构造前基线比较，最后目标正常退出 0。

最终报告 `.local/live-acceptance/debug-object-preservation-aad3061b0ea944bbbbbd146605b9efc7.json`，SHA-256 `b19e78d060a636364901b873ce9086e5e5fa14bd4eb088ed6e1ffe43b50a1401`。15 例来源指纹前后一致、所有子进程和句柄退出、无 cleanup errors；九个新场景还收到辅助线程／原生事件正常关闭确认。各例仅运行一次，不代替完整故障矩阵及连续资格。

本轮修正了“运行时寄存器值可原样作为 Windows 写入表示”的假设。失败报告依次保留：

- `debug-object-preservation-f32b6aea448443839266b68e489bd2b6.json`、`53cfb311710f4c94971d16818af60eaa`：零基线六例及已有暂停三例通过，非零基线在附加前失败；新增证据显示请求 DR6=0，目标运行后读到 `0xffff07f0`，DR7 固定位也发生表示变化。
- `debug-object-preservation-711b11410a18404c8427b68d546150d2.json`、`387855fcebc14493b595c56c489da34d`：直接请求 DR6=`0xffff0ff0`，即时回读为 0，完整回读拒绝。
- 独立 `debug-register-roundtrip-ea3eafee83854609949c506a5ae90c56.json`：在未启用断点的同一自建暂停线程中，十个 DR6 写入样本均回读 0；其他寄存器未变，原状态及检查暂停恢复、两秒心跳、正常退出通过。**“写入后等于指定掩码”假设为假，报告仍 passed=false。** 不把这一场景推广为所有上下文都能忽略 DR6。
- `debug-object-preservation-2408a102b5fd44b9abeebf9639823ae5.json`：使用实际运行基线后，附加时的写入证实 DR6 回读 0、DR7 bit 10 清除；恢复的严格比较没有被删掉。

`encode_debug_context` 现在只接受本机已观察的两个无待处理原因的 DR6 基线表示（0 与 `0xffff07f0`），编码为 Windows 写入值 0，并清除 DR7 的固定 bit 10。其他地址、启用和类型／长度位全部保留，写后必须完整等于编码值；恢复执行后必须与**原始完整运行基线**逐项相同。其他 DR6 原状态在附加前拒绝，不声称能够保全任意未验证状态。

[微软 SetThreadContext 文档](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-setthreadcontext)说明部分上下文值由操作系统处理；这里的具体编码仅来自确切本机文件和上述实验。另依据 [Intel DR6 状态说明](https://cdrdv2-public.intel.com/819710/252046-sdm-change-document.pdf#page=369)收紧实际断点命中的判断：BLD/RTM 位出现其他调试条件时拒绝继续，未将这些位一概作为保留位忽略。实验构造阶段的原始状态、实际事件原因判断、写入表示和最终完整恢复是四个独立检查。

当前源码 SHA-256：

- `probe_debug_object_recovery.py`：`02c741b122c31b9a9bc76383055d1b3e42f1544635355d7718dd0e844f69cfdf`。
- `probe_debug_object_preservation.py`：`a200fb534ea9a809b9cb7f4e3c2f8dca2273eb44678ebd67d4c6912d9a885fb6`。

环境仍为 Windows `10.0.26300`、Python 3.12.10 x64；本机处理器登记为 Intel Core Ultra 7 155H，GenuineIntel，Family 6 Model 170 Stepping 4。系统 DLL 指纹见下文，原 DbgEng 和旧 Win32 失败报告及四本原额度／草稿账本哈希不变。本轮没有操作微信、工作桌面、键鼠或剪贴板，没有发送、API 调用或额度追加。

```powershell
.\.venv\Scripts\python.exe -B -X utf8 scripts/probe_debug_object_preservation.py --run-synthetic
```

默认 disabled；完整显式命令只创建自己的无窗口目标。`--calibrate-registers` 另需 `--run-synthetic`，仅重查上述已被否定的旧掩码假设，无需原样重跑。新增／扩展 15 项离线契约，完整 **695 项通过，12.495 秒，无跳过**，日志 `offline-debug-object-preservation-20261026.txt`。

下一步仍须覆盖：意图保存／设置／取事件／继续／分离之间的故障窗口，监护及联合故障，多目标、新增／销毁线程和重复资格，以及受限栈采集。非零状态本次只覆盖一个启用的其他槽位、一个预先暂停线程及二者组合；不宣称任意已有调试状态可恢复。M1 的真实发送、消息身份、模型处理及记忆验收仍未完成。

## 此前六例对照

**新原生候选的六个自建目标场景通过；原 DbgEng 异常恢复仍失败，真实调试禁用，M1 未完成。** 本次实际终止了持有调试会话的进程，不是上次的分析工作进程隔离。覆盖仍有限，不能将这六例当作完整资格矩阵。

## 为什么改查系统调试对象

新增 `scripts/probe_debug_object_retention.py`，通过绑定进程句柄查询并保留它的系统调试对象，然后让原 DbgEng 持有者正常退出、异常退出或被父进程终止。只运行 attached 阶段，不设置硬件断点，也不修正暂停计数。

报告 `.local/live-acceptance/debug-object-retention-dcfed73841904572bbf6e05e7d1ef1d9.json`，SHA-256 `61029491496a5bfebf082d68a9c5226c5ebcb58cf2fcd1964b27e86bff1d91a9`：

- 正常退出：完整线程基线恢复，心跳连续两秒。
- 崩溃和强杀：持有者退出后，只要父进程仍保留调试对象，系统报告的调试关联仍存在；关闭该句柄后关联解除。
- 两种故障中，四条原线程及一条附加线程的暂停计数仍为 1，关闭对象也未恢复。这两例判失败，最后只终止自建目标和引导进程，全部资源退出。

这证明了本机上保留原调试关联的前提，但没有修复 DbgEng 自身额外增加的暂停计数。微软的[线程控制说明](https://learn.microsoft.com/en-us/windows-hardware/drivers/debugger/controlling-processes-and-threads)描述了 DbgEng 附加／执行／分离时的计数操作；保留句柄不能替代这部分状态管理。

## 原生事件候选

新增 `scripts/probe_debug_object_recovery.py`，不更改现有 DebugEngine、Win32 对照或生产适配器。它使用原生 Win32 调试事件和保留下来的同一个系统调试对象：

1. 目标由当前程序创建，没有窗口；父进程持有进程对象，并核验进程创建时间。主线程句柄再核验所属进程及线程创建时间，不按复用的 PID/TID 恢复。
2. 附加后先把调试对象保留到父进程。硬件断点设置前，父进程核对原 DR 状态并将固定单槽位修改意图 flush/fsync 到新的本地文件，确认后子进程才设置；设置后完整回读。
3. 记录确切待处理事件。硬件命中要求 PID、主线程 TID、异常代码、异常地址和预先记录的修改相符。
4. 持有者异常退出或被终止后，先确认其 Process 对象已经退出，再检查原调试关联。父进程从保留句柄读取目标线程上下文，只有预期槽位及命中原因匹配时才恢复**记录的原值**；写后完整回读。
5. 对原系统调试对象执行 NtDebugContinue，再执行 NtRemoveProcessDebug。没有恢复额外暂停计数的 ResumeThread，没有重新附加，也没有把 DR 全部清零当作通用恢复策略。
6. 核验全部线程集合、创建时间、暂停计数、DR0–DR3/DR6/DR7、调试关联和两秒连续心跳。最后目标应按停止标记正常退出；失败时只清理自己创建的目标，不重试未知恢复。

线程状态检查复用既有检查器，会配对增加／撤销一次自身检查暂停；它不撤销此前已有暂停。硬件断点只改变自建目标的调试寄存器，不写进程代码或磁盘 DLL。

## 两项实验缺陷及修正

保留以下失败报告，不覆盖或删改：

- `debug-object-recovery-0863a8e94035487297c33d5b74fcbf86.json`：正常子进程关闭管道后，父进程的 poll 抛出 BrokenPipeError；修正为管道关闭与进程退出分别处理，仍须通过持有的 Process 对象确认退出。
- `debug-object-recovery-8ab9f424d5eb4ee8a55c5b49aa7d8668.json`：前三例通过，硬件断点正常组被寄存器一致性检查拒绝。
- `debug-object-recovery-86249e1e236d4b149d2c904535b3fc70.json`：增加有界寄存器证据，确认设置回读 DR7 为 `0x1`，实际命中为 `0x401`；DR6 为 `0xffff0ff1`。拒绝条件在此次采证中保持原样。

[Intel SDM Volume 3B 图 17-2](https://www.intel.com/content/dam/www/public/us/en/documents/manuals/64-ia-32-architectures-software-developer-vol-3b-part-2-manual.pdf#page=119)将 DR7 bit 10 标为固定保留 1。结合本机实测，只在**命中比较**中允许该位的表示差异；启用位、断点地址和类型／长度配置仍须一致，其他调试异常原因拒绝。恢复后的完整原值回读和最终基线比较没有忽略该位。新增负例验证其他位的变化仍拒绝。

## 六例实际结果

最终报告：`.local/live-acceptance/debug-object-recovery-255c6223d4014998b9bf8aea06480876.json`。

SHA-256：`8b1981cfee7c3f6ccd41540e0d368182a7bd90a0f7b6c45c6ada83c106d88bcd`。

| 固定故障位置 | 正常退出 | 持有者异常退出 17 | 持有者强制终止 -15 |
| --- | --- | --- | --- |
| 初始原生断点事件，未设置硬件断点 | 通过 | 原对象继续事件并分离，通过 | 同左，通过 |
| 硬件执行断点命中 | 通过 | 原 DR 恢复、原对象继续事件并分离，通过 | 同左，通过 |

全部六例的四条目标线程最终状态与初始状态完全相同，系统调试关联解除，目标持续执行两秒并正常退出 0；句柄和子进程清理成功，无 cleanup errors，来源指纹前后一致。这里只有每例一次、单目标、一个硬件槽位、零暂停／零 DR 基线，未采调用栈。

精确环境 Windows `10.0.26300`，Python 3.12.10 x64。系统文件签名均为有效的 Microsoft Windows：

| 文件 | SHA-256 |
| --- | --- |
| ntdll.dll | `651ac9041af8e6b5ebd331d53163244c0a27d36d1433f0f15cac1f28c5f2e82c` |
| kernel32.dll | `e132eba4166256e2f414b91fe7d5386590bc5ea8626aee5af9c9738383cf2ae1` |
| dbgeng.dll（原对照使用） | `bfa188b10eb64a94f1eb59bfb0f8d85eb5dfc803cd0f6b5c554816fe311a236e` |
| probe_debug_object_retention.py | `17e518652f05cf62e56c42267caab5e3ddc35be95a2188c9daeb839a71288c7c` |
| probe_debug_object_recovery.py | `2abbfbbde2f1cbc9b125bc5ea271ad433a75e054769f33ef90acfd49e4728d06` |

原资格报告保留原样：DbgEng 12/24、旧 Win32 1/3，SHA-256 分别为 `02d18da76db34cc650be4cec3ecc7feddb9d691e9b0865cea08d6732413b4e38`、`be96c0e202f8e1f660ec4d9daa3eaf315a2042060163395a7925f5cbf9fcbb91`。新候选没有把这些失败改成通过。

## 复现、回归与尚缺范围

两个入口默认 disabled，无任意 PID、窗口、阶段或缩写启动参数。显式自建对照命令：

```powershell
.\.venv\Scripts\python.exe -B -X utf8 scripts/probe_debug_object_retention.py --run-synthetic
.\.venv\Scripts\python.exe -B -X utf8 scripts/probe_debug_object_recovery.py --run-synthetic
```

每次写入新的 UUID 报告，不覆盖历史文件。第一条预期整体失败，因为它不修正 DbgEng 计数；第二条本轮六例通过。默认测试不会执行这两条原生分支。

新增 18 项纯离线契约；完整 **680 项通过，12.315 秒，无跳过**。日志 `.local/live-acceptance/offline-debug-object-recovery-20261026.txt`。本轮没有操作微信、工作桌面、键鼠或剪贴板，没有模型请求；四本原诊断／草稿／额度账本哈希未变，发送 0/3、OpenAI 0/10。

这是一条新恢复候选，不能直接接到真实诊断。尚缺：

- 原完整故障阶段和多目标拓扑的等量覆盖及连续重复资格；不能用本次两个固定 checkpoint 排除中间窗口的故障。
- 附加／保留对象之前、意图落盘与实际设置之间、事件已取出但未登记、继续与分离之间等位置的恢复；监护进程自身及联合故障。
- 已有暂停、已有非零 DR、线程新增／销毁、对象身份变化、原生调用超时／恢复失败的原生对照；纯函数的非零 DR 保留测试不等于原生通过。
- 不依赖 DbgEng 暂停机制的受限调用栈和对象元数据采集，以及与限时真实协调器的集成。
- 后台发送、己方消息确认、群 @、模型联调、180 秒摘要和重启恢复等 M1 未完成验收。

ProcessDebugObjectHandle、NtDebugContinue 和 NtRemoveProcessDebug 不是本项目声明支持的稳定公开适配接口。其 ABI 参考固定提交的 [phnt](https://github.com/winsiderss/phnt/tree/53fbbdc5b5d2b08761db1c7b26bfa8c820924356)，许可证和使用范围见 [第三方记录](THIRD_PARTY.md)；[微软 NtQueryInformationProcess 文档](https://learn.microsoft.com/en-us/windows/win32/api/winternl/nf-winternl-ntqueryinformationprocess)也说明此接口可能变化。代码以当前系统文件指纹限制研究，不调用未核验的内部地址，不改变生产准入。
