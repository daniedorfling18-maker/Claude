@echo off
setlocal
cd /d "%~dp0"
if "%POLYMARKET_SCAN_QUERY_MODE%"=="" set POLYMARKET_SCAN_QUERY_MODE=rotate
if "%POLYMARKET_MAX_SCAN_QUERIES%"=="" set POLYMARKET_MAX_SCAN_QUERIES=1
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_polymarket_local_live.ps1" -ForceRestart
pause
