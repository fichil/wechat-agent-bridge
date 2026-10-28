# M1 精确搜索与固定导航续验

2026-10-27：**已实际完成单个搜索控件检查及一次精确收件微信号搜索更新；没有核验新的联系人或私聊，M1 未完成。** 本轮只使用项目原生接口，没有混用 computer-use。发送账号仍为 synthetic_owner，收件人 wxid_synthetic_archived_owner，群为合成测试群。API **8/10**、发送 **3/5**，004 未预留；原 unknown、pending 和运行锁保留。

## 实测结果

环境为 Windows 10.0.26300、Python 3.12.10 x64、微信 4.1.15.56；原绑定 PID 17416、HWND 67678、创建身份 134374491831767460。DLL SHA-256 为 932dd6700f22e32faeb9b0cc9581a1f601e60ace151067680d3f6596496fab99。生产读发适配表仍为空，没有重建或加载发送 helper。

| 步骤 | 实际结果 | 不能据此认定 |
| --- | --- | --- |
| 窄搜索检查 | 唯一搜索 Edit、显示名分类、可写；0.766 秒，零事件，退出／恢复正常 | 联系人或聊天身份 |
| 自建精确搜索 | 私有非输入桌面、自建 Edit；一次 SetValue，FALSE 关联、UIA／原生完整回读、清理通过 | 微信兼容性 |
| 精确搜索更新 | 1.141 秒，一次 SetValue 改为准确收件微信号，19 字符完整回读，零事件 | 搜索结果就是该联系人 |
| 搜索弹层 | SearchContentPopover→XVBoxView→SearchContentView→XTableView；六类联系人结果控件均未暴露 | 好友不存在或没有结果 |
| 固定通讯录导航 | 唯一 XTabBarItem、Button、Invoke 可用；无快捷键；DPI 修正后边界一致 | Invoke 能实际导航 |
| 一次通讯录 Invoke | 0.984 秒返回，零事件；三类候选页面结构未观察到变化，独立只读跟进仍为零 | 通讯录已打开，或所有页面结构都已检查 |

上述操作不按 Enter、不选搜索联系人、不读聊天正文／草稿、不截图、不调用模型、不发送。只输出查询分类、长度以及固定结构数量等元数据，不导出通讯录，navigation_verified=false 始终保留。

### 坐标与身份问题分别处理

原 UIA 导航边界 [73,218,133,254]、DWM 物理窗口 [144,152,1590,1630]，DPI=192。诊断工作线程在创建 UIA 客户端前进入 per-monitor-aware-v2，退出前恢复旧线程上下文后，UIA 边界变为 [146,436,266,508]，与导航及窗口边界一致。只改变诊断线程，不改系统显示或微信属性。依据微软 [UIA 缩放说明](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-screenscaling) 和 [线程 DPI 上下文及恢复接口](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setthreaddpiawarenesscontext)。没有按比例猜测／截断不合格坐标，没有据此点击。

这是诊断坐标问题的修复，不是微信抢焦点或会话选择的修复。Invoke 返回与业务导航分开记录；三个候选类未出现，不能证明客户端完全没有变化。该按钮不属于发送 helper，本轮没有扩展其功能。

静态文件检查发现 ChatSessionCell／SearchContactCellView 继承 QFReuseWidget；声明属性中没有微信 ID getter。SearchContactInfoIconView 只有 hoverEnteredIcon／hoverExitIcon 信号，XTabBarItem 有 clicked／stateChanged 信号。这些只属静态线索，不证明当前业务身份或实际调用链，也不授权调用内部信号。RuntimeId 只能定位控件，不能代替稳定联系人 ID；动态属性或其他 provider 能力尚未排除。

## 一次动作和恢复

inspect_bound_search.py 仍是默认关闭的纯只读 CLI，只在精确 MainWindow 下过滤唯一 mmui::XValidatorTextEdit／Edit／空 AutomationId，读取 Value／IsReadOnly，只输出分类和长度。新增弹层和固定导航检查通过内部只读组合复用协调器，没有任意 worker／PID／选择器 CLI。

development_exact_search.py 是独立显式写入口，只能把仍完整匹配的任务显示名改为指定收件 ID；用户改变的查询不覆盖。检查实际 ValuePattern 的 FALSE 客户端关联，先持久化一次 case／pending，后 SetValue。未知不重试；只在 setter 已返回有证据时允许纯只读恢复。此次 case 已消费，成功只清除自己的 pending，原 Select pending／锁不动。

development_contacts_navigation.py 只允许 MainTabBar 内唯一精确“通讯录”按钮，核验旧终态、原锁、精确进程及实际 InvokePattern 关联。动作前持久化 case，由原导航 request、进程指纹及控件 RuntimeId 决定；复制／重命名报告不能重开机会。只调用一次公共 Invoke，返回、未知、未回读、恢复失败分别记录；不授予后台能力、不调用内部方法。源码后来补齐 Invoke 已返回后超时的事实保留，不重放已消费动作。

结束审计：字节 0、helper 不在进程；patch／thread_hook／draft_probe／window_restore／exact_search pending 为空。原 development_navigation/pending=terminal_unknown、request 及锁 token 匹配。私聊 8 项不可覆盖、禁止上传的加密历史基线保留；原全局恢复锁继续阻止常规发送及记忆上传。预算没有新增消耗或扩展。

## 证据与资格

完整离线 **936 项通过，15.897 秒，无跳过**；日志 offline-exact-search-navigation-final-20261027.txt，SHA-256 3c2c9320fb514212cef8826b40805c7133f46ab74fc729f376f7c2471cd8a03b。默认测试均为合成数据／自建对象，未连接微信或付费 API。当前源码：development_exact_search.py=303b2d4ace01b5af7963db15c8b9f187af61a01ce50806ea5c37bf13f2b21513，development_contacts_navigation.py=7fe44e8a91dec8147fe2e23b638438c0e39387c60f53d5fcf322dc98dc282d2a，inspect_contacts_navigation.py=94ef436751cd368ad29bacad667d0dd08538f40c8fcac27640ade8142f048a32。源码版本不用于重开已消费 case。

以下均在 .local/live-acceptance/，不含消息正文或截图；各报告记录其当时的源码哈希，改版不回写旧报告。

| 文件 | SHA-256 |
| --- | --- |
| bound-search-control-1c906f9a67bd41318a74e757e70b748e.json | 7ea221485b91ff8dc5584eeb8b205c7d15ccff4d294fc47925c6572a687ab544 |
| exact-search-synthetic-066ce093750643e29920d92108b41c73.json | cb33f2d873cb8f46b9d8bca7153de94a34d292b361622cce740f22839deed32c |
| exact-recipient-search-b6196c8b88f5481091b0d2d02f06cb09.json | c1b6a7c82a7f37fa7af633a7493e70d3f0e16d497da3dc498ce6d88076a6349b |
| search-popover-metadata-6f404f17b97e4970bc97005a987e761e.json | 91eb0eb43113c2e84cfddd9ec429ed9693d62cf70ae7e4e23e4a5f612942672a |
| development-contacts-navigation-461ad22084f04cfdac5eca460e595683.json | 629709ddcb2f27fa0324ec4c87b74c34b9550bd1cd39fe4bc4ccf73482073a7e |
| contacts-navigation-metadata-01aa7bbc9dbb48e7b86a5b7d8a078a79.json | 6e72cd1f69370fbf28d06d5510534f37d53a52f7162e3ec4b51a606555c42695 |

navigation-recovery-audit-fcacb68be64447a6a7d226e5a3281ad2.json 的只读投影 navigation-recovery-audit-projection-43314a1b32584aba943387660e7245b6.json 去除了未计算的常量 sources_unchanged 标志，保留原件及来源哈希；该审计仅证明当时进程／账本状态，不作源码资格证明。首次沙箱内原生检查返回 patch_process_access_denied；随后同一窄操作经自动审查允许的非沙箱执行通过，未改系统安全设置。

原搜索自建资格是在当时源码运行；只读协调器之后增加内部组合，旧自建报告现在不能作为新写入资格。搜索写入及导航 Invoke 均已消费，不再运行。无新增第三方依赖或复制上游代码。

## 自动审批限制和下一步

此前 CUA 对同名结果点击及完整主窗截图＋文本的两次自动审批拒绝仍有效：准确收件人未核验，完整捕获可能包含非测试会话。没有重复这些动作；本轮通过的是更窄原生检查、准确搜索输入和固定应用导航，不是候选联系人选择。

后续需通过允许的独立 computer-use 开发步骤或已验证外部语义入口打开准确测试对象，再核对资料卡 ID。不能用昵称、旧 RuntimeId 或上述坐标直接放行聊天。computer-use 和直接 UIA 继续分轮；SDK 尚未返回独立搜索弹窗 Window，不能把原生 HWND 伪造成 SDK Window，也不重试被拒完整捕获。

当前安全可复现检查只读固定导航／页面元数据，不调用 Invoke：

    .\.venv\Scripts\python.exe -B -X utf8 scripts/inspect_contacts_navigation.py --inspect-navigation-only --allow-accessibility-patch --account synthetic_owner --private-id wxid_synthetic_archived_owner --private-label synthetic_owner --target-evidence .local/live-acceptance/contact-profile-def9f90432b24727809c12bdce1a1f0f.json --context-evidence .local/live-acceptance/uia-node-context-faea0f12f99e4c6cbb69960274a76e4c.json

无新客户端状态／问题时不重复该检查。准确恢复私聊后，才运行原只读 unknown 复核：核对准确对象、当前标题、空草稿、原锁 token，原 unknown 记录永不删除。不能直接运行 private mode=0、004、摘要上传，不能重跑旧 Select、精确搜索写入或导航 Invoke。

后台私聊、群 @、通用己方识别、分页、客户端附件和视觉仍未通过；本轮进展不能替代 M1 必需验收。
