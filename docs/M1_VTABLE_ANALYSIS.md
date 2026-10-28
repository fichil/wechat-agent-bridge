# M1 输入控件虚函数续查：2026-10-23

后续更新：运行时实例与接收者元数据已有实际核验，详见 [运行时诊断](M1_RUNTIME_DIAGNOSTICS.md)。下文“运行时绑定未确认”是本次静态阶段的历史结论；实际执行顺序和抢焦点根因仍未闭合。

本轮把此前未解析的虚表边界推进到具体候选实现和文本内容更新路径。**全部来自确切文件的静态分析；没有调用这些地址，没有重新设置草稿，没有解除真实读发禁用。M1 仍未完成。**

## 已交叉核验的静态链

目标仍是微信 4.1.15.56、SHA-256 `932dd6700f22e32faeb9b0cc9581a1f601e60ace151067680d3f6596496fab99`。所有 RVA 都是该文件内的证据位置，不是可执行适配配置。

1. 解码 Qt 元对象，确认 `mmui::ChatInputField → mmui::VoiceInputField → mmui::XTextEdit → QTextEdit → QAbstractScrollArea → QFrame → QWidget → QObject`。ChatInputField 元对象 RVA 为 `0x9050AD8`。
2. `mmui::accessible::TextEditAccessible::scrollToSubstring` 的诊断字符串引用定位到 `0x9D5A80`。该地址出现在文本接口虚表中；MSVC Complete Object Locator 将它与主虚表关联到同一个类型描述符 `0xB3D7A70`。
3. 主虚表 `0x908F258` 的 `+0x60` 槽位为 `0x9D5990`，与 UIA ValueProvider 的虚表调用偏移相符。构造函数 `0x9D54A0` 安装这些主/副虚表；工厂函数在 `QTextEdit` 类名分支调用该构造函数（`0x183EF7` 至 `0x183F25`）。
4. 候选 setter 在文本类型为 Value 时检查只读状态，随后尾调用 `0x1E96DE0`。该函数结构与 QTextEdit::setText 相符；根据内容格式进入 `0x26A7C80` / `0x26A7CC0` 两个叶函数，二者尾调用 `0x26A1460` 文本内容更新函数。

这使“QTextEdit 的无障碍 setter”成为有多项静态证据支撑的候选。**仍未证明当前运行的聊天输入框一定绑定此工厂产物**：运行时可能有其他无障碍工厂、缓存或自定义实例。报告明确保留 `runtime_binding=unverified_static_only` 和 `write_permission=false`。

## 没有找到独立的外部发送入口

ChatInputField 自身元数据有 `send`、`SendEmoticon`、`focusStateChanged`、`fontChanged`、`contentInserted`，全部标记为 signal；VoiceInputField 的两个方法及 XTextEdit 的 `textDidChanged`、`emoticonInsertPlainText` 也均为 signal。`0x06` 是 public + signal，不能把名称中的 send 当作外部聊天 API；判定参照 [Qt MethodFlags](https://raw.githubusercontent.com/qt/qtbase/5.15/src/corelib/kernel/qmetaobject_p.h)。没有发射这些信号、调用元对象方法或注册回调。

候选 setter 的已解析片段未发现直接激活窗口的调用，但其后续还有信号分派和间接调用。公开 [Qt 文本内容更新实现](https://raw.githubusercontent.com/qt/qtbase/5.15/src/widgets/widgets/qwidgettextcontrol.cpp) 可用于理解机制，不能替代微信自定义接收者的证据。因此不能宣称已经定位抢焦点根因，也不能以“局部代码没有 SetFocus”为理由重新实验原写入路径。

继续追踪 XTextEdit 的 `textDidChanged` 发射函数：`0x9182E0` 将元对象及信号索引交给公共分派函数；静态直接调用者 `0x3E39190` 是一个分支回调，处理后尾调用该发射函数。这进一步表明文本更新后仍有运行时连接参与。未解析所有接收者，也未将分派函数当作外部调用入口。

## 工具、测试与复现

新增纯标准库 `static_types.py`：有界解析 Qt 5 元对象、方法类别、继承链、MSVC COL 和虚表槽位；没有运行时进程读取或写入接口。研究脚本继续使用隔离的 Capstone 5.0.6；每次验证精确 DLL 哈希、继承链、类型描述符及函数边界，不修改安装目录。

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/trace_uia_static.py --dll 'D:\apps\wechat\Weixin\4.1.15.56\Weixin.dll' --output .local/live-acceptance/accessible-static.json
.\.venv\Scripts\python.exe -X utf8 scripts/trace_chat_input_static.py --dll 'D:\apps\wechat\Weixin\4.1.15.56\Weixin.dll' --output .local/live-acceptance/chat-input-vtable.json
```

研究依赖安装方法沿用 [安装目录检查](M1_STATIC_INSPECTION.md)。新增 8 项合成测试覆盖方法类别、继承循环、版本/数量边界、字符串长度、COL 身份及虚表目标边界；默认测试不读取真实 DLL，不接触微信或付费模型。

验证：完整离线套件 **166 项通过**，脚本语法和差异检查通过；确切 DLL 上的静态脚本成功回读继承链、虚表和函数边界。没有进行新的真实草稿、发送或模型请求；这不增加实际读发支持声明。

## 当前边界

还缺少实际无障碍实例绑定及文本更新后接收者的完整证据，故没有满足新写入实验门槛。独立读取、分页、己方/@ 语义、附件、AI 记忆和发送验收也不能由这条静态链替代。后续研究应针对实际实例绑定与信号接收者补证，或找到公开且独立的语义入口；不把内部地址转换成进程注入或额外客户端补丁。用户可继续正常使用电脑，本轮未操作鼠标、键盘、焦点或剪贴板。
