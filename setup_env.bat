@echo off
REM =============================================================
REM  KiAssist – Python environment bootstrap (Windows)
REM
REM  This script:
REM   1. Locates a compatible Python (3.10 – 3.12).
REM   2. Creates / re-uses a venv in .\venv.
REM   3. Installs all project dependencies (core + ai + local-llm).
REM   4. If the NVIDIA CUDA Toolkit is available (nvcc) it builds
REM      llama-cpp-python with GPU (CUDA) support; otherwise it
REM      installs the CPU-only wheel from PyPI.
REM   5. Installs npm / Node dependencies and builds the frontend.
REM
REM  Usage:
REM    setup_env.bat          – full setup (default)
REM    setup_env.bat --skip-npm   – skip npm install / frontend build
REM =============================================================
setlocal enabledelayedexpansion

set "SKIP_NPM=0"
if "%~1"=="--skip-npm" set "SKIP_NPM=1"

echo.
echo ============================================
echo  KiAssist – Environment Setup
echo ============================================
echo.

REM -------------------------------------------------------
REM  1. Find a compatible Python interpreter (3.10 – 3.12)
REM -------------------------------------------------------
set "PY="

REM Prefer the Python Launcher (py.exe) which ships with
REM the official Windows installer and lets us pick a version.
where py >nul 2>&1
if %errorlevel%==0 (
    py -3.12 --version >nul 2>&1
    if !errorlevel!==0 (
        set "PY=py -3.12"
        echo Found Python 3.12 via py launcher.
    )
)

REM Fallback: plain "python" on PATH if it happens to be 3.12.
if not defined PY (
    python --version >nul 2>&1
    if %errorlevel%==0 (
        for /f "tokens=2 delims= " %%A in ('python --version 2^>^&1') do (
            for /f "tokens=1,2 delims=." %%M in ("%%A") do (
                if %%M==3 if %%N==12 (
                    set "PY=python"
                    echo Found Python 3.12 on PATH.
                )
            )
        )
    )
)

if not defined PY (
    echo ERROR: Python 3.12 not found.
    echo        Install Python 3.12 from https://www.python.org/downloads/
    exit /b 1
)

echo Using: %PY%
%PY% --version

REM -------------------------------------------------------
REM  2. Create virtual environment
REM -------------------------------------------------------
if not exist "venv\" (
    echo.
    echo Creating virtual environment...
    %PY% -m venv venv
    if errorlevel 1 (
        echo ERROR: Failed to create venv.
        exit /b 1
    )
)

call venv\Scripts\activate.bat
if errorlevel 1 (
    echo ERROR: Failed to activate venv.
    exit /b 1
)

echo.
echo Activated venv – Python executable:
python --version
python -c "import sys; print(sys.executable)"

REM -------------------------------------------------------
REM  3. Upgrade pip / setuptools / wheel
REM -------------------------------------------------------
echo.
echo Upgrading pip...
python -m pip install --upgrade pip setuptools wheel >nul

REM -------------------------------------------------------
REM  4. Install project dependencies (core + ai extras)
REM -------------------------------------------------------
echo.
echo Installing project dependencies...
cd python-lib
python -m pip install -e ".[dev,ai]"
if errorlevel 1 (
    echo ERROR: Failed to install project dependencies.
    cd ..
    exit /b 1
)
cd ..

REM -------------------------------------------------------
REM  5. Install llama-cpp-python (with CUDA if available)
REM -------------------------------------------------------
echo.
echo Detecting GPU / CUDA support...

set "HAS_CUDA=0"
set "CUDA_VER="

REM Check for NVIDIA GPU via nvidia-smi
where nvidia-smi >nul 2>&1
if %errorlevel%==0 (
    echo   NVIDIA GPU detected (nvidia-smi found^)
    REM Check for CUDA Toolkit (nvcc)
    where nvcc >nul 2>&1
    if !errorlevel!==0 (
        echo   CUDA Toolkit detected (nvcc found^)
        set "HAS_CUDA=1"
        for /f "tokens=5 delims= " %%V in ('nvcc --version 2^>^&1 ^| findstr /C:"release"') do (
            set "CUDA_VER=%%V"
        )
        echo   CUDA version: !CUDA_VER!
    ) else (
        echo   nvcc not found – will try pre-built CUDA wheel
        REM Even without nvcc, we can use pre-built CUDA wheels if GPU present
        set "HAS_CUDA=1"
    )
) else (
    echo   No NVIDIA GPU detected – installing CPU-only wheel.
)

if "%HAS_CUDA%"=="1" (
    echo.
    echo Installing llama-cpp-python with CUDA GPU support...

    REM Detect GPU architecture and Python version for pre-built wheel selection
    set "GPU_ARCH="
    set "WHEEL_ARCH="
    for /f "tokens=*" %%G in ('nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2^>nul') do (
        set "GPU_CAP=%%G"
    )
    if defined GPU_CAP (
        REM Map compute capability to architecture
        echo   GPU compute capability: !GPU_CAP!
        if "!GPU_CAP:~0,3!"=="8.9" ( set "GPU_ARCH=sm89" & set "WHEEL_ARCH=sm89.ada" )
        if "!GPU_CAP:~0,3!"=="8.6" ( set "GPU_ARCH=sm86" & set "WHEEL_ARCH=sm86.ampere" )
        if "!GPU_CAP:~0,3!"=="7.5" ( set "GPU_ARCH=sm75" & set "WHEEL_ARCH=sm75.turing" )
        if "!GPU_CAP:~0,4!"=="10.0" ( set "GPU_ARCH=sm100" & set "WHEEL_ARCH=sm100.blackwell" )
    )

    REM Detect Python version (e.g., cp312)
    for /f "tokens=2 delims=." %%M in ('python --version 2^>^&1') do (
        for /f "tokens=1 delims=." %%m in ("%%M") do set "PY_MINOR=%%m"
    )
    for /f "tokens=1-2 delims=." %%a in ('python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"') do (
        set "PY_TAG=cp%%a%%b"
    )

    REM Strategy 1: Try pre-built CUDA wheel from dougeeai/llama-cpp-python-wheels (fast, no compile)
    if defined GPU_ARCH (
        set "WHEEL_BASE=https://github.com/dougeeai/llama-cpp-python-wheels/releases/download"
        set "WHEEL_NAME=llama_cpp_python-0.3.16+cuda12.1.!WHEEL_ARCH!-!PY_TAG!-!PY_TAG!-win_amd64.whl"
        set "WHEEL_TAG=v0.3.16-cuda12.1-!GPU_ARCH!-py!PY_TAG:cp=!"
        echo   Trying pre-built CUDA wheel: !WHEEL_NAME!
        python -m pip install "!WHEEL_BASE!/!WHEEL_TAG!/!WHEEL_NAME!" --force-reinstall 2>nul
        if !errorlevel!==0 (
            REM Install server extras
            python -m pip install uvicorn fastapi sse-starlette starlette-context pydantic-settings 2>nul
            REM Verify GPU offload
            python -c "import llama_cpp; assert llama_cpp.llama_supports_gpu_offload(), 'no gpu'" 2>nul
            if !errorlevel!==0 (
                echo   Pre-built CUDA wheel installed with GPU offload!
                goto :llama_done
            )
            echo   Pre-built wheel did not enable GPU offload.
            python -m pip uninstall llama-cpp-python -y >nul 2>&1
        ) else (
            echo   Pre-built wheel not available for this configuration.
        )
    )

    REM Strategy 2: Build from source with CUDA flags (requires nvcc)
    where nvcc >nul 2>&1
    if !errorlevel!==0 (
        echo   Building from source with CUDA support (this may take 10-30 minutes^)...
        set "CMAKE_ARGS=-DGGML_CUDA=on"
        set "FORCE_CMAKE=1"
        python -m pip install "llama-cpp-python[server]>=0.3.16" --force-reinstall --no-binary llama-cpp-python --no-cache-dir
        if !errorlevel!==0 (
            echo   CUDA source build completed!
            set "CMAKE_ARGS="
            set "FORCE_CMAKE="
            goto :llama_done
        )
        echo   WARNING: CUDA build failed.
        set "CMAKE_ARGS="
        set "FORCE_CMAKE="
    )

    REM Strategy 3: Fall back to CPU wheel
    echo   Falling back to CPU-only wheel...
    python -m pip install "llama-cpp-python[server]>=0.3.16"
    if errorlevel 1 (
        echo WARNING: llama-cpp-python installation failed.
        echo          Local LLM features will not be available.
    )
) else (
    echo.
    echo Installing llama-cpp-python (CPU-only^)...
    python -m pip install "llama-cpp-python[server]>=0.3.16"
    if errorlevel 1 (
        echo WARNING: llama-cpp-python installation failed.
        echo          Local LLM features will not be available.
    )
)
:llama_done

REM -------------------------------------------------------
REM  6. Verify critical imports
REM -------------------------------------------------------
echo.
echo Verifying imports...
python -c "from kiassist_utils.main import KiAssistAPI; print('  kiassist_utils  OK')"
if errorlevel 1 (
    echo WARNING: kiassist_utils import failed – check errors above.
)
python -c "import llama_cpp; print('  llama_cpp       OK  (version %%s, GPU offload: %%s)' %% (llama_cpp.__version__, llama_cpp.llama_supports_gpu_offload()))" 2>nul
if errorlevel 1 (
    echo WARNING: llama_cpp not available – local LLM features disabled.
)

REM -------------------------------------------------------
REM  7. npm / frontend (unless --skip-npm)
REM -------------------------------------------------------
if "%SKIP_NPM%"=="1" (
    echo.
    echo Skipping npm install / frontend build (--skip-npm).
) else (
    echo.
    echo Installing npm dependencies...
    call npm install
    if errorlevel 1 (
        echo WARNING: npm install failed.
    )
)

echo.
echo ============================================
echo  Setup complete.
echo ============================================
echo.
echo  To activate the environment later:
echo    call venv\Scripts\activate.bat
echo.
echo  To start in dev mode:
echo    npm run dev          (in one terminal)
echo    python -m kiassist_utils.main --dev
echo.

endlocal
