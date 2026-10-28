# M1 发送按钮动作验收：2026-10-26

2026-10-26 19:05 新进展：已授权的临时 GUI 线程适配实际触发了原按钮 clicked 信号，取得清空草稿及新精确回显，全程零焦点事件，卸载与恢复通过。旧 UIA Press 无效入口仍关闭，旧 unknown 未改判或重试。新请求、额度审计、18 个恢复场景、24 项原生内存检查及 800 项离线回归见 [M1_BOUND_SIGNAL_SEND.md](M1_BOUND_SIGNAL_SEND.md)。以下保留根因与早期证据。

**后台输入与清稿通过，固定发送仍为 unknown，M1 未完成。** 本次实际发送按钮的无障碍实现只处理 SetFocus，UIA Invoke 传入 Press，因此该接口不能据“可获取、调用返回”认定有效。运行时绑定和磁盘／加载代码已核验，未采集执行调用栈；不把静态分支结论写成实际调用链。

## 后续公开接口与发送接收者核验

2026-10-26 续查没有写入或发送。公开语义、信号连接、捕获对象三个真实只读检查分别耗时 2.094 秒、3.079 秒、3.172 秒，零前台／焦点事件、输入环境未变，工作进程退出和临时开关恢复通过。最终完整离线 **778 项通过，12.890 秒，无跳过**；日志为 `offline-send-capture-final-20261026.txt`。此前 775 项日志保留。

- `probe_bound_semantics.py` 检查输入框、发送按钮、已绑定会话行、输入区和当前群末尾 5 个消息节点。发送按钮无独立 HWND，Legacy.GetIAccessible 返回空，ObjectModel／ExpandCollapse 不可用；消息列表未提供 Scroll。会话行报告 SelectionItem，但本轮未调用，不能标为切换能力通过。首次探测因 COM 将空 HWND 返回为 None 而失败，已修正为 0 并补测试，旧失败报告保留。
- `trace_send_widget_static.py` 从确切 DLL 的字符串头、元对象引用和完整继承链定位类。实际发送类继承 `XOutlineButton → XSkiaWidget → QWidget → QObject`，没有 QAbstractButton 或声明的 click 槽位。当前文件的通用无障碍动作不支持 Press，不能借用标准按钮的点击能力推定此按钮可用。
- `probe_send_accessible.py --inspect-connections` 沿同一发送节点缓存，核验弱引用、对象 getter 和类型，只读 1,077 字节。按钮连接索引 7 的接收者为 `mmui::ChatInputView`，回调 RVA `0x1C231B0`；完整继承链的父类信号数为 7，因此该索引对应本类第一个 `clicked` 信号。
- 回调的调用分支将参数 3 传给 `0x1C11050`。输入区元数据的第 8 个方法为 `Send` 槽位，metacall `0x8A8CA0` 的有界跳转表将该索引转到 `0x8A8FCE`，该分支同样进入 `0x1C11050`。脚本逐字节校验这些函数及跳转表，输出复现证据。它证明对象绑定和静态接线，**没有证明这些函数本轮执行或后台发送无副作用**。
- 最后补核回调实际读取的 `slot_object+0x10` 捕获对象，确认与连接接收者相等；同时核验加载的 72 字节回调与文件一致。本次总读取 1,237 字节，结束时再次核对所有见证值。没有把 receiver 类型相同误当作对象相同，仍未执行回调。

已在固定发送实验入口拒绝当前确切指纹（`send_accessible_press_unsupported`），未知指纹也保持 unverified；普通预检／发送在启用临时开关及占额前停止，底层 Invoke 还有独立拒绝。只读复核、精确测试清稿继续可用，旧 unknown、固定槽位和账本均保留。后续新的候选必须独立实现并验证，不能通过重置槽位恢复此入口。

新增报告均位于本地忽略目录：

| 报告 | SHA-256 |
| --- | --- |
| `bound-semantics-e6b4c6b362b54aea952286399c77334b.json` | `fbf2e434f96f983f2bea126ed26e7a8f50c46ec67c7729ec0f8c4e0272f40f66` |
| `send-accessible-d667eb0f6c9a4d0399f9d86510794d40.json` | `60d558058c91807e781a9bd691a36cca6d70345f9cc885fddd7ed49b146798fd` |
| `send-accessible-dc54a1148a104067932570abff9dbd7f.json` | `135eebaf42f70146ab6e54420aaba652b99ad6ae926e6f31e4b0aacdce387ca3` |
| `send-widget-dispatch-20261026.json` | `88e62a6ca4a157d7e871ce183fabc0861dd5749c2f833fb7f783612bc6dc8495` |

静态复现，不接触运行微信；输出需使用未存在的新文件名：

```powershell
.\.venv\Scripts\python.exe -B -X utf8 scripts/trace_send_widget_static.py --dll 'D:\apps\wechat\Weixin\4.1.15.56\Weixin.dll' --output '.local/live-acceptance/send-widget-review-new.json'
```

下一候选及其授权边界见 [进程内发送适配候选](M1_SEND_REPAIR_CANDIDATE.md)。现有公开接口检查未找到有效入口，不能据此声称已经排除客户端所有可能接口。

## 真实检查

环境：Windows 10.0.26300、Python 3.12.10 x64、微信运行文件 4.1.15.56；主 DLL SHA-256 为 `932dd6700f22e32faeb9b0cc9581a1f601e60ace151067680d3f6596496fab99`。复用已有账号资料卡身份、PID／创建时间／窗口／模块绑定。宿主只读进程检查确认旧进程未退出；computer-use 的当前窗口枚举缺失不能证明进程退出。用户重新选择指定群后，仅会话行 RuntimeId 重建，保存新目标证据，未覆盖旧报告。

| 检查 | 实际结果 |
| --- | --- |
| 消息读取 | 读取指定群当前可访问的 5 个 ListItem，正文 DPAPI 加密；不滚动、不上传、不读其他聊天 |
| 固定发送 | 空稿核验、FALSE pattern 关联、持久化固定槽位、SetValue 完整回读、一次 Invoke；5 秒内没有完整发送回读，保留 unknown |
| 后续只读 | 固定草稿仍在；仍为 5 个消息项，固定文本完全匹配及包含匹配均为 0 |
| 测试清稿 | 按最新开发授权，精确匹配本任务固定草稿后 SetValue 空值并完整回读；不调用发送，不清理其他内容 |
| 无干扰观察 | 上述操作及后续按钮读取均零前台／焦点事件，前台、焦点、剪贴板及两个虚拟桌面的检查未变 |
| 恢复 | 每次工作进程退出、临时无障碍开关恢复，无清理错误；不附加调试器、不改磁盘客户端 |

发送额度计的是尝试：当前 1/3，确认成功 0；上述发送阶段 API 为 5/10，后续独立视觉对照后为 6/10。扩大额度不能把此 unknown 变成可重试。新的发送候选须用独立请求留痕，不能重复调用这次未知请求。

## 运行时绑定与静态分支

只沿核验发送按钮的 UIA RuntimeId 查询已验证的 Qt 缓存，没有扫描堆或读取聊天／凭证内存。最后一次共读取 589 字节元数据与代码，每次检查进程身份、内存区域、读取预算及对象一致性。

1. UIA 控件为 `mmui::XOutlineButton`；实际主虚表 RVA `0x908CD08`。
2. 当前文件的 Invoke（`0x66291E0`）调用主虚表 `+0x98` 获取 ActionInterface(3)，随后向动作虚表 `+0x20` 传入 Press。
3. 运行时主虚表的转换方法 `0x9D05E0`，文件和加载的 14 字节一致；该确切方法在类型 3 时返回 `this+0x10`。
4. 对应动作虚表 `0x908CDB8`，doAction 为 `0x9D0330`；文件和加载的 127 字节一致，SHA-256 `2485e303d02e2a46a1f35e283fcc3a5db1f3986ae7a93ba8ce08081aa17eda31`。
5. 此 doAction 在可用状态下仅比较 `0x868F20` 返回的 SetFocus，不相等即返回；Invoke 使用的 `0x868A60` 返回 Press。两个字符串通过当前文件的初始化指令、返回槽位与 QStringLiteral 长度／内容核验，不是凭函数名称猜测。

该结构与 Qt 的通用 [QAccessibleWidget::doAction](https://raw.githubusercontent.com/qt/qtbase/5.15/src/widgets/accessible/qaccessiblewidget.cpp) 相符；[UIA Invoke provider](https://raw.githubusercontent.com/qt/qtbase/5.15/src/plugins/platforms/windows/uiautomation/qwindowsuiainvokeprovider.cpp) 调用 Press 后返回成功，并不验证操作效果。这里的判断以当前微信运行时绑定和确切二进制为主，开源实现仅作对照。未复制 Qt 源代码或调用内部地址。

## 实现与复现

新增三个显式研究入口，默认均 disabled：

- `scripts/probe_bound_message_labels.py`：严格限当前已绑定群最近最多 50 条可访问消息，正文直接 DPAPI 保护，公共报告只留结构。
- `scripts/verify_bound_send.py`：`--preflight`、`--run-fixed-send`、`--inspect-after-attempt`、`--clear-test-draft` 互斥；未知发送不重试，清稿只能匹配本任务固定文本。固定发送槽位现已占用，不能再次运行发送模式。
- `scripts/probe_send_accessible.py`：`--inspect-bound` 仅查发送按钮接口元数据，不调用 Invoke 或内部函数。识别当前 focus-only 实现时返回 `press_action_supported=false`。

实际执行使用明确账号、群、私聊、当前目标证据、自建 UIA 关联证据、虚拟桌面 GUID 和 `--allow-accessibility-patch`；这些账号参数不写入仓库。当前目标证据为 `.local/live-acceptance/m1-target-rebound-20261026.json`，旧绑定保留。若进程或选择已变，先重新核验，不能照搬旧 RuntimeId。

完整离线 **758 项通过，13.414 秒，无跳过**，日志 `offline-send-action-final-20261026.txt`。新增 24 项测试覆盖缺省不接真实客户端、正文保护失败不落盘、数量限制、身份变化、重复固定消息、预算／日志失败、已有草稿、接管、超时 unknown、精确清稿及动作代码不匹配。默认测试不调用微信或模型。

```powershell
$env:TEMP = Join-Path (Get-Location) '.local/test-tmp-desktop2'
$env:TMP = $env:TEMP
.\.venv\Scripts\python.exe -B -X utf8 -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -B -X utf8 scripts/verify_bound_send.py
.\.venv\Scripts\python.exe -B -X utf8 scripts/probe_send_accessible.py
```

后两条只是安全复核默认关闭状态。

## 证据与剩余项

本地忽略目录 `.local/live-acceptance/`：

| 报告 | SHA-256 |
| --- | --- |
| `message-labels-2646d9ff6dc34cf09f3c5f92ef4c3018.json` | `1ca6f555ed0b0d63524218b7ec550bff9b5e5a79160ada6aefa794bf1abdf1b6` |
| `fixed-send-b164a4063cbc4ce3811a23b54a0c84cc.json` | `4490a6e9be44be2cab342b913eb01feeae4a6793ff8f6aee5feac0dbaa062edb` |
| `fixed-send-ab246a09b92c42fdbd43044b4ad0bb81.json` | `dd0b966c88e0f9b885c7494575e7eed25dd02c11ee5554e7fa84e5ebbda90421` |
| `fixed-send-8d17549c778f46adbf08e42bbef8bca2.json` | `40d53f9a163d4d11d506efd4d01caa02dc51401fcf52fa35437b406506f0d429` |
| `send-accessible-66d8e7cfd033478f925d79f0d35c9df6.json` | `3af27bb794ff411c3a0e71d994ab54ca8e9b09f83804a8c0c30e7af77677bd40` |

适配器修订由各报告的完整源码 SHA-256 集合标识，不能仅用仓库 HEAD 代表这个未提交工作区。`send-interface-cast-static-20261026.json`、`send-do-action-static-20261026.json`、`send-action-names-static-20261026.json` 和 `send-action-literals-20261026.json` 保存磁盘指令与动作字面量来源；早期只读接口报告保留，不用新分类替换它们。

当前无干扰发送路径缺少有效 Press 行为，生产读发适配表继续为空。下一步可研究客户端另一个可验证的外部语义入口；若只能新增客户端功能补丁，先交付修复点和范围，不能把本次读取／普通测试授权扩展为新功能补丁。开发期前台 computer-use 辅助可继续采用用户既有授权，但其成功不能作为后台发送验收。己方身份、私聊身份、群 @、附件、180 秒真实摘要和跨重启联调仍分别待验收。
