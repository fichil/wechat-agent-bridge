# M1：任务视图 Desktop 2 对照记录

本页记录 2026-10-26 新增的一次独立授权对照。目标是检查：微信放在非当前任务视图桌面时，一次固定草稿写入是否仍改变用户当前前台、桌面或剪贴板。仅将窗口放到“桌面 2”不能作为无干扰读写的验收证据，M1 仍未完成。

本轮状态：**资料卡核验与只读预检通过；唯一一次真实草稿对照因前台／焦点事件变化停止，结果为 `unknown`。操作员随后明确反馈仍发生切屏，无干扰要求失败。** 本次新额度已消耗，不得重跑；操作员已确认手动清稿，随后只读复核通过同一精确目标的空草稿检查。账号、测试会话名称及实际 HWND／桌面 GUID 仅保留在本机授权与证据中，本页不记录。

## 已实现的只读检查

`wechat_agent_bridge/adapters/virtual_desktop.py` 使用公开 `IVirtualDesktopManager`，只绑定 `IUnknown::Release`、`IsWindowOnCurrentVirtualDesktop` 和 `GetWindowDesktopId`。构造不读取窗口；COM 初始化、创建、查询、释放和反初始化限定同一线程。没有绑定移动窗口接口，也不枚举窗口、激活窗口、切换桌面或发送输入。错误使用固定原因；清理失败不会因再次调用 `close()` 而变成成功。

初始检查要求目标窗口不在当前任务视图桌面、原前台窗口在当前桌面，两者桌面 GUID 非空且不同。后续检查绑定原目标 HWND／GUID 和原前台 HWND／GUID：目标仍在原桌面且非当前，前台仍是原窗口和原桌面，并仍属于当前桌面。程序使用 GUID 核验，不根据 GUID 推断“桌面 2”的显示序号。抽样通过不代表连续的输入隔离。

已发现全局 UIA 根枚举在另一任务视图桌面可能返回空，但直接通过已绑定 HWND 取得 UIA 包装仍可返回主窗口类。资料卡身份检查因此改为：

1. 直接核验绑定 HWND 对应的 UIA HWND、PID 和 `mmui::MainWindow` 类。
2. 原生 `EnumWindows` 只保留同 PID 且 `IsWindowVisible` 的句柄，再直接读取这些窗口的 UIA 类名。绑定主窗口必须在该集合中，且该 PID 的可见 `MainWindow` 必须恰好只有绑定窗口。
3. 仅在 `mmui::ProfileUniquePop` 唯一时读取其字段。其他窗口不读取 Name、聊天正文或子树。
4. 仍以资料卡中的精确微信号核验账号，保留 `uia_own_profile_exact_id_user_selected_window` 来源和绑定实例；昵称、窗口可见性和桌面归属均不能代替账号身份。

没有资料卡返回 `account_identity_uncertain_open_own_profile`，只读观察可等待操作员打开资料卡。多个资料卡返回 `account_profile_identity_ambiguous`；多个主窗口、绑定主窗口缺失、候选窗口失效或 PID／HWND 不一致均拒绝，不按“暂未打开”继续。

资料卡观察脚本新增 `--observe-seconds`，允许 15–180 秒，默认 45 秒。该参数只延长只读资料卡等待时间，不授予写入能力，也不改变草稿运行的 45 秒采集上限。本次资料卡观察使用 180 秒等待上限，实际 18.969 秒完成。

## 单次授权与持久化限制

`scripts/probe_taskview_draft.py` 默认禁用。它为本轮已明确授权的一次固定草稿使用固定账本 `.local/live-acceptance/taskview-desktop2-20261026.sqlite3`。改变桌面 GUID、目标进程或重启程序不能生成新额度。写入放行前先持久化预留和消耗记录；结果未知也不能自动重试。

旧 `.local/live-acceptance/diagnostics.sqlite3` 保留原样。既有最多 3 条发送、10 次 OpenAI 请求的验收额度不重置；本次对照不调用发送或模型接口。新增账本只表达这次独立授权，不开启生产写入或自动回复能力，也不能作为下一次尝试的授权。

候选仍要求精确进程启动身份、文件指纹、账号绑定、指定测试会话和空草稿。允许的临时无障碍开关仍需显式启用、回读和恢复，不修改磁盘客户端或防撤回文件。固定草稿仅写一次，不模拟按键、OCR 点击、粘贴或发送。发现焦点／桌面／剪贴板变化、人工接管、身份变化或超时后停止；不得自动抢回焦点。残留草稿按既有规则由操作员手动清理。

## 离线验证

最新完整离线回归 **559 项通过，12.430 秒**，相对上一轮 488 项新增 71 项。记录位于 `.local/live-acceptance/offline-taskview-desktop2-complete-20261026.txt`。其中 `tests/test_desktop_test_binding.py` 的资料卡／测试目标绑定契约为 15 项，包含观察时间的范围和默认值。默认测试使用合成 fixture，不连接真实微信、不调用付费 API。

新增覆盖包括只读 COM 的精确接口及同线程清理、桌面／前台变化拒绝、直接 HWND 资料卡身份检查、多个主窗口与资料卡、原生可见集合缺失、单次账本跨重启／跨 GUID 不复用，以及旧账本和发送／API 额度不变。同期 SetFocus 采集缩窗仅完成离线修改，不构成本次真实桌面实验的结果。

```powershell
.\.venv\Scripts\python.exe -B -X utf8 -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -B -X utf8 scripts/probe_taskview_draft.py
```

第二条默认只输出 `disabled`，不会开始对照。

## 只读复核模板

以下仅保留只读复核模板，必须用本机已经核验、仍处于授权范围内的标识替换。文档不是新增授权；本次草稿额度已使用，禁止通过修改标识、桌面 GUID、账本路径或目录重复运行写入。

资料卡观察只读，限定操作员主动打开的本人资料卡和已授权测试目标；不选择会话、不设置草稿、不发送：

```powershell
.\.venv\Scripts\python.exe -B -X utf8 scripts/verify_desktop_test_binding.py `
  --pid '<BOUND_PID>' --hwnd '<BOUND_HWND>' --created '<BOUND_PROCESS_CREATION_TIME>' `
  --account '<AUTHORIZED_WECHAT_ID>' `
  --group '<AUTHORIZED_GROUP_NAME>' --private '<AUTHORIZED_PRIVATE_NAME>' `
  --allow-accessibility-patch --observe-seconds 180
```

资料卡和当前测试会话核验通过后，才使用其本机证据文件进行预检。预检不执行草稿写入：

```powershell
.\.venv\Scripts\python.exe -B -X utf8 scripts/probe_taskview_draft.py --preflight `
  --account '<AUTHORIZED_WECHAT_ID>' `
  --group '<AUTHORIZED_GROUP_NAME>' --private '<AUTHORIZED_PRIVATE_NAME>' `
  --target-evidence '<ABSOLUTE_LOCAL_VERIFIED_TARGET_REPORT>' `
  --virtual-desktop '<VERIFIED_TARGET_DESKTOP_GUID>' --allow-accessibility-patch
```

不得将以上预检命令改为写入运行。本轮单次尝试已消耗，即使重启、换桌面或预检再次通过，也不能重发草稿。模板仅用于原授权范围内的只读状态与恢复核验；本轮清稿复核已完成，不需要为此原样重复观察。

## 本轮原生结果

记录环境为 Windows `10.0.26300`，运行微信 `4.1.15.56`。本轮资料卡报告核验的客户端 DLL SHA-256 为 `932dd6700f22e32faeb9b0cc9581a1f601e60ace151067680d3f6596496fab99`；系统 UIAutomationCore 为 `be5037bf46e61566d3e5a83a6c628cebe9035771e4ae741a7b9aa27b5696d989`。实际草稿报告中的 `windows_uia.py` 修订指纹为 `9acb7b5a9ae419d95d7a5475fe8cd9b72443f7550405073b7255c73046d0acd3`。这些事实仅适用于所记录环境，不声明其他版本兼容。

以下报告均位于 `.local/live-acceptance/`，保持原始内容不变：

| 步骤与报告 | 核验结果 |
| --- | --- |
| 资料卡 `desktop2-binding-6d2861c6996d4c95a73de08e8a12d37e.json` | `profile_verified`，18.969 秒；精确账号身份与测试目标通过，临时无障碍字节恢复为 `restored`，没有草稿／发送／API 调用 |
| 首次预检 `taskview-desktop2-9db716564b444671aae216ac99f16c95.json` | `failed / human_takeover`，0.359 秒，在启用临时开关前停止；`attempted=false`，没有消耗新尝试。此报告的 `patch_restored=false` 表示未进入启用／恢复步骤，不是恢复失败 |
| 再次只读预检 `taskview-desktop2-ed3406dd73f34609994547f5ed304647.json` | `preflight_verified`，1.422 秒，0 个事件；无写入尝试，临时字节已恢复、工作进程已退出、来源未变 |
| 单次草稿 `taskview-desktop2-7bdb3ba8391844cb882b62d1237793a5.json` | `unknown / desktop_event_change_observed`，1.672 秒；`attempted=true`，观察到 3 个 `commit_write` 阶段事件：1 个 foreground、2 个 focus。停止后临时字节恢复、工作进程退出、来源未变，清理错误列表为空 |
| 手动清稿后的只读复核 `taskview-desktop2-3aa91252db6f48c59d6cc17e5f168f2e.json` | `preflight_verified`，1.469 秒；`target_verified=true`，同一输入框／会话 RuntimeId 和空草稿核验通过，0 个事件、桌面抽样未变。临时字节恢复、工作进程退出、来源未变；`attempted=false`，没有新增写入、发送或 API 请求 |

五份报告 SHA-256 依次为：

- 资料卡：`f51765d8faafbdceecdb702d807333284bafb80e0e8e0d695af87877b36d6b5b`
- 首次预检：`012119cc5ba08ea07fa190788ab1758ff0d9aba68bb3370fdd569a84d7b77806`
- 通过的预检：`e68ef87f5673f0c29660ee0572c08e6356bb6386ea41508f78531002c9d98514`
- 单次草稿：`ca8651fc9742e86610401122331f0023710f682b4ff88a934a51b2d80d065405`
- 清稿只读复核：`aa6a7d6c9dfd046390e12938aa36d23baec28365eb3ad773342348d7e1bb2cfc`

最终机器快照中，目标窗口的 `current=false`，同时前台 HWND 等于目标 HWND，前台对应窗口的 `current` 也为 false。程序因此能记录前台／焦点变化，但这组快照本身不能证明可见屏幕切换或弹窗。操作员随后明确反馈本次仍发生切屏；该人工观察单独记录，不能倒改为原始报告中的机器结论。无干扰要求已失败。

写入阶段没有取得 `readback_verified` 或完整 worker 结果，不能断言固定草稿完整写入。此时 `attempted=true` 表达持久化放行记录；写入结果保持 `unknown`，不会自动重试。操作员之后明确回复已手动清除，随后的只读预检再次验证同一目标且草稿为空；该独立清理证据不补成写入成功或完整写后回读。

| 后续状态 | 当前证据 |
| --- | --- |
| 用户目视反馈 | 已确认本次仍发生切屏；不满足无干扰要求 |
| 残留草稿及手动清理 | 操作员已确认手动清除；独立只读复核同一目标的空草稿通过，没有自动代为清理 |
| 恢复及清理 | 草稿报告 `patch_restored=true`、`worker_exited=true`、`source_unchanged=true`、`virtual_desktop_reader_closed=true`，`cleanup_errors=[]` |
| 本次单次账本 | `attempt_used=true`、`draft_attempted=true`，本次独立额度已使用，不得重跑 |
| 旧账本与既有额度 | 已将当前 `diagnostics.sqlite3` 和 `quota.sqlite3` 的 SHA-256 与 `desktop2-budget-before.json` 对照，两者均未改变；本轮 0 次发送、0 次 API 请求 |
| 发送、模型处理、附件和记忆 | 本次未执行，仍需各自独立验收 |

手动清稿与只读空值复核已完成，但切屏问题仍未修复。继续保留写入阻塞，不采用写后恢复前台来掩盖影响；本轮不安排第二次写入。后台发送、自动回复和 M1 均未完成真实验收。
