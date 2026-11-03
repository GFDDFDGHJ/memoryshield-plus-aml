# Dedicated v3.17.1 backend; the v3.16.0 listener and store remain independent.
# Bind only this computer's Tailscale IP. This script never changes Funnel,
# firewall rules, proxies, the legacy service, or any other listener.
[CmdletBinding()]
param(
    [string]$RuntimeRoot = '',
    [string]$PythonExe = $(if ($env:AML_PYTHON) { $env:AML_PYTHON } else {
        'C:\Users\Administrator\AppData\Local\Doubao\User Data\sandbox_runtime\bases\772984f6fd3a8ae0ae7d2422cd7c2bfa\python\python.exe'
    })
)

$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($RuntimeRoot)) {
    $RuntimeRoot = Split-Path -Parent $PSScriptRoot
}
$runtimePath = (Resolve-Path -LiteralPath $RuntimeRoot -ErrorAction Stop).ProviderPath
if (-not (Test-Path -LiteralPath $runtimePath -PathType Container)) {
    throw 'The dedicated runtime directory is unavailable'
}
$runtimePath = [IO.Path]::GetFullPath($runtimePath).TrimEnd('\')
$legacyRoot = 'C:\Users\Administrator\Doubao\chats\2026-10-17\new-chat\aml-server'
foreach ($protectedRoot in @($legacyRoot, (Join-Path $legacyRoot 'deployments\textual-v3.16.0'))) {
    if ([string]::Equals($runtimePath, $protectedRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'An existing service directory cannot be used as this runtime'
    }
}
$pythonPath = (Resolve-Path -LiteralPath $PythonExe -ErrorAction Stop).ProviderPath
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw 'The configured Python executable is unavailable'
}
$pythonPath = [IO.Path]::GetFullPath($pythonPath)
Set-Location -LiteralPath $runtimePath

$bindAddress = '100.93.38.9'
$portNumber = 8480
$expectedDns = 'hlq5070.tail50f4d4.ts.net'
$expectedVersion = 'v3.17.1'
$tailscalePath = 'C:\Program Files\Tailscale\tailscale.exe'
$logPath = Join-Path $runtimePath 'isolated-textual-watcher.log'
$lockPath = Join-Path $runtimePath 'isolated-textual-watcher.lock'
$stdoutPath = Join-Path $runtimePath 'isolated-textual-api.out.log'
$stderrPath = Join-Path $runtimePath 'isolated-textual-api.err.log'
$script:lastState = ''
$script:ownedProcess = $null

function Write-StableLog([string]$Message) {
    try {
        Add-Content -LiteralPath $logPath -Value ("{0}  {1}" -f [DateTime]::UtcNow.ToString('o'), $Message)
        $item = Get-Item -LiteralPath $logPath -ErrorAction Stop
        if ($item.Length -gt 2MB) {
            $tail = Get-Content -LiteralPath $logPath -Tail 500
            Set-Content -LiteralPath $logPath -Value $tail
        }
    } catch { }
}

function Set-WatcherState([string]$Message) {
    if ($script:lastState -ne $Message) {
        Write-StableLog $Message
        $script:lastState = $Message
    }
}

function Test-SelfIdentity {
    if (-not (Test-Path -LiteralPath $tailscalePath -PathType Leaf)) { return $false }
    try {
        # Exclude peers at the CLI; inspect only Self and backend state. Do not
        # log status JSON or any other node's identity/network information.
        $statusText = (& $tailscalePath status --json --peers=false 2>$null | Out-String)
        if ($LASTEXITCODE -ne 0) { return $false }
        $status = $statusText | ConvertFrom-Json -ErrorAction Stop
        $dnsName = ([string]$status.Self.DNSName).TrimEnd('.').ToLowerInvariant()
        return ($status.BackendState -eq 'Running' -and $dnsName -ceq $expectedDns -and
            @($status.Self.TailscaleIPs) -contains $bindAddress)
    } catch { return $false }
}

function Update-PrivateEnvironment {
    foreach ($name in @('OPENAI_API_KEY', 'DASHSCOPE_API_KEY')) {
        $freshValue = [Environment]::GetEnvironmentVariable($name, 'User')
        if ([string]::IsNullOrWhiteSpace($freshValue)) {
            $freshValue = [Environment]::GetEnvironmentVariable($name, 'Machine')
        }
        # Clear stale inherited values as well as refreshing current ones.
        [Environment]::SetEnvironmentVariable($name, $freshValue, 'Process')
    }
    [Environment]::SetEnvironmentVariable('EMBEDDING_API_KEY', $env:DASHSCOPE_API_KEY, 'Process')
    [Environment]::SetEnvironmentVariable('AML_RERANK_API_KEY', $env:DASHSCOPE_API_KEY, 'Process')
    $authPath = Join-Path $runtimePath '.secrets\aml_auth_token.txt'
    if (-not (Test-Path -LiteralPath $authPath -PathType Leaf)) { return $false }
    $authValue = [IO.File]::ReadAllText($authPath).Trim()
    if ([string]::IsNullOrWhiteSpace($authValue)) { return $false }
    [Environment]::SetEnvironmentVariable('AML_AUTH_TOKEN', $authValue, 'Process')
    $authValue = $null
    $freshValue = $null

    $env:AML_HOST = $bindAddress
    $env:AML_PORT = [string]$portNumber
    $env:AML_VERSION = $expectedVersion
    $env:AML_DB_PATH = Join-Path $runtimePath 'aml_text_gpt_v317.db'
    $env:AML_ADD_SELECTION_FAILURE_POLICY = 'raw_on_unmatched'
    $env:DASHSCOPE_BASE_URL = 'https://dashscope.aliyuncs.com/compatible-mode/v1'
    $env:EMBEDDING_BASE_URL = $env:DASHSCOPE_BASE_URL
    $env:EMBEDDING_MODEL = 'text-embedding-v4'
    $env:EMBEDDING_MODE = 'standard'
    $env:EMBEDDING_DIM = '1024'
    $env:EMBEDDING_BATCH_SIZE = '10'
    $env:EMBEDDING_CONCURRENCY = '8'
    $env:EMBEDDING_TIMEOUT = '45'
    $env:AML_ADD_LLM_CONCURRENCY = '2'
    $env:AML_ADD_LLM_TIMEOUT = '60'
    $env:AML_ADD_LLM_TOTAL_TIMEOUT = '180'
    $env:AML_ADD_LLM_CHUNK_CHARS = '12000'
    $env:AML_ADD_LLM_MAX_OUTPUT_TOKENS = '2048'
    $env:AML_RERANK = 'true'
    $env:AML_RERANK_BASE_URL = 'https://dashscope.aliyuncs.com/api/v1'
    $env:AML_RERANK_MODEL = 'qwen3-rerank'
    $env:AML_RERANK_TIMEOUT = '6'
    $env:AML_GOVERNANCE = 'false'
    $env:AML_QUARANTINE = 'false'
    return $true
}

function Test-StartupConfiguration {
    try {
        if (-not (Update-PrivateEnvironment)) { return $false }
        # Import configuration and helper modules only: importing app would
        # create/open its database. This preflight does neither.
        $check = @'
import os, sys, fastapi, httpx, openai, sqlite_vec, uvicorn
from config import CONFIG as c
from add_memory import AddMemoryExtractor
from add_index import ensure_schema, store_extraction, rank_source_boosts
expected_db = os.path.normcase(os.path.abspath(os.path.join(sys.argv[1], 'aml_text_gpt_v317.db')))
assert c.version == 'v3.17.1' and c.host == '100.93.38.9' and c.port == 8480
assert os.path.normcase(os.path.abspath(c.db_path)) == expected_db
assert c.auth_token and c.embedding_api_key and c.add_llm_api_key
assert c.add_llm_base_url == 'https://api.openai.com/v1' and c.add_llm_model == 'gpt-4o-mini'
assert c.embedding_base_url == 'https://dashscope.aliyuncs.com/compatible-mode/v1'
assert c.embedding_model == 'text-embedding-v4' and c.embedding_mode == 'standard' and c.embedding_dim == 1024
assert c.rerank_enabled and c.rerank_model == 'qwen3-rerank'
assert c.add_llm_concurrency == 2 and c.add_llm_timeout == 60 and c.add_llm_total_timeout == 180
assert c.embedding_batch_size == 10 and c.embedding_concurrency == 8 and c.embedding_timeout == 45
assert c.add_selection_failure_policy == 'raw_on_unmatched'
extractor = AddMemoryExtractor(c, selection_failure_policy=c.add_selection_failure_policy)
assert extractor.selection_failure_policy == 'raw_on_unmatched'
'@
        & $pythonPath -c $check $runtimePath *> $null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}

function Get-VerifiedProcess([int]$ProcessId, [long]$CreatedTicks = 0) {
    try {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction Stop
        if (-not $process -or -not $process.ExecutablePath -or -not $process.CommandLine) { return $null }
        if (-not [string]::Equals([IO.Path]::GetFullPath($process.ExecutablePath), $pythonPath,
                [StringComparison]::OrdinalIgnoreCase)) { return $null }
        $command = [string]$process.CommandLine
        if ($command -notmatch '(?:^|\s)-m\s+uvicorn\s+app:app(?:\s|$)' -or
            $command -notmatch '(?:^|\s)--host\s+100\.93\.38\.9(?:\s|$)' -or
            $command -notmatch '(?:^|\s)--port\s+8480(?:\s|$)' -or
            $command -notmatch '(?:^|\s)--workers\s+1(?:\s|$)') { return $null }
        $appDirMatch = [regex]::Match($command, '(?:^|\s)--app-dir\s+(?:"(?<quoted>[^"]+)"|(?<plain>\S+))')
        if (-not $appDirMatch.Success) { return $null }
        $appDir = if ($appDirMatch.Groups['quoted'].Success) {
            $appDirMatch.Groups['quoted'].Value
        } else { $appDirMatch.Groups['plain'].Value }
        if (-not [string]::Equals([IO.Path]::GetFullPath($appDir).TrimEnd('\'), $runtimePath,
                [StringComparison]::OrdinalIgnoreCase)) { return $null }
        $actualTicks = ([datetime]$process.CreationDate).ToUniversalTime().Ticks
        if ($CreatedTicks -ne 0 -and $actualTicks -ne $CreatedTicks) { return $null }
        return [pscustomobject]@{ Id = [int]$process.ProcessId; CreatedTicks = $actualTicks }
    } catch { return $null }
}

function Test-PortOccupied {
    $listener = New-Object Net.Sockets.TcpListener([Net.IPAddress]::Parse($bindAddress), $portNumber)
    try {
        $listener.Start()
        return $false
    } catch { return $true }
    finally { $listener.Stop() }
}

# One lock for this runtime. Its live file handle, rather than stale PID text,
# determines ownership. No global process searches or stop-all operations.
$lockStream = $null
$httpClient = $null
try {
    $lockStream = [IO.File]::Open($lockPath, [IO.FileMode]::OpenOrCreate,
        [IO.FileAccess]::ReadWrite, [IO.FileShare]::Read)
    $lockStream.SetLength(0)
    $pidBytes = [Text.Encoding]::UTF8.GetBytes("$PID`r`n")
    $lockStream.Write($pidBytes, 0, $pidBytes.Length)
    $lockStream.Flush()
} catch {
    if ($lockStream) { $lockStream.Dispose() }
    Write-StableLog 'Dedicated watcher lock unavailable; no service was changed'
    exit 1
}

try {
    Add-Type -AssemblyName System.Net.Http
    $handler = New-Object Net.Http.HttpClientHandler
    $handler.UseProxy = $false
    $handler.AllowAutoRedirect = $false
    $httpClient = New-Object Net.Http.HttpClient($handler)
    $httpClient.Timeout = [TimeSpan]::FromSeconds(5)
    function Test-ApiHealth {
        $response = $null
        try {
            $response = $httpClient.GetAsync("http://${bindAddress}:$portNumber/health").GetAwaiter().GetResult()
            if ([int]$response.StatusCode -ne 200) { return $false }
            $body = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult() | ConvertFrom-Json -ErrorAction Stop
            return ($body.status -eq 'ok' -and $body.version -eq $expectedVersion -and
                $body.embedding_model -eq 'text-embedding-v4' -and
                $body.add_model -eq 'gpt-4o-mini' -and $body.add_model_required -eq $true -and
                $body.add_model_configured -eq $true -and
                $body.add_selection_failure_policy -eq 'raw_on_unmatched')
        } catch { return $false }
        finally { if ($response) { $response.Dispose() } }
    }

    Write-StableLog 'Dedicated textual watcher started; legacy service and public routes are untouched'
    $healthFailures = 0
    while ($true) {
        try {
            if (-not (Test-SelfIdentity)) {
                Set-WatcherState 'Tailscale self identity unavailable or mismatched; no API process changed'
            } elseif (Test-ApiHealth) {
                $healthFailures = 0
                Set-WatcherState 'Dedicated textual API is healthy'
            } else {
                $healthFailures++
                $owned = $null
                if ($script:ownedProcess) {
                    $owned = Get-VerifiedProcess $script:ownedProcess.Id $script:ownedProcess.CreatedTicks
                }
                if ($owned -and $healthFailures -lt 3) {
                    Set-WatcherState 'Dedicated API is starting or temporarily unhealthy; waiting'
                } else {
                    if ($owned) {
                        # Recheck immediately before stopping only this watcher's
                        # exact child and creation time, never an unknown listener.
                        $confirmed = Get-VerifiedProcess $owned.Id $owned.CreatedTicks
                        if ($confirmed) {
                            Stop-Process -Id $confirmed.Id -Force -ErrorAction Stop
                            Write-StableLog 'Restarting the verified dedicated API child after repeated failed health checks'
                            Start-Sleep -Seconds 2
                        }
                    }
                    $script:ownedProcess = $null
                    if (Test-PortOccupied) {
                        Set-WatcherState 'Dedicated port is occupied; its unverified listener was left unchanged'
                    } elseif (-not (Test-StartupConfiguration)) {
                        Set-WatcherState 'Dedicated API preflight failed; check local dependencies and private configuration'
                    } elseif (-not (Test-SelfIdentity)) {
                        Set-WatcherState 'Tailscale self identity changed before launch; API was not started'
                    } else {
                        $arguments = '-m uvicorn app:app --app-dir "' + $runtimePath + '" --host ' +
                            $bindAddress + ' --port ' + $portNumber + ' --workers 1'
                        $child = Start-Process -FilePath $pythonPath -ArgumentList $arguments `
                            -WorkingDirectory $runtimePath -WindowStyle Hidden -PassThru `
                            -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -ErrorAction Stop
                        $script:ownedProcess = Get-VerifiedProcess $child.Id
                        $healthFailures = 0
                        Write-StableLog 'Dedicated API child launched hidden with one worker'
                        Start-Sleep -Seconds 5
                    }
                }
            }
        } catch {
            Set-WatcherState 'Dedicated watcher operation failed; unverified processes and public routes were left unchanged'
        }
        Start-Sleep -Seconds 30
    }
} catch {
    Write-StableLog 'Dedicated watcher initialization failed; no public route was changed'
    exit 1
} finally {
    if ($httpClient) { $httpClient.Dispose() }
    if ($lockStream) { $lockStream.Dispose() }
}
