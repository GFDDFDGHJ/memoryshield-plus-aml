#requires -Version 5.1
# Run in a fresh PowerShell process. Invented credentials only; no provider calls.
[CmdletBinding()]
param([string]$PythonExe = '')
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$sourcePath = Join-Path $PSScriptRoot 'run-portable.ps1'
$sourceRoot = Split-Path -Parent $PSScriptRoot
$tokens = $null
$parseErrors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($sourcePath, [ref]$tokens, [ref]$parseErrors)
if (@($parseErrors).Count) { throw 'Portable launcher did not parse.' }
foreach ($name in @('Get-PortableSecret', 'Set-PortableEnvironment')) {
    $function = @($ast.FindAll({ param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq $name }, $true))
    if ($function.Count -ne 1) { throw ('Missing function: ' + $name) }
    . ([scriptblock]::Create($function[0].Extent.Text))
}
function Assert-True($Value, [string]$Message) { if (-not $Value) { throw $Message } }
function Assert-Equal($Actual, $Expected, [string]$Message) { if ($Actual -cne $Expected) { throw $Message } }
function Assert-Throws([scriptblock]$Action, [string]$Pattern) {
    $caught = $false
    try { & $Action | Out-Null } catch {
        $caught = $true
        Assert-True ($_.Exception.Message -match $Pattern) 'Unexpected exception.'
    }
    Assert-True $caught 'Expected rejection.'
}
$script:mockEnvironment = @{}
$script:scopeReads = @()
function Get-PortableEnvironmentValue([string]$Name, [string]$Scope) {
    $script:scopeReads += $Scope
    return $script:mockEnvironment[$Scope + ':' + $Name]
}
$fixtureRoot = Join-Path ([IO.Path]::GetTempPath()) ('aml-portable-offline-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path (Join-Path $fixtureRoot '.secrets') -Force | Out-Null
$fixtureRoot = [IO.Path]::GetFullPath($fixtureRoot)
$testsPassed = 0
try {
    $keyPath = Join-Path $fixtureRoot '.secrets\openai_api_key.txt'
    $script:mockEnvironment = @{ 'Process:OPENAI_API_KEY' = 'invented-process'; 'User:OPENAI_API_KEY' = 'invented-user'; 'Machine:OPENAI_API_KEY' = 'invented-machine' }
    [IO.File]::WriteAllText($keyPath, 'invented-file')
    Assert-Equal (Get-PortableSecret 'OPENAI_API_KEY' 'openai_api_key.txt' $fixtureRoot) 'invented-file' 'File priority failed.'
    Assert-Equal $script:scopeReads.Count 0 'File credential unnecessarily read environment.'
    $testsPassed++
    [IO.File]::Delete($keyPath)
    Assert-Equal (Get-PortableSecret 'OPENAI_API_KEY' 'openai_api_key.txt' $fixtureRoot) 'invented-process' 'Process priority failed.'
    $script:mockEnvironment.Remove('Process:OPENAI_API_KEY')
    Assert-Equal (Get-PortableSecret 'OPENAI_API_KEY' 'openai_api_key.txt' $fixtureRoot) 'invented-user' 'User fallback failed.'
    $script:mockEnvironment.Remove('User:OPENAI_API_KEY')
    Assert-Equal (Get-PortableSecret 'OPENAI_API_KEY' 'openai_api_key.txt' $fixtureRoot) 'invented-machine' 'Machine fallback failed.'
    $script:mockEnvironment.Clear()
    Assert-Throws { Get-PortableSecret 'OPENAI_API_KEY' 'openai_api_key.txt' $fixtureRoot } 'Missing credential'
    $testsPassed += 4
    [IO.File]::WriteAllText($keyPath, '  ')
    Assert-Throws { Get-PortableSecret 'OPENAI_API_KEY' 'openai_api_key.txt' $fixtureRoot } 'Empty credential'
    $testsPassed++

    foreach ($item in @(@('openai_api_key.txt','invented-openai'), @('dashscope_api_key.txt','invented-dashscope'), @('aml_auth_token.txt','invented-auth'))) {
        [IO.File]::WriteAllText((Join-Path $fixtureRoot ('.secrets\' + $item[0])), $item[1])
    }
    $env:EMBEDDING_API_KEY = 'invented-stale-ark'
    $env:EMBEDDING_MODEL = 'invented-stale-model'
    $env:EMBEDDING_MODE = 'multimodal'
    $env:EMBEDDING_BASE_URL = 'https://invalid.example'
    $env:AML_GOVERNANCE = 'true'
    $env:AML_EXTENDED_CONTEXT_CHARS = '100'
    $env:AML_CONTEXT_CONTINUATION_CHARS = '200'
    $env:AML_RECENCY_WEIGHT = '9.99'
    $env:AML_CANDIDATE_POOL = '5'
    $env:AML_VERSION = 'v3.16.0'
    $env:AML_ADD_SELECTION_FAILURE_POLICY = 'strict'
    $captured = @(Set-PortableEnvironment $fixtureRoot 18123 '::1' *>&1 | Out-String)
    Assert-True (($captured -join '') -notmatch 'invented-') 'Configuration leaked credentials.'
    Assert-Equal $env:OPENAI_API_KEY 'invented-openai' 'OpenAI credential not loaded.'
    Assert-Equal $env:EMBEDDING_API_KEY 'invented-dashscope' 'Stale embedding credential retained.'
    Assert-Equal $env:AML_RERANK_API_KEY 'invented-dashscope' 'Reranker credential differs.'
    Assert-Equal $env:EMBEDDING_MODEL 'text-embedding-v4' 'Stale model retained.'
    Assert-Equal $env:EMBEDDING_MODE 'standard' 'Stale mode retained.'
    Assert-Equal $env:EMBEDDING_BASE_URL 'https://dashscope.aliyuncs.com/compatible-mode/v1' 'Stale base URL retained.'
    Assert-Equal $env:AML_GOVERNANCE 'false' 'Governance not pinned.'
    Assert-Equal $env:AML_EXTENDED_CONTEXT_CHARS '160000' 'Stale context budget retained.'
    Assert-Equal $env:AML_CONTEXT_CONTINUATION_CHARS '128000' 'Stale continuation budget retained.'
    Assert-Equal $env:AML_RECENCY_WEIGHT '0.10' 'Stale recency weight retained.'
    Assert-Equal $env:AML_CANDIDATE_POOL '4000' 'Stale candidate pool retained.'
    Assert-Equal $env:AML_HOST '::1' 'IPv6 binding failed.'
    Assert-Equal $env:AML_PORT '18123' 'Custom port failed.'
    Assert-Equal $env:AML_VERSION 'v3.17.0' 'Method version not pinned.'
    Assert-Equal $env:AML_ADD_SELECTION_FAILURE_POLICY 'raw_on_unmatched' 'Selection policy not pinned.'
    Assert-Equal $env:AML_DB_PATH (Join-Path $fixtureRoot 'data\aml_text_gpt_v317.db') 'Database isolation failed.'
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $fixtureRoot 'data'))) 'Environment setup created data.'
    $testsPassed += 18

    # Execute the actual launch branch with a fake Python command. No server or
    # provider process is launched. Quiet preflight and one uvicorn worker matter.
    $main = @($ast.FindAll({ param($n) $n -is [Management.Automation.Language.TryStatementAst] -and $n.Body.Extent.Text.Contains('Configuration ready:') }, $true))
    Assert-Equal $main.Count 1 'Main launch branch not found.'
    $script:invocations = @()
    function Invoke-PortablePython([string[]]$Arguments, [switch]$Quiet) {
        $script:invocations += ,@{ Arguments = $Arguments; Quiet = [bool]$Quiet }
    }
    $projectRoot = $fixtureRoot; $Port = 18123; $BindAddress = '0.0.0.0'
    $preflight = 'invented-preflight'; $CheckConfiguration = $true; $CheckModels = $true
    Push-Location $fixtureRoot
    & ([scriptblock]::Create($main[0].Extent.Text)) *> $null
    Assert-Equal $script:invocations.Count 1 'Configuration-only path invoked model/server.'
    Assert-True $script:invocations[0].Quiet 'Preflight output was not suppressed.'
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $fixtureRoot 'data'))) 'Configuration-only path created data.'
    $testsPassed += 3
    $script:invocations = @(); $CheckConfiguration = $false; $CheckModels = $false
    Push-Location $fixtureRoot
    & ([scriptblock]::Create($main[0].Extent.Text)) *> $null
    Assert-Equal $script:invocations.Count 2 'Default path unexpectedly called paid model check.'
    Assert-Equal ($script:invocations[1].Arguments -join '|') '-m|uvicorn|app:app|--host|0.0.0.0|--port|18123|--workers|1' 'Foreground launch arguments incorrect.'
    $testsPassed += 2
    $script:invocations = @(); $CheckModels = $true
    Push-Location $fixtureRoot
    & ([scriptblock]::Create($main[0].Extent.Text)) *> $null
    Assert-Equal $script:invocations.Count 3 'Opt-in paid check was omitted.'
    Assert-Equal ($script:invocations[1].Arguments -join '|') 'probe_gpt_add.py|--allow-small-network-test' 'Paid check did not use existing disposable probe.'
    Assert-True $script:invocations[1].Quiet 'Paid check error output was not suppressed.'
    $testsPassed += 3

    # Parameter validation executes before credential loading.
    Assert-Throws { & $sourcePath -Port 80 -CheckConfiguration } 'Port'
    Assert-Throws { & $sourcePath -BindAddress 'not-an-ip' -CheckConfiguration } 'BindAddress'
    $testsPassed += 2

    if (-not [string]::IsNullOrWhiteSpace($PythonExe)) {
        # Isolated copy: no official DB, no real secret files. Block socket and
        # SQLite connects, so even an accidental import cannot contact/open one.
        $isolatedRoot = Join-Path $fixtureRoot 'preflight-project'
        New-Item -ItemType Directory -Path (Join-Path $isolatedRoot 'ops') -Force | Out-Null
        New-Item -ItemType Directory -Path (Join-Path $isolatedRoot '.secrets') -Force | Out-Null
        Get-ChildItem -LiteralPath $sourceRoot -Filter '*.py' -File | Copy-Item -Destination $isolatedRoot
        Copy-Item -LiteralPath $sourcePath -Destination (Join-Path $isolatedRoot 'ops\run-portable.ps1')
        foreach ($item in @('openai_api_key.txt','dashscope_api_key.txt','aml_auth_token.txt')) {
            [IO.File]::WriteAllText((Join-Path $isolatedRoot ('.secrets\' + $item)), 'invented-offline-only')
        }
        $guard = @'
import socket, sqlite3
def blocked(*args, **kwargs):
    raise RuntimeError('Offline preflight attempted network or database access')
socket.socket.connect = blocked
socket.socket.connect_ex = blocked
socket.create_connection = blocked
sqlite3.connect = blocked
'@
        [IO.File]::WriteAllText((Join-Path $isolatedRoot 'sitecustomize.py'), $guard)
        $env:AML_PYTHON = (Resolve-Path -LiteralPath $PythonExe).ProviderPath
        $actualOutput = (& (Join-Path $isolatedRoot 'ops\run-portable.ps1') -Port 18125 -CheckConfiguration *>&1 | Out-String)
        Assert-True ($actualOutput -match 'Configuration ready: v3.17.0, 127.0.0.1:18125') 'Actual Python preflight failed.'
        Assert-True ($actualOutput -match 'Selection policy: raw_on_unmatched') 'Actual selection policy was not reported.'
        Assert-True ($actualOutput -notmatch 'invented-offline-only') 'Actual preflight leaked a credential.'
        Assert-True (-not (Test-Path -LiteralPath (Join-Path $isolatedRoot 'data'))) 'Actual preflight created a database directory.'
        Assert-True (@(Get-ChildItem -LiteralPath $isolatedRoot -Recurse -Filter '*.db').Count -eq 0) 'Actual preflight created a database.'
        $testsPassed += 5

        # Import only the probe's metadata helpers and the injected-client
        # extractor. No config, deployed data, provider client, or socket use.
        $probeChecks = @'
import logging, socket, sqlite3, sys
from types import SimpleNamespace as NS
def blocked(*args, **kwargs):
    raise RuntimeError('Offline probe checks attempted network or database access')
socket.socket.connect = blocked
socket.socket.connect_ex = blocked
socket.create_connection = blocked
sqlite3.connect = blocked
sys.path.insert(0, sys.argv[1])
from probe_gpt_add import _runtime_metadata, _selection_diagnostics
from add_memory import AddMemoryExtractor
logging.getLogger('add_memory').disabled = True
c = NS(version='invented-method', add_selection_failure_policy='raw_on_unmatched')
store = NS(_add_extractor=NS(selection_failure_policy='strict'))
metadata = _runtime_metadata(c, store)
assert metadata['candidate_version'] == 'invented-method'
assert metadata['selection_failure_policy'] == 'strict' and not metadata['method_policy_ok']
assert _selection_diagnostics([NS(rejected_ungrounded_quotes=2, rejected_source_references=1),
                              NS(rejected_ungrounded_quotes=0, rejected_source_references=2)]) == {
    'rejected_ungrounded_quotes': 2, 'rejected_source_references': 3,
    'completed_extraction_count': 2}
try:
    _selection_diagnostics([NS(rejected_ungrounded_quotes=True, rejected_source_references=0)])
except ValueError:
    pass
else:
    raise AssertionError('Boolean count accepted')
import json
c = NS(add_llm_api_key='', add_llm_base_url='https://api.openai.com/v1',
       add_llm_model='gpt-4o-mini', add_llm_timeout=60, add_llm_total_timeout=180,
       add_llm_concurrency=2, add_llm_chunk_chars=12000, add_llm_max_output_tokens=2048,
       add_selection_failure_policy='raw_on_unmatched')
source = 'Aster\u00a0sample uses cobalt ceramic.'
def create(**kwargs):
    return {'model':'gpt-4o-mini', 'choices':[{'finish_reason':'stop', 'message':{
        'content': json.dumps({'selections':[
            {'message_index':0,'segment_index':0,'quote':'Aster sample','kind':'fact'},
            {'message_index':0,'segment_index':0,'quote':'cobalt ceramic','kind':'fact'}]})}}],
        'usage':{'prompt_tokens':1,'completion_tokens':1}}
extractor = AddMemoryExtractor(c, NS(chat=NS(completions=NS(create=create))))
result = extractor.extract([{'role':'user','content':source}])
assert extractor.selection_failure_policy == 'raw_on_unmatched'
assert len(result.spans) == 1 and source[result.spans[0].start:result.spans[0].end] == 'cobalt ceramic'
assert _selection_diagnostics([result]) == {'rejected_ungrounded_quotes':1,
    'rejected_source_references':0, 'completed_extraction_count':1}
'@
        & $env:AML_PYTHON -B -c $probeChecks $sourceRoot *> $null
        Assert-Equal $LASTEXITCODE 0 'Offline probe metadata/policy/rejection checks failed.'
        $testsPassed += 4
    }
    Write-Host ('PASS portable launcher: ' + $testsPassed + ' checks; invented credentials; no provider calls or server launch.')
} finally {
    # Validate the sole cleanup target before deleting this test-owned directory.
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if (-not $fixtureRoot.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -or
        [IO.Path]::GetFileName($fixtureRoot) -notmatch '^aml-portable-offline-[0-9a-f]{32}$') {
        throw 'Refusing cleanup outside the test temporary directory.'
    }
    Remove-Item -LiteralPath $fixtureRoot -Recurse -Force
}
