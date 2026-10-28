# 第三方依赖与许可证记录

2026-10-27 合成视觉格式对照使用本机已有 Windows PowerShell 与 .NET Framework 的 System.Drawing（实测程序集版本 4.0.0.0），只对脚本生成的固定 PNG 做离屏解码／JPEG 编码和像素核验，不处理微信截图或真实附件。代码独立编写，不新增 pip 包、下载或复制第三方实现，不分发系统／框架二进制；不是控制台通用图片转换依赖。确切 OS、结果、源码指纹和限制见[视觉格式对照](M1_VISION_FORMAT_CONTROL.md)。

2026-10-26 19:05 临时进程适配续作仍为独立编写的自有代码，增加 `bcrypt.lib` 系统 SHA-256 校验和有界对象检查，不复制 Qt 实现或客户端代码块；只记录当前文件的 RVA、哈希及已核验字段。helper 通过 Windows hook 机制临时映射到明确授权进程，验证后已卸载，未修改或分发磁盘微信／防撤回 DLL。只读构建不调用 Qt；显式发送候选构建仅触发固定按钮的已核验 clicked metacall。原生源码、产物指纹和验证见 [真实后台发送](M1_BOUND_SIGNAL_SEND.md)。无新增下载依赖；本地 MSVC 静态运行库产物仍不对外分发。

2026-10-26 18:00 后新增自有 C++ hook 合成程序，使用本机已安装 MSVC 14.29.30133 和 Windows SDK 10.0.19041.0；不下载编译工具、不复制 Qt 或上游 hook 实现。链接 Windows 系统库及本机 MSVC 静态运行库，产物仅在忽略目录本地实验，未分发第三方运行库或二进制。以后打包须复核本机 Visual Studio/SDK 许可及对应可再发行文件条款；本次未增加 Python 依赖。实现与构建指纹见 [线程合成验证](M1_THREAD_HOOK.md)。

2026-10-26 发送接收者续查仅新增标准库的精确 PE／Qt 元数据解析、已有 COM 接口查询和受限对象指针比较；没有复制 Qt 实现，没有新增或分发客户端二进制、helper 或功能补丁。Qt 源码仍仅作为已记录的对照资料，现有 Capstone 研究依赖版本不变。视觉对照图由标准库生成，不使用第三方图片或新增图像依赖。

2026-10-26 调试对象研究参考 [phnt 固定提交](https://github.com/winsiderss/phnt/tree/53fbbdc5b5d2b08761db1c7b26bfa8c820924356) 的 `ntpsapi.h`／`ntdbg.h` ABI，在使用前核对该提交 MIT 许可，保留 [完整 LICENSE](../third_party/phnt/LICENSE) 和 [范围说明](../third_party/phnt/NOTICE.md)。独立声明 ProcessDebugObjectHandle、NtDebugContinue、NtRemoveProcessDebug 的小型 ctypes 接口，没有复制实现或头文件、安装或运行上游程序，没有新增依赖。仅限确切系统指纹和自建进程，未作为稳定公开接口或真实适配能力。DR7 bit 10 的表示核对 Intel SDM 图 17-2，未复制其图表或代码。详见 [接管研究](M1_DEBUG_OBJECT_RETENTION.md)。

核查日期：2026-10-23。运行环境：Windows x64 / CPython 3.12.10。安装输入为 [requirements-windows.txt](../requirements-windows.txt)，直接和传递依赖均固定到精确版本；在项目 `.venv` 安装并执行 `pip check`，结果为 `No broken requirements found`。M0 和 mock 控制台核心不要求这些包。

没有提交或分发第三方 wheel/二进制。后台续作采用了下面单独记录的 Apache-2.0 静态指令特征；其余包记录来自本机实际安装包的 METADATA 和随包许可证文件。安装成功只证明解析/安装，不证明客户端兼容。

## 无障碍静态特征来源

`adapters/accessibility_gate.py` 参考 [wechatauto-replica/accessibility.py](https://github.com/fanyuantaier/wechatauto-replica/blob/d322c4a9b1124228820bab363e5676016f188fc6/wechatauto/accessibility.py)，固定提交 `d322c4a9b1124228820bab363e5676016f188fc6`。已在借用前核查 Apache License 2.0，保留 [完整 LICENSE](../third_party/wechatauto-replica/LICENSE) 与 [来源及修改说明](../third_party/wechatauto-replica/NOTICE.md)；该提交无上游 NOTICE 文件。

只参考静态字节特征与进程内 Qt 开关思路，独立实现单一精确指纹校验和恢复。没有安装或运行上游库、示例、登录/数据库工具、前台发送实现，没有复制 GPL 防撤回工具代码或修改用户的防撤回 DLL。新增实现使用标准库 ctypes/struct/re 和既有固定依赖，没有新增包。

## 固定 Python 依赖

| 包 | 固定版本 | 用途 | 核查许可证 |
| --- | --- | --- | --- |
| [pywinauto](https://pypi.org/project/pywinauto/0.6.9/) | 0.6.9 | Windows UI Automation，独立串行子进程 | BSD 3-Clause；实际 dist-info/LICENSE，与指定版本上游 LICENSE 核对 |
| [comtypes](https://pypi.org/project/comtypes/1.4.17/) | 1.4.17 | pywinauto COM 支持及独立 IUIAutomation2 客户端 | MIT，dist-info/licenses/LICENSE.txt |
| [pywin32](https://pypi.org/project/pywin32/312/) | 312 | Windows 进程/窗口/文件版本 API | 包元数据标注 PSF；实际 win32/com/pythonwin 有各自 BSD 风格声明，不能用元数据替代全部文件许可 |
| [six](https://pypi.org/project/six/1.17.0/) | 1.17.0 | pywinauto 兼容依赖 | MIT，dist-info/LICENSE |
| [pypdf](https://pypi.org/project/pypdf/6.21.1/) | 6.21.1 | PDF 文本提取 | BSD 3-Clause，dist-info/licenses/LICENSE |
| [python-docx](https://pypi.org/project/python-docx/1.2.0/) | 1.2.0 | DOCX 段落/表格提取 | MIT，dist-info/licenses/LICENSE |
| [lxml](https://pypi.org/project/lxml/6.1.3/) | 6.1.3 | python-docx XML 依赖 | 主体 BSD 3-Clause；必须另读 LICENSES.txt 的例外及二进制捆绑库声明 |
| [typing_extensions](https://pypi.org/project/typing-extensions/4.16.0/) | 4.16.0 | python-docx 类型依赖 | PSF 2.0，dist-info/licenses/LICENSE |
| [openpyxl](https://pypi.org/project/openpyxl/3.1.5/) | 3.1.5 | XLSX 缓存值读取 | MIT，dist-info/LICENCE.rst |
| [et_xmlfile](https://pypi.org/project/et-xmlfile/2.0.0/) | 2.0.0 | openpyxl 传递依赖 | MIT 及所含 Python 标准库代码的许可；dist-info/LICENCE.rst、LICENCE.python |
| [defusedxml](https://pypi.org/project/defusedxml/0.7.1/) | 0.7.1 | XML 实体/文档安全解析 | PSF，dist-info/LICENSE |

## 分发时不能遗漏的声明

- pywinauto 的 [0.6.9 LICENSE](https://raw.githubusercontent.com/pywinauto/pywinauto/0.6.9/LICENSE) 要求保留版权、许可条件和免责声明；二进制分发在随附材料中保留，不可用作者名背书。
- pywin32 含多个组成部分：除 win32/com/pythonwin 许可外，还包括 Scintilla、IDLE、MAPIStubLibrary 等各自声明；随包 adodbapi 的许可包含 LGPL 2.1。当前适配器只调用 win32 API，但以后打包整个环境时必须处理整个分发物，不能只写“pywin32 = PSF”。
- lxml 实际 `LICENSES.txt` 列有 ElementTree/CPython、测试脚本、isoschematron 资源等不同来源；二进制 wheel 另含 zlib、iconv（LGPL 2.1）、libxml2、libxslt/libexslt 的声明。不能把整个 wheel 简化为 BSD。本项目不使用 schematron/XSLT，也没有重新分发这些二进制。
- 如果以后发布打包应用，应收集实际选用 wheel 的完整许可、版权、对应源代码及适用的 LGPL 材料要求，再作分发核查；本轮记录不是一份已完成的二进制分发清单。不要删去安装包自带许可证目录。

## 引入理由与运行限制

ClawBot 研究固定 Tencent/openclaw-weixin 提交 `874637a0dbc24050f72d1385add4fc6170a2d726`，已核验其 [MIT 许可证](https://github.com/Tencent/openclaw-weixin/blob/874637a0dbc24050f72d1385add4fc6170a2d726/LICENSE)。本轮只参考公开字段独立编写纯数据实验，未复制上游源代码、安装插件或新增运行依赖；以后复制实质代码须保留腾讯版权和许可全文。Win32 合成调试对照仅声明系统公开 ABI，不重新分发微软 SDK 或二进制。

运行时研究通过标准库 ctypes 调用本机 Windows 自带的 DbgEng COM API；本次系统 `dbgeng.dll` 为 10.0.26100.1，微软签名有效，实际文件 SHA-256 记入忽略目录的自测证据。项目不打包或重新分发微软 DLL/SDK 头文件，只声明所需公开 ABI。[微软 DbgEng 文档](https://learn.microsoft.com/en-us/windows-hardware/drivers/debugger/debugger-engine-overview)及 SDK 声明用于核验接口；[win32metadata 许可证](https://github.com/microsoft/win32metadata/blob/main/LICENSE)明确不改变原 SDK 头文件许可，不能把头文件视作本项目 MIT 代码。Qt 连接结构按当前 DLL 指令及运行时元数据独立解析，未复制 Qt 实现。

独立 `probe_debugger_resume_flag.py` 仅按公开 ABI 使用附加标志 `0x10` 及进程/线程身份检查，未复制 SDK 头文件、增加包或分发系统 DLL。此合成实验没有开启真实微信诊断；失败证据保留在本地。

焦点事件研究同样只用标准库 ctypes 独立声明 TDH、ETW 和私有测试桌面的公开 Windows ABI，使用系统自带 `tdh.dll`、`advapi32.dll`、`user32.dll`，没有新增 Python 依赖、复制 SDK 头文件或分发微软二进制。事件元数据与资源文件哈希保存在忽略目录；合成跟踪不是正式微信适配器，也不能凭 [TDH 条件构造成功](https://learn.microsoft.com/en-us/windows/win32/api/tdh/nf-tdh-tdhcreatepayloadfilter) 宣称真实过滤或调用栈已验证。

事件源续查对本机两个确切 Win32k 系统文件作有界磁盘分析，不读取内核内存、加载驱动、下载符号或分发系统文件。独立传输检查的随机 provider 清单由项目自行生成，只经 TdhLoadManifest 进入进程缓存，卸载后删除；没有复制微软或第三方清单、安装系统 provider 或新增依赖。自建 user-mode provider 的结果不能替代内核 Win32k 验证。

用户态续查读取固定指纹的 user32、win32u、UIAutomationCore、sechost 和 ntdll 磁盘函数。UIA 源及跨进程范围／栈组合验证仅在自建子进程加载微软签名有效的系统 UIAutomationCore.dll，按公开 ABI 调用 NULL provider 错误路径；私有 ETL 有大小限制，停止消费后删除，不含真实 UI 或聊天数据。没有复制 Qt 或微软实现、修改系统文件、安装服务或增加依赖。单元素 TextChanged 订阅目前仅为公开接口和 Qt 机制对照，尚未实现或注册真实监听。

公开客户端配置续作通过既有 `comtypes==1.4.17` 使用 `CUIAutomation8/IUIAutomation2.AutoSetFocus`，依据[微软接口文档](https://learn.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomation2-put_autosetfocus)独立实现设置、回读及隔离检查；没有新增包、复制 SDK/Qt/上游项目源代码或重新分发系统 DLL。合成验证仅创建自有 private non-input desktop 和标准 Edit 控件，使用系统 UIAutomationCore.dll；前后文件指纹相等。comtypes 的现有生成接口与退出 callback 由已安装包提供，未复制进仓库；仅在固定版本的自有一次性子进程先注销原 `atexit` callback，再提前调用同一 `_shutdown`，不将该私有接口作为生产通用生命周期。再次核对随包 MIT 许可的 Thomas Heller / Comtypes Developers 版权声明；今后分发该包或实质代码时仍须保留版权及许可全文。结果与限制见 [AutoSetFocus 合成验证](M1_AUTOFOCUS_CLIENT.md)，不能据此宣称微信适配已通过。

静态研究另用 `capstone==5.0.6`，固定于 `requirements-research.txt`，仅安装到忽略目录 `.local/static-tools`，不进入控制台依赖或默认测试。用途是读取确切 DLL 的有界机器指令，不执行被分析代码。PyPI Windows wheel 的 `capstone-5.0.6.dist-info/LICENSE.TXT` 已检查，为 BSD 3-Clause；当前不重新分发 wheel。若以后分发，须保留版权、条件、免责声明且不得用作者名称背书。[固定版本许可](https://raw.githubusercontent.com/capstone-engine/capstone/5.0.6/LICENSE.TXT)。Qt 公开源码只用于对照研究，没有复制进项目。

2026-10-26 跨进程 tree canary 仅复用本项目冻结合成代码、Python 标准库与既有 comtypes 1.4.17，不新增包或复制上游实现。DbgEng/UIA 的增量报告只保存有界本机静态证据到忽略目录，不加载研究函数或重新分发系统二进制；新增的静态关联结论不是公开 API 或生产适配能力。

同日新增 SetFocus 335/336 解析与独立采集器，仍复用既有标准库/系统 ABI，不引入第三方代码或依赖。仅自建进程的 ETL 和停止意图保留在忽略目录，尤其不确定或未完成结果不得删除；离线诊断只读受限 header。此保留策略不同于此前成功后删除临时 ETL 的实验，不含真实微信内容，不开放生产能力。

2026-10-26 SetFocus 正控续作仅增加标准库时间记录、固定三秒自建窗口和正常管道关闭处理；使用现有 ETW ABI、comtypes 和受限栈解析。没有新增依赖、复制上游代码或修改系统 DLL。自建正控的完整校准通过不授予真实客户端或 DbgEng 使用能力；详见 [合成校准记录](M1_SETFOCUS_CALIBRATION.md)。

同日新增当前诊断进程内的 UIA 关联只读检查和客户端顺序修复，仅使用现有 comtypes、标准库 ctypes／struct 及 Windows 公开的 VirtualQuery、ReadProcessMemory ABI。内存读取句柄固定由 GetCurrentProcess 取得，不接受外部 PID；未调用内部 getter、修改指针、注入或分发系统二进制。虚表及字段偏移来自确切本机 DLL 的有界静态核验，Capstone 仍为原有研究工具固定版本，无新增包或复制上游代码。它是版本受限的研究守卫，不声明为公开稳定 ABI。详见 [UIA 关联证据](M1_UIA_CLIENT_CONTEXT.md)。

UIA 只在明确选择 Windows 模式时导入、运行；采用工作进程以便在 UI 调用卡住时终止。没有执行上游示例，没有关闭自动化保护，也没有用上游实现覆盖剪贴板/草稿。

2026-10-26 任务视图对照新增 `virtual_desktop.py`，仅使用标准库 ctypes 调用 Windows 的公开 [IVirtualDesktopManager](https://learn.microsoft.com/en-us/windows/win32/api/shobjidl_core/nn-shobjidl_core-ivirtualdesktopmanager) 查询：读取窗口桌面 ID 和是否位于当前桌面，未绑定 MoveWindowToDesktop。COM 声明由公开 ABI 独立编写，没有复制 SDK 实现或新增第三方包；不分发系统 DLL。资料卡定位沿用现有 pywinauto，原生 EnumWindows 只在绑定 PID 范围内提供 HWND 元数据。同期 SetFocus 缩窗修改也未引入依赖，旧源文件归档仅用于保全既有报告的指纹。

PDF/DOCX/XLSX 不适合靠临时正则解析，使用固定成熟解析库并放入有大小/时间边界的独立进程。DOCX 的 lxml parser 使用 `resolve_entities=False`；XLSX 使用 defusedxml、`read_only=True`、`data_only=True`、`keep_links=False`。不执行公式或宏，不跟随附件外链；ZIP 展开大小、数量与单条目大小先检查。

所有相关测试均为程序生成的合成文件。当前原生 HTML/CSS/JavaScript 无 CDN、远程字体、前端依赖或构建工具。

2026-10-26 原生调试事件候选复用 Python 标准库 ctypes、multiprocessing 共享缓冲区与现有固定系统 DLL。DBGUI_WAIT_STATE_CHANGE 及 NtCreateDebugObject／NtDebugActiveProcess／NtWaitForDebugEvent 签名按 phnt 固定提交 `53fbbdc5b5d2b08761db1c7b26bfa8c820924356` 独立声明，已核对 MIT 许可，保留于 `third_party/phnt/LICENSE`，增补声明见 `third_party/phnt/NOTICE.md`。不复制、运行或分发上游实现／头文件；无新依赖，不宣称私有 ABI 稳定兼容。

2026-10-26 消息类磁盘元数据研究仅独立解析 revision-8 属性记录，核对 Qt 5.15 的 qmetaobject_p.h／qmetaobject.cpp 格式声明，没有复制或分发 Qt 源码或二进制，无新增依赖。所查阅上游文件列有商业及 LGPLv3/GPL 许可选项；本次仅作格式参考，不将这些私有元数据当作稳定公开接口。实际格式引用与确切本机 DLL 指纹见 [消息类属性分析](M1_UIA_CLIENT_CONTEXT.md)。

2026-10-26 发送按钮研究复用既有 Capstone 5.0.6、comtypes、pywinauto 和标准库。仅对照 Qt 5.15 的 qwindowsuiainvokeprovider.cpp、qaccessiblewidget.cpp、simplewidgets.cpp 与 qaccessible.cpp；这些文件列有商业／LGPLv3／GPL 许可选项，本项目未复制它们的实现或分发 Qt 二进制。运行时仅从指定按钮的缓存 ID 沿已核验字段读取元数据／代码，不调用内部方法。消息保护和固定发送事务独立实现，无新增依赖；源链接及确切二进制证据见 [发送动作报告](M1_SEND_ACTION.md)。
