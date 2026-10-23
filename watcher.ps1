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
#   3) checks the fixed HTTPS hostname via public DNS every 120 seconds without
#      treating a public ingress failure as a reason to kill a healthy API
#
# Manual start:  powershell -NoProfile -ExecutionPolicy Bypass -File watcher.ps1

$dir = [IO.Path]::GetFullPath($PSScriptRoot)
Set-Location $dir
$expectedVersion = '2026.10.23-rc8'
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
    $env:AML_RERANK = 'true'
    $env:AML_RERANK_API_KEY = $env:EMBEDDING_API_KEY
    $env:AML_RERANK_BASE_URL = 'https://dashscope.aliyuncs.com/api/v1'
    $env:AML_RERANK_MODEL = 'qwen3-rerank'
    $env:AML_RERANK_TIMEOUT = '6'
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

$script:publicIngressHost = 'hlq5070.tail50f4d4.ts.net'
$script:publicIngressNextCheck = [DateTime]::MinValue
$script:publicIngressAddresses = @()
$script:publicIngressDnsState = $null
$script:publicIngressStates = @{}

function Test-PublicIPv4([string]$Address) {
    # IPAddress.TryParse accepts shorthand such as 127.1; accept dotted quads
    # only, and reject private, shared, documentation and reserved ranges.
    if ($Address -notmatch '^\d{1,3}(?:\.\d{1,3}){3}$') { return $false }
    $parsed = $null
    if (-not [Net.IPAddress]::TryParse($Address, [ref]$parsed) -or
        $parsed.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork) {
        return $false
    }
    if ($Address -cne $parsed.ToString()) { return $false }
    $b = $parsed.GetAddressBytes()
    if ($b[0] -eq 0 -or $b[0] -eq 10 -or $b[0] -eq 127 -or $b[0] -ge 224 -or
        ($b[0] -eq 100 -and $b[1] -ge 64 -and $b[1] -le 127) -or
        ($b[0] -eq 169 -and $b[1] -eq 254) -or
        ($b[0] -eq 172 -and $b[1] -ge 16 -and $b[1] -le 31) -or
        ($b[0] -eq 192 -and $b[1] -eq 168) -or
        ($b[0] -eq 192 -and $b[1] -eq 0 -and $b[2] -in @(0, 2)) -or
        ($b[0] -eq 192 -and $b[1] -eq 88 -and $b[2] -eq 99) -or
        ($b[0] -eq 198 -and $b[1] -in @(18, 19)) -or
        ($b[0] -eq 198 -and $b[1] -eq 51 -and $b[2] -eq 100) -or
        ($b[0] -eq 203 -and $b[1] -eq 0 -and $b[2] -eq 113)) {
        return $false
    }
    return $true
}

function Invoke-PublicCurl([string[]]$CurlArguments) {
    try {
        $curlCommand = Get-Command curl.exe -CommandType Application -ErrorAction Stop | Select-Object -First 1
        $content = (& $curlCommand.Source @CurlArguments 2>&1 | Out-String).Trim()
        return [pscustomobject]@{ ExitCode = $LASTEXITCODE; Content = $content }
    } catch {
        return [pscustomobject]@{ ExitCode = -1; Content = '' }
    }
}

function Get-PublicIngressAddresses {
    # MagicDNS on this machine resolves the hostname to its Tailscale address.
    # Query public recursive resolvers over HTTPS instead, with no proxy. A
    # resolver failure must never fall back to the machine's private DNS answer.
    $urls = @(
        "https://dns.google/resolve?name=$script:publicIngressHost&type=A",
        "https://cloudflare-dns.com/dns-query?name=$script:publicIngressHost&type=A"
    )
    foreach ($url in $urls) {
        $result = Invoke-PublicCurl -CurlArguments @('--silent', '--show-error', '--fail',
            '--noproxy', '*', '--connect-timeout', '4', '--max-time', '8',
            '--header', 'accept: application/dns-json', $url)
        if ($result.ExitCode -ne 0) { continue }
        try {
            $dns = $result.Content | ConvertFrom-Json -ErrorAction Stop
            if ($dns.Status -ne 0) { continue }
            $addresses = @($dns.Answer | Where-Object {
                $_.type -eq 1 -and (Test-PublicIPv4 ([string]$_.data))
            } | ForEach-Object { [string]$_.data } | Sort-Object -Unique)
            if ($addresses.Count -gt 0) { return $addresses }
        } catch {
            # Try the other public resolver; neither response is trusted code.
        }
    }
    return @()
}

function Update-PublicIngressHealth {
    $now = [DateTime]::UtcNow
    if ($now -lt $script:publicIngressNextCheck) { return }
    # Fixed cadence also bounds retries while the public network is failing.
    $script:publicIngressNextCheck = $now.AddSeconds(120)
    $addresses = @(Get-PublicIngressAddresses)
    if ($addresses.Count -gt 0) {
        $newSet = $addresses -join ','
        $oldSet = $script:publicIngressAddresses -join ','
        if ($newSet -ne $oldSet) {
            Write-Log "public ingress DNS: $script:publicIngressHost -> $newSet"
            foreach ($oldAddress in @($script:publicIngressStates.Keys)) {
                if ($oldAddress -notin $addresses) { $script:publicIngressStates.Remove($oldAddress) }
            }
        }
        if ($script:publicIngressDnsState -eq 'failed') {
            Write-Log 'public ingress DoH resolution recovered'
        }
        $script:publicIngressDnsState = 'ok'
        $script:publicIngressAddresses = $addresses
    } else {
        if ($script:publicIngressDnsState -ne 'failed') {
            $fallback = if ($script:publicIngressAddresses.Count) {
                'keeping the last public IPv4 addresses'
            } else { 'no public IPv4 cache; skipping HTTPS probes' }
            Write-Log "public ingress DoH resolution failed; $fallback"
        }
        $script:publicIngressDnsState = 'failed'
    }

    foreach ($address in $script:publicIngressAddresses) {
        # --resolve preserves the URL hostname for SNI and certificate checks.
        # Never use -k, a numeric HTTPS URL, or the local MagicDNS answer here.
        $result = Invoke-PublicCurl -CurlArguments @('--silent', '--show-error', '--fail',
            '--noproxy', '*', '--connect-timeout', '5', '--max-time', '12',
            '--resolve', "${script:publicIngressHost}:443:$address",
            '--write-out', "`n%{http_code}", "https://$script:publicIngressHost/health")
        $state = 'failed'
        if ($result.ExitCode -eq 0) {
            try {
                $split = $result.Content.LastIndexOf("`n")
                if ($split -gt 0 -and $result.Content.Substring($split + 1).Trim() -eq '200') {
                    $body = $result.Content.Substring(0, $split) | ConvertFrom-Json -ErrorAction Stop
                    if ($body.status -eq 'ok' -and $body.version -eq $expectedVersion -and
                        $body.embedding_model -eq $expectedModel) { $state = 'ok' }
                }
            } catch { }
        }
        if ($script:publicIngressStates[$address] -ne $state) {
            if ($state -eq 'ok') {
                Write-Log "public ingress HTTPS healthy: $script:publicIngressHost via $address"
            } else {
                Write-Log "public ingress HTTPS failed: $script:publicIngressHost via $address (curl exit $($result.ExitCode)); leaving local API running"
            }
            $script:publicIngressStates[$address] = $state
        }
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
import sys, fastapi, httpx, openai, sqlite_vec, uvicorn
from config import CONFIG
assert CONFIG.embedding_api_key and CONFIG.auth_token
assert CONFIG.embedding_model == 'text-embedding-v4'
assert CONFIG.embedding_mode == 'standard' and CONFIG.embedding_dim == 1024
assert CONFIG.embedding_base_url == 'https://dashscope.aliyuncs.com/compatible-mode/v1'
assert CONFIG.version == sys.argv[1]
'@
    & $py -c $check $expectedVersion *> $null
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

        # Observational keepalive only: local health above remains the sole
        # health signal used to restart uvicorn.
        Update-PublicIngressHealth
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
