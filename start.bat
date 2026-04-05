@echo off
REM Startup script for KiAssist (Windows)

echo Starting KiAssist...

REM Bootstrap environment if venv doesn't exist
if not exist "venv\" (
    echo No virtual environment found – running first-time setup...
    call setup_env.bat
    if errorlevel 1 (
        echo Error: Environment setup failed.
        exit /b 1
    )
)

REM Activate virtual environment
call venv\Scripts\activate.bat
if errorlevel 1 (
    echo Error: Failed to activate virtual environment.
    exit /b 1
)

REM Quick sanity check – correct Python version in venv?
python -c "import sys; v=sys.version_info; exit(0 if v.minor==12 and v.major==3 else 1)" 2>nul
if errorlevel 1 (
    echo Error: venv uses an incompatible Python version. Python 3.12 is required.
    echo        Delete the venv folder and re-run setup_env.bat.
    exit /b 1
)

REM Install / update Python dependencies
echo Checking Python dependencies...
cd python-lib
python -m pip install -e ".[ai]" -q >nul 2>&1
if errorlevel 1 (
    echo Warning: Failed to install Python dependencies.
    cd ..
    exit /b 1
)
cd ..

REM Build frontend
echo Building frontend...
call npm run build
if errorlevel 1 (
    echo Error: Failed to build frontend.
    exit /b 1
)

REM Run the Python application
echo Launching application...
python -m kiassist_utils.main
