#!/bin/bash
# Build script for creating KiAssist distributable packages

set -e

echo "Building KiAssist for distribution..."

# Step 1: Install dependencies
echo "Step 1: Installing dependencies..."
npm install
cd python-lib
pip install -e ".[dev]"
cd ..

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
