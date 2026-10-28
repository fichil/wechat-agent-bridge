# M1 真实后台发送候选：2026-10-26

**19:05 首次取得实际发送调用、清空草稿和一个新精确回显；未检测到焦点、桌面或剪贴板变化。M1 整体未完成，生产适配表仍关闭。**

20:59:55 续验：新增固定 case `memory-003`，在**当前桌面后台**完成另一次发送和精确回显，5.328 秒、零焦点事件、剪贴板未变、空稿和完整卸载／恢复通过。原生 helper 未重建，继续使用下文经过合成恢复验证的同一构建。此前当前桌面 mode=0 只读加载／卸载也通过。此结果不称为虚拟桌面隔离，也不代表用户持续键盘操作时的稳定性已经验收。

该模式只取消“目标必须在另一桌面”的前提，目标仍须在指定桌面且 `current=true`；前台 HWND、键盘焦点、剪贴板、事件观察、账号／会话、空草稿、对象身份及卸载检查保持。显式 `--current-desktop-background` 仅用于已审查候选，不开放旧 Press；发送证明必须来自同一桌面模式的 mode=0 检查，旧隔离模式证据不能直接放行。

固定内容为 `微信 Agent Bridge M1 记忆验证 003：项目编号 BRIDGE-MEM-003；测试会议时间为 2026-10-27 14:30；请仅作为测试记录。`。这是纯合成记忆夹具，不是实际会议。新审计扩展 `bound-memory-20261026-003` 增加一个独立槽位；累计发送 **3/5**，其中两个新编号有界面回显，原 unknown 保留。CLI 仅允许固定 `--case send-002|memory-003`，没有任意正文参数；二者已消费，不得重放。

新增证据均在 `.local/live-acceptance/`：

| 报告 | 结果 | SHA-256 |
| --- | --- | --- |
| `bound-thread-hook-1a620e70f8624b0694c8a1244cb24168.json` | 当前桌面 mode=0，Qt 未调用，零事件及完整恢复 | `990c7fa2715f4786c678ab1ecbfb8a97d4949346c838854ccecf04e483132770` |
| `signal-send-3c666444adc34465ae4394c9c5cb27c2.json` | 003 实际进入／返回一次、异常码 0、一个新精确回显；helper 卸载及恢复 | `c12a64db1127c4709c0414a751c4f641c73178655432f2a2fd805f3aefe85226` |
| `offline-incremental-memory-tail-20261026.txt` | 838 项离线通过，13.879 秒，无跳过 | `767ca065898a083a47d848355cb8c2a2985c439cd971ebfaccc53ce93fc91358` |

后续记忆／模型结果见 [群记忆验收](M1_GROUP_MEMORY_ACCEPTANCE.md)。下文保留 19:05 的初次发送过程、当时额度及后续只读结构研究。

## 实现与范围

用户已授权仅绑定微信进程的临时发送适配，并允许开发期间自主处理普通测试及本任务草稿、按需追加额度。此次只操作既有绑定账号和已授权测试群，没有打开其他会话，没有上传聊天或调用模型。磁盘微信 DLL、防撤回文件和备份未改动。

原输入焦点问题沿用 client-first／AutoSetFocus=FALSE 修复。旧发送按钮 UIA Press 无实际动作，入口继续拒绝。新路径通过 `SetWindowsHookExW(WH_CALLWNDPROC)` 把自有 helper 限于绑定窗口的一个 GUI 线程，独立持有进程负责卸载，20 秒硬截止。每条专用请求核验控制者／持有者／目标的启动身份、随机 nonce、窗口、非零线程、序号、暂停和截止时间；不提供任意函数地址、代码写入或键鼠／剪贴板回退。

`scripts/native/bound_send_inspector.h` 在目标线程只沿三个既定 RuntimeId 查询缓存，核对输入、按钮、会话对象及弱引用；由确切文件指令核验会话 `XTableCell + 0x210` 选中标记，验证唯一 clicked 接收者与捕获对象，检查当前 foreground、最后输入时间和剪贴板序号。当前文件四段关键代码哈希逐段核验。发送时只进入 `XOutlineButton` 的 metacall（RVA `0x911110`，Call=0、method=0），由 Qt 自身发出无参数 clicked 信号并管理连接生命周期，不直接调用捕获 lambda。

父进程在设置前核验空草稿，完整回读编号文本，持久化新请求与 unknown 后才释放调用。加载 helper 后、调用前再次核验账号、会话、完整草稿及对象一致性。Native 回调记录实际进入和返回；超时／异常结果不重试。当前最终发送检查仍依赖限定会话 UIA 身份及控件生命周期，不构成跨重启稳定聊天 ID 的证明。

新增独立入口 `scripts/verify_bound_signal_send.py`，默认关闭，要求 `--allow-process-helper`、`--allow-accessibility-patch`、精确账号／两测试会话、同构建合成证据及当前进程只读加载证据。普通控制台不会因此自动获得 helper 或发送权限。

## 环境与证据

- OS `10.0.26300`，Python `3.12.10 x64`，运行微信 `4.1.15.56`。
- 微信 DLL SHA-256：`932dd6700f22e32faeb9b0cc9581a1f601e60ace151067680d3f6596496fab99`。
- helper SHA-256：`976b026d37b714d97136f77aab7ab142922c43a55dec8ddebc3bca434652ca96`；原生源码／编译器指纹及完整产物保存在 `.local/thread-hook-bound-send/archive/c461d15a1bd1cd44e028f2454ac9fa4937e43ff91a8953c86b5e46aa492339b1/`。
- MSVC `14.29.30133`、SDK `10.0.19041.0`，`/W4 /WX /MT /GS /guard:cf`、DYNAMICBASE、NXCOMPAT；无新增下载依赖。

所有下列证据在 `.local/live-acceptance/`，源码哈希在报告内：

| 证据 | 结果 | SHA-256 |
| --- | --- | --- |
| `thread-hook-synthetic-0babe13d34dc4b2a84525c222b9ad5fb.json` | 18 个实际恢复场景、24 项原生内存夹具检查通过；自建目标正常退出和模块卸载 | `421fdd9071fa2e84a2f2306e08850223ae93851b7710c6e161981e9dabacf1cf` |
| `bound-thread-hook-2cf657789ff348bb8f8cc834aa4d116d.json` | 同一最终构建在真实绑定进程 mode=0 检查通过，没有调用 Qt 方法；恢复通过 | `9b0b4470d922bc424491d3ef4a016f4a4a6b04e5e4c35570af550808ddedc347` |
| `signal-send-0b57a4fbde654b69b1897bf24a765599.json` | 4.953 秒、实际 GUI 线程方法进入并返回、输入框清空、一个新精确回显、零事件、清理通过 | `5053ead1b8b0adbb16de73747535f9d0813a72b6601d10374153807472e56351` |
| `offline-bound-signal-20261026.txt` | 800 项离线测试，13.660 秒，无跳过 | `31d1d2aed810ff75c1ea201b4752eb4d510d155991d3ecac7c3a52d89b1e8936` |

合成场景包含重复请求、暂停、超时、身份不符、对象改变／销毁、动作前及动作中持有者退出／取消、控制者实际退出、安装前退出、安装后空闲退出、目标退出。真实检查确认 Unhook 成功、在途回调排空、卸载后独立消息确实送达而无新回调、目标中 helper 模块消失、持有进程正常退出、映射关闭和临时无障碍字节恢复。自建夹具检查失效缓存、循环、错误虚表、不同捕获对象、额外连接、取消选择和代码变化；默认离线套件不执行这些原生实验。

修复了新增测试的提前 native 导入改变 `sys.path` 导致默认 unittest discovery 冲突的问题。保留默认 `python -m unittest discover -s tests -v`，未用换发现方式掩盖问题。

## 请求与额度

新消息固定为 `微信 Agent Bridge M1 验证（后台适配 002）`，与旧尝试正文区分。旧 `fixed` 槽位及 unknown 保留。`AcceptanceLedger.extend()` 创建经授权且不可变的扩展记录，新 `extra:bound-clicked-20261026-002` 槽位只可消费一次，重启不重置；此函数不暴露给聊天内容、模型或控制台 HTTP。

累计发送尝试 **2/4**，其中新实验有一次界面回显，旧实验仍 unknown；API **6/10** 未增加。此候选入口再次 `--run-fixed-send` 会拒绝已消费槽位，不要删账本或换请求 ID重跑。后续若需其他测试，应有独立目的、编号、范围核验和扩展审计。

## 限制与下一步

一次固定文本的界面回显是实际证据，但不能单独证明通用己方消息身份或收件人送达。当前不会据此启用自动回复。需继续在获准会话核验发送者／群 @、私聊目标、历史覆盖与分页、附件后台获取、真实 AI 回复、180 秒增量记忆、重启暂停及去重。视觉三次合成测试失败仍独立记录，不自动切换模型。

下一步从刚新增的编号消息做只读结构／身份观察，随后接会话选择和完整读取；不重放编号 002。需要开发辅助点击、打开测试窗口或清理本任务草稿时沿用已授权 computer-use 范围，不重复询问操作者。

可复现离线与原生合成命令（不接触微信）：

```powershell
.\.venv\Scripts\python.exe -B -X utf8 -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -B -X utf8 scripts/build_thread_hook.py --build-bound-send
.\.venv\Scripts\python.exe -B -X utf8 scripts/test_thread_hook.py --run-synthetic --bound-send
```

重新构建会产生新的产物指纹，必须使用该构建的新合成恢复证据及真实 mode=0 证据，不能引用旧文件放行发送。

## 发送后的只读跟进

`signal-send-3a55dbb632574701913df6025d644324.json` 再次确认输入框为空、6 个已加载项中恰好一个编号文本，零事件、临时开关恢复，没有再次加载 helper 或发送。SHA-256 `c8026d92e6933e46c0ef1ac1897485e444fde038c11c5dc97c6e021f7bb7a405`。

新增 `scripts/probe_sent_message_metadata.py`，只接受既有成功回显请求、相同进程指纹、确切 RuntimeId 和本任务编号文本；从该消息的缓存实例读取虚表与元对象，不扫描堆、不沿消息模型读字段、不调用内部方法。报告 `sent-message-metadata-1a1000e782bc4ee385b193989ee15b24.json`（SHA-256 `9d7f359988804f8da023ae17729801ae837804d32bbca00ebbf759b73d8cd3a8`）：651 字节，2.657 秒，零事件及正常恢复。实际类是 `mmui::ChatTextItemView`，虚表 RVA `0x934FBD8`；accessible 虚表 `0x908DD58` 与会话单元通用实现相同，不能把选中／checked 位当作己方身份。

确切文件上的 Name 路径为 accessible `0x9D1F70` → widget vtable `+0x1A0` → `0x1BE9490`；它还组合 `+0x1F8`（`0x3D71560`）与 `+0x200`（`0x3D66E10`）等显示文本。这只是静态线索，不证明任何字段是发送者 ID。静态记录 `sent-message-name-static-20261026.json`，SHA-256 `da38e7d415e2c48bcda999cc8a0b626e303e32def37c825adc5b15765e3e5956`。后续可沿这些已绑定控件的显示文本来源研究身份接口，禁止直接将 `+0x3C0` 等未解释字段命名为“己方”或读取任意消息模型内存。

新三项合成测试后，最新完整 **803 项通过，13.811 秒，无跳过**，日志 `offline-sent-metadata-20261026.txt`。发送与 API 额度未再增加。
