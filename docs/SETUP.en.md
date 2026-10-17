# New computer setup and prerequisites

[简体中文](SETUP.zh-CN.md) · [English](SETUP.en.md) · [Back to README](../README.en.md)

LinX Remote manages remote providers, models, SSH tunnels, and per-user tools. Cindy, SSH access, Docker, and Sub2API are separate prerequisites.

## Choose a connection path

| Path | Where model requests go | Local Sub2API / Docker needed? |
| --- | --- | --- |
| Direct API provider | Server → API supporting Responses | No |
| Local Sub2API | Server → reverse SSH tunnel → Sub2API on this computer | Sub2API is required; the example below uses Docker Desktop |

Both paths require a Linux server reachable over SSH. Your computer does not need a public IP address. A server accessible through your existing VPN or jump host is also fine, provided local OpenSSH can log in. The tunnel path additionally requires SSH remote port forwarding to be allowed on the server.

## 1. Prepare Cindy and SSH

1. Install Cindy from its [official releases](https://github.com/makecindy/cindy/releases/latest). Cindy and LinX Remote are separate applications.
2. Run `ssh -V` in PowerShell. If the command is unavailable, install the **OpenSSH Client** through Windows Optional Features. You do not need the OpenSSH Server component. Installing system components may require local administrator access.
3. Prepare the host, SSH port, username, and authentication method. Securely transfer your existing key, or create a new key and have the server administrator authorize its public key. Never upload private keys to GitHub.
4. Add and save the server in Cindy's remote connection settings. Menu locations may change between Cindy versions. Select a private key **file on the new computer**, or ensure the correct key is loaded in your SSH agent.
5. Test the connection. Verify the host fingerprint with a trusted source before accepting it. Once login works, select the remote project directory.

To test a manual connection, replace the example host, username, and port:

```powershell
ssh -p 22 developer@server.example
```

Use `exit` to leave the remote shell. A successful password login in Cindy does not necessarily enable unattended login from a separate OpenSSH process. LinX Remote does not store SSH passwords; background operations require working key/agent authentication or another noninteractive method. Its SSH login terminal helps check authentication and host trust.

If Cindy has saved the SSH connection but creating a remote project fails on missing gawk or Codex downloads, prepare the environment with LinX Remote first, then retry in Cindy. An already-created remote project is not required for the manager's preparation step.

## 2. Download and connect LinX Remote

1. Open [LinX Remote Releases](https://github.com/LinX-Wang/linx-remote-manager/releases/latest) and download `LinX-Remote-<version>-windows.zip`. **Code → Download ZIP** is a source archive, not the Windows executable package.
2. Fully extract it to a writable folder. Open `LinX-Remote` and run **`LinX 远程模型管理.exe`**. Python is not required for this EXE. The separate `LinX-Remote.exe` release asset can also run independently.
3. In the manager, choose **服务器连接 → 从 Cindy 选择服务器**, select the saved server, and click **使用并连接**. Manual connections are also supported.
4. Check the actual account and Codex HOME. The manager detects the login account's home directory; root uses root's home. Existing installations must use the same Codex HOME as the Cindy project.
5. Click **一键检查并准备** to check or install per-user gawk and the complete Codex package. Working installations are reused. No server-side sudo is used.

The server requires Python 3.8+. Automatic Codex downloads also need curl and tar. Automatic gawk setup primarily supports Debian/Ubuntu with apt-get and dpkg-deb. Follow the UI guidance or ask the administrator to supply missing base tools.

Cindy's existing local provider settings are not automatically copied to server-side Codex. Add remote providers through the manager or read them from the same server Codex HOME; model synchronization targets that remote environment.

**For a direct API provider**, you can now add the base URL and key, select models, save, and activate. Skip local Sub2API deployment.

## 3. Deploy local Sub2API — tunnel path only

### 3.1 Prepare Docker Desktop

Follow the [official Docker Desktop Windows installation guide](https://docs.docker.com/desktop/setup/install/windows-install/), including WSL 2 and virtualization requirements. Local installation may require administrator access; this is separate from sudo-free per-user setup on the remote server.

Start Docker Desktop, wait for its engine, then check in PowerShell:

```powershell
docker version
docker compose version
```

The first command should include Server information. Resolve Docker engine errors before continuing. If Sub2API already runs correctly, skip to section 3.3 instead of initializing or overwriting existing data.

### 3.2 Fresh Windows deployment example

This example uses Sub2API's [official local-directory Compose file](https://github.com/Wei-Shaw/sub2api/blob/main/deploy/docker-compose.local.yml) for easier backup and migration. Run it in Windows PowerShell for a **new deployment only**. It creates a `Sub2API` folder under your user home.

If that folder already exists, the command stops. Check the existing deployment before proceeding. Do not overwrite an existing `.env` or regenerate its encryption keys. Consult the [official deployment guide](https://github.com/Wei-Shaw/sub2api/blob/main/deploy/README.md) for current image and deployment requirements.

```powershell
& {
    $ErrorActionPreference = 'Stop'
    $deployDir = Join-Path $HOME 'Sub2API'
    if (Test-Path -LiteralPath $deployDir) {
        throw 'Sub2API folder exists. Check the existing deployment; do not overwrite it.'
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
            throw ('Official environment template field missing; check upstream instructions: ' + $name)
        }
        $envText = [regex]::Replace($envText, $pattern, ($name + '=' + $values[$name]))
    }
    Set-Content -LiteralPath '.env' -Value $envText -Encoding UTF8
    New-Item -ItemType Directory -Path data,postgres_data,redis_data | Out-Null
    docker compose -f docker-compose.local.yml up -d
    if ($LASTEXITCODE -ne 0) { throw 'Startup failed. Check Docker output before retrying.' }
}
```

The service is published only on this computer's `127.0.0.1:8080`; no public port is needed. If 8080 is occupied, change `SERVER_PORT` in `.env`, run Compose again, and update the manager's local port. For image download failures, check Docker's network and proxy settings.

From the deployment directory, check services and initialization logs:

```powershell
Set-Location -LiteralPath (Join-Path $HOME 'Sub2API')
docker compose -f docker-compose.local.yml ps
docker compose -f docker-compose.local.yml logs --tail 100 sub2api
Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:8080/health'
```

Once ready, the health endpoint should return HTTP 200. Open `http://127.0.0.1:8080` in a browser. If you did not set an initial administrator, follow the current upstream instructions to find the generated administrator email and password in the first initialization logs. Those logs may contain credentials; do not share them publicly.

On macOS, install Docker Desktop for Mac and follow the [official Sub2API deployment instructions](https://github.com/Wei-Shaw/sub2api/blob/main/deploy/README.md) for your shell. The PowerShell example above targets Windows. LinX Remote's Mac launcher still requires Python, and full physical-Mac regression testing is pending.

### 3.3 Configure accounts, groups, and an API key

1. Log in to Sub2API and add or import working upstream accounts/channels according to its documentation. A reachable web page alone does not mean models can be called.
2. Optionally create a group dedicated to your server, assign working accounts, and check model access.
3. Create a **Sub2API API key** linked to the intended group and allowed models.
4. Verify an actual model call locally before configuring the tunnel. Fetching a model list does not prove that all calls are authorized.

The administrator password, upstream account credentials, Sub2API API key, and SSH key serve different purposes. Enter the **API key created in Sub2API** into the manager's provider Key field, not the administrator password. Keep credentials out of documentation and public reports.

## 4. Establish the tunnel and activate a provider

1. Confirm that Docker and local Sub2API are running.
2. Connect from the manager's **本机 Sub2API** page. By default, server `127.0.0.1:18765` forwards to computer `127.0.0.1:8080`.
3. Add a provider using **本机 Sub2API**, the server-side address `http://127.0.0.1:18765/v1`, and your Sub2API API key.
4. Fetch and select models, save, and explicitly activate. Optional single-model tests may incur a small usage charge.
5. Confirm the model menu and an actual request in a Cindy remote project on that server.

Keep your computer, local service, and SSH network online. Do not expose the server's tunnel port publicly. If remote forwarding is prohibited, ask the server administrator about the policy.

## What to migrate between computers

| Item | Where it lives | What to do on the new computer |
| --- | --- | --- |
| Manager application | Old computer | Download a clean Windows package from GitHub Releases |
| Cindy SSH settings, key/agent, and host trust | Local authentication environment | Configure and verify again; the manager does not copy them automatically |
| Providers, API keys, and selected models | Codex HOME on the original server | Reconnect to the same account and directory; no re-entry is needed if server data is intact |
| Sub2API accounts, groups, keys, and databases | The computer or service running Sub2API | For the tunnel path, back up and restore separately, or configure a fresh instance |
| Personal manager migration export | Private backup on the old computer | Can carry connection settings, but not SSH private keys or Sub2API databases; never publish it |

For Sub2API backup, stop services and consistently back up `.env`, `data`, `postgres_data`, and `redis_data` from the deployment directory, or use the supported database backup method. Preserve encryption keys and compatible versions when restoring. Do not copy a running database's data directory; follow upstream migration requirements for database version changes.

If a fresh instance creates a different Sub2API key, edit the tunnel provider's Key in the manager. An old key remains valid only when its data is correctly restored or the original instance still serves it. Direct providers do not depend on local Sub2API and do not need that part of the migration.

See the [user guide (中文)](USER_GUIDE.md) for subsequent operations.

Saving edits to the active provider automatically applies them and refreshes the selected models. New or inactive providers never activate on save. Active tasks block application and leave the previous configuration unchanged; save again after tasks finish. Re-enabling an unchanged active configuration only verifies it; endpoint or catalogue changes still require a remote service reload.
