# 微信 4.1.15.56 安装目录静态检查

2026-10-23：只读检查当前版本目录，未加载微信 DLL、调用导出函数、修改磁盘文件或操作真实微信界面。**找到了 UIA 实现及内部通信线索，但没有验证出可用的独立聊天读写接口，M1 未完成。**

## 实际发现

检查 `D:\apps\wechat\Weixin\4.1.15.56` 顶层的 DLL、EXE、BIN：22 个 PE 文件完成命名导出、普通导入、延迟导入枚举；6 个 BIN 不是 PE，未解包或执行。未枚举匿名导出，也未声称穷尽所有内部接口。

主 DLL SHA-256 仍为 `932dd6700f22e32faeb9b0cc9581a1f601e60ace151067680d3f6596496fab99`，本轮分析只接受这个指纹。

| 位置 | 发现 | 对 M1 的意义 |
| --- | --- | --- |
| Weixin.dll 命名导出 | 共 117 项；112 项属于 mmcronet 名称空间，另有 GetHandleVerifier、HostStartupExceptionReport、SetUseUserCA、SetWeixinCallbackFunc、WeChatMain | 没有从这些命名导出确认聊天读取或发送 API |
| SetWeixinCallbackFunc | RVA `0x34B460`；按第一个参数 0/1 选择存储位置，将第二个参数写入该位置并返回 | 静态表现是注册指针，不是可直接调用的发送函数；没有调用或注册回调 |
| Qt UIA SetValue | 函数 RVA `0x6629EA0` 至 `0x662A104`（结束不含）；字符串交叉引用与 PE 异常函数表相互定位 | 在 `0x6629FCB` 经虚表偏移 `0x60` 调用具体实现，未解析当前聊天输入控件的运行时虚表 |
| Qt UIA Invoke | RVA `0x66291E0` 至 `0x662931F`（结束不含） | 经虚表获取 action 接口并调用，不能据此认定发送按钮的实际行为 |
| 延迟导入 | 主 DLL 333 个普通导入、884 个延迟导入；后者含 SetFocus、SetForegroundWindow 等 | 仅查普通导入会漏掉焦点 API；导入存在不证明哪个聊天动作调用它 |
| mmmojo_64.dll | 含 SendMMMojoWriteInfo 等 26 个命名导出 | 进程间通信线索；未获得聊天协议、受支持调用契约或账号绑定证据，不连接或发请求 |
| 其他模块 | 网络 WebSocket SendMessage、owl 协程 channel send_msg 等 | 名称中的 send/message 不等于微信聊天发送 |

主 DLL 静态反汇编与公开 [Qt 5.15 ValueProvider 源码](https://raw.githubusercontent.com/qt/qtbase/5.15/src/plugins/platforms/windows/uiautomation/qwindowsuiavalueprovider.cpp) 中把文本交给 accessible->setText 的结构相符，这是辅助解释，不代表微信与 Qt 上游完全相同。尚未解析间接调用及完整后续调用图，**不能断言抢焦点的具体根因，也不能从这段函数未直接调用焦点 API 推断写入无干扰**。

之前真实 SetValue 抢焦点的证据保持有效。此次没有发现可满足独立接口验证门槛的新候选，因此没有新草稿实验、发送或 AI 请求。

## 可复现工具

纯标准库的 PE 清单工具不需要可选依赖：

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/inspect_installation.py --directory 'D:\apps\wechat\Weixin\4.1.15.56' --output .local/live-acceptance/installation-static.json
```

有界静态反汇编使用隔离研究依赖，不加入控制台运行环境：

```powershell
$env:TEMP = (Resolve-Path .local/test-temp).Path
$env:TMP = $env:TEMP
.\.venv\Scripts\python.exe -m pip install --only-binary=:all: --no-deps --target .local/static-tools -r requirements-research.txt
.\.venv\Scripts\python.exe -X utf8 scripts/trace_uia_static.py --dll 'D:\apps\wechat\Weixin\4.1.15.56\Weixin.dll' --output .local/live-acceptance/uia-static.json
```

输出禁止放回安装目录。工具只读文件字节，不 LoadLibrary 微信模块，不反射调用地址。反汇编通过标签引用和 PE 函数边界定位，每段最多 16 KB；结果不是自动生成的适配配置。研究依赖许可见第三方记录。

本机证据：`installation-static.json`、`uia-static.json`、`callback-static.json`，均位于忽略目录 `.local/live-acceptance/`。新增 PE 测试使用合成字节，覆盖有效空映像、截断文件、无文件支持的 RVA、命名导出、超限导出、延迟导入；默认测试不读取安装目录。

## 后续条件

后续虚表研究已补齐 ChatInputField 继承链、TextEditAccessible 候选 setter 和文本内容更新函数之间的静态关联，见 [虚函数续查](M1_VTABLE_ANALYSIS.md)。运行时实例绑定和后续接收者仍未确认，原写入禁用不变。

验证结果：完整离线套件 **158 项通过**；两个研究脚本语法检查与 `git diff --check` 通过。主 DLL 及 `.h.bak` 的 SHA-256 均与此前记录一致。研究没有操作真实微信界面，未新增发送或付费模型请求。

需要进一步把上述虚函数分派解析到本构建的聊天控件实现，或取得有明确调用契约的外部语义接口，才有理由安排新的草稿验证。仅找到内部地址、回调槽位或通信导出不足以解除限制；本轮没有增加内部调用、注入或绕过焦点行为的补丁。后台读取、身份稳定性、附件及真实 AI 记忆验收也仍未完成，不能用静态研究代替 M1 验收。
