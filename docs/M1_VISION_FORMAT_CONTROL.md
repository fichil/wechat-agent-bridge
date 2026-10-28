# M1 视觉格式对照：2026-10-27

**JPEG/high 的一个合成样例实测通过；PNG/high 仍失败，整体视觉未启用，M1 未完成。** 本轮新增两次 API，累计 10 次；按操作者既有授权独立追加 2 次容量，当前 **10/12**。发送 **3/5** 不变，unknown 不重试，旧账本不重置。

## 目的和实测

原三次 PNG 测试只记录请求模型和回答正文，未核对最终请求图片字节或返回模型。此次沿用本机原密钥、官方接口、gpt-4.1-mini-2025-04-14 和相同 512×256 红左／蓝右图，不换模型或自动重试。官方[快照说明](https://developers.openai.com/api/docs/models/gpt-4.1-mini)及[图像协议](https://developers.openai.com/api/docs/guides/images-vision)仅用于核对模型输入、Base64、格式与 detail 参数，不替代实测。

| 检查 | 结果 |
| --- | --- |
| PNG/high，第 9 次请求 | 5.125 秒，左 red、右 yellow，严格颜色判定失败 |
| 同图转 JPEG/high，第 10 次请求 | 3.016 秒，严格左右颜色判定通过 |
| 两次 HTTP 边界 | 单张固定图、正确 MIME／完整字节／模型／store=false／stream=false 匹配 |
| 两次服务响应 | 返回快照匹配；每次 prompt=256、completion=22、total=278 tokens |
| 微信 | CUA 仅枚举窗口，无截图、UI 文本、输入、发送、开关或 helper |

PNG SHA-256 为 ec4054e294c1263d42be87a25babd045d172c25d94434cda0ed810722670d075；JPEG 为 a1ffaef1a53c527285e205319095d0d49a386de5ea8b0811a31e3649526888d8。

JPEG 由同一个 PNG 离屏转换，尺寸不变。Windows 现有 System.Drawing 4.0.0.0 独立解码并检查原图全部 **131072** 像素；转换后解码 JPEG，检查远离分界线的 **50** 样点，RGB 容差 5。未外放、创建窗口或处理真实附件。JPEG 有损，文字、小字、透明度、照片及通用转换尚未验证。

**各一次对照不能证明 PNG 格式就是错误根因，不能宣称所有 JPEG 可用。** 这只是格式相关线索；排除了图片未按预期进入 HTTP 边界及返回模型不同两种解释。原 PNG 失败保留；没有接入普通聊天转换或自动开启控制台视觉。

## 实现和依赖

CompatibleProvider.describe_image 增加可选 detail=auto/low/high，默认仍 auto，非法值上传前拒绝。验收脚本新增显式单项 --inspect-vision-wire：核对固定图片完整 Base64 解码、MIME 和模型；拒绝重复调用，核对实际返回快照。只记录哈希、模型、detail 及有界整数 usage，不记录请求头、密钥或响应全文；回答仍 DPAPI 加密保存。错误模型即使回答正确也不能通过。

--vision-fixture jpeg-format-control-v4 只能用于单独视觉审计。prepare_vision_jpeg.ps1 仅接受验收目录中脚本新建的固定合成 PNG，逐像素验证后 CreateNew 保存对照，不能覆盖输出或接受任意附件。

环境：Windows 10.0.26300、Python 3.12.10 x64。只使用本机现有 .NET Framework/System.Drawing，无新增 pip 包、上游实现、系统二进制分发或执行策略改变。沙箱内 AuthorizationManager 检查失败；同一离屏检查经自动审查允许的非沙箱执行通过，不需要操作者设置管理员或改变系统安全设置。

## 证据

均在 .local/live-acceptance/，各报告保留当时源码版本：

| 文件 | SHA-256 |
| --- | --- |
| provider-acceptance-39563685aed540028c26c63446e7da50.json | 7024c274d8c9aa30a5eb55a58a12bc3958a83dfec8aa9132d56d24e1c6849108 |
| provider-acceptance-9e41e3f964574b648cb576b7796d0f10.json | a34d610ff2ec7dadd654b6d5d7af69ec33a68411b8c6fd2f769e33699f04f585 |

容量追加记录 api-extension-caab96db77a1410e9908ca57daf31de8.json，grant_id=required-live-replies-20261027-01：保留全部 10 次消耗，新 2 次容量供后续已授权群／私聊回复验收，尚未调用。CUA 枚举收据 development-cua-window-inventory-83ca00d2d70d41ccae128fab3a326727.json 的路径仅作显示，不能重构为 SDK Window，后续仍须重新枚举。

源码 SHA-256：providers.py=b6f049d66a155f45a8e4a4e10f86ecac6866706fd5b8c1d45ac88a741e64b049；verify_provider_acceptance.py=ce65181cc56f3485bc97c822f4db8750012b5cf5d6d3e83b658c293039282bbd；prepare_vision_jpeg.ps1=ca477d00e6b7c265fd0ac325356f27487b49042972be8b5b4be762e1c2ba2666。

## 检查与后续

完整 **940 项通过，15.793 秒，无跳过**。日志 offline-vision-wire-format-20261027.txt，SHA-256 87e689881873d3eed6f932dfc7ef2881497df47b05e3d40107f015a8474fc671。新增覆盖精确图片／MIME／模型、错误返回模型不能通过、密钥不入报告、非法 detail 不上传及无自动重试。默认套件不读取真实密钥、访问微信、调用付费 API 或 Windows 图片转换。

离线复核：

    $env:TEMP=(Resolve-Path -LiteralPath '.local/test-tmp').Path
    $env:TMP=$env:TEMP
    .\.venv\Scripts\python.exe -B -X utf8 -m unittest discover -s tests -v

以下是真实历史复现命令，会消耗新额度，不因成功而重复运行：

    .\.venv\Scripts\python.exe -B -X utf8 scripts/verify_provider_acceptance.py --run-synthetic --env-file .env.local --capability vision --vision-fixture jpeg-format-control-v4 --vision-detail high --inspect-vision-wire

后续若研究通用图像处理，先检查文字／透明度／尺寸／失败降级，不从双色样例直接推广。准确私聊导航仍受此前自动审批限制，已异步请求操作者一次恢复准确私聊界面，并非重新请求常规发送授权。旧 unknown／锁保留，私聊历史不上传，群 @、私聊回复、分页、客户端附件仍未完成真实验收。
