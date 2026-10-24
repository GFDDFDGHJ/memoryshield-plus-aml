# Supervise the shared URL transport only; never stop either memory backend.
$ErrorActionPreference = 'Stop'
$dir = [IO.Path]::GetFullPath($PSScriptRoot)
Set-Location -LiteralPath $dir
$py = if ($env:AML_GATEWAY_PYTHON) { $env:AML_GATEWAY_PYTHON } else {
    'C:\Users\Administrator\AppData\Local\Doubao\User Data\sandbox_runtime\bases\772984f6fd3a8ae0ae7d2422cd7c2bfa\python\python.exe'
}
$ts = 'C:\Program Files\Tailscale\tailscale.exe'
$endpoint = 'hlq5070.tail50f4d4.ts.net:443'
$desired = 'http://127.0.0.1:8282'
function Log([string]$message) { Add-Content -LiteralPath (Join-Path $dir 'service.log') -Value "$(Get-Date -Format s) $message" }
function Healthy {
    try {
        $state = Invoke-RestMethod -Uri 'http://127.0.0.1:8282/__gateway_health' -TimeoutSec 4
        return ($state.status -eq 'ok' -and $state.system -eq 'MemoryShield+ track gateway' -and $state.version -eq 'v1.0.0')
    } catch { return $false }
}
function Gateway-Processes {
    $pattern = '(?i)--app-dir\s+"?' + [regex]::Escape($dir) + '(?:"|\s|$)'
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object {
        $_.CommandLine -match '\buvicorn\s+gateway:app\b' -and $_.CommandLine -match $pattern -and
        $_.CommandLine -match '--port\s+8282(?:\s|$)'
    }
}
function Reconcile-Funnel {
    # Check the exact 443 mapping, including after the old Textual supervisor
    # restores its historical 8080 fallback following a Funnel configuration loss.
    $raw = & $ts funnel status --json 2>$null | Out-String
    if ($LASTEXITCODE -ne 0) { throw 'Tailscale status is unavailable' }
    $state = $raw | ConvertFrom-Json
    if ($state.Web.$endpoint.Handlers.'/'.Proxy -ne $desired -or $state.AllowFunnel.$endpoint -ne $true) {
        & $ts funnel --bg --https=443 --yes $desired *> $null
        if ($LASTEXITCODE -ne 0) { throw 'Shared Funnel route update failed' }
        Log 'Public 443 routes through the shared credential gateway'
    }
}
$lease = $null
try {
    $lease = [IO.File]::Open((Join-Path $dir 'watcher.lock'),[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::Read)
} catch { exit 0 }
try {
    & $py -c "from gateway import load_settings; load_settings(); import fastapi,httpx,uvicorn" *> $null
    if ($LASTEXITCODE -ne 0) { throw 'Gateway configuration preflight failed' }
    Log "Shared gateway supervisor started pid=$PID"
    $failures = 0
    while ($true) {
        try {
            if (Healthy) { $failures = 0; Reconcile-Funnel } else {
                $failures++
                $owned = @(Gateway-Processes)
                if ($owned.Count -gt 1) { throw 'Multiple gateway processes; leaving them unchanged' }
                if ($owned.Count -eq 0 -or $failures -ge 3) {
                    $owned | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
                    $listener = New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback,8282)
                    try { $listener.Start() } finally { $listener.Stop() }
                    $arguments = '-m uvicorn gateway:app --app-dir "'+$dir+'" --host 127.0.0.1 --port 8282'
                    Start-Process -FilePath $py -ArgumentList $arguments -WorkingDirectory $dir `
                        -RedirectStandardOutput (Join-Path $dir 'server.out.log') `
                        -RedirectStandardError (Join-Path $dir 'server.err.log') -WindowStyle Hidden
                    Log 'Shared gateway started on loopback 8282'
                    Start-Sleep -Seconds 3
                    if (Healthy) { $failures = 0; Reconcile-Funnel }
                }
            }
        } catch { Log ('Gateway supervisor check failed: '+$_.Exception.Message) }
        Start-Sleep -Seconds 20
    }
} finally { if ($lease) { $lease.Dispose() } }
