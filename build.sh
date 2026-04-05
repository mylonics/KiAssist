#!/bin/bash
# Build script for creating KiAssist distributable packages

set -e

echo "Building KiAssist for distribution..."

# Step 1: Setup environment
echo "Step 1: Setting up environment..."
if [[ ! -d "venv" ]]; then
    ./setup_env.sh
else
    source venv/bin/activate
    echo "Checking Python dependencies..."
    cd python-lib
    python -m pip install -e ".[dev,ai]" -q
    cd ..
    npm install
fi

# Step 2: Build the frontend
echo "Step 2: Building frontend..."
npm run build

# Step 3: Build the executable with PyInstaller
echo "Step 3: Building executable..."
pyinstaller kiassist.spec --clean

echo ""
echo "✓ Build complete!"
echo ""
echo "Distributable files are in the 'dist' directory"
