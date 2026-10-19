# 新电脑与前置部署指南

[简体中文](SETUP.zh-CN.md) · [English](SETUP.en.md) · [返回 README](../README.md)

本指南说明使用 LinX Remote 之前要准备什么。管理器负责远程供应商、模型、SSH 隧道和个人目录工具；Cindy、SSH 登录、Docker 和 Sub2API 需要分别准备。

## 先选连接方式

| 方式 | 模型请求走哪里 | 本机需要 Sub2API / Docker 吗？ |
| --- | --- | --- |
| 直连中转站 | 服务器 → 支持 Responses 的 API | 不需要 |
| 本机 Sub2API | 服务器 → 反向 SSH 隧道 → 这台电脑的 Sub2API | 需要 Sub2API；下面使用 Docker Desktop 部署 |

两种方式都需要一台能通过 SSH 访问的 Linux 服务器。电脑无需公网 IP；服务器也可以经已有 VPN、跳板机等路径访问，只要本机 OpenSSH 能登录。隧道方式还要求服务器允许 SSH 远程端口转发。

## 1. 在新电脑准备 Cindy 与 SSH

1. 从 [Cindy 官方 Releases](https://github.com/makecindy/cindy/releases/latest) 安装 Cindy。它与 LinX Remote 是两个独立应用。
2. 在 PowerShell 执行 `ssh -V`。若找不到命令，在 Windows「可选功能」中安装 **OpenSSH 客户端**；不需要安装 OpenSSH 服务器。安装系统组件可能要求本机管理员权限。
3. 准备服务器地址、SSH 端口、登录用户名和认证方式。新电脑可使用已有密钥的安全副本，或创建新密钥并将公钥交给服务器管理员授权。不要将私钥上传到 GitHub。
4. 打开 Cindy 的「远程连接」设置，新增服务器并填写连接信息；具体菜单位置可能随 Cindy 版本调整。若使用私钥，选择**这台电脑上的私钥文件路径**；若使用 SSH Agent，先确认所需密钥已加载。
5. 测试并保存连接。首次出现主机指纹时，向管理员或可信来源核对后再接受。确认能登录服务器，再选择远程项目所在目录。

可以在终端验证手动连接，替换示例中的主机、用户名和端口：

```powershell
ssh -p 22 developer@server.example
```

退出服务器终端使用 `exit`。Cindy 能使用密码登录，不一定代表独立后台 OpenSSH 已能免交互登录；LinX Remote 不保存 SSH 密码，后台连接需要可用的 Key / Agent 等认证。管理器提供「打开 SSH 登录终端」用于检查指纹和认证。

如果 Cindy 已保存 SSH 连接，但新建远程项目卡在缺少 gawk 或下载 Codex，可先完成下文管理器的「一键检查并准备」，再返回 Cindy 重试。管理器准备过程不需要成功创建一个远程项目作为前提。

## 2. 下载并连接 LinX Remote

1. 打开 [LinX Remote Releases](https://github.com/LinX-Wang/linx-remote-manager/releases/latest)，选择 `LinX-Remote-<版本>-windows.zip`。不要用「Code → Download ZIP」代替 Windows 发行包。
2. 完整解压到自己可写的目录。进入 `LinX-Remote`，双击 **`LinX 远程模型管理.exe`**。ZIP 内不需要额外安装 Python；下载页单独提供的 `LinX-Remote.exe` 也可独立启动。
3. 在管理器选择「服务器连接 → 从 Cindy 选择服务器」，找到刚保存的服务器，点击「使用并连接」。也可以手动添加。
4. 检查实际账号和远程 Codex HOME。默认从实际登录账号个人目录识别，root 使用 root 的个人目录；已有部署应确认与 Cindy 项目使用同一 Codex HOME。
5. 点击「一键检查并准备」，检查或安装个人目录中的 gawk 和完整 Codex。已有可用工具会跳过，不执行服务器 sudo。

服务器需具备 Python 3.8+，自动下载 Codex 需要 curl、tar；gawk 自动准备主要适配 Debian/Ubuntu 的 apt-get、dpkg-deb。缺少基础条件时按界面提示处理，或联系管理员提供这些基础工具。

Cindy 本机已有的供应商配置不会因此自动复制到服务器上的 Codex。远程供应商由管理器添加或从相同服务器 Codex HOME 读取；模型同步也针对这个远程环境。

**使用直连服务时**，现在即可添加 API 根地址、Key 并获取模型，保存后主动启用，无需继续部署 Sub2API。

### 自动检测说明

连接时会自动核验 Codex 目录、服务器隧道端口与本机 Sub2API 端口，也可点击「重新检测」提前查看结果。

- Codex：检查所填目录与当前登录账号的常见 Cindy 安装位置；root 使用 root 的个人目录。多个候选目录需要选择，特殊安装位置仍可手动填写。没有安装时只建议个人目录，不会在检测时安装。
- 服务器端口：复用现有供应商的隧道端口；新连接的端口发生冲突时，在有限候选范围内建议空闲端口。若已有供应商的端口仍被旧电脑或其他连接占用，会提示核对，不终止连接或悄悄改写供应商地址。端口检查是即时结果，建立 SSH 隧道时仍会再次检查。
- 本机服务：读取运行中的 Sub2API Docker 端口映射，并核验健康检查；非 Docker 服务结合页面标识识别。只检查所填端口、识别到的映射与少量常用端口，不扫描整个系统。多个实例可在结果中选择。自定义部署无法识别时手动填写；直连供应商不要求本机部署 Sub2API。
- 换电脑：重新选择 Cindy 中的服务器并连接，即可重新检测新电脑的本机服务。SSH 登录和 Sub2API 部署仍需先准备；迁移包里的旧值也会核验。后台隧道运行期间保持原端口，如需更换先停止旧隧道。

「重新检测」只填入建议，不保存；「使用并连接」会重新检测后保存。检测失败或需要选择 Codex 目录时，原连接设置保持不变。

## 3. 部署本机 Sub2API（仅隧道方式）

### 3.1 准备 Docker Desktop

Windows 使用 [Docker Desktop 官方安装指南](https://docs.docker.com/desktop/setup/install/windows-install/)，按系统要求启用 WSL 2 / 虚拟化等组件。安装和启用系统组件可能要求本机管理员权限；这与服务器上个人目录工具安装无 sudo 是两件事。

启动 Docker Desktop，等待引擎就绪，然后在 PowerShell 检查：

```powershell
docker version
docker compose version
```

`docker version` 应包含 Server 信息。只有 Client 或引擎连接报错时，先让 Docker 恢复可用。已有运行正常的 Sub2API 可以直接跳到 3.3，不要重复初始化或覆盖数据。

### 3.2 Windows 全新部署示例

示例使用 [Sub2API 官方本地目录版 Compose](https://github.com/Wei-Shaw/sub2api/blob/main/deploy/docker-compose.local.yml)，便于备份和换机。以下命令适用于**全新空目录**，在 Windows PowerShell 运行；会将部署文件放到个人目录的 `Sub2API` 文件夹。

如果该文件夹已经存在，命令会停止，请先核实是否已有部署。不要覆盖已有 `.env` 或重新生成旧部署的加密密钥。镜像和部署要求以 [Sub2API 官方部署说明](https://github.com/Wei-Shaw/sub2api/blob/main/deploy/README.md) 为准。

```powershell
& {
    $ErrorActionPreference = 'Stop'
    $deployDir = Join-Path $HOME 'Sub2API'
    if (Test-Path -LiteralPath $deployDir) {
        throw 'Sub2API 目录已存在；请先检查旧部署，不要覆盖。'
    }
    New-Item -ItemType Directory -Path $deployDir | Out-Null
    Set-Location -LiteralPath $deployDir

    Invoke-WebRequest -UseBasicParsing -Uri 'https://raw.githubusercontent.com/Wei-Shaw/sub2api/main/deploy/docker-compose.local.yml' -OutFile 'docker-compose.local.yml'
    Invoke-WebRequest -UseBasicParsing -Uri 'https://raw.githubusercontent.com/Wei-Shaw/sub2api/main/deploy/.env.example' -OutFile '.env'

    function New-Secret {
        $bytes = New-Object byte[] 32
        $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
        try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
        -join ($bytes | ForEach-Object { $_.ToString('x2') })
    }
    $values = @{
        BIND_HOST = '127.0.0.1'
        SERVER_PORT = '8080'
        POSTGRES_PASSWORD = (New-Secret)
        JWT_SECRET = (New-Secret)
        TOTP_ENCRYPTION_KEY = (New-Secret)
    }
    $envText = Get-Content -LiteralPath '.env' -Raw
    foreach ($name in $values.Keys) {
        $pattern = '(?m)^' + [regex]::Escape($name) + '=[^\r\n]*'
        if ($envText -notmatch $pattern) {
            throw ('官方环境模板缺少字段，请核对部署说明：' + $name)
        }
        $envText = [regex]::Replace($envText, $pattern, ($name + '=' + $values[$name]))
    }
    Set-Content -LiteralPath '.env' -Value $envText -Encoding UTF8
    New-Item -ItemType Directory -Path data,postgres_data,redis_data | Out-Null
    docker compose -f docker-compose.local.yml up -d
    if ($LASTEXITCODE -ne 0) { throw '服务启动失败，请检查 Docker 输出后重试。' }
}
```

服务只向电脑的 `127.0.0.1:8080` 发布，无需开放公网端口。若 8080 被占用，可在 `.env` 修改 `SERVER_PORT`，重新运行 Compose，并同步修改管理器的本机端口。下载镜像失败时检查本机 Docker 的网络 / 代理。

回到部署目录后查看状态和初始化日志：

```powershell
Set-Location -LiteralPath (Join-Path $HOME 'Sub2API')
docker compose -f docker-compose.local.yml ps
docker compose -f docker-compose.local.yml logs --tail 100 sub2api
Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:8080/health'
```

等待服务就绪，健康接口应返回 HTTP 200。浏览器打开 `http://127.0.0.1:8080`；未自行指定初始管理员时，按当前版本官方说明从首次初始化日志查找生成的管理员邮箱和密码。日志可能包含凭据，请勿公开粘贴。

macOS 可安装 Docker Desktop for Mac 后，按 [Sub2API 官方部署说明](https://github.com/Wei-Shaw/sub2api/blob/main/deploy/README.md) 使用对应终端命令；上面的 PowerShell 示例面向 Windows。LinX Remote 的 Mac 入口仍需 Python，且尚未完成真机完整回归。

### 3.3 配置账号、分组和 API Key

1. 登录 Sub2API 管理页面，按其说明导入或添加可用上游账号 / 渠道。仅仅打开网页不代表已具备可调用的模型。
2. 根据需要建立专门供服务器使用的分组，将可用账号加入该分组，并确认模型及权限可用。
3. 创建绑定目标分组、可访问所需模型的 **Sub2API API Key**。
4. 在本机确认所需模型可正常调用，再配置隧道。模型列表可获取并不代表实际调用权限一定通过。

管理员登录密码、上游账号凭据、Sub2API API Key 和 SSH 密钥用途不同。管理器的供应商 Key 应填写 **Sub2API 创建的 API Key**，不是管理员密码；不要将这些凭据写入 README 或公开反馈。

## 4. 接通隧道并启用供应商

1. 确认 Docker 和本机 Sub2API 正常运行。
2. 在 LinX Remote 的「本机 Sub2API」页面建立连接。默认将服务器 `127.0.0.1:18765` 转发到电脑 `127.0.0.1:8080`。
3. 添加供应商时选择「本机 Sub2API」，使用服务器看到的地址 `http://127.0.0.1:18765/v1`，填写 Sub2API API Key。
4. 获取并勾选模型，保存，然后主动点击启用。可选取单个模型测试；测试会产生少量模型用量。
5. 在 Cindy 的同一服务器远程项目中确认模型菜单和实际调用。

使用期间电脑、本机服务和 SSH 网络需要在线。服务器仅需 SSH 可访问，不需要将隧道端口开放至公网。若服务器禁止远程转发，请联系管理员确认策略。

## 换电脑时分别迁移什么

| 内容 | 实际存放位置 | 换电脑怎么处理 |
| --- | --- | --- |
| 管理器程序 | 原电脑 | 从 GitHub Releases 重新下载干净 Windows 包 |
| Cindy SSH 连接、Key / Agent、主机信任 | 本机及账号认证环境 | 在新电脑配置并重新核实；管理器不会自动复制 |
| 供应商、API Key、已选模型 | 原服务器的 Codex HOME | 连接同一账号和目录重新读取，服务器数据仍在时无需重填 |
| Sub2API 账号、分组、Key、数据库 | 运行 Sub2API 的电脑或其独立服务环境 | 使用隧道时另行备份恢复，或在新部署重新配置 |
| 管理器个人迁移包 | 原电脑的私人备份 | 可迁移连接设置，不包含 SSH 私钥或 Sub2API 数据库；不要公开上传 |

备份 Sub2API 时，按官方要求先停止服务，再一致性备份部署目录中的 `.env`、`data`、`postgres_data`、`redis_data`，或使用其支持的数据库备份方式。恢复时保留原加密密钥和相容版本，不要边运行数据库边直接拷贝数据目录；旧数据库版本有迁移要求时按官方说明处理。

如果新电脑重新部署并创建了新的 Sub2API Key，需在管理器里编辑隧道供应商的 Key；旧 Key 仅在原数据被正确恢复或仍由原实例提供服务时继续有效。直连供应商不依赖本机 Sub2API，不需要做这部分迁移。

后续操作见 [使用指南](USER_GUIDE.md)。

编辑当前正在使用的供应商后，保存会自动应用并刷新所选模型。新建或编辑未启用的供应商不会自动切换。服务器有任务运行时会阻止应用，原配置保持不变；任务结束后再次保存即可。重复启用未变配置只做校验；实际更换接口或模型目录仍需等待远程服务重新加载。



