# M1 私聊联系人身份核验

2026-10-26：**computer-use 视觉核对、项目 UIA 精确私聊身份核验和 8 个可访问项的加密历史基线均已通过。M1 未完成。** 收件人与发送账号显示名相同，核验使用资料卡完整微信号，发送账号绑定保持原样；发送者方向、自动回复和附件取得仍未验收。

## 项目真实结果

OS `10.0.26300`、Python `3.12.10 x64`、微信 `4.1.15.56`；DLL SHA-256 `932dd6700f22e32faeb9b0cc9581a1f601e60ace151067680d3f6596496fab99`，原绑定进程／创建身份／窗口一致。

| 验收 | 实际结果 |
| --- | --- |
| 私聊身份 | 1.469 秒，资料卡 91 个节点中带标签的完整微信号精确匹配；两次读取、私聊标题栏、选中行及空草稿均稳定 |
| 私聊初始基线 | 1.906 秒，8 个当前可访问项，读取前后再核对完整身份，DPAPI 捕获；8 个事件哈希唯一且与解密内容重新计算一致 |
| 上传范围 | 基线 `historical=true`、`upload_eligible=false`；本轮没有 Provider 或发送调用，累计 API 8/10、发送 3/5 未变 |
| 清理 | 两次成功检查均零事件、开关恢复、子进程退出及来源校验通过；最终 pending／锁为空，原发送账号绑定未变 |

这是已在前台展开资料卡的开发只读检查，`background_qualified=false`。8 项只是当前加载尾部，其中包括通用／文件显示／引用控件；没有把其显示文字当作附件正文或完整历史。正文仅在受保护文件中，不进入公开报告。基线存入独立、按收件人哈希及进程创建身份隔离的 DPAPI `private_baselines` 桶，身份刷新不覆盖它，同一进程的重复基线捕获在启用开关前拒绝。新消息过滤和回复调度尚未接入该基线，不能声称私聊自动回复已经运行。

发现并修正了两个定位错误：该确切 DLL 的私聊类名是 `mmui::ChatTitleBarChatSingleView`，原 `ChatTitleBarContactView` 字符串不存在；项目的新私聊检查及共享读取器的输入标签为空时的备用定位均已修正。真实通过同时证明该类存在于当前私聊，并非仅据静态字符串推断。新增私聊标题栏进程／RuntimeId 两次一致检查；错误类、其他进程或重建控件仍拒绝。

以下证据均在 `.local/live-acceptance/`，失败原样保留：

| 文件 | 结果 | SHA-256 |
| --- | --- | --- |
| `contact-profile-dba5aaedc3a5497e867231610d27f62e.json` | 原错误标题类拒绝，零事件／恢复 | `57b666f53d1287da8b4f072a3b341515bfdc00bf5c8f809a2490fb0d1d7b9afa` |
| `contact-profile-a1ef6e236b1c4188b875d345b78c51ef.json` | 共享备用定位未修正时拒绝，零事件／恢复 | `ad0bee558a270b0d800ab72099b7148f6e517940eead223eb1e05ce2eb83ba7a` |
| `private-header-static-98bde00803c44bbc80a1195229f53a02.json` | 确切文件标题类名清单，不操作进程 | `d4924f405c7bf2bcc282ef2a85ea551563121d61b4b80b8806c3c0ce65b3a7f3` |
| `contact-profile-7b08fd7f57b4472e9a4bcfe555e5bb0d.json` | 精确私聊身份通过 | `3856b1ee19e5f6da9825e6acfa66ec02248ffd18bada0fd985932c4e01418491` |
| `contact-profile-def9f90432b24727809c12bdce1a1f0f.json` | 身份和加密基线通过 | `4fb7db3fc634c7d5a9009682f78c7ef12827c14d1a90abb85dabe4dfbc876e69` |
| `private-baseline-finalize-80a7c5f7aaba4070b4d5d1e818f54cb8.json` | 将既有成功基线独立索引并回读核验；不再次接触微信 | `29705fd6be0dc43c6f6adf63780966dc74b0f541d54297143432d144c144261c` |

## 实际操作和证据

computer-use 的当前窗口列表返回原微信窗口；恢复窗口后，测试群编号 002／003 和空草稿可见。仅搜索操作者给出的显示名，从精确同名联系人进入私聊，再打开该聊天信息栏中的联系人资料卡；未打开其他搜索结果。资料卡显示的完整微信号（跨两行显示）与获准收件人相符。当前保持这张卡展开，方便下一独立项目核验。

上述 computer-use 阶段没有发送消息、使用模型、导出联系人、保存截图或索引／上传私聊历史。其后项目只读阶段建立了本页记录的加密历史基线，仍没有上传。开发前台操作不作为无干扰能力证明。

本机记录 `.local/live-acceptance/development-cua-private-c705994804414c079283c2f827fc2d77.json`，SHA-256 `4e0618bf9952bc94075b5ade22f1402b06e6affb54ab796b2e54adb01112a96b`。记录只保留核验布尔结果、预期 ID／显示名哈希、工具返回窗口号及原账号指纹引用；没有复制图片。指纹引用是既有绑定，不伪装成本轮原生进程检查，后续必须再次匹配实际进程创建身份。

## 已实现的只读入口

- `scripts/contact_profile_identity.py`：限定一个私聊标题和唯一选中行、空草稿，再读取唯一 ProfileUniquePop。最多 160 个节点、每个 30 个子节点、总文本 16,384 字符。只接受带“微信号”标签的准确 ID；昵称、备注中的嵌入文本和多个冲突 ID 均不通过。两次读取后复查资料卡和会话 RuntimeId，不能静默重绑。
- `scripts/verify_contact_profile.py`：默认 disabled；显式提供发送账号、收件 ID／显示名、CUA 观察和现有合成证据。实际运行必须重新核验原进程版本／DLL／创建身份／窗口，使用既有单字节无障碍开关及恢复日志。默认只读资料卡和会话元数据；额外 `--capture-private-baseline` 才可在精确身份核验前后保护下读取最多 50 个可访问项并加密保存。工作进程限时，不写输入、不发送、不加载发送 helper、不更改发送账号绑定。
- 成功记录仅在恢复、退出及来源校验通过后写入独立的 DPAPI `verified_contacts` 桶；不会开启普通控制台读发权限。`persistent_conversation_id_verified=false` 保留，尚不能证明跨微信重启身份。临时开关／其他实验有 pending 时拒绝，恢复失败保留锁和日志。
- 开发时允许已展开资料卡处于前台，但前台窗口、焦点、剪贴板或事件发生变化仍停止；始终 `background_qualified=false`。没有通过回车、剪贴板或后台点击代替验证。

代码创建曾被自动审批拒绝，理由是将资料卡读取的临时开关视为超出发送按钮 helper 范围。随后核对用户此前对无障碍开关和私聊读取的授权，以及 `M1_SEND_REPAIR_CANDIDATE.md` 中明确区分的两个实现，提交复审后获准创建该默认关闭入口；没有通过其他执行渠道绕过拒绝。后续独立项目运行已按既有授权通过宿主审查并完成上表真实检查。

## 离线检查

累计新增 20 项合成测试：标签／昵称区别、冲突 ID、错误会话、群聊、草稿、其他进程、循环及边界、资料卡替换、读取期间会话变化、旧视觉证据／PID 创建身份变化、发送账号不变、默认关闭、超时、恢复失败及资源清理；新增覆盖真实类名／标题重建、空输入标签的备用定位、基线读取前后核验、未知读取结果、基线独立且不可覆盖、未完整清理不能保存。所有替身只操作合成数据，不连接微信或付费 API。

完整 **858 项通过，14.679 秒，无跳过**；日志 `.local/live-acceptance/offline-contact-baseline-final-20261026.txt`，SHA-256 `7c6d2e8dce1c27bf01ced28a8bd1295ff90709f808084b227f07a46d29fc6ce6`。当前入口 SHA-256 `25665a1f449c1824f04cb240581bee9832f128fc1c932e05d3cbc1c439d68651`，身份模块 `ea0633e257a5285afbe3996cab7cf8f263094bfa2aa1d80b0ad2495c0e421290`；各次实际运行的源码哈希在各报告内。此前 852／856 日志保留。默认 CLI 返回 disabled，`git diff --check` 通过；没有新增依赖或上游代码。

API **8/10**、发送 **3/5** 未变，旧 unknown 和所有账本保留。

## 复现与后续

[computer-use SKILL.md](D:/CDriveSlimData/maintenance-20260917-161410/live/plugins/cache/openai-bundled/computer-use/26.1021.31345/skills/computer-use/SKILL.md)要求先读 [guidance.md](D:/CDriveSlimData/maintenance-20260917-161410/live/plugins/cache/openai-bundled/computer-use/26.1021.31345/docs/guidance.md)，其中明确规定：

> Do not mix direct PowerShell UI Automation code in the same turn as Computer Use.

CUA 导航与本页真实项目检查已分轮完成。后续 CUA 已收起资料卡，第二次 Escape 后窗口捕获不可用，未证明最小化／退出；下一原生轮先检查原进程，不重复登录、搜索或采集旧基线。新的明确私聊 scope 和 mode=0／固定发送准备见 [私聊后台验收](M1_PRIVATE_BACKGROUND_ACCEPTANCE.md)。下列身份只读命令已运行，供审计复现；若卡已关闭会拒绝，应按需用独立 computer-use 轮重新打开，不能调用自己的账号核验方法把收件人误绑为发送账号。

```powershell
.\.venv\Scripts\python.exe -B -X utf8 scripts/verify_contact_profile.py `
  --inspect-contact-profile --allow-accessibility-patch `
  --account '<当前已绑定发送微信号>' --private-id '<已授权收件微信号>' `
  --private-label '<操作者提供的显示名>' `
  --cua-evidence .local/live-acceptance/development-cua-private-c705994804414c079283c2f827fc2d77.json `
  --synthetic-evidence .local/live-acceptance/uia-node-context-faea0f12f99e4c6cbb69960274a76e4c.json
```

该检查仅为私聊身份依据，不代表私聊自动回复、消息方向、群 @、分页或附件已经通过。历史私聊依旧不能上传；后续真实回复仅使用本轮新测试消息。

已消费的 `--capture-private-baseline` 不应重复运行；当前进程的独立基线已保存，刷新身份不得重新定义旧／新边界。后续私聊 mode=0、发送和新消息调度应使用明确的私聊 scope 与这份 exact-ID 证据，不能把私聊名称冒充 group 参数来绕过旧群实验的 index=0 守卫。普通生产读发适配表保持为空。
