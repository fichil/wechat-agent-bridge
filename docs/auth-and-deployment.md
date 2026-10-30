# 后台认证与 Windows HTTPS 部署

此版本交付代码与部署入口。实际云主机部署、域名解析、证书签发和公网开放在部署阶段执行。下列域名均为占位示例，不绑定真实域名。

## 认证行为

- 单管理员，默认用户名 `admin`，与微信账号无关，无默认密码、无网页注册入口。
- 管理员未初始化时，网页登录页只提示在服务器终端初始化；业务 API 和 WebSocket 拒绝未登录访问。
- 电脑、手机可同时登录。每次登录有效期固定为 7 天，使用不会续期，正常重启不会丢失会话。
- 退出只注销当前 Cookie 对应的会话，同时清理同一浏览器的其他标签页。修改、重置密码注销全部设备。
- 退出、过期或撤销会话后，前端停止业务请求并重新载入登录页，销毁缓存与未保存草稿；旧请求结果不能重新写回。密码、CSRF、Cookie 不写入浏览器持久存储。
- 网页会话和后台调度独立。退出网页不会关闭同步、AI 或发送任务。
- `.local/auth.sqlite3` 独立保存 Argon2id 密码哈希、会话令牌摘要和限流记录。聊天数据库结构不因认证而迁移。
- 登录按来源 IP 限制每 15 分钟 10 次，全局每分钟 30 次（成功登录也计数）。超限 429；限流记录重启后保留。修改密码也受此限流保护。

密码长度为 12–128 字符；Argon2id 参数为内存 19 MiB、迭代 2 次、并行度 1。会话令牌使用 256 位随机数，数据库只存 SHA-256 摘要。
生产 Cookie 名为 `__Host-bridge_session`，设置 Secure、HttpOnly、SameSite=Strict、Path=/，不设 Domain。

## 初始化与密码找回

在项目根目录、交互式 PowerShell 中运行：

```powershell
.\.venv-backend\Scripts\python.exe -m backend.admin init
# 可选的非默认用户名：在 init 后添加 --username yourname

# 忘记密码时，在服务器终端执行；保留现有用户名，撤销所有会话
.\.venv-backend\Scripts\python.exe -m backend.admin reset-password
```

命令会隐藏输入并要求确认，不接受密码参数或管道输入。不要把密码写进部署脚本、终端历史、源码或日志。管理员初始化后再次执行 `init` 会拒绝覆盖；恢复应使用 `reset-password`。
如配置了自定义 `BRIDGE_AUTH_DATABASE`，启动与管理员命令必须使用同一路径。重置生效后已连接的 WebSocket 会在下次推送前或约一秒内关闭。

## 准备部署文件

以运行微信的同一 Windows 用户登录服务器。需要 Windows x64、Python 3.12、项目支持的微信版本；本次认证验收未替代服务器上的微信读取/发送兼容性验收。
在可信构建机器或服务器上准备依赖与前端构建：

```powershell
python -m venv .venv-backend
.\.venv-backend\Scripts\python.exe -m pip install -r backend\requirements.lock.txt
Push-Location frontend
npm ci
npm run build
Pop-Location
```

可将已构建的 `frontend/dist` 与源码一起交付云主机；生产运行不需要 Node.js、`node_modules` 或 Vite。
从 [Caddy 官方发行页](https://github.com/caddyserver/caddy/releases) 下载 Windows amd64 版本并核对对应校验文件，不把执行文件、证书或运行数据提交 Git。

## 域名配置与启动

复制 `deploy/production.example.ps1` 到本地不入库的配置文件，例如 `.local/production-config.ps1`，填入实际值：

```powershell
$env:BRIDGE_PUBLIC_ORIGIN = 'https://bridge.example.com'
$env:BRIDGE_CADDY = 'C:\Tools\Caddy\caddy.exe'
```

`BRIDGE_PUBLIC_ORIGIN` 必须是实际 HTTPS 域名，无路径、查询参数或非 443 端口；国际化域名先转换为 ASCII/Punycode。Origin 严格匹配，无通配来源。
部署阶段把域名解析到服务器，按 Caddy 的证书验证要求配置公网 TCP 80/443；不要开放 8765、5173 或管理端口。没有准备好 DNS/证书前，不通过 HTTP 提交后台密码。

```powershell
. .\.local\production-config.ps1
.\.venv-backend\Scripts\python.exe -m backend.admin init
.\scripts\production.ps1 -Check
.\scripts\production.ps1
# 排查时可在终端保持前台运行
# .\scripts\production.ps1 -Foreground
# 停止本项目本次生产启动拥有的进程
.\scripts\production.ps1 -Stop
```

`-Check` 检查管理员、构建、Caddy 配置和端口，但不启动服务、不签发证书。端口占用时退出，不抢占其他进程。
生产入口强制 `BRIDGE_ENV=production`，启动一个仅监听 `127.0.0.1:8765` 的后端与 Caddy。Caddy 仅托管 `frontend/dist`，`/api/*`（含 WebSocket）转发后端，站内深链接回退 `index.html`。Caddy 管理 API 已关闭。
Caddy 覆盖转发的客户端地址与 HTTPS 协议信息；后端仅信任 `127.0.0.1` 代理。请勿额外配置宽泛的可信代理或把后端监听改成公网地址。
生产日志在 `.local/production/`，启动入口返回后应检查日志及 HTTPS 登录页，确认后端和 Caddy 均成功运行。公网入口 `/api/v1/health` 只返回服务健康标记，不包含账号、聊天或配置；业务与文档不允许匿名读取。

程序在当前 Windows 用户会话运行，不安装系统服务，不配置开机自动登录。远程桌面断开与 Windows 注销不同：不要注销运行微信的用户。重启后需要该用户登录、运行微信并启动项目。

## 本地开发

完成管理员初始化后运行 `scripts/dev.ps1`；入口显式设置开发模式。开发 Cookie 仅用于显式配置的回环 HTTP 环境，认证和 CSRF 仍启用。独立启动方式见 README。
生产启动不会沿用开发模式；缺少生产域名配置时后端拒绝启动。

## 备份、迁移与回退

1. 上线前停止旧后端并等待任务正常收尾，备份整个 `.local` 到受访问控制的位置。尤其保留聊天数据库、同步水位、AI/发送状态、认证数据库及相关 WAL/SHM 文件；不在运行中随意只复制 SQLite 主文件。
2. 在相同 Windows 用户下恢复运行数据。认证数据库可保留现有会话；如果不希望保留，执行 `reset-password`。数据库包含会话校验资料，应像凭据一样保护，不放进 Git 或网页静态目录。
3. API 密钥依赖 Windows DPAPI；跨机器/跨用户复制后通常需要重新输入密钥。保持聊天记录和发送状态，不通过新建空聊天库替代迁移；不自动重试状态不确定的发送。
4. Caddy 证书及私钥存储目录单独备份并限制访问；与源码备份分开。源码的 `.gitignore` 排除 `.local`、数据库、环境文件和依赖目录。
5. 切换版本前保留旧源码与数据快照。回退到无认证旧版时，**先停止 Caddy 并撤下公网入口**，再启动旧版；不能把旧版后台继续公开。保持数据兼容性检查，必要时使用切换前快照。

## 验证命令

```powershell
.\.venv-backend\Scripts\python.exe -m unittest discover -s backend/tests -v
.\.venv-backend\Scripts\python.exe -m backend.export_openapi
Push-Location frontend
npm run check:api
npm run test
npm run build
npm run test:e2e
npm run test:e2e:ai
# 需要本地 Caddy，使用隔离端口、内存库及测试证书，不安装信任根
$env:BRIDGE_CADDY = 'C:\Tools\Caddy\caddy.exe'
npm run test:e2e:https
Pop-Location
```

浏览器回归使用隔离端口 15373、15173/18765；HTTPS 验收使用回环 19443/18766 与合成账号。测试中的密码仅是合成夹具，生产没有任何默认密码。HTTPS 夹具只跳过测试证书信任检查，不改变生产模板或操作系统信任库。

依据：[OWASP 密码存储](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)、[OWASP 会话管理](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)、[Caddy 部署模式](https://caddyserver.com/docs/caddyfile/patterns)。
