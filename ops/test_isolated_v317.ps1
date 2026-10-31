#requires -Version 5.1
# Fresh process only. Invented environment/health/processes; never run the watcher.
param([string]$PythonExe = '', [string]$SourceRoot = '')
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$script:checks = 0
function Assert-Check($Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
    $script:checks++
}
$sourcePath = Join-Path $PSScriptRoot 'watch-isolated-v317.ps1'
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($sourcePath, [ref]$tokens, [ref]$errors)
Assert-Check (@($errors).Count -eq 0) 'Watcher syntax failed.'
$source = $ast.Extent.Text
Assert-Check ($source -match '\$portNumber\s*=\s*8480') 'Dedicated port missing.'
Assert-Check ($source -match "\`$expectedVersion\s*=\s*'v3\.17\.0'") 'Method version missing.'
Assert-Check ($source -match "\`$bindAddress\s*=\s*'100\.93\.38\.9'") 'Dedicated binding missing.'
Assert-Check ($source -match '-WindowStyle Hidden') 'Child launch is not hidden.'
Assert-Check ($source -match 'deployments\\textual-v3\.16\.0') 'Old runtime protection missing.'
Assert-Check ($source -notmatch '(?m)^\s*[^#\r\n]*(?:tailscale\s+funnel|Set-NetFirewall|New-NetFirewall)') 'Unexpected public route mutation.'
foreach ($name in @('Update-PrivateEnvironment', 'Get-VerifiedProcess', 'Test-ApiHealth')) {
    $functions = @($ast.FindAll({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq $name }, $true))
    Assert-Check ($functions.Count -eq 1) ('Expected one function: ' + $name)
    $functionSource = $functions[0].Extent.Text
    if ($name -eq 'Update-PrivateEnvironment') {
        $functionSource = $functionSource.Replace("[Environment]::GetEnvironmentVariable(`$name, 'User')", "`$script:mockUser[`$name]").Replace("[Environment]::GetEnvironmentVariable(`$name, 'Machine')", "`$script:mockMachine[`$name]")
        Assert-Check ($functionSource -notmatch 'GetEnvironmentVariable') 'Persistent credential mock incomplete.'
    }
    . ([scriptblock]::Create($functionSource))
}
$fixtureRoot = [IO.Path]::GetFullPath((Join-Path ([IO.Path]::GetTempPath()) ('aml-v317-watcher-offline-' + [guid]::NewGuid().ToString('N'))))
try {
    New-Item -ItemType Directory -Path (Join-Path $fixtureRoot '.secrets') -Force | Out-Null
    [IO.File]::WriteAllText((Join-Path $fixtureRoot '.secrets\aml_auth_token.txt'), 'invented-auth')
    $runtimePath = $fixtureRoot
    $bindAddress = '100.93.38.9'; $portNumber = 8480; $expectedVersion = 'v3.17.0'
    $script:mockUser = @{ OPENAI_API_KEY = 'invented-user-openai'; DASHSCOPE_API_KEY = 'invented-user-dashscope' }
    $script:mockMachine = @{ OPENAI_API_KEY = 'invented-machine-openai'; DASHSCOPE_API_KEY = 'invented-machine-dashscope' }
    $env:AML_ADD_SELECTION_FAILURE_POLICY = 'strict'
    Assert-Check (Update-PrivateEnvironment) 'Invented environment refresh failed.'
    Assert-Check ($env:OPENAI_API_KEY -eq 'invented-user-openai') 'User credential priority changed.'
    Assert-Check ($env:EMBEDDING_API_KEY -eq 'invented-user-dashscope' -and $env:AML_RERANK_API_KEY -eq $env:EMBEDDING_API_KEY) 'Embedding/rerank credentials not aligned.'
    Assert-Check ($env:AML_HOST -eq $bindAddress -and $env:AML_PORT -eq '8480') 'Wrong listener environment.'
    Assert-Check ($env:AML_VERSION -eq 'v3.17.0') 'Wrong method environment.'
    Assert-Check ($env:AML_ADD_SELECTION_FAILURE_POLICY -eq 'raw_on_unmatched') 'Actual policy not pinned.'
    Assert-Check ($env:AML_DB_PATH -eq (Join-Path $fixtureRoot 'aml_text_gpt_v317.db')) 'Wrong independent store path.'
    Assert-Check ($env:AML_ADD_LLM_CONCURRENCY -eq '2' -and $env:AML_ADD_LLM_TIMEOUT -eq '60' -and $env:AML_ADD_LLM_TOTAL_TIMEOUT -eq '180') 'Model concurrency or timeout changed.'
    Assert-Check ($env:EMBEDDING_TIMEOUT -eq '45' -and $env:AML_RERANK -eq 'true' -and $env:AML_RERANK_MODEL -eq 'qwen3-rerank') 'Embedding/rerank configuration changed.'
    $script:mockUser.Clear()
    Assert-Check (Update-PrivateEnvironment) 'Machine fallback refresh failed.'
    Assert-Check ($env:OPENAI_API_KEY -eq 'invented-machine-openai') 'Machine fallback changed.'

    # Only fabricated process metadata enters the ownership verifier.
    $pythonPath = Join-Path $fixtureRoot 'invented-python.exe'
    $created = [datetime]'2026-10-31T00:00:00Z'
    $script:fakeProcess = [pscustomobject]@{
        ExecutablePath = $pythonPath; ProcessId = 987654; CreationDate = $created
        CommandLine = ('"' + $pythonPath + '" -m uvicorn app:app --app-dir "' + $runtimePath + '" --host 100.93.38.9 --port 8480 --workers 1')
    }
    function Get-CimInstance { param($ClassName, $Filter, $ErrorAction) return $script:fakeProcess }
    Assert-Check ($null -ne (Get-VerifiedProcess 987654)) 'Dedicated child was not recognized.'
    $script:fakeProcess.CommandLine = $script:fakeProcess.CommandLine.Replace('--port 8480', '--port 8380')
    Assert-Check ($null -eq (Get-VerifiedProcess 987654)) 'Old 8380 process could be claimed.'
    $script:fakeProcess.CommandLine = $script:fakeProcess.CommandLine.Replace('--port 8380', '--port 8480').Replace($runtimePath, (Join-Path $fixtureRoot 'other-runtime'))
    Assert-Check ($null -eq (Get-VerifiedProcess 987654)) 'Another runtime could be claimed.'

    # Task-shaped objects implement the health interface without sockets.
    function New-ResolvedTask($Value) {
        $task = [pscustomobject]@{ Value = $Value }
        $task | Add-Member ScriptMethod GetAwaiter { return $this }
        $task | Add-Member ScriptMethod GetResult { return $this.Value }
        return $task
    }
    $httpClient = [pscustomobject]@{}
    $httpClient | Add-Member ScriptMethod GetAsync { param($Address) $script:lastAddress = $Address; return (New-ResolvedTask $script:healthResponse) }
    function Set-FakeHealth($Body) {
        $content = [pscustomobject]@{ Text = ($Body | ConvertTo-Json -Compress) }
        $content | Add-Member ScriptMethod ReadAsStringAsync { return (New-ResolvedTask $this.Text) }
        $script:healthResponse = [pscustomobject]@{ StatusCode = 200; Content = $content }
        $script:healthResponse | Add-Member ScriptMethod Dispose { }
    }
    $body = @{ status = 'ok'; version = 'v3.17.0'; embedding_model = 'text-embedding-v4'; add_model = 'gpt-4o-mini'; add_model_required = $true; add_model_configured = $true; add_selection_failure_policy = 'raw_on_unmatched' }
    Set-FakeHealth $body
    Assert-Check (Test-ApiHealth) 'Correct health was rejected.'
    Assert-Check ($script:lastAddress -eq 'http://100.93.38.9:8480/health') 'Health addressed another listener.'
    $body.add_selection_failure_policy = 'strict'; Set-FakeHealth $body
    Assert-Check (-not (Test-ApiHealth)) 'Strict policy incorrectly considered ready.'
    $body.add_selection_failure_policy = 'raw_on_unmatched'; $body.version = 'v3.16.0'; Set-FakeHealth $body
    Assert-Check (-not (Test-ApiHealth)) 'Old method incorrectly considered ready.'
    Assert-Check (-not (Test-Path -LiteralPath (Join-Path $fixtureRoot 'aml_text_gpt_v317.db'))) 'Offline test created a store.'

    if ($PythonExe -and $SourceRoot) {
        foreach ($name in @('config.py', 'add_memory.py', 'add_index.py')) {
            Copy-Item -LiteralPath (Join-Path $SourceRoot $name) -Destination $fixtureRoot
        }
        $guard = @'
import socket, sqlite3
def blocked(*args, **kwargs):
    raise RuntimeError('Offline watcher preflight attempted network or DB access')
socket.socket.connect = blocked
socket.socket.connect_ex = blocked
socket.create_connection = blocked
sqlite3.connect = blocked
'@
        [IO.File]::WriteAllText((Join-Path $fixtureRoot 'sitecustomize.py'), $guard)
        $pythonChecks = @($ast.FindAll({ param($node) $node -is [Management.Automation.Language.StringConstantExpressionAst] -and $node.Value.StartsWith('import os, sys, fastapi') }, $true))
        Assert-Check ($pythonChecks.Count -eq 1) 'Expected one Python preflight.'
        Push-Location -LiteralPath $fixtureRoot
        try { & $PythonExe -B -c $pythonChecks[0].Value $fixtureRoot *> $null; $preflightExit = $LASTEXITCODE }
        finally { Pop-Location }
        Assert-Check ($preflightExit -eq 0) 'Actual isolated Python preflight failed.'
        Assert-Check (@(Get-ChildItem -LiteralPath $fixtureRoot -Recurse -Filter '*.db').Count -eq 0) 'Preflight created a database.'
    }
    Write-Host ('PASS isolated v3.17 watcher: ' + $script:checks + ' checks; no service, provider, real credentials, or operational database used.')
} finally {
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if (-not $fixtureRoot.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -or
        [IO.Path]::GetFileName($fixtureRoot) -notmatch '^aml-v317-watcher-offline-[0-9a-f]{32}$') {
        throw 'Refusing cleanup outside the test-owned temporary directory.'
    }
    Remove-Item -LiteralPath $fixtureRoot -Recurse -Force
}
