# AML service supervisor.
#
# Why this exists: uvicorn started from an interactive/sandboxed shell is killed
# as soon as that shell's job object is torn down, so the API silently dies and
# the leaderboard's Add/Search calls start failing. This script is meant to be
# launched by the Windows Task Scheduler (task name: AML-MemoryServer), which
# keeps it outside any interactive session.
#
# It loops forever and:
#   1) probes http://127.0.0.1:8080/health, restarting uvicorn if it is down
#      (HTTP probe, not Get-NetTCPConnection: the latter is unavailable in some
#      sandboxed contexts and silently returns nothing)
#   2) supervises this project's Cloudflare tunnel and existing Tailscale Funnel
#
# Manual start:  powershell -NoProfile -ExecutionPolicy Bypass -File watcher.ps1

$dir = [IO.Path]::GetFullPath($PSScriptRoot)
Set-Location $dir
$expectedVersion = '2026.10.22-rc2'
$expectedModel = 'text-embedding-v4'

function Update-DashScopeEnvironment {
    # Read persisted values before every child launch, even in a long-lived watcher.
    foreach ($name in @('DASHSCOPE_API_KEY')) {
        $value = [Environment]::GetEnvironmentVariable($name, 'User')
        if ([string]::IsNullOrWhiteSpace($value)) {
            $value = [Environment]::GetEnvironmentVariable($name, 'Machine')
        }
        if (-not [string]::IsNullOrWhiteSpace($value)) {
            [Environment]::SetEnvironmentVariable($name, $value, 'Process')
        }
    }
    # This deployment uses the verified DashScope key and an isolated v4 store.
    # Pin public configuration so stale inherited Ark settings cannot leak in.
    if (-not [string]::IsNullOrWhiteSpace($env:DASHSCOPE_API_KEY)) {
        $env:EMBEDDING_API_KEY = $env:DASHSCOPE_API_KEY
    }
    $env:DASHSCOPE_BASE_URL = 'https://dashscope.aliyuncs.com/compatible-mode/v1'
    $env:EMBEDDING_BASE_URL = $env:DASHSCOPE_BASE_URL
    $env:EMBEDDING_MODEL = $expectedModel
    $env:EMBEDDING_MODE = 'standard'
    $env:EMBEDDING_DIM = '1024'
    $env:AML_DB_PATH = Join-Path $dir 'aml_text_v4.db'
    $env:AML_VERSION = $expectedVersion
}

$py = if ($env:AML_PYTHON) { $env:AML_PYTHON } else {
    "C:\Users\Administrator\AppData\Local\Doubao\User Data\sandbox_runtime\bases\772984f6fd3a8ae0ae7d2422cd7c2bfa\python\python.exe"
}

$ts = "C:\Program Files\Tailscale\tailscale.exe"
$cloudflaredPath = Join-Path $dir 'cloudflared.exe'
$log = Join-Path $dir "service.log"
$lock = Join-Path $dir "watcher.lock"

function Write-Log([string]$msg) {
    Add-Content -Path $log -Value "$(Get-Date -Format 's')  $msg"
    $item = Get-Item $log -ErrorAction SilentlyContinue
    if ($item -and $item.Length -gt 2MB) {
        $tail = Get-Content $log -Tail 500
        Set-Content -Path $log -Value $tail
    }
}

function Test-Health {
    try {
        $r = Invoke-WebRequest "http://127.0.0.1:8080/health" -TimeoutSec 5 -UseBasicParsing
        $body = $r.Content | ConvertFrom-Json -ErrorAction Stop
        return ($r.StatusCode -eq 200 -and $body.status -eq 'ok' -and
            $body.version -eq $expectedVersion -and $body.embedding_model -eq $expectedModel)
    } catch {
        return $false
    }
}

function Test-UvicornRunning {
    $procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue
    foreach ($p in $procs) {
        if ($p.CommandLine -and $p.CommandLine -match '\buvicorn\b' -and
            $p.CommandLine -match 'app:app' -and
            $p.CommandLine -match ('--app-dir\s+"?' + [regex]::Escape($dir) + '(?:"|\s|$)')) {
            return $p.ProcessId
        }
    }
    return $null
}

function Get-ProjectCloudflared {
    Get-CimInstance Win32_Process -Filter "Name='cloudflared.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.ExecutablePath -eq $cloudflaredPath -and $_.CommandLine -and
            $_.CommandLine -match '--url\s+"?http://(?:127\.0\.0\.1|localhost):8080(?:"|\s|$)' }
}

function Test-Cloudflared {
    return [bool]@(Get-ProjectCloudflared).Count
}

function Test-PortOccupied {
    # Socket binding also detects an unrelated process when CIM cannot map a listener.
    $listener = New-Object System.Net.Sockets.TcpListener([Net.IPAddress]::Any, 8080)
    try {
        $listener.Start()
        return $false
    } catch [System.Net.Sockets.SocketException] {
        return $true
    } finally {
        $listener.Stop()
    }
}

function Test-StartupConfiguration {
    Update-DashScopeEnvironment
    $check = @'
import fastapi, httpx, openai, sqlite_vec, uvicorn
from config import CONFIG
assert CONFIG.embedding_api_key and CONFIG.auth_token
assert CONFIG.embedding_model == 'text-embedding-v4'
assert CONFIG.embedding_mode == 'standard' and CONFIG.embedding_dim == 1024
assert CONFIG.embedding_base_url == 'https://dashscope.aliyuncs.com/compatible-mode/v1'
assert CONFIG.version == '2026.10.22-rc2'
'@
    & $py -c $check *> $null
    return ($LASTEXITCODE -eq 0)
}

function Update-CloudflaredUrl {
    $file = Join-Path $dir "cf-url.txt"
    $lines = @()
    $lines += Get-Content (Join-Path $dir "cf-tunnel.log") -ErrorAction SilentlyContinue
    $lines += Get-Content (Join-Path $dir "cf-tunnel.err.log") -ErrorAction SilentlyContinue
    $m = $lines | Select-String -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" | Select-Object -Last 1
    if (-not $m) { return }
    $new = $m.Matches[0].Value
    $old = if (Test-Path $file) { (Get-Content $file -Raw).Trim() } else { "" }
    if ($new -ne $old) {
        Set-Content -Path $file -Value $new
        Write-Log "PUBLIC URL (cloudflare): $new"
        if ($old) { Write-Log "WARNING: public URL CHANGED (was $old) -- update the AML submission page" }
    }
}

function Start-Cloudflared {
    Get-ProjectCloudflared | ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 1
    Remove-Item (Join-Path $dir "cf-tunnel.log"), (Join-Path $dir "cf-tunnel.err.log") -Force -ErrorAction SilentlyContinue
    Start-Process -FilePath $cloudflaredPath `
        -ArgumentList 'tunnel --url http://127.0.0.1:8080 --protocol quic --no-autoupdate' `
        -WorkingDirectory $dir `
        -RedirectStandardOutput (Join-Path $dir "cf-tunnel.log") `
        -RedirectStandardError (Join-Path $dir "cf-tunnel.err.log") `
        -WindowStyle Hidden -ErrorAction Stop
    Start-Sleep -Seconds 18
    Update-CloudflaredUrl
}

# Hold the open file for the full lifetime. Stale PID text cannot block a launch,
# and a second writer cannot acquire the same file between check and write.
$lockStream = $null
try {
    $lockStream = [IO.File]::Open($lock, [IO.FileMode]::OpenOrCreate,
        [IO.FileAccess]::ReadWrite, [IO.FileShare]::Read)
    $lockStream.SetLength(0)
    $pidBytes = [Text.Encoding]::UTF8.GetBytes("$PID`r`n")
    $lockStream.Write($pidBytes, 0, $pidBytes.Length)
    $lockStream.Flush()
} catch {
    if ($lockStream) { $lockStream.Dispose() }
    Write-Log 'watcher lock is already held or unavailable; exiting'
    exit 0
}

try {
if (-not (Test-StartupConfiguration)) {
    throw 'Startup configuration check failed; verify Python dependencies, key, and auth locally'
}
Write-Log "watcher started (python=$py, pid=$PID, version=$expectedVersion, model=$expectedModel)"
$healthFails = 0
if (-not (Test-Cloudflared)) { Write-Log "cloudflared not running, starting"; Start-Cloudflared } else { Update-CloudflaredUrl }

while ($true) {
    try {
        if (Test-Health) {
            $healthFails = 0
        } else {
            $healthFails++
            $uvicornPid = Test-UvicornRunning
            if ($uvicornPid -and $healthFails -lt 3) {
                # Still booting / transient: wait one more round before acting.
                Write-Log "health fail #$healthFails, uvicorn pid $uvicornPid alive, waiting"
            } else {
                if ($uvicornPid) {
                    # Process is up but has been unhealthy for >=3 rounds: hung.
                    Write-Log "uvicorn pid $uvicornPid hung, killing"
                    Stop-Process -Id $uvicornPid -Force -ErrorAction SilentlyContinue
                    Start-Sleep -Seconds 2
                }
                if (Test-PortOccupied) {
                    Write-Log 'port 8080 is occupied by an unverified process; leaving it running'
                } elseif (-not (Test-StartupConfiguration)) {
                    Write-Log 'startup configuration check failed; uvicorn was not started'
                } else {
                    Write-Log 'starting uvicorn'
                    $uvicornArguments = '-m uvicorn app:app --app-dir "' + $dir + '" --host 0.0.0.0 --port 8080'
                    Start-Process -FilePath $py `
                        -ArgumentList $uvicornArguments `
                        -WorkingDirectory $dir `
                        -RedirectStandardOutput (Join-Path $dir "server.out.log") `
                        -RedirectStandardError (Join-Path $dir "server.err.log") `
                        -WindowStyle Hidden -ErrorAction Stop
                    Start-Sleep -Seconds 10
                    if (Test-Health) { Write-Log 'uvicorn healthy'; $healthFails = 0 }
                    else { Write-Log 'uvicorn still unhealthy after start' }
                }
            }
        }

        if (-not (Test-Cloudflared)) {
            Write-Log "cloudflared down, restarting"
            Start-Cloudflared
        } else {
            # A quick-tunnel URL can arrive after the initial startup wait.
            Update-CloudflaredUrl
        }

        if (Test-Path $ts) {
            $funnel = & $ts funnel status 2>&1 | Out-String
            if ($LASTEXITCODE -ne 0) {
                # tailscale.exe talks to tailscaled over a named pipe; a failure
                # here can mean the Tailscale *service* stopped, not that Funnel
                # is off. Starting the service is allowed without admin on this
                # box, so try it before giving up.
                $script:funnelWarned = $true
                Write-Log "tailscale unreachable, trying to start the service"
                try {
                    Start-Service Tailscale -ErrorAction Stop
                    Start-Sleep -Seconds 6
                    $funnel = & $ts funnel status 2>&1 | Out-String
                } catch {
                    if ($script:funnelWarned -ne "reported") {
                        Write-Log "cannot start Tailscale service: $($_.Exception.Message)"
                        $script:funnelWarned = "reported"
                    }
                }
            }
            if ($LASTEXITCODE -eq 0 -and $funnel -notmatch "Funnel on") {
                $en = & $ts funnel --bg --yes 8080 2>&1 | Out-String
                if ($LASTEXITCODE -eq 0) {
                    Write-Log "funnel re-enabled"
                    $script:funnelWarned = $false
                } else {
                    Write-Log "funnel enable failed: $($en.Trim())"
                }
            }
        }
    } catch {
        Write-Log "watcher error: $($_.Exception.Message)"
    }

    Start-Sleep -Seconds 30
}
} catch {
    Write-Log "watcher stopped: $($_.Exception.Message)"
    throw
} finally {
    if ($lockStream) { $lockStream.Dispose() }
}
