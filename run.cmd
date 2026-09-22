@echo off
rem Autorig Workbench: starts the GUI and opens it in the browser.  run.cmd [--models DIR] [--work DIR] [--port N]
rem Needs Python 3.9+ on PATH (py or python) and Blender (found automatically, or set AUTORIG_BLENDER).
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 -m autorig %*
) else (
  python -m autorig %*
)
exit /b %errorlevel%
