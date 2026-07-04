@echo off
REM media-catalog launcher for Windows.
REM Run this once to install, then again to launch. Double-click to start.

cd /d "%~dp0"

IF NOT EXIST ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    python -m venv .venv
    echo Installing dependencies...
    .venv\Scripts\pip install -r requirements.txt
    echo.
    echo Setup complete. Launching media-catalog...
)

echo Starting media-catalog UI at http://localhost:8503
.venv\Scripts\streamlit run app.py --server.port 8503
pause
