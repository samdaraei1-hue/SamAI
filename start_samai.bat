@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Python virtual environment not found at .venv
  pause
  exit /b 1
)
set PYTHONUTF8=1
".venv\Scripts\python.exe" -m uvicorn dashboard:app --host 127.0.0.1 --port 8000
