# M1 开发阶段窗口导航

2026-10-26：操作者已允许开发阶段临时影响桌面，要求所需点击、测试会话打开和本任务草稿处理自主完成。该授权不改变正式运行时的后台验收标准。

## 当前结果

后续 computer-use 已成功取得原窗口，从自己的资料卡复核发送微信号；私聊搜索无结果，没有添加联系人或打开未核验会话。已返回原群、确认空稿并点击最小化。随后独立项目验收完成当前桌面后台编号 003 发送和两次真实定时记忆／重启暂停检查，详见 [M1_GROUP_MEMORY_ACCEPTANCE.md](M1_GROUP_MEMORY_ACCEPTANCE.md)。操作者已给出同名私聊线索，下一 computer-use 轮需按准确微信号核验资料卡，无须再问常规授权。下文的“尚未重新列举”保留为项目聚焦刚完成时的阶段记录。

上一轮 computer-use `list_windows` 未返回桌面 2 的微信窗口。应用目录中的微信条目指向已知安装路径，但显示 `isRunning=false`；独立只读进程核验仍匹配已有 PID、创建时间和窗口，不能据应用目录断言微信退出。一次 `launch_app` 获准执行，约 17 秒后报 `launched app did not expose a targetable window`；随后窗口清单仍无微信。该次不是自动审批拒绝，没有盲目重复启动、自动登录或换用企业微信。

没有向 computer-use 伪造窗口对象或传入从其他接口取得的 HWND。后续独立项目验收已成功激活原绑定窗口及其桌面；尚未让 computer-use 重新列举，不能提前声明该工具已可操作。没有通过它切换私聊、删除草稿或发送消息。

真实报告 `development-navigation-d81febe93c8349579cdf08f5b60961ba.json`（SHA-256 `c4a627acb0fe5ed1c32c24c84514831a272325dc23b019de59d71bb57b188c81`）：原 PID／创建身份／HWND／DLL 指纹一致；空草稿和原会话核验通过；一次 SetFocus 后前台为绑定窗口，原桌面从 `current=false` 变为 `true`，记录 1 个 foreground 和 2 个 focus 事件，均属于目标进程。耗时 1.828 秒，子进程正常退出、临时字节恢复、清理错误为空。后续确认 patch／thread_hook／draft_probe／development_navigation pending 和运行锁均为空。`background_qualified=false`，这是开发操作成功，不是无干扰证明。

保留两次动作前失败：沙箱内无法 OpenProcess（`patch_process_access_denied`，没有启用字节）；经授权的宿主检查因新入口用 `is False` 判断 COM BOOL 整数 `0` 而拒绝，报告 `development-navigation-bba133955bc24814b96aca8ff3bf2ea1.json`（SHA-256 `6ea4aca1d891f89e264284a64cff240ea99dfcc91c58dfa3f2906e02d8c0e74b`），零事件及恢复通过。修正为仅接受 bool/int 的零值，并增加其他类型、非零和已有草稿的回归；没有放宽空草稿要求。修正后的第一次实际聚焦成功，没有重试 unknown。

## 独立开发入口

新增 `scripts/development_navigation.py`，默认只返回 disabled。显式启用时，限定到已绑定账号、确切进程指纹、虚拟桌面及既定测试群的输入 RuntimeId；检查选中会话、空草稿、FALSE UIA 客户端关联及当前指纹。只有开发前台授权参数存在，才持久化一次请求，并执行一次公开 `IUIAutomationElement.SetFocus()`。

[Microsoft 文档](https://learn.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationelement-setfocus)说明该方法设置元素的键盘焦点；[对应托管接口文档](https://learn.microsoft.com/en-us/dotnet/api/system.windows.automation.automationelement.setfocus?view=windowsdesktop-10.0)也说明是否可见或前置取决于 provider。因此脚本必须在调用后重新核验原窗口确实处于前台；返回成功不能独自证明切换桌面或激活成功。

该入口不设置文字、不发送、不使用键鼠／剪贴板、不注入 helper，不加入生产适配表。所有结果固定 `background_qualified=false`，预期聚焦事件明确标为开发辅助。人工输入、剪贴板变化、锁屏、绑定变化、停止文件及 25 秒截止仍会停止；不自动抢回焦点、不回退其他方法。创建 `.local/live-acceptance/STOP` 可停止这一入口，终端 Ctrl+C 也会进入恢复路径。

独立工作进程承载 UIA，父进程持有临时无障碍字节并负责恢复。一次清理失败不会跳过其他资源；仅终止自建工作进程，绝不终止微信。已尝试但无法确认的聚焦保留 `unknown` 和 `development_navigation/pending`，不会自动重复；应先只读核查，而不是删除状态解锁。

项目 UIA 聚焦与 computer-use 在独立轮次执行；其 [SKILL.md](D:/CDriveSlimData/maintenance-20260917-161410/live/plugins/cache/openai-bundled/computer-use/26.1021.31345/skills/computer-use/SKILL.md)规定：

> Do not mix direct PowerShell UI Automation code in the same turn as Computer Use.

本轮已完成独立项目 UIA 控制，不混跑 computer-use。下一步直接让 computer-use 重新列举已激活窗口，核验该工具返回的窗口后完成已授权会话导航，**不要重复聚焦或再次启动微信**。无需为常规开发操作另问操作者确认。任何开发前台辅助均不能作为后台能力证据。

## 离线验证与复现

新增 12 项测试覆盖默认关闭、单次聚焦、目标变化、已有草稿、落盘失败、人工接管、超时／未观察到激活、未知结果保持、COM BOOL 两种表示及各清理阶段异常。最新完整套件 **815 项通过，13.470 秒，无跳过**；日志 `.local/live-acceptance/offline-development-navigation-live-20261026.txt`，SHA-256 `a908041686d72cabf9196216978efe2d200f876f9b95ae4af921e30352376608`。前一版 814 项日志保留。没有增加发送或模型调用，累计发送 **2/4**、API **6/10**。

```powershell
.\.venv\Scripts\python.exe -B -X utf8 -m unittest tests.test_development_navigation
.\.venv\Scripts\python.exe -B -X utf8 -m unittest discover -s tests -v
```

以下记录本次已执行命令，不需要原样重跑；只适用于现存绑定且无人接管、无未清理记录时的开发辅助，若指纹或 RuntimeId 已变化必须重新只读核验：

```powershell
.\.venv\Scripts\python.exe -B -X utf8 scripts/development_navigation.py `
  --activate-bound-window --development-foreground --allow-accessibility-patch `
  --account synthetic_owner --group '合成测试群' --private wxid_synthetic_archived_owner `
  --target-evidence .local/live-acceptance/m1-target-control-refresh-20261026.json `
  --synthetic-evidence .local/live-acceptance/uia-node-context-faea0f12f99e4c6cbb69960274a76e4c.json `
  --virtual-desktop e07e970b-901e-447b-90c9-cb48ba9068d5
```

下一步用 computer-use 重新取得窗口、打开指定私聊并核验其微信号，再继续项目消息语义；不重复已经消费的编号 002 发送。现有一次后台发送及恢复的成功记录保持有效，M1 整体仍未完成。
