@echo off
rem comic-comfy: локальный веб-интерфейс. Работает из любого каталога.
setlocal
set "ROOT=%~dp0"
if exist "%ROOT%.venv-web\Scripts\python.exe" (
  set "PY=%ROOT%.venv-web\Scripts\python.exe"
) else (
  set "PY=python"
)
"%PY%" "%ROOT%webapp\run.py" %*
if errorlevel 1 (
  echo.
  echo Не удалось запустить. Установи зависимости:
  echo   python -m venv .venv-web
  echo   .venv-web\Scripts\python.exe -m pip install -r requirements-web.txt
  pause
)
