@echo off
cd /d "%~dp0"
python -c "import PIL" >nul 2>nul
if errorlevel 1 (
    echo Installing required packages...
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo Failed to install required packages.
        pause
        exit /b 1
    )
)
for /f "usebackq delims=" %%P in (`powershell -NoProfile -Command "$p=(python -c 'import sys; print(sys.executable)' 2>$null); if ($p) { $w=Join-Path (Split-Path $p) 'pythonw.exe'; if (Test-Path $w) { $w } else { $p } }"`) do set "PYTHON_GUI=%%P"
if defined PYTHON_GUI (
    start "" "%PYTHON_GUI%" "%~dp0screen_share_party.py"
) else (
    start "" pythonw "%~dp0screen_share_party.py"
)
exit /b 0
