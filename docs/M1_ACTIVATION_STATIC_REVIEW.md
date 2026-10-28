# M1 激活候选复核：2026-10-23

**没有定位到已执行的抢焦点调用链，也没有解除真实写入禁用。** 本轮仅读取固定 DLL 文件；未访问微信进程、设置草稿、发送消息或调用模型。

文件为微信 4.1.15.56 的 `Weixin.dll`，SHA-256：
`932dd6700f22e32faeb9b0cc9581a1f601e60ace151067680d3f6596496fab99`。

## 新增静态证据

1. 旧直接边脚本仅跟踪异常函数表中的 `call/jmp` 目标，遗漏没有 unwind 记录的叶函数及条件尾跳。已绑定回调 `0x3E39190` 的尾跳目标 `0x9182E0`、`0x1C43BD0` 的条件尾跳目标 `0x1C48680` 都属于这些缺口。
2. `0x2F6760` 的日志引用包含 `requestActivate() called for `，在 `0x2F67C9` 尾跳至平台对象虚表 `+0xF8`。确切磁盘虚表 `0x94A98E8` 的 `+0xF8` 槽位为原激活候选 `0x263BF70`；COL 和槽位已校验。
3. `0x5C86D0` 的状态判断及尾调用结构与 Qt 的 `QWidgetPrivate::setFocus_sys` 相符：检查窗口、应用状态及嵌入条件后才调用激活入口。这是源码结构比对，不能替代微信当前平台窗口对象的实际绑定。参考 [Qt QWidget 源码](https://raw.githubusercontent.com/qt/qtbase/5.15/src/widgets/kernel/qwidget.cpp)。

文本 setter 仍经已观察到的无障碍实现更新内容；没有发现已验证、可以独立绕开后续接收者的外部语义写入入口。Qt 的 [UIA ValueProvider](https://raw.githubusercontent.com/qt/qtbase/5.15/src/plugins/platforms/windows/uiautomation/qwindowsuiavalueprovider.cpp) 同样将文本交给可访问对象的 setter。源码只是机制参考，没有复制上游实现或加载它的库。

## 被排除的函数级伪连接

从已绑定的 `ChatMessagePage` 回调出发，静态函数图可以走到：

```text
0x1C43BC0 → 0x1C48680 → 0x3EEC100 → 0x5C2310 → 0x5C1FE0
→ 0x5C99F0 → 0x5C92B0 → 0x5C9EB0 → 0x6B2630
→ 0x6B1780 → 0x5C86D0 → 0x2F6760 → [平台虚表 +0xF8]
```

**这不是已经闭合的可行路径。** 在 `0x6B2E44` 调用 `0x6B1780` 之前，`xor ecx, ecx` 将第一个参数置空；而 `0x6B1780` 中调用 `0x5C86D0` 的 `0x6B182A` 位于非空参数分支。只连接函数名会错误拼接互斥分支。

另一个经过 `0x6B2E86 → 0x5C7FB0 → 0x6B1780` 的函数级候选依赖全局窗口状态及中间事件处理，尚未证明其条件可同时成立。没有把它列为真实根因。

下一步需要实际调用栈与对象关联，或更完整、带状态约束的证据；不能将时间相近、函数图可达、静态虚表存在当作运行时执行。不得用修改窗口焦点属性作为修复；用户的日常使用要求保持不变。

## 工具和验证

新增 `scripts/review_activation_candidates.py`，只读取已固定指纹的磁盘文件，复核 18 个明确边界的函数和对应指令；输出包含 `execution_proven=false`、`runtime_platform_instance_bound=false`、`write_permission=false`。研究地址不进入正式适配表。

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -p test_static_activation_candidates.py -v
.\.venv\Scripts\python.exe -X utf8 scripts/review_activation_candidates.py --dll 'D:\apps\wechat\Weixin\4.1.15.56\Weixin.dll' --output .local/live-acceptance/activation-candidate-review.json
```

本轮新增的 6 项纯合成测试通过，覆盖条件尾跳、无 unwind 的叶尾跳、间接调用不冒充直接边、零参数、参数重写和跨分支失效。真实 DLL 的 18 函数静态复核通过。测试导入不会读取 DLL 或连接微信。完整套件及调试恢复结果由主线统一记录；本报告不宣称这些检查完成了 M1 真实验收。
