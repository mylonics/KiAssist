#!/bin/bash
# Startup script for KiAssist (Unix-like systems)

set -e

echo "Starting KiAssist..."

# Check if dist directory exists
if [ ! -d "dist" ]; then
    echo "Building frontend..."
    npm run build
fi

# Install Python dependencies if needed
echo "Checking Python dependencies..."
cd python-lib
pip install -e . > /dev/null 2>&1 || echo "Python dependencies already installed"
cd ..

# Run the Python application
echo "Launching application..."
python -m kiassist_utils.main
