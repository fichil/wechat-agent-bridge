# 数据库设计与恢复

SQLite 位于 `.local/bridge.sqlite3`。数据只存于本机，按当前 Windows 用户访问的账号隔离；公开 ID 为持久化随机 UUID，不使用 PID 作为历史身份。

## 关系和索引

| 表 | 主键、约束与用途 |
| --- | --- |
| accounts | account_id 主键，wxid 唯一；资料、在线状态、最后同步时间、账号 revision |
| conversations | conversation_id 主键，(account_id, source_id) 唯一；摘要、分类、排序、头像引用 |
| messages | message_id 主键，(account_id, conversation_id, identity_key) 唯一；结构化正文、发送者、源引用、sort_key、change_seq |
| sync_state | (account_id, shard, table_name) 主键；generation、baseline_id、last_id、upper_id、signature、started_at |
| history_ranges | (account_id, conversation_id, lower_key, upper_key) 主键；经过完整源读取证明的连续排序区间 |
| media_cache | cache_key 主键；头像/图片的 BLOB、MIME、SHA-256、字节数、质量和最近使用时间 |
| conversation_settings | 稳定账号指纹主键；完整七项布尔设置 JSON |
| sync_jobs | task_id 主键；状态和小型结果引用，完成任务最多保留 256 个且不超过一天 |
| cursors | 不透明 token 主键；账号/会话/操作绑定、边界和有效期，数量有界 |
| metadata、schema_migrations | 一次性设置迁移标记、来源状态及 schema 版本 |

消息去重优先使用正数 server_id（十进制字符串）。无有效 server_id 时采用来源代次、分片及 local_id，账号和会话始终参与唯一约束。
源文件身份或盐改变、local_id 回退时使来源代次变化，避免把新记录覆盖到旧历史。WAL checkpoint 本身不改变逻辑代次。

`message_order(account_id,conversation_id,sort_key,message_id)` 支持复合游标深分页；`message_changes(account_id,conversation_id,change_seq,message_id)` 支持增量变更；变更流与消息显示顺序分开，避免低排序值迟到消息丢失。
文本正文、发送者和解析结果以 JSON 保存，排序、来源、编号、代次另有索引列。图片采用 BLOB，不在任务表重复保存 Base64。

## 事务与覆盖语义

数据库由一个专用线程、一个连接拥有；HTTP 查询在线程池等待结果，异步事件循环保持可响应。启用 WAL、FULL 同步、外键、内存临时表及有限页缓存；写入和 checkpoint 在同一连接串行执行。文件锁拒绝第二个后端实例。

每一批消息写入与对应分片水位在同一事务提交，提交后才发布通知。磁盘满、SQL 错误或进程中断不能留下已推进而消息缺失的水位。重试采用 UPSERT，不重复创建稳定消息 ID。

首次只记录每表最高 local_id；之后固定本批 upper_id，按 local_id 排序分批读取，未处理完保留 upper_id。新增会话/分片同样有界处理。联系人、原始会话及分片 Name2Id 联合解析源会话；无法映射时保留明确问题，不猜测归属。

历史覆盖不使用新增水位推断。源历史分页完整成功后才合并 history_ranges；失败分片重试原边界。已缓存的两个区间之间存在缺口时，不宣称完整历史，不静默跨越缺口。`coverage=complete` 表示当前连接到源起点的已读取历史；离线状态和最近同步时间另外披露。

聊天归档保留已经取得的消息，不自动镜像源库删除。变化来源每表复核最近 25 条缓存记录及最多 25 条待转写语音，后者按最后复核时间轮转，不限于最近消息；只保存微信已有转写。更早的其他内容修改需要显式刷新相应范围。迁移版本 2 为待转写语音增加部分索引。

## 容量和故障

默认有效图片缓存为 512 MiB，普通图片、静态表情与 GIF 共用 `kind=image` 的 LRU 配额，不删除文字或头像。数据库删除 BLOB 后会复用空闲页，运行中不执行阻塞性 VACUUM，因此文件大小不等于有效图片容量。任务表保存消息 ID 引用和图片元数据，不另存图片正文。

表情仍保留 `type=sticker` 及原消息 ID，通过已有 `source_ref` 按需定位源消息；旧归档无需迁移或重新导入。成功获取后将原文件作为 BLOB 保存，HTTP/工作进程 JSON 边界使用标准 Base64，GIF 动画字节不变。下载地址、表情加密字段及完整 XML 不写入消息响应、任务表或日志。资源首次未缓存且微信离线时返回 `source_offline`；缓存命中不访问源或网络。

数据库版本不匹配、完整性检查失败、占用或迁移失败均停止写入并报错；不会自动删除、重命名或重建原库。旧设置 JSON 只迁移一次，损坏文件保持原样。

## 备份与恢复

归档排除出 Git，不随源码同步到远端。源码备份不等于聊天数据备份。

最简单的备份方式：正常停止此项目后端，确认数据库所有者已退出，再复制数据库及仍存在的同名 WAL/SHM 到用户指定的本地备份目录。不要在服务运行中仅复制主 `.sqlite3` 文件，也不要删除 WAL。需要在线备份时应在数据库所有者线程调用 SQLite backup API；本次未提供自动备份任务。

恢复前正常停止后端，保留当前数据库及 WAL/SHM 的完整副本，再恢复同一备份集合；启动时检查 quick_check 和版本。归档可以离线查询，源微信同步仍需重新确认当前账号。不要用数据库查看工具在服务运行时并发修改归档。

参考：[SQLite WAL](https://www.sqlite.org/wal.html)、[SQLite UPSERT](https://www.sqlite.org/lang_upsert.html)、[SQLite 复合游标](https://www.sqlite.org/rowvalue.html)。
