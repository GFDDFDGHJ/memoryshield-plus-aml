#requires -Version 5.1
# Foreground portable runtime. Does not alter tunnels, firewall, or other services.
[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)][int]$Port = 18080,
    [ValidateScript({ $ip = $null; [Net.IPAddress]::TryParse($_, [ref]$ip) })]
    [string]$BindAddress = '127.0.0.1',
    [switch]$CheckModels,
    [switch]$CheckConfiguration
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$projectRoot = (Resolve-Path -LiteralPath $projectRoot).ProviderPath

function Get-PortableEnvironmentValue([string]$Name, [string]$Scope) {
    return [Environment]::GetEnvironmentVariable($Name, $Scope)
}

function Get-PortableSecret([string]$Name, [string]$FileName, [string]$Root) {
    $secretPath = Join-Path $Root ('.secrets\' + $FileName)
    if (Test-Path -LiteralPath $secretPath -PathType Leaf) {
        $value = [IO.File]::ReadAllText($secretPath).Trim()
        if ([string]::IsNullOrWhiteSpace($value)) { throw ('Empty credential file: ' + $FileName) }
        return $value
    }
    foreach ($scopeName in @('Process', 'User', 'Machine')) {
        $value = Get-PortableEnvironmentValue $Name $scopeName
        if (-not [string]::IsNullOrWhiteSpace($value)) { return $value.Trim() }
    }
    throw ('Missing credential: ' + $Name + ' / .secrets/' + $FileName)
}

function Set-PortableEnvironment([string]$Root, [int]$PortNumber, [string]$Address) {
    $env:OPENAI_API_KEY = Get-PortableSecret 'OPENAI_API_KEY' 'openai_api_key.txt' $Root
    $env:DASHSCOPE_API_KEY = Get-PortableSecret 'DASHSCOPE_API_KEY' 'dashscope_api_key.txt' $Root
    $env:AML_AUTH_TOKEN = Get-PortableSecret 'AML_AUTH_TOKEN' 'aml_auth_token.txt' $Root
    $settings = @{
        EMBEDDING_API_KEY = $env:DASHSCOPE_API_KEY
        AML_RERANK_API_KEY = $env:DASHSCOPE_API_KEY
        OPENAI_BASE_URL = 'https://api.openai.com/v1'
        OPENAI_API_BASE = 'https://api.openai.com/v1'
        OPENAI_MODEL = 'gpt-4o-mini'
        DASHSCOPE_BASE_URL = 'https://dashscope.aliyuncs.com/compatible-mode/v1'
        EMBEDDING_BASE_URL = 'https://dashscope.aliyuncs.com/compatible-mode/v1'
        EMBEDDING_MODEL = 'text-embedding-v4'; EMBEDDING_MODE = 'standard'
        EMBEDDING_DIM = '1024'; EMBEDDING_BATCH_SIZE = '10'
        EMBEDDING_CONCURRENCY = '8'; EMBEDDING_TIMEOUT = '45'
        AML_ADD_LLM_CONCURRENCY = '2'; AML_ADD_LLM_TIMEOUT = '60'
        AML_ADD_LLM_TOTAL_TIMEOUT = '180'; AML_ADD_LLM_CHUNK_CHARS = '12000'
        AML_ADD_LLM_MAX_OUTPUT_TOKENS = '2048'
        AML_ADD_SELECTION_FAILURE_POLICY = 'raw_on_unmatched'
        AML_RERANK = 'true'; AML_RERANK_MODEL = 'qwen3-rerank'
        AML_RERANK_BASE_URL = 'https://dashscope.aliyuncs.com/api/v1'
        AML_RERANK_TIMEOUT = '6'; AML_GOVERNANCE = 'false'; AML_QUARANTINE = 'false'
        AML_EXTENDED_CONTEXT_CHARS = '160000'; AML_CONTEXT_CONTINUATION_CHARS = '128000'
        AML_RECENCY_WEIGHT = '0.10'; AML_CANDIDATE_POOL = '4000'
        AML_VERSION = 'v3.17.1'; AML_HOST = $Address; AML_PORT = [string]$PortNumber
        AML_DB_PATH = Join-Path $Root 'data\aml_text_gpt_v317.db'
        PYTHONUTF8 = '1'; PYTHONDONTWRITEBYTECODE = '1'
    }
    foreach ($entry in $settings.GetEnumerator()) {
        [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, 'Process')
    }
}

function Invoke-PortablePython([string[]]$Arguments, [switch]$Quiet) {
    if ($Quiet) { & $script:pythonPath @Arguments *> $null }
    else { & $script:pythonPath @Arguments }
    if ($LASTEXITCODE -ne 0) { throw 'Python operation failed. Check dependencies, configuration, and model access.' }
}

$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $venvPython -PathType Leaf) { $script:pythonPath = $venvPython }
elseif (-not [string]::IsNullOrWhiteSpace($env:AML_PYTHON)) {
    $script:pythonPath = (Resolve-Path -LiteralPath $env:AML_PYTHON).ProviderPath
} else {
    $script:pythonPath = (Get-Command python -CommandType Application -ErrorAction Stop).Source
}
if (-not (Test-Path -LiteralPath $script:pythonPath -PathType Leaf)) { throw 'Python executable is unavailable.' }

Set-PortableEnvironment $projectRoot $Port $BindAddress
$preflight = @'
import os, sys, fastapi, httpx, openai, sqlite_vec, uvicorn
from config import CONFIG as c
from add_memory import AddMemoryExtractor
from add_index import ensure_schema, store_extraction, rank_source_boosts
assert sys.version_info >= (3, 10), 'Python 3.10 or newer is required'
assert c.auth_token and c.embedding_api_key and c.add_llm_api_key
assert c.version == 'v3.17.1' and c.host == sys.argv[2] and c.port == int(sys.argv[3])
assert os.path.normcase(os.path.abspath(c.db_path)) == os.path.normcase(os.path.abspath(os.path.join(sys.argv[1], 'data', 'aml_text_gpt_v317.db')))
assert c.add_llm_base_url == 'https://api.openai.com/v1' and c.add_llm_model == 'gpt-4o-mini'
assert c.embedding_base_url == 'https://dashscope.aliyuncs.com/compatible-mode/v1'
assert c.embedding_model == 'text-embedding-v4' and c.embedding_mode == 'standard' and c.embedding_dim == 1024
assert c.embedding_batch_size == 10 and c.embedding_concurrency == 8 and c.embedding_timeout == 45
assert c.add_llm_concurrency == 2 and c.add_llm_timeout == 60 and c.add_llm_total_timeout == 180
assert c.add_llm_chunk_chars == 12000 and c.add_llm_max_output_tokens == 2048
assert c.rerank_enabled and c.rerank_model == 'qwen3-rerank' and c.rerank_timeout == 6
assert c.rerank_base_url == 'https://dashscope.aliyuncs.com/api/v1' and c.rerank_api_key == c.embedding_api_key
assert not c.governance_enabled and not c.quarantine_enabled
assert c.extended_context_chars == 160000 and c.context_continuation_chars == 128000
assert c.recency_weight == 0.10 and c.candidate_pool == 4000
assert c.add_selection_failure_policy == 'raw_on_unmatched'
extractor = AddMemoryExtractor(c, selection_failure_policy=c.add_selection_failure_policy)
assert extractor.selection_failure_policy == 'raw_on_unmatched'
'@

Push-Location -LiteralPath $projectRoot
try {
    Invoke-PortablePython -Arguments @('-c', $preflight, $projectRoot, $BindAddress, [string]$Port) -Quiet
    Write-Host ('Configuration ready: v3.17.1, ' + $BindAddress + ':' + $Port)
    Write-Host 'Models: gpt-4o-mini / text-embedding-v4 / qwen3-rerank'
    Write-Host 'Selection policy: raw_on_unmatched'
    Write-Host ('Database: ' + $env:AML_DB_PATH)
    if ($CheckConfiguration) { return }
    if ($CheckModels) {
        Invoke-PortablePython -Arguments @('probe_gpt_add.py', '--allow-small-network-test') -Quiet
        Write-Host 'Small paid model check passed using invented data and a temporary database.'
    }
    New-Item -ItemType Directory -Path (Join-Path $projectRoot 'data') -Force | Out-Null
    Write-Host 'Starting foreground service. Keep this window open; press Ctrl+C to stop.'
    Invoke-PortablePython -Arguments @('-m', 'uvicorn', 'app:app', '--host', $BindAddress, '--port', [string]$Port, '--workers', '1')
} finally {
    Pop-Location
}
