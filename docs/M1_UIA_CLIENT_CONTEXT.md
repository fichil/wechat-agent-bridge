# UIA 客户端关联与初始化顺序修复

后续发送实测：FALSE 关联的固定 SetValue/Invoke 零焦点事件，但未产生消息回读。当前 XOutlineButton 的实际无障碍动作绑定仅实现 SetFocus，Press 没有对应行为；测试草稿已精确清理。详见[发送动作与保留的 unknown](M1_SEND_ACTION.md)。下文原草稿证据保持有效，不扩展为发送支持。

2026-10-26，真实只读对照发现：请求客户端的 `AutoSetFocus=FALSE` 并不等于目标节点实际关联客户端的配置为 FALSE。旧顺序下，微信输入框节点关联到不同的客户端指针，关联配置为 TRUE；先创建 FALSE 客户端并取得、持有目标元素与 pattern，再初始化旧读取器后，关联回到所请求客户端且为 FALSE。

项目已据此修正草稿候选的初始化和对象复用，并增加实际 pattern 的关联检查。**本机该测试群的单次后台草稿已完整回读，零焦点事件，用户确认没有切屏或焦点丢失；手动清稿及只读复核均完成。发送和完整 M1 仍未验收。** 原 DbgEng 暂停／DR 恢复仍未通过，真实生产读发适配表仍为空。

## 只读方法与边界

新增 `scripts/uia_context_evidence.py`、`uia_context_native.py`，只读取诊断进程自身持有的 UIA 对象。native port 内部取得当前进程伪句柄，不接受外部 PID／进程句柄，不调用 OpenProcess、WriteProcessMemory 或内部虚函数。

每一步要求确切系统 DLL 指纹、已知虚表及方法槽、已提交可读内存；虚表必须来自确切映像分配。字段每次 4 或 8 字节，总读取上限 4096 字节，完成时再次核验已读字段。未知布局、区域变化、短读或对象变化均停止。输出仅记录模块 RVA、布尔关系和数量，不记录指针地址、聊天正文或内存转储。

环境为 Windows `10.0.26300`、Python `3.12.10`、微信运行版本 `4.1.15.56`；UIAutomationCore SHA-256 为 `be5037bf46e61566d3e5a83a6c628cebe9035771e4ae741a7b9aa27b5696d989`。这些布局只适用于该确切映像。

实际微信目标使用了与自建 Edit 不同的节点：

| 已核验项 | 静态与运行时关系 |
| --- | --- |
| 值模式 | 表 `0x2e5ef0` 的 SetValue 槽为 `0x1c6670`，模式 `+0x50` 指向当前元素 |
| 元素 | 表 `0x2db360`，元素 `+0x38` 指向当前节点 |
| 自建 Edit 节点 | 表 `0x2df4f8`，getter `0x15b790` 从接口 `+0x20` 返回关联 |
| 真实目标节点 | 表 `0x2dffd8`，getter `0x15f740` 是四条指令的终端委托，从接口 `+0x28` 取得下层对象并派发其 `+0x28` 槽 |
| 下层对象 | 表 `0x2e38d0`，getter `0x120450` 从 `+0x170` 返回关联，非空时 AddRef |
| 关联后的客户端 | 沿已核验的关联／上下文字段到客户端，核对公开 AutoSetFocus getter 槽与对应字段；没有执行这些内部方法 |

两次遇到未知布局时，检查器先停止；完成对应的磁盘静态核验和自建回归后才继续。静态证据为 `.local/live-acceptance/uia-observed-node-bounded-thunk-20261026.json` 和 `uia-observed-delegate-getter-static-20261026.json`。较早未经正确函数边界校验的窗口输出已保留但不作为依据；终端 tail-jump 后的其他函数不属于该 getter。

## 真实只读对照

以下报告均在 `.local/live-acceptance/`，具体账号、群名和窗口标识不写入仓库。两种顺序使用同一已核验账号、同一当前测试群和空输入框，没有写入或发送：

| 顺序／报告 | 实际观察 |
| --- | --- |
| 先旧读取器，再新 FALSE 客户端；`uia-bound-context-f2d6a2fd7ce046da94a48625951c1c4f.json` | 关联存在，但指向另一客户端；请求配置 FALSE、关联配置 TRUE；稳定读取，312 字节，1.687 秒 |
| 先 FALSE 客户端及目标节点，再旧读取器；`uia-bound-context-bee6cd4761304d67ad931a16aa52731f.json` | 创建首个客户端时 pywinauto 未加载；关联指向所请求客户端，两个配置均 FALSE；稳定读取，292 字节，1.656 秒 |
| 修复后候选本身的预检；`taskview-desktop2-966282c3c1af45d880d659917dfb0d48.json` | 同一 FALSE 关联、客户端和上下文关系通过，1.500 秒；`attempted=false`，没有调用写入函数 |

三次均为零前台／焦点事件、桌面及剪贴板抽样未变、临时无障碍字节恢复、工作进程退出、清理错误为空。最初当前窗口不属于已绑定测试群时已拒绝，用户手动选择后才继续；没有放宽历史 RuntimeId、账号或会话检查。

上述三个报告 SHA-256 依次为：

- `403a528d2476321147f66808097a72ccce4f05dd578af5d4d2f19cdc6471a08e`
- `2374e6ecd7853365955f7b23a8e6fd84f74e44f129078ba27673b6588f4db761`
- `d5fd002f94633c76c1c7c927c478ad365619dcf69da3b4f4c53a2a409fc18311`

这些证据支持同进程默认客户端／节点缓存复用是原因候选。尚未在实际 SetValue 现场观测自动焦点分支、系统前置条件或 Qt 激活调用，因此不把只读关系或静态分支当成完整因果链。仅提前构造客户端还不足以表达本次修复：同时要先取得并持有对应元素、pattern，再启动旧读取器。

## 已实现的候选修复

`scripts/uia_client_bootstrap.py` 先创建并回读 FALSE 客户端，通过授权窗口及输入框 RuntimeId 取得、持有元素和 pattern；这时只读取结构身份。待既有控制器核验所选账号、会话及空草稿后，才读取关联。候选复用该客户端，不再在旧读取器之后构造另一个客户端。

`uia_draft_candidate.write_fixed_draft` 新增可选 `pattern_guard`，收到实际持有的 raw client、element 和 pattern，在预留之前及最终提交检查之前各核验一次。关联必须存在、稳定、属于所请求客户端和上下文，且两处 AutoSetFocus 均为 FALSE。TRUE、NULL、未知布局、读取失败及变化均拒绝；已预留后拒绝也不退还单次机会。最后 commit 检查仍不进行 UIA 查找，所有授权、焦点、草稿和持久化规则保留。

预检不会调用写入函数。原来两个已消费的单次账本不重置。用户随后已明确允许一次新的空闲固定草稿，新增固定账本 `uia-client-order-20261026.sqlite3`；初次预检因当前目标不匹配停止、开关恢复，当时尚未写入；用户重新选择后依下述流程刷新临时控件绑定并完成一次试验，机会现已使用。生产适配器不因此启用。

## 验证与复现

完整离线 **605 项通过，11.581 秒，无跳过**，日志 `offline-uia-client-order-fix-20261026.txt`。新增覆盖自进程内存边界、未知／委托布局、对象变化、真实目标不匹配、只读入口不能进入写入、初始化顺序、客户端复用，以及 pattern 守卫在预留前／后拒绝和异常脱敏。测试发现的脚本导入路径污染已隔离修复，默认测试不调用原生 UIA 或付费 API。

当前源码重新取得以下原生证据：

- 只读自建四组关联：`uia-node-context-9ae4619f270d421cbc3f0efde4c1feba.json`，直接／树查找、默认／FALSE 均关联到正确客户端；112 次输入环境抽样未变。
- 冻结四阶段跨进程回归：`uia-autofocus-cross-process-27fa825de6a442ac9482af4c441356a3.json`，完整回读和正常清理通过；301 次抽样未变。
- SetFocus 合成栈校准：`uia-setfocus-stack-e990ee8a977845bc8f94ecac72a94862/report.json`，完整配对及同步栈、Stop 和正常清理通过；674 次抽样未变。

```powershell
.\.venv\Scripts\python.exe -B -X utf8 scripts/probe_uia_node_context.py
.\.venv\Scripts\python.exe -B -X utf8 scripts/probe_bound_uia_context.py
.\.venv\Scripts\python.exe -B -X utf8 -m unittest discover -s tests -v
```

前两条默认只输出 disabled。自建观察显式使用 `--run-synthetic`；真实只读入口为 `--inspect-bound`，要求原授权账号、两个目标、原绑定报告、当前源码的自建报告和非当前桌面 GUID。`--client-first` 只改变只读初始化顺序；入口没有 `--run`、发送或外部 PID 参数。

用户另外批准的一次新固定草稿已执行且通过本次无干扰检查。有充分证据后再继续既有受限发送验收。旧 unknown 不能重试或换账本绕过。附加调试器仍被恢复门槛阻止；本轮没有调用 DbgEng、读取微信进程中的数据库／凭证、修改客户端磁盘文件或系统焦点设置。


## 自建原生配置变化负例

在等待新的真实草稿授权期间，另行验证了守卫面对实际配置变化的行为。研究脚本 `.local/probe_uia_guard_mutation.py` 在不可见私有桌面的四组自建控件上，仅在读取本进程客户端标志后，通过公开 AutoSetFocus 属性把该测试客户端的值翻转一次，并在 finally 中恢复原值。没有 SetValue、Invoke、内部方法调用或微信操作。

原生报告 `.local/live-acceptance/uia-context-mutation-9b08b165ae23495fa37e52284c9b5f0e.json`，SHA-256 `598952db7b7c60c2826e62549df49a0caa40fb3cadcd9ed3e18aa6d7789be413`：直接／树查找与默认／FALSE 四组均触发 `object_changed_during_read`，每组只翻转一次，原配置均恢复，空文本与控件身份复核通过。两个自建进程正常退出，原生完整清理及窗口、句柄、私有桌面清理通过；114 次当前输入环境抽样未变。

该负例说明在本机自建对象上，配置读取与关联读取之间的实际变化会被拒绝；它不代表任意竞争窗口或所有故障场景均被覆盖，也不代替实际写入试验。605 项离线回归的生产源码未因该一次性研究脚本而改变，当时三个原有额度账本哈希仍相同，尚未创建新的真实草稿额度；随后授权状态见下节。

## 新单次试验的授权与准备

用户已明确允许现在开始一次修复后的固定草稿，不发送、不调用 API，最多 60 秒。新增入口 `scripts/probe_client_order_draft.py` 默认 disabled，只选择固定 `client-order-20261026` case；不同桌面 GUID 或重启不能产生额外机会，禁止任意账本路径。旧诊断、桌面 2 及发送/API 账本哈希保持不变。

新增四项纯测试验证默认关闭、固定 case／GUID、CLI 参数及跨重启去重和旧账本保留；完整回归 **609 项通过，12.118 秒**，日志 `offline-client-order-trial-20261026.txt`。初次只读预检报告为 `client-order-draft-f6592d8f51a746168c52c095df83c5b4.json`：目标不匹配，未写入、未消耗新机会，临时字节已恢复。用户打开指定群后才继续，授权无需重复申请。

## 新单次真实草稿结果

用户手动重新选择指定群后，核验发现只有会话列表项 RuntimeId 重建；账号、进程指纹和输入框 RuntimeId 未变，指定群唯一选中且空稿。依据这次人工选择生成独立的 `client-order-target-rebound-20261026.json`，原绑定报告保留；之后每次检查仍严格匹配该新快照。RuntimeId 是临时控件身份，不能当作跨重建的持久会话 ID，也没有把刷新扩展为自动选择其他目标。

| 步骤／本机报告 | 结果 |
| --- | --- |
| 新绑定预检 `client-order-draft-4a07d014975d4fcfb0d06583d259a830.json` | 账号、当前群、空稿及同一 FALSE 关联通过，未写入 |
| 单次草稿 `client-order-draft-886ed85b9cd64d488c7d034c34fe902f.json` | **readback_verified**，2.422 秒；一次固定 SetValue、完整回读；三次关联检查均为同一 FALSE 客户端；0 前台/焦点事件，桌面、前台和剪贴板检查未变；开关恢复、进程退出、来源未变，清理错误为空 |
| 用户反馈 | 明确回复“没有，已清除”，确认没有切屏或焦点丢失并已手动清理；未由程序自动清稿 |
| 清稿只读复核 `client-order-draft-a493e186df8b4579a6fab293dc51959f.json` | preflight_verified，1.547 秒；同一目标空稿，0 事件，开关恢复和退出通过，没有新增写入 |

草稿报告 SHA-256：`82eb62c148c06896da13950b0bb1fbd60d397d4aaadbe3ebbec0c13f08f444d7`。清稿复核 SHA-256：`14436161ac9b8f1bf003de6b9205203b946c95f2fc736f052025b2ed1643431b`。授权、结果及人工反馈汇总保存在本机 `client-order-authorization-20261026.json` 与 `client-order-live-outcome-20261026.json`，原始报告不改写。

最新完整离线 **609 项通过，12.070 秒，无跳过**，日志 `offline-client-order-live-result-20261026.txt`。此次新账本为 used/finished，不允许重跑；原两个诊断账本与发送/API 账本哈希未变，发送 0/3、请求 0/10。

该结果支持客户端初始化和关联复用修复了记录环境中的本次草稿抢焦点路径。没有取得真实 SetValue 的完整系统/Qt 调用栈，不推广为所有窗口或版本兼容；下一步仍需独立验证后台发送调用及己方消息回读，再完成私聊、群 @、模型、多媒体、记忆和重启场景。真实生产适配表保持关闭，DbgEng 异常恢复仍未通过，M1 未完成。

## 发送 pattern 的独立静态门槛（2026-10-26）

后续原生 Button 校准已执行，发现 FALSE 关联并不能保证 Invoke 不聚焦，见下节。不得仅凭静态门槛开放真实发送。

继续分析同一 SHA-256 的本机 UIAutomationCore.dll，字符串 `CInvokePattern::Invoke` 的 RIP 引用绑定到函数 RVA `0x1c2180`；磁盘中该函数指针唯一对应的候选接口表为 `0x2eb250`（Invoke 槽 `+0x18`）。该函数从 `this+0x50` 取元素，在实际调用 provider 之前调用 `0x226940`。后者包含焦点处理路径，因此不能把输入框 SetValue 成功当作发送按钮不会聚焦的证明。磁盘证据保存在 `.local/live-acceptance/uia-invoke-static-20261026.json`；这些是静态关系，不是微信的实际调用栈。

纯读取器新增显式 `pattern_kind='invoke'`：仅允许上述独立表、方法及父元素关系，再沿现有已校验节点关联读取客户端。默认仍为 value，错误类别、拿 ValuePattern 冒充 Invoke、方法错位和父元素不匹配均停止；不调用 Invoke 或内部函数。尚未接入真实发送入口，生产适配表不变。

四项新增离线测试与完整回归 **613 项通过，12.243 秒**，日志 `.local/live-acceptance/offline-invoke-context-20261026.txt`。修改读取器后重新执行自建隐藏桌面只读校准，报告 `uia-node-context-faea0f12f99e4c6cbb69960274a76e4c.json` 为 `owned_context_verified`。该校准覆盖既有 ValuePattern 路径；Invoke 新布局目前只有磁盘与合成字节证明，尚无原生 Button/Invoke 校准，不能声称发送能力通过。原有真实草稿报告仍是当时源码的历史证据，不自动为此次新增 Invoke 功能背书。本轮未访问真实微信、未附加调试器、未写草稿、未发送或调用模型。

## 自建 Button 原生反例（2026-10-26）

新增 `scripts/probe_uia_invoke.py`，默认 disabled，仅接受完整 `--run-synthetic`，无外部 PID/HWND 参数。复用冻结 GUI 生命周期，在自建不可见私有桌面将一个自建 Edit 替换为 BS_NOTIFY Button；自建父窗口分别记录 BN_CLICKED 与 BN_SETFOCUS/BN_KILLFOCUS。点击后父窗口更改固定标题作为独立原生回执；同时检查 UIA 名称、控件身份和输入环境。没有修改冻结源文件，没有把自建结果当作微信能力。GUI 与 MTA 位于同一自建进程，此处未验证跨进程 Button。

保留全部失败报告：`896e76bb9b8a4709a858c85665424eb3` 在离线测试意外触发参数缩写时未能创建桌面，已关闭 argparse 缩写；`dbf973a50eef453dbbc4b01addc757da` 与 `c2f90eb34ead4386a91b0b36406c9b0f` 分别在无标题按钮 NULL BSTR 的身份检查和新增元数据长度处理处停止，均正常清理；这些不计为调用成功。

实际完成两组 Invoke 的报告为 `.local/live-acceptance/uia-invoke-bff1238a2221497e9a90c38c0c6c26b3.json`，SHA-256 `b3dbd8918eaf974cb8b575a4eaf0472132778b0049866949557d4b7442f0b8c6`：

| 项目 | 默认客户端 | FALSE 客户端 |
| --- | --- | --- |
| pattern 表、父元素与节点关联 | 稳定，同一请求客户端 | 稳定，同一请求客户端 |
| 原生关联 AutoSetFocus | TRUE | FALSE |
| Invoke 返回、BN_CLICKED、UIA／原生回读 | 均通过，点击恰好 1 次 | 均通过，点击恰好 1 次 |
| Button 焦点通知 | set / kill / set | set |
| 最终私有桌面焦点 | 从另一个 Edit 转到 Button | 从另一个 Edit 转到 Button |

无干扰资格判定为 **失败**，没有放宽 FALSE 组的零焦点条件。当前输入环境抽样未变，来源和系统 DLL 哈希未变，子进程正常退出，窗口、类、COM、桌面均正常清理且没有强制终止。记录中的焦点变化只发生在不可见自建桌面，不代表本次操作了用户微信或切换了工作桌面。

后续纯分类器单独呈现“调用已执行”与“FALSE 焦点未变”，避免失败被误读为接口未执行；正控允许有界的多次真实焦点通知，但负控继续要求无通知且焦点不变。新增八项测试，完整离线 **621 项通过，12.595 秒**，日志 `.local/live-acceptance/offline-invoke-button-20261026.txt`。原生报告绑定的是增加分类器之前的源指纹，保留为历史反例，不冒充最新源码重新原生验收通过。

本结果说明 `AutoSetFocus=False` 不能普遍阻止 provider/控件自身在 Invoke 时聚焦，尚未定位本次 Button 焦点变化的完整调用链；也不能据此断言微信的自定义 provider 必然同样聚焦。下一步需区分 UIA 客户端前置聚焦和 provider 调用中的聚焦，再对已授权微信发送按钮做严格只读关联核验。真实发送仍禁用，没有消耗微信发送或 API 额度，DbgEng 异常恢复及 M1 均未完成。

## 自建按钮接收端因果证据（2026-10-26）

新增 `scripts/owned_notification_stack.py`，只捕获当前自建进程的通知栈，最多 16 条记录，固定每条 96 帧；只输出明确允许的系统模块名称／RVA、线程 ID 和模块磁盘 SHA-256，未知帧只计数，不保存地址、参数或内存。模块使用当前进程伪句柄和已加载的 System32 模块身份校验，不打开外部进程或加载额外 provider。首轮 48 帧全部达到上限，故保留报告 `uia-invoke-9c9a7d85a51a48cc9161951bcf344562.json` 并把固定上限改为 96，显式记录 `frame_limit_reached`。第二轮报告 `uia-invoke-a931151c3aad4c17a01b891f2ea0c349.json` 获得未截断的通知栈，但 GUI 线程栈没有 UIA 调用者，不能由它闭合跨线程来源。

随后仅对自建子进程刚创建的 Button 安装消息观察回调，原窗口过程通过 CallWindowProc 完整转发；不修改任何微信窗口，也不改变焦点策略。记录 BM_CLICK、WM_LBUTTONDOWN/UP、WM_SETFOCUS/KILLFOCUS 的进入和同步嵌套，父窗口通知记录同一活动消息链。回调存活到自建窗口销毁，沿用既有正常退出及私有桌面清理检查。

原生报告 `uia-invoke-1a1c41e09a1947349211de8d14661de2.json`，SHA-256 `75cafc28cd7927a0d06d4da25ed71faa87e8a55f868b1c1e5244bc5a2d457b1c`：FALSE 组在同一 GUI 线程记录到 `BM_CLICK (245) → WM_LBUTTONDOWN (513) → WM_SETFOCUS (7) → BN_SETFOCUS (6)`，每一步在前一步仍未退出时进入。点击恰好一次，UIA／原生回读与同一 FALSE 关联通过，但最终焦点仍为 Button。默认组则在 BM_CLICK 前已有一次焦点设置，接收端链不能与 FALSE 组混为同一路径。

这直接证明 FALSE 组的这次标准按钮聚焦发生在 **BM_CLICK 处理内部**，而不是根据时间接近推断。捕获焦点通知的一个栈达到 96 帧上限，其余该组五条记录未截断；嵌套因果关系来自回调进入／退出及同线程记录，不依赖被截断的完整栈。没有取得跨线程投递的 UIA 发送端栈，也未证明任何微信自定义 provider 调用链。无干扰判定依旧失败。

两组正常结束，输入环境 102 次抽样未变，窗口、COM、句柄和私有桌面清理通过，无强制终止或 debugger。原生源指纹保留；后来增加的纯分类器对原报告进行一次离线重判，结果保存在 `invoke-receiver-relation-20261026.json`，明确标为 posthoc，未重跑原生或改写原报告。新增七项测试，完整离线 **628 项通过，11.856 秒**，日志 `offline-invoke-receiver-20261026.txt`。本轮没有接触微信或消耗发送/API 额度。

复现仅自建实验：`python -B -X utf8 scripts/probe_uia_invoke.py --run-synthetic`；无干扰条件失败时退出码为 1，即使点击与清理均成功。默认不带该开关返回 disabled；不能用 `--run` 缩写启用。当前结论不要求重复运行旧实验。下一步可追查自建 Button 的 Invoke 到 BM_CLICK 发送端关系，并独立研究微信按钮语义；生产发送和 DbgEng 实时附加继续关闭。

## 微信发送按钮只读关联（2026-10-26）

`probe_bound_uia_context.py` 新增 `--inspect-send-context`，必须同时有 `--inspect-bound`、原账号／目标／桌面绑定、当前源码的自建只读证据及显式临时开关选项；不接受参数缩写，也不增加写入或发送入口。发送检查强制 client-first，在导入旧读取器前只于唯一 ChatInputView 中固定唯一语义发送 Button 及 Invoke pattern。先经过父协调器的原目标、空草稿和焦点检查，才读取当前子进程的 pattern 关联；之后再次核验输入、按钮 RuntimeId 和当前目标。按钮与输入均须为同一 FALSE 关联，失败不调用任何动作。

首次只读报告 `uia-bound-context-15706e39572140b8b644b35989893cc3.json` 因当前群无法核验停止，零事件、未写入，临时字节恢复。用户随后明确回复“已打开”，重新检查使用原绑定，未放宽或刷新 RuntimeId。

通过报告 `.local/live-acceptance/uia-bound-context-43f7278855b54c1d822e0a871911e1fe.json`，SHA-256 `f0bb9a26b244b8c8566b1d92ea3d2e52f76b0e1c3b0a8a7ffd8c7925a31249c9`：

- 精确进程创建身份、微信 4.1.15.56、原 DLL 指纹、账号绑定及指定群唯一选中均通过，空草稿保持为空。
- 输入 ValuePattern 与发送 InvokePattern 均为稳定、同一 FALSE 客户端和上下文体关联；每项只读 292 字节，未调用内部函数。
- 发送接口存在，空草稿下按钮 disabled；没有为探测启用状态而填入文本。
- 1.782 秒结束，0 前台／焦点事件、桌面和剪贴板检查未变，来源未变、工作进程正常退出、临时字节恢复，清理错误为空。
- SetValue、Invoke、发送、API 均未调用；原四本账本哈希前后相同，发送 0/3、API 0/10，单次草稿机会不增加。

新增七项纯测试覆盖强制初始化顺序、只读 CLI、按钮缺失／重名、容器及按钮进程错配、枚举上限、按钮重建，以及不调用 Invoke。完整离线 **635 项通过，12.015 秒**，日志 `offline-send-context-20261026.txt`。这一步只证明发送对象的只读绑定，不能代替 Invoke 无干扰验证或己方消息确认。下一步必须补齐已授权会话的消息回读和己方消息识别，再进行受限发送；生产适配表仍关闭，DbgEng 异常恢复与 M1 均未完成。

## 指定群消息控件结构（2026-10-26）

只读入口新增 `--inspect-message-schema`，强制 client-first 和发送上下文守卫，之后只在父协调器已核验的当前群中查询唯一消息 List。新增 `scripts/message_control_schema.py`，最多 256 个已加载直接行、只检查末尾 5 行、总计 120 个节点、每节点 32 个子控件、3 层深度；到达任何边界均标注覆盖限制。期间核对进程及 RuntimeId，发现对象变化或循环停止。不滚动、不读取消息 Name/Value/Text，不获取头像、不导出联系人；不会把 ItemType 提示或控件位置当作已验证的发送者。

真实报告 `.local/live-acceptance/uia-bound-context-8d4e6c252aa1476e8bcefe0504d5c9e2.json`，SHA-256 `41fc3070412dede87684906006afed7db7dddcfc36545be51888d3045c15b379`：原账号、测试群及输入 RuntimeId 仍匹配，空稿不变，输入／发送关联均为同一 FALSE。消息列表当前暴露 5 个 ListItem，类分别为 ChatTextItemView／ChatItemView；FindAll 未枚举到子控件，ItemType 没有匹配到已知方向提示。没有读取正文，因此本次不证明正文读取、己方标识、群 @、附件或历史分页。

1.750 秒完成，0 前台／焦点事件、桌面与剪贴板检查未变，来源未变、正常退出、临时开关恢复，清理错误为空。四本原账本 SHA-256 与上一轮相同，未写入、未 Invoke、未发送、未调用 API。结构结果明确为 `own_identity_verified=false`、`sender_decision=unverified`，不能用于放行发送或自动回复去重。

新增九项测试覆盖禁止正文读取、仅保留末尾结构、元数据脱敏、跨进程和循环、对象变化、边界标注、列表歧义、只读 worker 及 CLI；完整离线 **644 项通过，12.055 秒**，日志 `offline-message-schema-20261026.txt`。新模块纳入协调器的前后源码指纹检查。

下一步须研究这些已定位消息类是否通过其他可验证的无障碍语义提供发送者／己方属性；不能由昵称、文本冒充身份，也不能从左右对齐推定自身消息。若该接口不可获取，发送确认及自动运行保持阻塞。原 DbgEng 线程／断点异常恢复仍未通过，不能使用附加调试器补足此处证据。

## 消息类声明属性的确切文件分析（2026-10-26）

新增 `scripts/trace_message_items_static.py`，仅读取磁盘字节，必须匹配原微信 DLL SHA-256、x64 架构及确切类锚点；输出限定在本地验收目录且禁止覆盖旧证据。脚本不加载 DLL、不打开进程、不调用元方法。`static_types.py` 的 revision-8 元数据解码器新增有界属性解析，最多 256 个属性；内置类型 ID 与高位标记的字符串类型严格区分，读取／写入标志仅记录，不执行。

本机报告 `.local/live-acceptance/message-items-static-20261026.json`，SHA-256 `c711fe3037a1d8568233cc8e4fa92be3feed94a00ed080c473ce412377e43c0c`：

- `ChatTextItemView` 元对象 RVA `0x904f970`，继承 `ChatBubbleReferItemView → ChatBubbleItemView → ChatItemView`；声明方法为点击、选区、初始选区到期和排版完成处理槽，没有发现声明的发送者 getter。
- `ChatItemView` 元对象 RVA `0x904fa00`，本类无声明方法，唯一声明属性是 `animation_color`，不是身份字段。相关 mmui 消息子类没有其他声明属性。
- 继续继承 XTableCell、QFReuseWidget、QFWidget、QWidget、QObject；QWidget 提供通用 `accessibleName`／`accessibleDescription` 属性。它们是下一步可研究的语义来源，但尚未核验微信如何将它们映射到 UIA，也不能由属性存在推定它包含发送者。

解析依据已核对 Qt 5.15 的 [元数据字段与类型标记](https://raw.githubusercontent.com/qt/qtbase/5.15/src/corelib/kernel/qmetaobject_p.h)及 [QMetaProperty 的三整数记录定义](https://raw.githubusercontent.com/qt/qtbase/5.15/src/corelib/kernel/qmetaobject.cpp)。只独立解析格式，没有复制上游实现；这是私有格式的版本受限研究，不能作为通用兼容性声明。

本轮六项新增测试覆盖属性类型／计数／偏移边界、错误文件哈希和证据路径／覆盖保护；完整离线 **650 项通过，11.897 秒**，日志 `offline-message-static-20261026.txt`。复现：

```powershell
.\.venv\Scripts\python.exe -B -X utf8 scripts/trace_message_items_static.py --dll 'D:\apps\wechat\Weixin\4.1.15.56\Weixin.dll' --output .local/live-acceptance/message-items-static-new.json
```

报告只覆盖静态声明：动态属性、普通 C++ 方法、实际无障碍实例和 provider 尚未被排除。没有运行时聊天／焦点操作、模型请求或额度变更；己方消息确认、发送和完整 M1 仍未通过，DbgEng 异常恢复门槛不变。

## 辅助属性与 FullDescription 读取修正（2026-10-26）

新增显式只读 `--inspect-message-auxiliary`，沿用原绑定、空稿、FALSE 关联、焦点保护和恢复机制。仅对原末尾 5 行检查 AutomationId、HelpText、FullDescription、ItemType、ItemStatus、AriaRole、AriaProperties。原文立即丢弃，仅保留空值／失败状态、4096 字符上限内的长度和格式投影；不读取消息 Name/Value/Text、不滚动、不上传、不 Invoke。辅助属性可能含文字，因此 `text_read=true` 与 `auxiliary_accessibility_properties_read=true` 明确记录访问范围，同时标记 `property_values_retained=false`。即使属性恰为 self，也不会放行己方身份。

首份报告 `uia-bound-context-63e2e75c94604acb98ef4187041d3934.json`，SHA-256 `3c93d195d4a21e3a5bdc69de920db89b2a51db93204200f34e6ded7e571c480b`：三条 ChatTextItemView 的 AutomationId 长度为 62，另两条为空，其他 getter 为空或失败。这里 FullDescription 的失败来自当前基础 element 接口没有新 getter，不能当作客户端不支持的证据。

现已通过基础接口的公开 `GetCurrentPropertyValue(UIA_FullDescriptionPropertyId=30159)` 查询该属性，无需调用新接口 getter。依据 [Microsoft 的属性查询接口](https://learn.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationelement-getcurrentpropertyvalue)。修正后报告 `uia-bound-context-3aad05ce56e84392bd79996fdc6772f2.json`，SHA-256 `b6a3096c56263f7868f8f0632f80f3c165db57d56ce38893552e9f7050704f03`：五条均为 `empty_or_default`，不把默认值认作 provider 明确支持。其余 HelpText、ItemType、ItemStatus、ARIA 属性为空，AutomationId 形状不变；没有找到已验证身份字段。

两次耗时 1.922 / 1.828 秒，均零前台／焦点事件，桌面和剪贴板检查未变，来源未变、进程正常退出、临时开关恢复且无清理错误。四本原账本哈希与之前相同，未写入、未发送、未调用 API。

另对确切微信 DLL 的 GetPropertyValue 诊断字符串做静态交叉引用：函数 RVA `0x4c541b0`，范围至 `0x4c54714`；分发表 RVA `0x99342fc`，属性 30011 分支 `0x4c54552` 调用 `0x4c54720`，该 helper 包含父对象循环、字符串前插和点分隔。分派报告 `accessible-properties-static-20261026.json` 与 helper 报告 `automation-id-helper-static-20261026.json`（SHA-256 `89b202ca61105fa3c6dde40a5e70b303ad10d39147e05fa16e74ad131c165569`）均来自磁盘，未执行地址。

这与 [Qt 5.15 的 AutomationId 构造](https://raw.githubusercontent.com/qt/qtbase/5.15/src/plugins/platforms/windows/uiautomation/qwindowsuiamainprovider.cpp)对应：它把 objectName 与父对象名称组成控件路径。该静态关系不是本次消息实例的完整运行时绑定，也未证明路径各段含义；62 字符的内部标识不能被直接当作微信 ID 或己方标记。HelpText／FullDescription 也不能混用，它们对应不同的无障碍文本类别。

新增六项纯测试覆盖辅助值不留存、伪 self 不授予身份、非法属性、超长／异常值、只读 worker 和基础属性 API；完整离线 **656 项通过，12.111 秒**，日志 `offline-message-auxiliary-20261026.txt`。下一步若继续研究 AutomationId，必须先核验其具体生成来源和字段意义，不能靠字符串形状猜测。己方消息确认、真实 Invoke 和完整 M1 仍未通过，DbgEng 异常恢复继续关闭真实附加。
