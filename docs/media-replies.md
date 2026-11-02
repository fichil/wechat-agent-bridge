# 图片、原生表情与多条回复

模型继续返回 `{"memory":"…","reply":"…"}`。`reply` 中独立一行的 `[[SPLIT]]` 分隔微信消息；普通换行保留，空段忽略，文字继续按 UTF-8 字节预算分段并保留组合 emoji。

```text
给你看看这个
[[SPLIT]]
[[STICKER:本次候选素材ID]]
[[SPLIT]]
是不是很可爱
```

媒体须独占一段。`[[IMAGE:消息ID]]` 只能引用当前账号、当前会话、已获取且列入本次请求候选的图片；`[[STICKER:素材ID]]` 引用候选表情。也支持真实图片的 `[[IMAGE:data:image/png;base64,…]]` 和 `[[STICKER:data:image/gif;base64,…]]`。没有新增绘图服务，不要求模型编造 Base64。整批解析、引用与媒体校验完成后，才在同一 SQLite 事务中提交记忆、触发消费和发送队列；非法段不会作为文字发出。

## 素材与接口

工作台“表情包”页全账号共用。内置固定提交的 27 张大肥鱼 PNG；支持上传自定义素材、预览、修改名称和使用场景、启停与删除自定义素材。内置素材可停用，不能删除。名称和描述供模型按角色、语境选择，最多同时启用 100 张。

素材来源、原作者与非商业许可保留在 [素材声明](../backend/assets/dsh-pet/NOTICE.md) 和 [上游许可原文](../backend/assets/dsh-pet/LICENSE.assets.md)。原始 PNG 和 Git blob 摘要随便携包分发。

所有接口均为 POST JSON，沿用登录、Origin、CSRF 保护。更新/删除要求 `expected_revision`：

| 接口 | 用途 |
| --- | --- |
| `/api/v1/stickers/query` | 元数据列表，不返回 Base64 |
| `/api/v1/stickers/get` | 原始图与发送版详情，按需返回 `mime_type`、`data_base64` |
| `/api/v1/stickers/create` | 上传名称、描述、启用状态与图片 |
| `/api/v1/stickers/update` | 修订保护下修改名称、描述、启用状态 |
| `/api/v1/stickers/delete` | 修订保护下删除自定义素材 |
| `/api/v1/ai-runs/media/query` | 按账号、会话、运行与序号获取回复媒体 |

普通图片最多 8 MiB。支持 PNG/JPEG/WebP/GIF 输入；普通静态 WebP/GIF 转为 PNG，动画须作为表情。表情静态图转为单帧 GIF，GIF 原字节保留动画；发送 GIF 不超过 1 MiB，宽高均不超过 1024。解码还限制 500 帧、累计 1 亿像素，超限明确拒绝。

## 顺序、快照与恢复

`AiRun.reply_parts` 保存有序的 `text/image/sticker` 项，旧 `reply` 保留可读摘要。运行面板按顺序显示内容、动画和各项状态。日志与后续记忆不重复保存回复 Base64。

请求候选与发送项指向内容寻址的二进制快照；素材被编辑、停用、删除或历史图片缓存被淘汰后，既有请求和待发项仍使用原图。回复媒体独立于历史图片 LRU。已由原生消息键与归档双重确认的发送项，支持按归档消息回读不可变发送快照；自定义表情的微信归档可能只有 MD5、没有 CDN 地址，这种情况下仍可在工作台预览原始 GIF。数据库 v11 增加 `reply_media`、`stickers`、`reply_candidates`，并扩展发送队列；完整迁移包含这些数据，角色/API 专用迁移包范围不变。

同账号串行发送，上一项确认成功后立即调度下一项。失败或结果不确定会停止后续项；重试只处理明确未发出的失败项。服务中断时在途项转为不确定；已成功项不会重发。关闭 AI 取消未发送项；模型执行中切换角色/API 时沿用原有冲突保护。

原生媒体仅适配生产 4.2.0.9 精确 SHA-256；4.1.15.56 继续使用已有文字发送。`send_text`、`send_image`、`send_sticker` 分别报告能力。原生进程只接收程序管理的随机临时文件标识和摘要，拒绝任意路径、路径穿越、格式不符及内容变更。原生任务结束且结果确定后删除临时文件；结果不确定或原生任务仍在运行时保留资源。

媒体原生函数返回只产生 `accepted` 证据；必须将该任务的消息标识、目标、类型与当前账号/会话中新的自发归档消息精确匹配，才能标为 `sent/native_archive`。相同归档消息不能确认两项发送。缺少任何证据均保留为 `unknown`，不自动重发。文本继续使用既有原生完成回调。

## 参考与核验

- [dsh-pet 的素材选择](https://github.com/PC2005-cloud/dsh-pet/blob/2265f24a941e6d6736d8a069cc06cf4216df4539/dsh-pet/src/host/memes.ts)：采用名称、描述和受限素材选择的思路。
- [WeChat-AI 的有序回复解析](https://github.com/SMNETSTUDIO/WeChat-AI/blob/9859a3fa0f7563411c61040cbf202a20c4f71922/packages/core/src/reply-format.ts)：参考混合消息列表思想，未复制源码。
- [helper 原生媒体](https://github.com/Prometheus-first/helper/tree/3e65e4bdaff438dc616f32d412175842fa138763/native)：原生派生代码保留 [许可与修改记录](../native_send/NOTICE.md)。

分阶段发布见 [回退与发布](media-reply-rollout.md)。真实验收使用显式指定账号/会话的 `scripts/verify_media_send_live.py`，只读生产归档，并在微信所在交互会话中观察窗口、焦点、剪贴板和草稿。草稿以只读 SessionTable 摘要和可用的 UI 观察交叉检查；不可观察时标记“不确定”，不以构建、健康检查或函数返回代替真实验收。
