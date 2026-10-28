# M1 已绑定窗口恢复记录

当前后续状态见[私聊选择与恢复](M1_PRIVATE_SELECTION.md)：窗口现已普通显示，聊天控件恢复；精确私聊 Select 一次尝试为 unknown，navigation pending／匹配运行锁保留，禁止重复窗口或选择请求。先在下一 CUA 轮打开获准私聊，再独立原生只读恢复。以下为较早窗口准备的分阶段证据，不覆盖当前恢复状态。

2026-10-27 00:17：**公开窗口接口已将原微信窗口从隐藏恢复为最小化，再显示为普通窗口，均零焦点事件。但聊天控件仍未恢复，私聊后台检查失败，未发送。M1 未完成。**

## 已实现与实际结果

`scripts/development_window_restore.py` 是默认关闭的开发入口；当前发送账号 synthetic_owner、准确私聊 wxid_synthetic_archived_owner／显示名 synthetic_owner。只接受既有精确资料卡与不可覆盖历史基线、先前 CUA 恢复失败证明，并重新核验 PID／窗口／进程创建时间／版本／DLL SHA-256；不通过窗口标题推定账号。

原绑定 PID 17416、HWND 67678、创建身份 134374491831767460，Windows 10.0.26300、微信 4.1.15.56、DLL SHA-256 `932dd6700f22e32faeb9b0cc9581a1f601e60ace151067680d3f6596496fab99`。原无障碍字节与防撤回文件不因窗口入口改变。

| 步骤 | 实测结果 |
| --- | --- |
| CUA 一次既有应用入口恢复 | 未暴露 targetable window；复核后停止，不重复启动／登录 |
| 隐藏→最小化，ShowWindowAsync flag=7 | 0.438 秒，visible=true、iconic=true、零事件 |
| 最小化状态 private mode=0 | inspection_input_ambiguous；开关恢复、worker 退出；未调用 Qt 方法 |
| 已确认最小化→普通显示，flag=4 | 0.469 秒，visible=true、iconic=false、零事件 |
| 普通状态 private mode=0 | 同样拒绝；开关恢复、worker 退出、零事件；未调用 Qt 方法 |
| 普通状态结构复查 | 开关前／后均仅 Window 和 Pane，无 Edit／List，controls_exposed=false |
| 最终只读复核 | 同一完整指纹；字节 0、所有 pending／全局锁为空，004 未预留 |

两次窗口转换是不同 case，均已消费。当前不再执行这两个恢复请求。控件缺失的原因尚未证明，不能把 Win32 可见状态等同于 Qt 内部状态或聊天可访问。没有读取／上传私聊历史，没有写草稿、发送或新 API 调用；累计 **API 8/10、发送 3/5**。

微软将 7 定义为最小化且不激活的 `SW_SHOWMINNOACTIVE`，4 为普通显示且不激活的 `SW_SHOWNOACTIVATE`；[ShowWindow](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-showwindow)。异步返回只表示请求已开始，因此程序另行回读可见／最小化状态并监测实际焦点；[ShowWindowAsync](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-showwindowasync)。文档没有替微信的行为作保证。

## 一次事务及恢复保护

入口固定两种状态转换，`--restore-hidden-window` 与 `--show-normal-after-minimized` 互斥；后者须有同进程已确认、零事件且清理正常的最小化准备记录。分别使用 hidden-private／normal-private 加进程创建身份的键。请求前持久化 unknown 与锁 token，不提供任意窗口指令、函数地址或重试接口。

身份、来源、停止文件、锁屏、前台、焦点或剪贴板变化即停止；unknown 保留 pending 和全局锁，其他读取／发送／记忆入口拒绝。`--inspect-after-attempt` 只对原终态 unknown 回查；普通状态须加 `--normal-scope`，连续确认正确状态和自己的锁 token 后才可清理。原 unknown 不改写、不重放。

该入口始终 `background_qualified=false`，不启用客户端字节、不加载 helper、不调用客户端内部方法、不输入、不发送、不改磁盘 DLL 或焦点属性／系统设置。临时无障碍字节只用于后续独立只读能力检查，并已恢复。开发辅助不替代生产后台验收。

## 合成修正与检查

初版报告未提供足够阶段信息；补齐后确认错误 1400 来自父进程从默认 Win32 桌面查询私有桌面的 HWND。修正为两个独立自建进程在同一私有非输入桌面工作：拥有者创建隐藏窗口并泵送消息，请求者核验其 PID 后执行最小化与普通显示；父进程只监测实际工作桌面。未修改公共 `test_focus_etw.py` 或原生发送构建。

最新原生合成两次请求各 1 次、回读通过，11 个工作桌面检查，局部焦点及工作桌面不变；两个子进程正常退出、窗口销毁、线程桌面还原、句柄关闭、私有桌面消失，没有强制结束。完整 **884 项离线通过，15.344 秒，无跳过**；专项 14 项包括模式默认关闭、固定指令、状态前提、持久化先于请求、状态变化／超时／接管、unknown 不重试、两种状态的只读恢复、锁 token 及来源／OS／清理门槛。默认单测不接触微信或付费 API。

最初在沙盒默认 TEMP 的专项运行遇到文件权限错误；使用已有 workspace `.local/test-tmp` 后专项和完整发现通过，没有放宽文件权限或跳过测试。先前同名测试导入失败和原生跨桌面失败报告全部保留。

## 证据

均在本机 `.local/live-acceptance/`，不包含聊天正文或密钥：

| 文件 | SHA-256 |
| --- | --- |
| `cua-window-restore-47f1799eb12443d7899f173e59e93abe.json` | `0c10cc0678e773987b603333a3005688f153bdaaabda741897775e9195be9998` |
| `window-restore-synthetic-b6c400e818334b1db9be37d439ec3d30.json` | `078a7e024996e69dac663279f7431151ddc8f6449890c9aacddfcc0d964355b1` |
| `development-window-restore-a393be184cde4cd9bef04aec98eca76c.json` | `e243a5639172ce08f9a94672fe711ae533e7b20b7909538ccd93df4653f23e3d` |
| `development-window-restore-702c76f741204a30b2dadc7aabe8c01d.json` | `b7a631613425dd1236fc72d2c91ac0e4395df95d40cf4537eb85f134fb455bd0` |
| `bound-thread-hook-b71a0e090b634f1a8030be35e3fa8144.json` | `bf869a8e678c5109cc659b550f160d52d1d84ed43b7dc8fe0a77b8079e1cec17` |
| `bound-thread-hook-096c5aa254ab422495294f6ef00387a6.json` | `9b38fbd7bfb565b2d8bcdfdcc36615a087e83742af4895570ab4574dd9583b60` |
| `structure-20261026T161626Z.json` | `678e5df6871c4f8e42dd1be6d4220951e0a0275b987e30bdf1fa9865efd3d588` |
| `private-window-normal-followup-c8a5c9f700b541f2ac3a46e780e4a6ce.json` | `a7d84f620203f6078b17ebcc623fd7db188152637a0e70d9dad87c2bbab76492` |
| `offline-window-normal-20261027.txt` | `5dd302d2c2cc416720830b6868554a0a17820be0e20eda3d92173e5c64268f97` |

当前源码：恢复入口 `f06c5fddbe3f9c17be49d077efe535da8d1b973976b6685a14c0fc20c2d249fd`，原生合成入口 `60d5de7d07b89b58baa5feef72884f546738e6b4e8b07c7b12b23eb18087ebba`。报告记录各次实际源码，不以新版本覆盖旧报告。

## computer-use 后续实际准备及下一步

新一轮 CUA 用早先返回的真实对象重新取得 HWND 67678，后续列表也重新出现微信。头像入口响应并视觉核对发送端微信号 synthetic_owner，说明客户端确实可交互；先前查找窗口已不存在。没有输入文字、发送、启动应用、触碰其他聊天或修改客户端。主体私聊最后可见且草稿为空，资料侧栏仍开。

一次资料卡外部点击意外打开图片预览，立即停止原坐标操作，按新列表中精确的“图片和视频”独立窗口使用 Alt+F4 关闭并复核其消失。没有保存截图文件、上传或解析预览内容。当前开发文件夹旧窗口已不可取得，没有选择企业微信代替。最后用当前主窗截图的标题栏最小化按钮，工具明确报告 `window is minimized; call activate_window, refresh with get_window, then retry get_window_state`，因此不再激活或重拍。

记录 `.local/live-acceptance/development-cua-window-ready-3e9698900ced4f7ca4458d05a2f7504f.json`，SHA-256 `18483b9ce1667132f4f48a46f6c6d92be64188e62fe3330e2585d59352bb5294`。这是开发前台准备，`background_qualified=false`；旧指纹仅作引用，未谎称本轮重新执行 NativeGate。API 8/10、发送 3/5、private-004 未预留。本轮无源码变更，沿用 884 项离线结果，没有重跑或新增后台资格。

下一独立原生轮先查精确身份／窗口状态／锁屏／虚拟桌面／原字节／pending／锁，再按真实当前桌面选择正确模式做一次新的 private mode=0。因为客户端交互和标准最小化已改变真实状态，这不是在相同条件盲目重试；若仍不可见输入框，则诊断窗口启用、所属弹窗与线程状态，停止重复恢复请求。通过新私聊 scope 后才能发固定 004；不重采基线、不重放旧 unknown／发送或已消费的窗口 case。
