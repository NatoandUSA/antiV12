@echo off
REM Open-AMZ-Toolkit.bat - Phase 7.14 Launcher Lite. Double-click this file to open the toolkit
REM in your browser when it is already running. It never starts a server: if the toolkit is not
REM running it tells you to run Start-AMZ-Toolkit first.
setlocal
title AMZ FBM Toolkit - Open
cd /d "%~dp0"
powershell.exe -NoProfile -NoLogo -ExecutionPolicy Bypass -File "%~dp0Open-AMZ-Toolkit.ps1"
set "TOOLKIT_RC=%ERRORLEVEL%"
if not "%TOOLKIT_RC%"=="0" (
  echo.
  echo   Press any key to close this window.
  pause >nul
)
endlocal & exit /b %TOOLKIT_RC%
