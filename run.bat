@echo off
REM ============================================================================
REM  microCT Segmentation Lab - Windows launcher (run from this folder)
REM  Uses an existing install if present; otherwise builds a local .venv.
REM ============================================================================
setlocal
cd /d "%~dp0"
if not exist ".env" copy ".env.example" ".env" >nul

set "PY=python"
python -c "import microct_lab" 1>nul 2>nul
if %errorlevel%==0 goto run

if not exist ".venv\Scripts\python.exe" (
  echo [setup] creating virtual environment ...
  python -m venv .venv
)
call ".venv\Scripts\activate.bat"
echo [setup] installing app ...
python -m pip install -e .
set "PY=.venv\Scripts\python.exe"
echo.
echo [note] For segmentation on this machine, also run once: pip install -e ".[seg]"
echo        Install a CUDA build of torch first for GPU speed.
echo.

:run
echo [start] launching job worker in a new window ...
start "microct-worker" cmd /k "%PY% -m microct_lab.worker"
echo [start] opening browser ...
timeout /t 3 >nul
start "" http://127.0.0.1:8000
echo [start] web server on http://127.0.0.1:8000  -  close this window to stop the app
"%PY%" -m uvicorn microct_lab.main:app --host 127.0.0.1 --port 8000
