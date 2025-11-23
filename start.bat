@echo off
REM Startup script for KiAssist (Windows)

echo Starting KiAssist...

REM Check if dist directory exists
if not exist "dist\" (
    echo Building frontend...
    call npm run build
)

REM Install Python dependencies if needed
echo Checking Python dependencies...
cd python-lib
pip install -e . >nul 2>&1 || echo Python dependencies already installed
cd ..

REM Run the Python application
echo Launching application...
python -m kiassist_utils.main
