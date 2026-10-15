# Start the AML submission server on 0.0.0.0:8080.
# The auth token and Ark key are read from .secrets/ automatically.
Set-Location -Path $PSScriptRoot

# Prefer the bundled Doubao sandbox runtime python that has all deps;
# otherwise fall back to whatever python is on PATH.
$candidates = @(
  (Get-ChildItem "C:\Users\*\AppData\Local\Doubao\User Data\sandbox_runtime\bases\*\python\python.exe" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName),
  "D:\app\Miniconda\python.exe",
  "python"
)
$py = $candidates | Where-Object { $_ } | Select-Object -First 1
Write-Host "Using python: $py"
& $py -m uvicorn app:app --host 0.0.0.0 --port 8080
