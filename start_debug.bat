@echo off
REM Startup script for KiAssist (Windows) - DEBUG VERSION

echo Starting KiAssist (Debug Mode)...

REM Bootstrap environment if venv doesn't exist
if not exist "venv\" (
    echo No virtual environment found – running first-time setup...
    call setup_env.bat
    if errorlevel 1 (
        echo Error: Environment setup failed.
        pause
        exit /b 1
    )
)

REM Activate virtual environment
call venv\Scripts\activate.bat
if errorlevel 1 (
    echo Error: Failed to activate virtual environment.
    pause
    exit /b 1
)

REM Quick sanity check – correct Python version?
python -c "import sys; v=sys.version_info; exit(0 if v.minor==12 and v.major==3 else 1)" 2>nul
if errorlevel 1 (
    echo Error: venv uses an incompatible Python. Python 3.12 is required.
    echo        Delete venv and re-run setup_env.bat.
    pause
    exit /b 1
)

REM Install / update Python dependencies
echo Checking Python dependencies...
cd python-lib
python -m pip install -e ".[ai]" -q >nul 2>&1
if errorlevel 1 (
    echo Warning: Failed to install Python dependencies.
)
cd ..

REM Build frontend
echo Building frontend...
call npm run build
if errorlevel 1 (
    echo Error: Failed to build frontend.
    pause
    exit /b 1
)

REM Show Python version and packages for debugging
echo.
echo ===== Debug Information =====
python --version
echo Python executable:
python -c "import sys; print(sys.executable)"
echo.
echo Checking llama-cpp-python:
python -c "import llama_cpp; print('  Version:', llama_cpp.__version__); print('  GPU offload:', llama_cpp.llama_supports_gpu_offload())" 2>nul || echo   llama_cpp not installed
echo.
echo Checking keyring availability:
python -c "from kiassist_utils.api_key import ApiKeyStore; store = ApiKeyStore(); print('Keyring available:', store._is_keyring_available())"
echo.
echo Current API key status:
python -c "from kiassist_utils.api_key import ApiKeyStore; store = ApiKeyStore(); print('Has API key:', store.has_api_key())"
echo ============================
echo.

REM Run the Python application
echo Launching application...
echo (Watch for [DEBUG] messages in the console)
echo.
python -m kiassist_utils.main

echo.
echo Application exited. Press any key to close...
pause
