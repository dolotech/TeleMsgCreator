@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0"

if not exist "python.exe" (
  echo [ERROR] python.exe not found.
  echo         Please extract the WHOLE zip again, and run this file inside
  echo         the extracted folder. Do not move this .bat out of the folder.
  echo.
  pause
  exit /b 1
)

echo ============================================================
echo   TeleMsgCreator
echo ------------------------------------------------------------
echo   A browser window will open automatically.
echo   The exact address is printed below (port may change if 8765
echo   is already taken by another program).
echo   Keep this window open while you use the editor.
echo   Close this window (or press Ctrl+C) to stop.
echo ============================================================
echo.

"python.exe" -X utf8 -m telemsg serve --open
set EXITCODE=%ERRORLEVEL%

echo.
if not "%EXITCODE%"=="0" (
  echo [ERROR] The program exited with code %EXITCODE%.
  echo         Run doctor.bat to see what is wrong.
) else (
  echo Server stopped.
)
echo.
pause
exit /b %EXITCODE%
