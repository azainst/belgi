@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" tools\wait_for_server.py
exit /b %errorlevel%
