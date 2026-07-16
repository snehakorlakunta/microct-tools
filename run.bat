@echo off
REM ============================================================================
REM  microCT Segmentation Lab — Windows launcher (portable: run from anywhere)
REM  First run creates a local .venv and installs the app. Needs internet once.
REM ============================================================================
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [setup] creating virtual environment...
  python -m venv .venv
  call ".venv\Scripts\activate.bat"
  python -m pip install --upgrade pip
  echo [setup] installing app (dashboard)...
  pip install -e .
  echo.
  echo [note] To actually RUN segmentation on this machine, also install the
  echo        compute extra once:   pip install -e ".[seg]"
  echo        (install a CUDA build of torch first for GPU speed.)
  echo.
) else (
  call ".venv\Scripts\activate.bat"
)

if not exist ".env" copy ".env.example" ".env" >nul

echo [start] launching job worker in a new window...
start "microct-worker" cmd /k ".venv\Scripts\microct-worker.exe"

echo [start] opening browser...
timeout /t 3 >nul
start "" http://127.0.0.1:8000

echo [start] web server (close this window to stop the app)
".venv\Scripts\microct-web.exe"
