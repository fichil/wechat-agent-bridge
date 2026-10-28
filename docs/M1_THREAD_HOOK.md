# M1 临时发送适配：GUI 线程合成验证

最新进展：最终候选已通过 **18 个实际恢复场景、24 项原生内存夹具检查**，包括控制者实际退出、安装前／后持有者退出和目标退出；随后完成真实只读加载、一次后台发送调用及卸载。完整离线 800 项通过，详见 [M1_BOUND_SIGNAL_SEND.md](M1_BOUND_SIGNAL_SEND.md)。下文保留最初 14 场景阶段及其失败修正，不代表当前仍未加载 helper。

2026-10-26 18:00 后续作。用户已明确授权 [临时进程内发送适配范围](M1_SEND_REPAIR_CANDIDATE.md) 和无人值守开发。**当前仅自建目标的 14 个原生场景通过；没有向微信加载 helper，没有真实发送，M1 未完成。**

## 实现

新增 `scripts/native/thread_hook.cpp`、`thread_hook_contract.h`、`thread_hook_synthetic.cpp`、`scripts/build_thread_hook.py` 和 `scripts/test_thread_hook.py`。入口默认关闭，原生测试不接受外部 PID／窗口参数，只启动本项目的合成子进程。helper 当前只能调用合成 EXE 导出的固定计数操作；没有微信函数地址或任意函数调用入口。

使用本机现有 MSVC 14.29.30133（编译器 SHA-256 `7d209e5c4650de6226159a70ceae4a36fb0d4559538a91985d28f4080762deb7`）与 Windows SDK 10.0.19041.0，无下载安装。启用 `/W4 /WX /MT /GS /guard:cf`、DYNAMICBASE 和 NXCOMPAT；构建及源码指纹记录于 `.local/thread-hook-synthetic/build.json`，产物不入库。

独立持有进程通过 [SetWindowsHookExW](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setwindowshookexw) 将自有 DLL 限定到自建窗口的 GUI 线程；只处理专用消息，其余调用传递给原 hook 链。它不修改目标代码、窗口过程或系统焦点设置。专用映射包含随机 nonce、进程启动身份、窗口／线程、请求序号、截止时间和停止标记。

操作前原子占用请求，检查身份、暂停、截止时间及对象代次；重复请求不执行。持有进程主动关闭时先停止，再移除 hook、等待在途回调排空，随后卸载本地库。微软说明 [UnhookWindowsHookEx 返回时仍可能有在途调用](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-unhookwindowshookex)，因此没有把函数返回当作完成恢复的唯一证据。

## 实际结果

最初一次正常动作执行成功，但由工作桌面发起的卸载回查未送达私有桌面，整体保持失败（`thread-hook-synthetic-355fe5a857f94de5b7e4199a9db180c6.json`）。已改用同一私有桌面的独立自建发送进程，必须确认探测消息确实送达，再判断无回调；没有放宽清理标准。

最终报告 `thread-hook-synthetic-edf3c3fbf2774ed68d1fe870bb8674be.json`，SHA-256 `ca236973b6b12332c4e2f5ee8faff5ae18e691ad85b3e74b06e4073bc6d4082a`：

| 场景 | 计数动作 | 结果 |
| --- | --- | --- |
| 正常、同序号重复 | 累计一次 | 实际在目标 GUI 线程执行，重复拒绝 |
| 预先暂停、截止时间已到 | 零次 | 拒绝 |
| 目标／持有者启动身份不符 | 零次 | 拒绝 |
| 对象代次变化、对象销毁 | 零次 | 拒绝 |
| 回调进入后、动作前取消 | 零次 | 拒绝 |
| 回调进入后持有者退出 | 零次 | 目标继续运行，系统移除 hook |
| 控制者启动身份失效 | 零次 | 拒绝，持有者退出清理 |
| 传输超时后取消待执行动作 | 零次 | 超时被实际观测，未转为重试 |
| 动作执行中持有者退出／暂停 | 各一次 | 已开始的动作不能撤回；后续不再执行 |

全部场景目标继续心跳、卸载后独立消息投递不再触发回调、目标中 helper 模块消失、子进程按预期退出、映射与私有桌面关闭。仅两例按设计终止自建持有者，没有清理阶段强杀目标；输入桌面／前台／剪贴板抽样未变。调用中退出的动作计数是合成窗口可观测事实，不能推广为真实发送确认；真实请求仍须在调用前持久化 unknown 并通过微信回读核验。

完整离线 **784 项通过，13.275 秒，无跳过**，日志 `offline-thread-hook-20261026.txt`；默认套件不创建上述原生窗口、hook 或调用微信／API。新增六项契约测试覆盖默认关闭、拒绝外部目标参数、ABI 宽度、构建清单完整性、输入环境改变及清理判定。

## 复现与下一步

```powershell
.\.venv\Scripts\python.exe -B -X utf8 scripts/build_thread_hook.py --build-synthetic
.\.venv\Scripts\python.exe -B -X utf8 scripts/test_thread_hook.py --run-synthetic
```

该构建尚无真实发送入口。下一步需要绑定微信实际按钮／输入框／选中会话及原生 GUI 线程，先实现只读加载与撤销，再补齐真正发送前的目标、草稿、状态和原代码校验。自建导出函数不等于 Qt clicked；当前还未测试微信对象销毁或真实内部调用。需继续补控制进程实际异常退出、安装中断和目标退出的恢复证据，不能把控制者身份失效当成所有崩溃情况的替代。

累计真实 API 仍为 6/10，发送尝试 1/3、确认成功 0。现有防撤回文件、磁盘客户端、未知结果及消费账本保持原记录。
