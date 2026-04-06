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

    # Auto-detect GPU architecture for optimal CUDA kernels
    GPU_CAP=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>/dev/null | head -1 | tr -d '.')
    if [[ -n "$GPU_CAP" ]]; then
        CUDA_ARCH="$GPU_CAP"
        echo "  GPU compute capability: ${GPU_CAP:0:1}.${GPU_CAP:1} → CUDA arch ${CUDA_ARCH}"
    else
        # Default: build for common architectures (Turing, Ampere, Ada, Hopper, Blackwell)
        CUDA_ARCH="75;80;86;89;90;100"
        echo "  Could not detect GPU arch – building for all common architectures"
    fi

    # Strategy 1: Build from source with CUDA + Ninja (requires nvcc)
    if command -v nvcc &>/dev/null; then
        echo "  Installing build dependencies..."
        python -m pip install scikit-build-core cmake ninja -q

        # Use Ninja for fast parallel CUDA compilation
        GENERATOR="Ninja"
        if ! command -v ninja &>/dev/null; then
            # ninja not on PATH – fall back to Unix Makefiles
            echo "  ninja not found, using Unix Makefiles (slower)"
            GENERATOR="Unix Makefiles"
        fi

        echo "  Building from source with CUDA support (Ninja parallel build)..."
        echo "  This may take 5-15 minutes on first build..."
        CMAKE_ARGS="-DGGML_CUDA=on -DCMAKE_CUDA_ARCHITECTURES=${CUDA_ARCH}" \
        CMAKE_GENERATOR="${GENERATOR}" \
        FORCE_CMAKE=1 \
            python -m pip install "llama-cpp-python[server]>=0.3.20" \
                --force-reinstall --no-binary llama-cpp-python --no-cache-dir \
                --no-build-isolation && LLAMA_OK=1
        if [[ "$LLAMA_OK" -eq 1 ]]; then
            echo "  CUDA source build completed!"
        else
            echo "  WARNING: CUDA build failed."
        fi
    else
        echo "  nvcc not found – cannot build with CUDA."
        echo "  Install the CUDA Toolkit: https://developer.nvidia.com/cuda-downloads"
    fi

    # Strategy 2: Fall back to CPU wheel
    if [[ "$LLAMA_OK" -eq 0 ]]; then
        echo "  Falling back to CPU-only wheel..."
        python -m pip install "llama-cpp-python[server]>=0.3.20" || \
            echo "WARNING: llama-cpp-python installation failed. Local LLM features disabled."
    fi
else
    echo ""
    echo "Installing llama-cpp-python (CPU-only)..."
    python -m pip install "llama-cpp-python[server]>=0.3.20" || \
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
