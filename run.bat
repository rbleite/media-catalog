@echo off
REM media-catalog launcher (Windows).
cd /d "%~dp0"
if exist ".venv\Scripts\streamlit.exe" (
    ".venv\Scripts\streamlit.exe" run app.py --server.port 8503
) else (
    python -m streamlit run app.py --server.port 8503
)
pause
