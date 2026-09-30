@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
  set "PY=py -3"
) else (
  where python >nul 2>nul
  if %errorlevel%==0 (
    set "PY=python"
  ) else (
    echo Не найден Python.
    echo Установите его с https://www.python.org/downloads/windows/ — на первом экране
    echo установщика обязательно поставьте галочку "Add python.exe to PATH".
    echo Затем запустите этот файл ещё раз.
    pause
    exit /b 1
  )
)

if not exist .venv (
  echo Первый запуск: создаю окружение ^(1-2 минуты^)...
  %PY% -m venv .venv
  if errorlevel 1 (
    echo Не удалось создать окружение.
    pause
    exit /b 1
  )
)

".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
if errorlevel 1 (
  echo Не удалось установить зависимости. Проверьте интернет.
  pause
  exit /b 1
)

echo.
echo Запускаю сервер...
start "" /B "%~dp0wait_and_open.bat"

".venv\Scripts\python.exe" run.py
pause
