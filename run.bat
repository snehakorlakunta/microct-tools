@echo off
REM ============================================================================
REM  microCT Segmentation Lab - Windows launcher (run from this folder)
REM  Uses an existing install if present; otherwise builds a local .venv and
REM  installs OFFLINE from .\dependencies when the bundled wheelhouse is present
REM  (falls back to installing from the internet when it is not).
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
set "PY=.venv\Scripts\python.exe"

if exist "dependencies\*.whl" goto offline
echo [setup] installing app from the internet ...
python -m pip install -e .
goto seghint

:offline
echo [setup] bundled wheels found - installing OFFLINE from .\dependencies ...
python -m pip install --no-index --find-links dependencies -e .

:seghint
echo.
echo [note] To run segmentation on THIS machine, install the engine once too.
echo        See DEPENDENCIES.md  - PyTorch + nnU-Net, use a CUDA build of torch.
echo.

:run
echo [start] launching job worker in a new window ...
start "microct-worker" cmd /k "%PY% -m microct_lab.worker"
echo [start] opening browser ...
timeout /t 3 >nul
start "" http://127.0.0.1:8000
echo [start] web server on http://127.0.0.1:8000  -  close this window to stop the app
"%PY%" -m uvicorn microct_lab.main:app --host 127.0.0.1 --port 8000
