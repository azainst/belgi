@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import sys; assert sys.version_info >= (3, 10)" >nul 2>nul
  if not errorlevel 1 goto existing
)
python -c "import sys; assert sys.version_info >= (3, 10)" >nul 2>nul
if not errorlevel 1 goto python
py -3 -c "import sys; assert sys.version_info >= (3, 10)" >nul 2>nul
if not errorlevel 1 goto launcher
echo Python 3.10+ not found. Install Python from https://www.python.org/downloads/windows/
pause
exit /b 1
:existing
".venv\Scripts\python.exe" tools\launch.py
goto done
:python
python tools\launch.py
goto done
:launcher
py -3 tools\launch.py
:done
set "RESULT=%errorlevel%"
if not "%RESULT%"=="0" echo Startup failed. See the error above.
pause
exit /b %RESULT%
