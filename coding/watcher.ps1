# Coding-only supervisor. Textual processes, files and Funnel 443 are untouched.
$ErrorActionPreference = 'Stop'
$dir = [IO.Path]::GetFullPath($PSScriptRoot)
Set-Location -LiteralPath $dir
$expectedVersion = 'v1.0.1'
$py = if ($env:CODING_PYTHON) { $env:CODING_PYTHON } else {
    'C:\Users\Administrator\AppData\Local\Doubao\User Data\sandbox_runtime\bases\772984f6fd3a8ae0ae7d2422cd7c2bfa\python\python.exe'
}
$log = Join-Path $dir 'service.log'
function Log([string]$message) { Add-Content -LiteralPath $log -Value "$(Get-Date -Format s) $message" }
function Configure-Coding {
    $taskKey = [Environment]::GetEnvironmentVariable('DASHSCOPE_API_KEY','User')
    if ([string]::IsNullOrWhiteSpace($taskKey)) { $taskKey = [Environment]::GetEnvironmentVariable('DASHSCOPE_API_KEY','Machine') }
    if (-not [string]::IsNullOrWhiteSpace($taskKey)) { $env:CODING_EMBEDDING_API_KEY = $taskKey }
    $env:CODING_DB_PATH = Join-Path $dir 'aml_coding_v4.db'
    $env:CODING_AUTH_TOKEN = (Get-Content -LiteralPath (Join-Path $dir '.secrets/auth_token.txt') -Raw).Trim()
    $env:CODING_RERANK = 'true'
    $releaseFile = Join-Path $dir 'deployment.json'
    if (Test-Path -LiteralPath $releaseFile) {
        $env:CODING_SOURCE_REVISION = (Get-Content -LiteralPath $releaseFile -Raw | ConvertFrom-Json).commit
    }
    & $py -c "from coding_config import CONFIG; import fastapi,httpx,uvicorn; assert CONFIG.auth_token and CONFIG.embedding_key; assert CONFIG.version=='v1.0.1' and CONFIG.dim==1024" *> $null
    if ($LASTEXITCODE -ne 0) { throw 'Coding startup configuration failed' }
}
function Healthy {
    try {
        $state = Invoke-RestMethod -Uri 'http://127.0.0.1:8181/health' -TimeoutSec 4
        return ($state.status -eq 'ok' -and $state.track -eq 'coding' -and $state.version -eq $expectedVersion)
    } catch { return $false }
}
function Coding-Processes {
    $pattern = '(?i)--app-dir\s+"?' + [regex]::Escape($dir) + '(?:"|\s|$)'
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object {
        $_.CommandLine -match '\buvicorn\s+app:app\b' -and $_.CommandLine -match $pattern
    }
}
$lease = $null
try {
    $lease = [IO.File]::Open((Join-Path $dir 'watcher.lock'),[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::Read)
} catch { exit 0 }
try {
    Configure-Coding
    Log "Coding supervisor started pid=$PID version=$expectedVersion"
    $failures = 0
    while ($true) {
        try {
            if (Healthy) { $failures = 0 } else {
                $failures++
                $owned = @(Coding-Processes)
                if ($owned.Count -gt 1) { throw 'Multiple Coding API processes; leaving them unchanged' }
                if ($owned.Count -eq 0 -or $failures -ge 3) {
                    $owned | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
                    Configure-Coding
                    $listener = New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback,8181)
                    try { $listener.Start() } finally { $listener.Stop() }
                    $arguments = '-m uvicorn app:app --app-dir "'+$dir+'" --host 127.0.0.1 --port 8181'
                    Start-Process -FilePath $py -ArgumentList $arguments -WorkingDirectory $dir `
                        -RedirectStandardOutput (Join-Path $dir 'server.out.log') `
                        -RedirectStandardError (Join-Path $dir 'server.err.log') -WindowStyle Hidden
                    Log 'Coding API started on loopback 8181'
                    Start-Sleep -Seconds 5
                }
            }
        } catch { Log ('Coding supervisor check failed: '+$_.Exception.Message) }
        Start-Sleep -Seconds 20
    }
} finally { if ($lease) { $lease.Dispose() } }
