# 当前账号私聊导航续验（2026-10-27）

本轮没有开启全部私聊。当前主服务继续使用原 PID/window 绑定及群回复；私聊偏好保留，但 `private_discovery.ready=0`。身份、导航、方向、发送分开判定，不能用一个联系人核验成功代替整个账号兼容。

## 已完成的真实检查

环境为 Windows 10.0.26300、微信 4.1.15.56，原绑定 PID4708/HWND132580，进程创建标识134375347512983186，DLL SHA256 `932dd6700f22e32faeb9b0cc9581a1f601e60ace151067680d3f6596496fab99`。未改磁盘 DLL、防撤回文件、备份或 native helper 构建。

在当前获准账号中，computer-use 打开测试私聊及对方资料卡，项目 `contact_profile_identity.inspect` 完成准确微信号、私聊标题、选中会话行和空稿核验。完整身份及控件证据加密保存于 gate.sqlite3 的 `private_runtime_probe`，没有采集新历史基线、上传正文、调用模型或发送消息。新消息进入生产回复仍需要独立方向和发送验证。

双方各一条消息的公开 UIA 元数据均为 `mmui::ChatTextItemView`、整行矩形、Legacy role34/state2097152，HelpText/ItemStatus/Description/Value为空，没有子控件。因此不能从这些字段区分己方/对方。有限对象元数据检查的两个静态候选值也没有区分双方，未提升为生产方向规则。

## 三种公开语义动作

`scripts/probe_private_invoke.py` 只在明确 `--run --candidate` 后执行；FALSE UIA客户端、当前指纹、精确会话行、源群空稿、暂停、持久化单次凭据及桌面事件均检查。候选行只读阶段没有读取聊天预览。每种方法单独留存原结果，不能复用或清除后重试。

| 方法 | 真实结果 | 事件/原群 | 脱敏证据文件（.local/live-acceptance） |
| --- | --- | --- | --- |
| InvokePattern.Invoke | 返回后没有私聊标题/选中行回读，unknown | 0事件，原群不变并恢复 | private-row-invoke-7b7f42f4298a80ef81cbef57fb7fa192eb4cbfacb2eec6efc1616ff5ce8de9fe.json |
| Legacy.DoDefaultAction（客户端标为双击） | 没有导航回读，unknown | 0事件，原群不变并恢复 | private-row-invoke-3c4775549d12fa9639da21578c751cc90f5728520abb24ca97fb2e10535910eb.json |
| SelectionItem.Select | 完整等待2秒仍无导航回读，unknown | 0事件，原群不变并恢复 | private-row-invoke-6e4ddb321cb90edd1eccdc444b4a2df13471c8704b221bc5ee7329efbeee9fdf.json |

前两次使用即时回读，不能仅由即时回读失败断言客户端绝对没有延迟动作；后续独立观察仍为原群，没有新私聊窗口。最终 Select 已补上有界等待，没有重复前两种动作。全部结果均保留 unknown，不当作可重试失败。

## 窗口消息候选：仅合成通过，尚未作用于微信

新增 `window_message_navigation.py`：只允许对一个当前核验的 HWND 投递 WM_LBUTTONDOWN/UP；位置来自当前 UIA 会话行矩形，须完整落在客户区，拒绝最小化及失效几何。每条消息最多500毫秒，限制PID/TID和消息类型；不使用全局输入、移动鼠标、主动激活、键盘、剪贴板、窗口属性修改或新进程补丁。传输成功不等于导航成功，更不等于后台无干扰。

依据：[WM_LBUTTONDOWN](https://learn.microsoft.com/en-us/windows/win32/inputdev/wm-lbuttondown)、[SendMessageTimeoutW](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendmessagetimeoutw)。超时不证明窗口没有处理消息，因此进入发送阶段的异常仍必须保留不确定结果。

`test_navigation_window_messages.py --run-synthetic` 只创建自己的隐藏窗口，无外部 HWND/PID参数。实际通过：精确down/up接收、桌面不变、30毫秒超时识别、工作进程正常退出。证据 `navigation-window-synthetic.json` 附同构建源码哈希。没有声称其证明 Qt/微信接受该消息。

真实候选两次均在 `human_takeover` 前置检查退出，尚未持久化 window-message 尝试，也没有投递消息。为把新建本地控制台 Chrome 页面作为前台参考，computer-use 在激活步骤被拦截：无法可靠判定浏览器当前URL以执行安全策略，要求停止本轮 Computer Use。未改用其他 UI 工具绕过。原群已由此前 CUA 恢复，HTTP `/api/resume` 重新开启原服务；微信最后处于非最小化前台，旧发送器可能等待其离开前台，读取和模型调度继续。

## 复现和下一步

现有服务 exec38839、127.0.0.1:8766 保留，没有重启或重置账本。先查实时状态，不重复开服务；历史 API/send 数不得当成当前值覆盖。私聊检查未增加 API 或微信发送。

下一独立获准 Computer Use 轮可选择**当前已确认URL**的本地控制台 Chrome 窗口作为前台参考，再在原微信正常后台、原群空稿且身份记录仍匹配时做一次窗口消息候选。不是重试三个旧 unknown。准备阶段失败不得消费动作凭据；若有新进程/新 RuntimeId，必须重新建立当前身份。脚本调用：

```powershell
.\.venv\Scripts\python.exe -B -X utf8 scripts/probe_private_invoke.py --run --candidate synthetic_owner --method window-message
```

仅在真实导航及无干扰回读通过后，才继续研究消息方向、私聊 native sender 的目标校验、mode=0和真实回显。不能先移除 own=None 保护、把群 helper 成功套给私聊，或启用前台点击作为生产降级。

完整离线1207项通过（16.659秒），日志 `offline-private-navigation-20261027.txt`；新增7项覆盖单次凭据/已有草稿/源变化/无回读/焦点变化/矩形边界/超时不重投，默认测试不访问微信或付费API。最终仅调整了诊断脚本前置检查顺序和资源清理，针对性7项重新通过。M1仍未完成。
