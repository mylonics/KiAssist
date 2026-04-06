@echo off
echo === Setting up MSVC environment ===
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvarsall.bat" x64
echo cl.exe location:
where cl.exe

echo === Activating Python venv ===
call "c:\Users\Rijesh\Source\KiAssist\venv\Scripts\activate.bat"

echo === Detecting GPU architecture ===
set "CUDA_ARCH=89"
for /f "tokens=*" %%G in ('nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2^>nul') do (
    set "GPU_CAP=%%G"
    set "CUDA_ARCH=%%G"
    set "CUDA_ARCH=!CUDA_ARCH:.=!"
)
echo   GPU compute capability: %GPU_CAP% (arch: %CUDA_ARCH%)

echo === Installing build dependencies ===
pip install scikit-build-core cmake ninja

echo === Setting CMAKE variables ===
set CMAKE_ARGS=-DGGML_CUDA=on -DCMAKE_CUDA_ARCHITECTURES=%CUDA_ARCH%
set CMAKE_GENERATOR=Ninja
set FORCE_CMAKE=1

echo === Starting build ===
pip install "llama-cpp-python[server]==0.3.20" --force-reinstall --no-binary llama-cpp-python --no-cache-dir --no-build-isolation -v

echo === Build complete - exit code: %ERRORLEVEL% ===
echo === Verifying ===
python -c "import llama_cpp; print('Version:', llama_cpp.__version__); print('GPU offload:', llama_cpp.llama_supports_gpu_offload())"
