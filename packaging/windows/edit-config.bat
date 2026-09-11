@echo off
setlocal
cd /d "%~dp0"

if not exist ".env" (
  if exist ".env.example" (
    copy /y ".env.example" ".env" >nul
    echo Created a new .env from .env.example
  ) else (
    echo [ERROR] .env.example is missing. Please re-extract the package.
    pause
    exit /b 1
  )
)

echo Opening .env in Notepad...
notepad ".env"
