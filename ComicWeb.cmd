@echo off
rem comic-comfy: local web UI. Works from any directory.
setlocal
set "ROOT=%~dp0"
if exist "%ROOT%.venv-web\Scripts\python.exe" (
  set "PY=%ROOT%.venv-web\Scripts\python.exe"
) else (
  set "PY=python"
)
"%PY%" "%ROOT%webapp\run.py" %*
set "CODE=%ERRORLEVEL%"
rem 0 = normal, 130 / -1073741510 / 3221225786 = Ctrl+C or Ctrl+Break
if "%CODE%"=="0" exit /b 0
if "%CODE%"=="130" exit /b 0
if "%CODE%"=="-1073741510" exit /b 0
if "%CODE%"=="3221225786" exit /b 0
echo.
echo Could not start. Install dependencies:
echo   python -m venv .venv-web
echo   .venv-web\Scripts\python.exe -m pip install -r requirements-web.txt
echo.
echo Cleaner Ctrl+C: ComicWeb.ps1
pause
exit /b %CODE%
