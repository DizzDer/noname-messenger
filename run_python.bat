@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if exist "%~dp0.python\python.exe" (
  set "PYTHONPATH=%~dp0.runtime"
  "%~dp0.python\python.exe" %*
) else (
  python %*
)
exit /b %errorlevel%
