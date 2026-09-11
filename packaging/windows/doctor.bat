@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0"

if not exist "python.exe" (
  echo [ERROR] python.exe not found. Please re-extract the whole zip.
  pause
  exit /b 1
)

"python.exe" -X utf8 -m telemsg doctor %*
echo.
pause
