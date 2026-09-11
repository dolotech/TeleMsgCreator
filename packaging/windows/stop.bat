@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0"

if not exist "tools\stop.ps1" (
  echo [ERROR] tools\stop.ps1 is missing. Please re-extract the package.
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "tools\stop.ps1"
echo.
pause
