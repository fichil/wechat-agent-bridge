# M1 私聊后台验收准备

最新：真实只读恢复仍在目标行选中检查处停止；原 unknown／锁保留，API 10/12、发送 3/5。恢复已要求当前资料卡精确微信号先于草稿读取，以及零事件／完整退出恢复／原 token／观察先落盘，成功分支尚未实测。948 项完整离线通过；准确界面恢复请求待操作者处理，不直接运行下文 mode=0／004。当前证据及操作顺序以[私聊恢复](M1_PRIVATE_SELECTION.md)首节为准。

当前更新：原生窄搜索检查及一次准确收件 ID 查询更新已实测通过；弹层没有提供身份。固定通讯录按钮的 DPI 元数据已修正，一次 Invoke 返回但没有导航回读；旧 unknown／pending／运行锁保留。CUA 同名项点击／完整主窗捕获拒绝不重试，不能直接执行下文选择／发送或解除锁。当前证据及后续限制见[搜索与导航](M1_SEARCH_READONLY.md)；以下为先前阶段记录。

当前 2026-10-27 01:05：聊天控件已恢复，但当前标题／选中行不匹配两个获准目标。公共私聊 Select 一次调用未获选中回读、unknown 和 navigation pending／全局锁保留；只读复核未确认，不重试。字节 0，无 helper，API 8/10、发送 3/5，004 未预留。完整 897 项通过。**先按[私聊选择记录](M1_PRIVATE_SELECTION.md)用下一轮 CUA 打开指定私聊，再独立原生只读恢复；禁止直接运行下方 mode=0／发送命令，直到 pending 与锁按原 token 正常解除。** 以下准备记录为较早阶段。

2026-10-27：**明确私聊范围和固定编号 004 的入口已实现，但实际 private mode=0 尚未通过，未发送。** 精确资料卡与 8 项不可覆盖加密基线已通过，见[私聊身份记录](M1_PRIVATE_CONTACT_IDENTITY.md)；私聊历史不上传、不重采。

原窗口已用公开接口恢复为最小化和普通显示，均零焦点事件；两种状态下输入框检查仍拒绝，普通状态仅 Window／Pane，无聊天控件。临时开关恢复、worker 退出、未调用 Qt 方法；最终指纹不变、字节 0、pending／运行锁为空。private-004 未预留，API **8/10**、发送 **3/5**。证据及摘要见[窗口恢复记录](M1_WINDOW_RESTORE_PREPARATION.md)。

最新完整 **884 项通过，15.344 秒，无跳过**，日志 `offline-window-normal-20261027.txt`。先前计划的 computer-use 准备现已完成，当前以本页新增记录和开发交接的原生只读步骤为准。窗口可见不等于聊天可读取，不用缺控件的证明发送。M1 未完成。

先前失败保留：隐藏窗口的 `bound-thread-hook-ed43c1b5f3924cb9abc6aa4ea2c4e991.json` 为 virtual_desktop_query_failed，发生在临时开关／worker／helper 前；恢复最小化后的 `bound-thread-hook-b71a0e090b634f1a8030be35e3fa8144.json`、普通状态的 `bound-thread-hook-096c5aa254ab422495294f6ef00387a6.json` 为 inspection_input_ambiguous，均未写稿或发送。失败不能当作私聊加载／卸载通过。CUA 一次既有入口恢复失败也已保留，不再原样重复。

最新 CUA 准备已完成：原窗口重新可被列举，发送端 synthetic_owner 资料卡有真实响应；收起卡后仍是该私聊与空草稿，最后用标题栏按钮最小化，工具明确确认 minimized。曾意外打开的预览已按独立返回窗口关闭，未保存／上传；没有新输入或发送。记录 `development-cua-window-ready-3e9698900ced4f7ca4458d05a2f7504f.json`，SHA-256 `18483b9ce1667132f4f48a46f6c6d92be64188e62fe3330e2585d59352bb5294`。**当前下一步改为独立原生只读检查**，重新核验当前完整指纹、窗口／桌面／原字节与恢复状态后，再做一次明确 private mode=0，不重复 CUA 点击或旧恢复 case。以下入口和旧测试保留。

## 实现

- `PrivateTestScope` 只接受完整收件微信号和操作者给定显示名，分别核对发送账号 own-profile 来源、进程创建身份、实际 UIA 资料卡记录、私聊标题／选中行 RuntimeId，以及独立历史基线。昵称不能代替 ID，CUA 视觉记录不能单独开启此 scope。
- 核对基线仍为历史且禁止上传、最多 50 个哈希、无重复、数量符合已加载尾部、捕获文件在固定目录内且加密哈希未变。操作途中联系人或基线记录变化立即停止；不重新采集／覆盖旧基线。
- `probe_background_draft.run(..., target_scope=...)` 只允许该 scope 进入显式只读检查，旧草稿写入入口不能因此开放。工作进程只收到已核验私聊的一个显示名；报告明确 `target_kind=private` 和收件 ID 哈希，不把私聊名称填成 group 规避旧守卫。
- `probe_bound_thread_hook.py --target-kind private --private-label ...` 使用同一已通过合成恢复的原生构建做 mode=0，仍不调用 Qt 方法。后续私聊发送证明必须标为 private、匹配准确收件 ID／进程／当前桌面模式／实际控件，并完整卸载；旧群聊证明不能放行。
- `verify_bound_signal_send.py --case private-004 --target-kind private` 只允许固定合成文本 `微信 Agent Bridge M1 私聊验证 004`。保留所有旧类别槽位和 unknown，发送前事务预留独立扩展 `bound-private-20261026-004`；当前未预留、未消耗。没有任意正文／PID／函数地址或键鼠、剪贴板发送回退。
- 普通生产读发保持关闭。此固定发送实验不是 AI 自动回复，不代表发送者／群 @、私聊新消息调度、分页或附件已通过。

## 已运行检查

新增 12 项合成回归覆盖准确 ID／进程／发送账号分离、错误或视觉-only 证据、群／私聊混用、RuntimeId 重建、基线撤销／可上传／数量错误、文件越界或哈希改变、操作中范围变化、群证明拒绝私聊发送、固定 case 与 scope 对应以及只读 scope 不开启旧草稿写入。

完整 **870 项通过，15.558 秒，无跳过**；日志 `.local/live-acceptance/offline-private-scope-ready-20261026.txt`，SHA-256 `975f59d6d97b3a326b5f39ff364c167e3c08aa4b9404468c8a508fc191782a8a`。`private_test_scope.py` SHA-256 `9ce9d82ce57e8a77d0508e8cc5e1163094e86825cab8cf9327a07957d03f5b93`。默认 CLI 仍 disabled，`git diff --check` 通过。首次专项发现已清空的基线记录为 None 时的未规范异常，已改为明确拒绝并重跑通过；没有用它操作客户端。

未重建原生 helper；继续使用 `thread-hook-synthetic-0babe13d34dc4b2a84525c222b9ad5fb.json` 对应的同一原生构建。Python 协调器改动不代表新后台资格，仍需当前私聊的新 mode=0 证据。

## 下一独立项目检查

computer-use 与原生检查按其[技能](D:/CDriveSlimData/maintenance-20260917-161410/live/plugins/cache/openai-bundled/computer-use/26.1021.31345/skills/computer-use/SKILL.md)引用的 guidance 规则分轮执行。原 PID／创建身份／HWND 已复核，当前窗口为普通可见状态但聊天控件缺失，先观察实际界面；不能因工具列表空推定退出。旧 HWND 不存在或身份变了应停止并重新准备绑定，不能向复用 PID 写入。

精确作用于已授权账号／私聊的只读 mode=0 命令如下（group 参数只保留原授权配置；scope 不会读取该群）：

```powershell
.\.venv\Scripts\python.exe -B -X utf8 scripts/probe_bound_thread_hook.py `
  --inspect-bound --allow-process-helper --allow-accessibility-patch --send-candidate `
  --current-desktop-background --target-kind private --private-label '<已核验私聊显示名>' `
  --account '<发送微信号>' --group '<原授权测试群>' --private '<准确收件微信号>' `
  --virtual-desktop e07e970b-901e-447b-90c9-cb48ba9068d5 `
  --target-evidence .local/live-acceptance/contact-profile-def9f90432b24727809c12bdce1a1f0f.json `
  --synthetic-evidence .local/live-acceptance/uia-node-context-faea0f12f99e4c6cbb69960274a76e4c.json `
  --hook-evidence .local/live-acceptance/thread-hook-synthetic-0babe13d34dc4b2a84525c222b9ad5fb.json
```

仅当真实后台检查成功且源／构建／当前范围保持一致，才将新的私聊 mode=0 报告用于 `verify_bound_signal_send.py --run-fixed-send --case private-004 --target-kind private --private-label ...`，其余参数相同，额外 `--allow-process-helper --readonly-evidence <新的私聊报告>`。必须继续核对空草稿、焦点／剪贴板、目标、暂停和发送后精确回显；unknown 不重试。
