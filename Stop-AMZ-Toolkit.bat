@echo off
REM Stop-AMZ-Toolkit.bat - Phase 7.14 Launcher Lite. Double-click this file to stop the toolkit.
REM It stops ONLY the console this launcher started, after proving it is that exact process.
REM It never stops every python.exe and never touches a program it did not start.
setlocal
title AMZ FBM Toolkit - Stop
cd /d "%~dp0"
powershell.exe -NoProfile -NoLogo -ExecutionPolicy Bypass -File "%~dp0Stop-AMZ-Toolkit.ps1"
set "TOOLKIT_RC=%ERRORLEVEL%"
if not "%TOOLKIT_RC%"=="0" (
  echo.
  echo   Press any key to close this window.
  pause >nul
)
endlocal & exit /b %TOOLKIT_RC%
