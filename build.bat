@echo off
REM Build script for creating KiAssist distributable packages (Windows)

echo Building KiAssist for distribution...

REM Step 1: Setup environment (Python venv + npm deps)
echo Step 1: Setting up environment...
if not exist "venv\" (
    call setup_env.bat
    if errorlevel 1 (
        echo ERROR: Environment setup failed.
        exit /b 1
    )
) else (
    call venv\Scripts\activate.bat
    echo Checking Python dependencies...
    cd python-lib
    python -m pip install -e ".[dev,ai]" >nul
    cd ..
    call npm install
)

REM Step 2: Build the frontend
echo Step 2: Building frontend...
call npm run build
if errorlevel 1 (
    echo ERROR: Frontend build failed.
    exit /b 1
)

REM Step 3: Build the executable with PyInstaller
echo Step 3: Building executable...
pyinstaller kiassist.spec --clean
if errorlevel 1 (
    echo ERROR: PyInstaller build failed.
    exit /b 1
)

echo.
echo Build complete!
echo.
echo Distributable files are in the 'dist' directory
