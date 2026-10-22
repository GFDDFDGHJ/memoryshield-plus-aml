@echo off
REM One-click launcher for the AML supervisor (watcher.ps1).
REM Run this from a normal console / double-click it. Do NOT rely on a server
REM started from inside a sandboxed shell: it is killed when that shell exits.
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0watcher.ps1"
