#!/bin/bash
# Startup script for KiAssist (Unix-like systems)

set -e

echo "Starting KiAssist..."

# Bootstrap environment if venv doesn't exist
if [[ ! -d "venv" ]]; then
    echo "No virtual environment found – running first-time setup..."
    ./setup_env.sh
fi

# Activate virtual environment
source venv/bin/activate

# Quick sanity check – correct Python version?
python -c 'import sys; v=sys.version_info; exit(0 if v.minor==12 and v.major==3 else 1)' || {
    echo "Error: venv uses an incompatible Python. Python 3.12 is required."
    echo "       Delete venv/ and re-run setup_env.sh."
    exit 1
}

# Install / update Python dependencies
echo "Checking Python dependencies..."
cd python-lib
python -m pip install -e ".[ai]" -q 2>/dev/null || echo "Warning: pip install failed."
cd ..

# Build frontend
echo "Building frontend..."
npm run build

# Run the Python application
echo "Launching application..."
python -m kiassist_utils.main
