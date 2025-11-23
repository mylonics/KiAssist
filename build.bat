@echo off
REM Build script for creating KiAssist distributable packages (Windows)

echo Building KiAssist for distribution...

REM Step 1: Install dependencies
echo Step 1: Installing dependencies...
call npm install
cd python-lib
pip install -e .[dev]
cd ..

REM Step 2: Build the frontend
echo Step 2: Building frontend...
call npm run build

REM Step 3: Build the executable with PyInstaller
echo Step 3: Building executable...
pyinstaller kiassist.spec --clean

echo.
echo Build complete!
echo.
echo Distributable files are in the 'dist' directory
