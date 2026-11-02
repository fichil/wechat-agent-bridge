# 自动生产部署

main 的 PR 合并和直接推送都触发测试、构建；只有全部通过的同一个提交可以部署。PR 不访问生产凭据。工作流可手动重新执行当前 main，生产部署串行且不会中断正在进行的切换，过期提交跳过。

## 首次接入

固定安装目录为 `C:\Apps\WeChatAgentBridge`，其中 `releases/<commit>` 存放不可变程序包，`data` 保存原有私人数据，`controller` 保存独立部署控制器及 Python 运行时，`inbox`、`status`、`backups` 分别保存上传、发布状态和冷备份。普通 ZIP 用户仍使用包内 data。

1. 在 GitHub 创建 production Environment，仅允许 main，无人工审批。配置 `DEPLOY_HOST`、`DEPLOY_USER`、`DEPLOY_PORT`、`DEPLOY_SSH_KEY` 和 `DEPLOY_KNOWN_HOSTS` Secrets。使用专用 SSH 密钥，主机公钥必须从已验证的服务器取得；私钥不入库、不打印。仓库变量 `AUTO_DEPLOY_ENABLED` 初始为 `false`。
2. 在固定目录放置 `installation.json`（`{"version":1}`）、`controller/deploy.py` 及已验证便携包的 `controller/runtime`，复制 deploy 目录中的三个运行脚本及安装脚本。管理员只通过已有 SSH 传输这些文件。
3. 以微信所属 Windows 用户注册任务：`install-tasks.ps1 -InstallRoot C:\Apps\WeChatAgentBridge -UserName <微信用户>`。两个任务均使用 InteractiveToken，不保存登录密码，不安装自动登录；运行任务持续托管 supervisor，部署任务独立执行切换。
4. 执行 `controller/runtime/python.exe controller/deploy.py bootstrap --root C:\Apps\WeChatAgentBridge --source <原便携包目录>`。命令提交首次接入请求，由桌面部署任务验证原包、正常关闭旧启动器并停止后台、校验复制数据，再通过独立运行任务启动原版本。原目录保持为恢复副本。迁移不得使用导入功能替代同机接入，以免撤销会话或丢弃队列。
5. 对 main 手动触发一次工作流并选择 deploy，验证候选程序、实际会话与数据，再将仓库变量 `AUTO_DEPLOY_ENABLED` 设为 `true`。后续 main 更新自动发布。

登录用户退出时计划任务无法运行；请求保持排队，用户下次登录后处理。微信需已安装并登录。不要将后台或部署控制器作为 OpenSSH 的常驻子进程启动。

## 发布与回退

包经过 SHA-256、提交、文件白名单和逐文件校验后才切换。部署前检查空间、现有程序漂移和数据库。当前自动部署协议支持聊天 schema 9，不自动执行不能回退的不兼容升级。

后台通过原正常停止入口退出，等待预算 34 分钟；不强杀微信或在途发送。停机后复制完整 data，校验两套 SQLite 与文件哈希。新版本先受 `deployment-hold.json` 阻挡，不启动同步、模型或发送调度；确认进程身份、健康接口、静态登录页、匿名业务接口 401 及数据库后删除阻挡，后台自动启动业务和就绪检查。

切换失败时回退程序并保留当前数据。新版本一旦启用业务，绝不自动用旧快照覆盖聊天或发送结果。中断的切换由 transaction.json 记录，下次部署任务优先恢复；相同请求可以重复查询和执行。正常保留最近三个成功版本及对应备份，首次接入原目录另行保留。

在服务器本机调用独立控制器：

```powershell
$controller = 'C:\Apps\WeChatAgentBridge\controller'
& "$controller\runtime\python.exe" "$controller\deploy.py" status --root C:\Apps\WeChatAgentBridge
& "$controller\runtime\python.exe" "$controller\deploy.py" status --root C:\Apps\WeChatAgentBridge --request-id <run-id-attempt>
& "$controller\runtime\python.exe" "$controller\deploy.py" rollback --root C:\Apps\WeChatAgentBridge
```

`preflight --request-id ...` 验证并展开已上传包，不停止业务；`submit --request-id ... --commit ... --sha256 ...` 接收已上传的 `.zip.part` 并触发部署任务。发布目录不提供 HTTP 管理接口。详情保存在 status、task-logs 和 data/production；GitHub 日志仅输出发布状态，不上传私人数据。

## 自动就绪行为

后台每五秒自动复查就绪状态，不依赖浏览器。首次安装、程序升级、微信进程变化和迁移不要求人工发送验收或点击恢复。微信的精确指纹、内存代码、账号唯一绑定及组件完整性仍必须通过；配置不完整的会话等待配置修复，不阻塞其他会话。

普通重启在身份不变时保留水位、队列和 epoch。迁移和身份变化会废弃旧触发，并在同步后建立新基线；每个账号的迁移完成标记保存在 compatibility_state 的快照中，避免离线账号阻塞其他账号。用户主动关闭或暂停不被重置。已提交但结果不确定的发送仍需核对处理，系统不会猜测成功或重复发送。

参考：[GitHub Actions 部署](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/control-deployments)、[SSH Action](https://github.com/appleboy/ssh-action)、[Windows 交互式任务](https://learn.microsoft.com/en-us/windows/win32/taskschd/schtasks)。传输使用原生 OpenSSH，服务生命周期由 Windows 任务承接。
