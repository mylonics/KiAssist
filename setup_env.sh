#!/usr/bin/env bash
# =============================================================
#  KiAssist – Python environment bootstrap (Linux / macOS)
#
#  This script:
#   1. Locates a compatible Python (3.10 – 3.12).
#   2. Creates / re-uses a venv in ./venv.
#   3. Installs all project dependencies (core + ai + local-llm).
#   4. If the NVIDIA CUDA Toolkit is available (nvcc) it builds
#      llama-cpp-python with GPU (CUDA) support; otherwise it
#      installs the CPU-only wheel from PyPI.
#   5. Installs npm / Node dependencies.
#
#  Usage:
#    ./setup_env.sh              – full setup (default)
#    ./setup_env.sh --skip-npm   – skip npm install
# =============================================================
set -euo pipefail

SKIP_NPM=0
if [[ "${1:-}" == "--skip-npm" ]]; then
    SKIP_NPM=1
fi

echo ""
echo "============================================"
echo " KiAssist – Environment Setup"
echo "============================================"
echo ""

# -------------------------------------------------------
#  1. Find a compatible Python interpreter (3.10 – 3.12)
# -------------------------------------------------------
PY=""
for candidate in python3.12 python3 python; do
    if command -v "$candidate" &>/dev/null; then
        ver=$("$candidate" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || true)
        if [[ "$ver" == "3.12" ]]; then
            PY="$candidate"
            echo "Found Python 3.12 ($candidate)."
            break
        fi
    fi
done

if [[ -z "$PY" ]]; then
    echo "ERROR: Python 3.12 not found."
    echo "       Install Python 3.12 from https://www.python.org/downloads/"
    exit 1
fi

echo "Using: $PY"
$PY --version

# -------------------------------------------------------
#  2. Create virtual environment
# -------------------------------------------------------
if [[ ! -d "venv" ]]; then
    echo ""
    echo "Creating virtual environment..."
    $PY -m venv venv
fi

# shellcheck disable=SC1091
source venv/bin/activate

echo ""
echo "Activated venv – Python executable:"
python --version
python -c "import sys; print(sys.executable)"

# -------------------------------------------------------
#  3. Upgrade pip / setuptools / wheel
# -------------------------------------------------------
echo ""
echo "Upgrading pip..."
python -m pip install --upgrade pip setuptools wheel -q

# -------------------------------------------------------
#  4. Install project dependencies (core + ai extras)
# -------------------------------------------------------
echo ""
echo "Installing project dependencies..."
cd python-lib
python -m pip install -e ".[dev,ai]"
cd ..

# -------------------------------------------------------
#  5. Install llama-cpp-python (with CUDA if available)
# -------------------------------------------------------
echo ""
echo "Detecting GPU / CUDA support..."

HAS_CUDA=0
CUDA_VER=""

# Check for NVIDIA GPU via nvidia-smi
if command -v nvidia-smi &>/dev/null; then
    echo "  NVIDIA GPU detected (nvidia-smi found)"

    # Check for CUDA Toolkit (nvcc)
    if command -v nvcc &>/dev/null; then
        echo "  CUDA Toolkit detected (nvcc found)"
        HAS_CUDA=1
        CUDA_VER=$(nvcc --version 2>/dev/null | grep "release" | sed 's/.*release //' | sed 's/,.*//')
        echo "  CUDA version: $CUDA_VER"
    else
        echo "  nvcc not found – will try pre-built CUDA wheel"
        # Even without nvcc, we can use pre-built CUDA wheels if GPU present
        HAS_CUDA=1
    fi
else
    echo "  No NVIDIA GPU detected – installing CPU-only wheel."
fi

LLAMA_OK=0

if [[ "$HAS_CUDA" -eq 1 ]]; then
    echo ""
    echo "Installing llama-cpp-python with CUDA GPU support..."

    # Strategy 1: Try pre-built CUDA wheel from dougeeai/llama-cpp-python-wheels (Windows only)
    if [[ "$(uname -s)" == *"MINGW"* ]] || [[ "$(uname -s)" == *"MSYS"* ]] || [[ "$(uname -s)" == *"CYGWIN"* ]] || [[ "$(uname -s)" == *"Windows"* ]]; then
        GPU_CAP=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>/dev/null | head -1)
        GPU_ARCH=""
        WHEEL_ARCH=""
        case "${GPU_CAP}" in
            8.9*) GPU_ARCH="sm89"; WHEEL_ARCH="sm89.ada" ;;
            8.6*) GPU_ARCH="sm86"; WHEEL_ARCH="sm86.ampere" ;;
            7.5*) GPU_ARCH="sm75"; WHEEL_ARCH="sm75.turing" ;;
            10.0*) GPU_ARCH="sm100"; WHEEL_ARCH="sm100.blackwell" ;;
        esac
        PY_VER=$(python -c "import sys; print(f'{sys.version_info.major}{sys.version_info.minor}')")
        PY_TAG="cp${PY_VER}"
        if [[ -n "$GPU_ARCH" ]]; then
            WHEEL_BASE="https://github.com/dougeeai/llama-cpp-python-wheels/releases/download"
            WHEEL_NAME="llama_cpp_python-0.3.16+cuda12.1.${WHEEL_ARCH}-${PY_TAG}-${PY_TAG}-win_amd64.whl"
            WHEEL_TAG="v0.3.16-cuda12.1-${GPU_ARCH}-py${PY_VER:1}" # e.g. py312
            echo "  Trying pre-built CUDA wheel: $WHEEL_NAME"
            if python -m pip install "${WHEEL_BASE}/${WHEEL_TAG}/${WHEEL_NAME}" --force-reinstall 2>/dev/null; then
                python -m pip install uvicorn fastapi sse-starlette starlette-context pydantic-settings 2>/dev/null
                if python -c "import llama_cpp; assert llama_cpp.llama_supports_gpu_offload()" 2>/dev/null; then
                    echo "  Pre-built CUDA wheel installed with GPU offload!"
                    LLAMA_OK=1
                else
                    echo "  Pre-built wheel did not enable GPU offload."
                    python -m pip uninstall llama-cpp-python -y &>/dev/null
                fi
            else
                echo "  Pre-built wheel not available for this configuration."
            fi
        fi
    fi

    # Strategy 2: Build from source with CUDA flags (requires nvcc)
    if [[ "$LLAMA_OK" -eq 0 ]] && command -v nvcc &>/dev/null; then
        echo "  Building from source with CUDA support (this may take 10-30 minutes)..."
        CMAKE_ARGS="-DGGML_CUDA=on" FORCE_CMAKE=1 \
            python -m pip install "llama-cpp-python[server]>=0.3.16" \
                --force-reinstall --no-binary llama-cpp-python --no-cache-dir && LLAMA_OK=1
        if [[ "$LLAMA_OK" -eq 1 ]]; then
            echo "  CUDA source build completed!"
        else
            echo "  WARNING: CUDA build failed."
        fi
    fi

    # Strategy 3: Fall back to CPU wheel
    if [[ "$LLAMA_OK" -eq 0 ]]; then
        echo "  Falling back to CPU-only wheel..."
        python -m pip install "llama-cpp-python[server]>=0.3.16" || \
            echo "WARNING: llama-cpp-python installation failed. Local LLM features disabled."
    fi
else
    echo ""
    echo "Installing llama-cpp-python (CPU-only)..."
    python -m pip install "llama-cpp-python[server]>=0.3.16" || \
        echo "WARNING: llama-cpp-python installation failed. Local LLM features disabled."
fi

# -------------------------------------------------------
#  6. Verify critical imports
# -------------------------------------------------------
echo ""
echo "Verifying imports..."
python -c "from kiassist_utils.main import KiAssistAPI; print('  kiassist_utils  OK')" || \
    echo "WARNING: kiassist_utils import failed – check errors above."
python -c "import llama_cpp; print(f'  llama_cpp       OK  (version {llama_cpp.__version__}, GPU offload: {llama_cpp.llama_supports_gpu_offload()})')" 2>/dev/null || \
    echo "WARNING: llama_cpp not available – local LLM features disabled."

# -------------------------------------------------------
#  7. npm / frontend (unless --skip-npm)
# -------------------------------------------------------
if [[ "$SKIP_NPM" -eq 1 ]]; then
    echo ""
    echo "Skipping npm install (--skip-npm)."
else
    echo ""
    echo "Installing npm dependencies..."
    npm install || echo "WARNING: npm install failed."
fi

echo ""
echo "============================================"
echo " Setup complete."
echo "============================================"
echo ""
echo " To activate the environment later:"
echo "   source venv/bin/activate"
echo ""
echo " To start in dev mode:"
echo "   npm run dev          (in one terminal)"
echo "   python -m kiassist_utils.main --dev"
echo ""
