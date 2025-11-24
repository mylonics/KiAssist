@echo off
REM Startup script for KiAssist (Windows)

echo Starting KiAssist...

REM Create virtual environment if it doesn't exist
if not exist "venv\" (
    echo Creating Python virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo Error: Failed to create virtual environment. Make sure Python is installed.
        exit /b 1
    )
)

REM Activate virtual environment
call venv\Scripts\activate.bat
if errorlevel 1 (
    echo Error: Failed to activate virtual environment.
    exit /b 1
)

REM Check if dist directory exists
if not exist "dist\" (
    echo Building frontend...
    call npm run build
)

REM Install Python dependencies if needed
echo Checking Python dependencies...
cd python-lib
pip install -e . >nul 2>&1
if errorlevel 1 (
    echo Warning: Failed to install Python dependencies. Trying without --editable...
    pip install .
    if errorlevel 1 (
        echo Error: Failed to install Python dependencies.
        cd ..
        exit /b 1
    )
)
cd ..

REM Run the Python application
echo Launching application...
python -m kiassist_utils.main
