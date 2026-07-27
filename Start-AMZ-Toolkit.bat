@echo off
REM Start-AMZ-Toolkit.bat - Phase 7.14 Launcher Lite. Double-click this file.
REM It starts the local toolkit console and opens your browser once it is genuinely ready.
REM Local only: no connection to Amazon Seller Central is ever made, and no seller-account
REM action of any kind is ever performed.
setlocal
title AMZ FBM Toolkit - Start
cd /d "%~dp0"
powershell.exe -NoProfile -NoLogo -ExecutionPolicy Bypass -File "%~dp0Start-AMZ-Toolkit.ps1"
set "TOOLKIT_RC=%ERRORLEVEL%"
if not "%TOOLKIT_RC%"=="0" (
  echo.
  echo   Press any key to close this window.
  pause >nul
)
endlocal & exit /b %TOOLKIT_RC%
