# Start the AML submission server on 0.0.0.0:8080.
# The auth token and DashScope key are read from .secrets/ automatically.
Set-Location -Path $PSScriptRoot

function Update-DashScopeEnvironment {
    # Refresh inherited values; explicit EMBEDDING_* overrides stay untouched.
    foreach ($name in @('DASHSCOPE_API_KEY', 'DASHSCOPE_BASE_URL')) {
        $value = [Environment]::GetEnvironmentVariable($name, 'User')
        if ([string]::IsNullOrWhiteSpace($value)) {
            $value = [Environment]::GetEnvironmentVariable($name, 'Machine')
        }
        if (-not [string]::IsNullOrWhiteSpace($value)) {
            [Environment]::SetEnvironmentVariable($name, $value, 'Process')
        }
    }
}

Update-DashScopeEnvironment

$py = if ($env:AML_PYTHON) { $env:AML_PYTHON }
      elseif (Test-Path -LiteralPath '.venv\Scripts\python.exe') { '.venv\Scripts\python.exe' }
      else { 'python' }
Write-Host "Using python: $py"
& $py -c "from config import CONFIG; import sqlite_vec,uvicorn; assert CONFIG.auth_token, 'Configure AML_AUTH_TOKEN or .secrets/aml_auth_token.txt'; assert CONFIG.embedding_api_key, 'Configure a DashScope embedding key'"
if ($LASTEXITCODE -ne 0) { throw 'Startup configuration check failed' }
$hostName = if ($env:AML_HOST) { $env:AML_HOST } else { '0.0.0.0' }
$portNumber = if ($env:AML_PORT) { $env:AML_PORT } else { '8080' }
& $py -m uvicorn app:app --host $hostName --port $portNumber
