# M1 运行时实例与焦点诊断：2026-10-23

后续异常恢复矩阵及 ClawBot 研究见 [恢复续查](M1_RECOVERY_AND_CLAWBOT.md)：210 项离线测试通过，异常恢复仍失败。下文 192 项及三场景结果为此前记录。

**已真实验证输入控件实例及接收者元数据读取，尚未修复抢焦点，M1 未完成。** 调试器故障恢复未通过，真实断点诊断及限时写入协调器保持禁用。没有新草稿、发送或 OpenAI 请求。本文的元数据读取不代表聊天正文、附件或后台发送兼容性。

## 环境与修订

- Windows 11 `10.0.26300`，Python 3.12.10 x64，微信运行文件 4.1.15.56。
- Weixin.dll SHA-256：`932dd6700f22e32faeb9b0cc9581a1f601e60ace151067680d3f6596496fab99`。
- 现有备份 Weixin.dll.h.bak SHA-256：`e0428a7b691853de496532523c2c09aa095f90941c37c9f4cfb3f137f3c66556`。收尾时重新核验，两份磁盘文件均未变化。
- 适配器仍为 `windows-uia-semantic-research-3`；独立诊断修订为 `focus-diagnostic-1`，正式读发适配表仍为空。
- 沿用已核验账号的同一窗口/进程创建身份，只检查获准会话。成功快照中 WinEvent 记录为空，焦点和剪贴板序号未变；临时无障碍字节均已恢复。
- 收尾只读检查：进程身份仍匹配，无障碍字节为 `0`，恢复日志无待处理项；本次验收累计发送 `0/3`、API `0/10`，一次真实诊断凭证仍未使用；没有遗留本次合成调试工作进程。

## 真实只读证据

`runtime_accessibility.py` 使用独立的查询/读取句柄，没有进程写入、远程调用、堆扫描或内存转储接口。布局来自确切 DLL 的静态指令，元对象类名由文件元数据核验，不使用已混淆的 RTTI 名称。

1. 实际 `GetRuntimeId` 实现在 `0x4C54D50`，ID getter 为 `0x51A730`；核验其追加 ID 形式及查询链。缓存指针 RVA `0xBBB13D0`，只遍历目标 ID 的桶链。
2. 真实无障碍主虚表为 `0x908F258`，`+0x60` setter 为 `0x9D5990`。检查弱引用存活后，关联到 `mmui::ChatInputField`，控件虚表 `0x93F1448`。
3. 经 QTextEdit 私有结构中已核验的 `+0x288` 关系，找到 `QWidgetTextControl`，虚表 `0x93AA728`。
4. 读取输入框 **22 条**、文本控件 **13 条**、唯一关联输入视图 **9 条**连接，共 **44 条**。当前连接类型均为 AutoConnection；不能据此断言某次写入执行了它们，或全部同步。

| 文本相关信号 | 已绑定接收者 | 回调 RVA |
| --- | --- | --- |
| QTextEdit::textChanged | ChatInputField | `0x3E39190`、`0x2126F80` |
| QTextEdit::textChanged | ChatInputView | `0x1C22DB0` |
| XTextEdit::textDidChanged | ChatInputField / ChatInputView | `0x2126BD0` / `0x1C22970` |
| ChatInputView::InputTextChanged | ChatInputRecommendSection | `0x3DE1F90` |
| ChatInputView::AdaptiveContentChanged | ChatMessagePage | 成员目标 `0x1C43BC0` |
| ChatInputView::InputLayoutSizeChanged | ChatMessagePage | 成员目标 `0x1C43BD0` |

后两项通过实际通用槽包装器 `0x1A2DF0` 的成员函数指针解析，没有执行这些地址。槽对象不读取未使用的 method-offset 字段。

内存访问有字节、节点、模块和遍历范围限制；每次核验进程身份，完成后复查结构，变化即丢弃。快照仍非原子，不能排除所有 ABA 变化，不得用于授予发送权限。详细元数据存于 DPAPI 保护的 `.local/live-acceptance/gate.sqlite3`，记录键为 `capability_evidence/runtime_metadata`；不上传模型。

## 根因未闭合

`0x263BF70` 的窗口激活路径与 [Qt requestActivateWindow](https://raw.githubusercontent.com/qt/qtbase/5.15/src/plugins/platforms/windows/qwindowswindow.cpp) 结构相符，包含 SetForegroundWindow/SetFocus；尚未证明原 SetValue 实验经过它。

从已观察的文本/光标回调出发，有界追踪了 1,090 个地址候选，未通过本次直接边追踪闭合到激活 API。叶函数、间接调用、分支条件和排队信号仍存在缺口；**这不证明路径无抢焦点行为**。原直接 COM SetValue 导致前台变化的事实与阻塞保持有效。

## 调试器恢复测试

新增标准库 ctypes DbgEng 端口，使用微软签名核验有效的系统 `dbgeng.dll`（10.0.26100.1），不安装服务、不重新分发 SDK 或二进制。DbgEng 在首次连接前拒绝设置进程选项，因此先连接自建引导进程，再设置退出分离。测试父进程持有并清理全部合成目标；真实微信没有被调试器附加。

| 自建合成进程场景 | 实际结果 |
| --- | --- |
| 硬件断点命中及调用栈读取 | 通过，23 帧 |
| 删除断点、处理挂起事件、正常分离 | 通过，目标心跳恢复 |
| 调试工作进程在断点暂停时异常退出 | 未通过，目标仍存活，但 3 秒恢复观察期内心跳未恢复 |
| 看门狗强制终止暂停中的调试工作进程 | 未通过，目标未恢复运行 |

早期正常分离曾向合成目标暴露 single-step 异常，调整断点清理和挂起事件处理顺序后正常路径通过。异常退出路径仍未修复，不能拿正常分离成功替代它。微软的 [DetachProcesses](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/dbgeng/nf-dbgeng-idebugclient-detachprocesses) 正常调用契约不能替代本机故障实测。

`diagnose_focus.py --run` 固定拒绝并返回 2，不附加、不预留真实诊断次数、不写草稿。限时窗口、一次性持久化账本、目标/草稿核验和证据分类已独立实现并测试；**真实限时写入协调器尚未开放**。原因是恢复前置条件未通过，不是账号或发送授权不足。

## 测试、复现与下一步

完整离线回归 **192 项通过**，含新增 26 项。覆盖 RuntimeId/指纹、PID 变化、对象变化、连接循环/越界、未使用字段、单次凭证、恢复门槛、断点额度、分离失败及时间相关性不能冒充因果性。默认套件不接触微信或付费 API。

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -X utf8 scripts/diagnose_focus.py
```

原生恢复测试只创建、调试自己的合成子进程，当前预期退出码 1：

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/test_debug_engine.py
```

结果及工具/系统 DLL 指纹保存到 `.local/live-acceptance/debugger-selftest.json`。报告缺失、过期或任一恢复场景失败均不准入。诊断账本独立于原有 3 条发送、10 次 API 额度；失败和重启不清零。

获准窗口只读复现需要原进程身份一致，句柄或生命周期变化时必须重新核验账号：

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/diagnose_focus.py --inspect --hwnd <已绑定窗口句柄> --group '<获准群名>' --private '<获准私聊名>' --allow-accessibility-patch
.\.venv\Scripts\python.exe -X utf8 scripts/trace_runtime_receivers.py --dll 'D:\apps\wechat\Weixin\4.1.15.56\Weixin.dll' --output .local/live-acceptance/runtime-receiver-static.json
```

下一步先修复合成进程的异常恢复并验证恢复期限；此前不执行真实限时写入。随后才由用户主动开始一次 60 秒诊断，补齐实际调用栈或异步投递关联。如果只能靠新的客户端功能补丁避免激活，应交付确切修复点并保持阻塞，不扩大补丁范围。

最终仍须在调试器退出后，分别验证无干扰设置/回读、真实发送、消息和附件、群 @、私聊、180 秒记忆及重启恢复。研究证据不降低 M1 完成标准。
